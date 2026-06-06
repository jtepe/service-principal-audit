"""Tests for group-membership collection and the group-name resolver.

These drive the Graph client through small fakes that mimic the kiota builder
chain (`member_of`/`transitive_member_of` paging and `groups.by_group_id`),
so the collection logic is exercised without a live tenant.
"""

from __future__ import annotations

import asyncio
from typing import cast

from msgraph import GraphServiceClient
from msgraph.generated.models.directory_object import DirectoryObject
from msgraph.generated.models.group import Group

from sp_audit.entra import (
    collect_by_object_ids,
    collect_group_memberships,
    resolve_group_name,
)
from sp_audit.single_flight import SingleFlight


class FakePage:
    def __init__(self, value: list[object], next_link: str | None = None) -> None:
        self.value = value
        self.odata_next_link = next_link


class FakeMembersBuilder:
    """Pages over a fixed list of FakePage; counts underlying GETs."""

    def __init__(self, pages: list[FakePage]) -> None:
        self._pages = pages
        self.get_calls = 0

    async def get(self, request_configuration: object = None) -> FakePage:
        self.get_calls += 1
        return self._pages[0]

    def with_url(self, url: str) -> FakeMembersBuilder:
        return FakeMembersBuilder(self._pages[1:])


class FakeSpItem:
    def __init__(self, member_of: object, transitive: object) -> None:
        self.member_of = member_of
        self.transitive_member_of = transitive


class FakeServicePrincipals:
    def __init__(self, item: FakeSpItem) -> None:
        self._item = item

    def by_service_principal_id(self, object_id: str) -> FakeSpItem:
        return self._item


class FakeGroupItem:
    def __init__(self, group: Group | None, counter: list[int]) -> None:
        self._group = group
        self._counter = counter

    async def get(self, request_configuration: object = None) -> Group | None:
        self._counter[0] += 1
        return self._group


class FakeGroups:
    def __init__(self, by_id: dict[str, Group], counter: list[int]) -> None:
        self._by_id = by_id
        self._counter = counter

    def by_group_id(self, group_id: str) -> FakeGroupItem:
        return FakeGroupItem(self._by_id.get(group_id), self._counter)


class FakeClient:
    def __init__(
        self,
        item: FakeSpItem | None = None,
        groups: FakeGroups | None = None,
    ) -> None:
        if item is not None:
            self.service_principals = FakeServicePrincipals(item)
        if groups is not None:
            self.groups = groups


def test_collects_direct_and_transitive_labeled_and_filters_non_groups() -> None:
    direct = FakeMembersBuilder(
        [
            FakePage(
                [
                    Group(id="g-1", display_name="direct-group"),
                    DirectoryObject(id="role-1"),  # not a group: dropped
                ]
            )
        ]
    )
    transitive = FakeMembersBuilder(
        [FakePage([Group(id="g-2", display_name="nested-group")])]
    )
    client = cast(GraphServiceClient, FakeClient(item=FakeSpItem(direct, transitive)))

    memberships = asyncio.run(collect_group_memberships(client, "sp-oid"))

    assert memberships == [
        {
            "groupId": "g-1",
            "displayName": "direct-group",
            "membershipType": "direct",
            "isAssignableToRole": None,
        },
        {
            "groupId": "g-2",
            "displayName": "nested-group",
            "membershipType": "transitive",
            "isAssignableToRole": None,
        },
    ]


def test_both_membership_queries_page_fully() -> None:
    direct = FakeMembersBuilder(
        [
            FakePage([Group(id="g-1", display_name="a")], next_link="next-direct"),
            FakePage([Group(id="g-2", display_name="b")]),
        ]
    )
    transitive = FakeMembersBuilder(
        [
            FakePage([Group(id="g-3", display_name="c")], next_link="next-trans"),
            FakePage([Group(id="g-4", display_name="d")]),
        ]
    )
    client = cast(GraphServiceClient, FakeClient(item=FakeSpItem(direct, transitive)))

    memberships = asyncio.run(collect_group_memberships(client, "sp-oid"))

    ids = [m["groupId"] for m in memberships]
    assert ids == ["g-1", "g-2", "g-3", "g-4"]


def test_resolver_caches_via_single_flight() -> None:
    counter = [0]
    groups = FakeGroups({"g-1": Group(id="g-1", display_name="finance")}, counter)
    client = cast(GraphServiceClient, FakeClient(groups=groups))

    async def scenario() -> tuple[str | None, str | None]:
        sf: SingleFlight[str, str | None] = SingleFlight()
        first = await resolve_group_name(client, sf, "g-1")
        second = await resolve_group_name(client, sf, "g-1")
        return first, second

    first, second = asyncio.run(scenario())

    assert (first, second) == ("finance", "finance")
    assert counter[0] == 1  # repeat lookup did not refetch


class FailingMembersBuilder:
    async def get(self, request_configuration: object = None) -> FakePage:
        raise PermissionError("403 Forbidden")

    def with_url(self, url: str) -> FailingMembersBuilder:  # pragma: no cover
        return self


def test_membership_failure_records_sp_gap_and_continues(monkeypatch) -> None:
    async def fake_resolve(client: object, object_id: str) -> object:
        return _StubSp()

    import sp_audit.entra as entra

    monkeypatch.setattr(entra, "_resolve_service_principal", fake_resolve)

    failing_item = FakeSpItem(FailingMembersBuilder(), FailingMembersBuilder())
    client = cast(GraphServiceClient, FakeClient(item=failing_item))

    records, _ = asyncio.run(collect_by_object_ids(client, ["sp-oid"]))
    record = records[0]

    assert record["groupMemberships"] == []
    assert any("group membership" in err.lower() for err in record["errors"])


class _StubSp:
    id = "sp-oid"
    app_id = None
    display_name = "sp"
    tags = None
