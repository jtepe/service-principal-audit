"""Tests for Directory Role collection across the four assignment paths.

The pure mapper (`directory_role_from_schedule`) is exercised directly; the
collector is driven through small fakes mimicking the kiota builder chain
(`role_management.directory.role_assignment_schedules` /
`role_eligibility_schedules` paging), so the four-path/via-group logic and the
per-group schedule single-flight cache are tested without a live tenant.
"""

from __future__ import annotations

import asyncio
import datetime
from typing import cast

from msgraph import GraphServiceClient
from msgraph.generated.models.expiration_pattern import ExpirationPattern
from msgraph.generated.models.request_schedule import RequestSchedule
from msgraph.generated.models.unified_role_assignment_schedule import (
    UnifiedRoleAssignmentSchedule,
)
from msgraph.generated.models.unified_role_definition import UnifiedRoleDefinition
from msgraph.generated.models.unified_role_eligibility_schedule import (
    UnifiedRoleEligibilitySchedule,
)

from sp_audit.entra import (
    collect_directory_roles,
    directory_role_from_schedule,
)
from sp_audit.models import GroupMembershipRecord
from sp_audit.single_flight import SingleFlight

UTC = datetime.UTC


def _schedule(
    cls: type,
    role_name: str | None,
    *,
    scope: str | None = "/",
    start: datetime.datetime | None = None,
    end: datetime.datetime | None = None,
):
    role_def = UnifiedRoleDefinition(display_name=role_name) if role_name else None
    expiration = ExpirationPattern(end_date_time=end) if end is not None else None
    info = RequestSchedule(start_date_time=start, expiration=expiration)
    return cls(role_definition=role_def, directory_scope_id=scope, schedule_info=info)


def test_mapper_extracts_raw_facts() -> None:
    start = datetime.datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime.datetime(2027, 1, 1, tzinfo=UTC)
    schedule = _schedule(
        UnifiedRoleAssignmentSchedule,
        "Global Reader",
        scope="/",
        start=start,
        end=end,
    )

    record = directory_role_from_schedule(schedule, "active", "direct", None)

    assert record == {
        "roleName": "Global Reader",
        "assignmentType": "active",
        "source": "direct",
        "sourceGroupId": None,
        "directoryScopeId": "/",
        "startDateTime": start.isoformat(),
        "endDateTime": end.isoformat(),
    }


def test_mapper_tolerates_missing_role_definition_and_dates() -> None:
    schedule = _schedule(UnifiedRoleEligibilitySchedule, None, scope=None)

    record = directory_role_from_schedule(schedule, "eligible", "finance-admins", "g-1")

    assert record == {
        "roleName": None,
        "assignmentType": "eligible",
        "source": "finance-admins",
        "sourceGroupId": "g-1",
        "directoryScopeId": None,
        "startDateTime": None,
        "endDateTime": None,
    }


class FakeSchedulesBuilder:
    """Pages a fixed list of FakePage; counts underlying GETs."""

    def __init__(self, pages: list[FakePage]) -> None:
        self._pages = pages
        self.get_calls = 0

    async def get(self, request_configuration: object = None) -> FakePage:
        self.get_calls += 1
        return self._pages[0]

    def with_url(self, url: str) -> FakeSchedulesBuilder:
        return FakeSchedulesBuilder(self._pages[1:])


class FakePage:
    def __init__(self, value: list[object], next_link: str | None = None) -> None:
        self.value = value
        self.odata_next_link = next_link


class FakeDirectory:
    def __init__(
        self,
        assignments_by_principal: dict[str, list[object]],
        eligibilities_by_principal: dict[str, list[object]],
        counter: dict[str, int],
    ) -> None:
        self._assignments = assignments_by_principal
        self._eligibilities = eligibilities_by_principal
        self._counter = counter
        self._active_builder = _FilterRouter(assignments_by_principal, counter)
        self._eligible_builder = _FilterRouter(eligibilities_by_principal, counter)

    @property
    def role_assignment_schedules(self) -> _FilterRouter:
        return self._active_builder

    @property
    def role_eligibility_schedules(self) -> _FilterRouter:
        return self._eligible_builder


class _FilterRouter:
    """Returns schedules for whichever principalId the $filter names.

    The collector builds one RequestConfiguration per principal; this fake reads
    the filter back out to route, and bumps a per-principal counter so tests can
    assert a group's schedules were fetched exactly once across SPs.
    """

    def __init__(self, by_principal: dict[str, list[object]], counter: dict[str, int]):
        self._by_principal = by_principal
        self._counter = counter

    async def get(self, request_configuration) -> FakePage:
        flt = request_configuration.query_parameters.filter
        principal_id = flt.split("'")[1]
        self._counter[principal_id] = self._counter.get(principal_id, 0) + 1
        return FakePage(self._by_principal.get(principal_id, []))


class FakeRoleManagement:
    def __init__(self, directory: FakeDirectory) -> None:
        self.directory = directory


class FakeClient:
    def __init__(self, directory: FakeDirectory) -> None:
        self.role_management = FakeRoleManagement(directory)


def _membership(group_id: str, name: str, *, assignable: bool) -> GroupMembershipRecord:
    return {
        "groupId": group_id,
        "displayName": name,
        "membershipType": "transitive",
        "isAssignableToRole": assignable,
    }


def test_collects_all_four_paths_with_via_group_attribution() -> None:
    counter: dict[str, int] = {}
    directory = FakeDirectory(
        assignments_by_principal={
            "sp-1": [_schedule(UnifiedRoleAssignmentSchedule, "Global Reader")],
            "g-1": [_schedule(UnifiedRoleAssignmentSchedule, "Groups Administrator")],
        },
        eligibilities_by_principal={
            "sp-1": [_schedule(UnifiedRoleEligibilitySchedule, "User Administrator")],
            "g-1": [_schedule(UnifiedRoleEligibilitySchedule, "Helpdesk Admin")],
        },
        counter=counter,
    )
    client = cast(GraphServiceClient, FakeClient(directory))
    memberships = [
        _membership("g-1", "finance-admins", assignable=True),
        _membership("g-2", "plain-group", assignable=False),  # not queried
    ]
    cache: SingleFlight[str, list] = SingleFlight()

    roles = asyncio.run(collect_directory_roles(client, "sp-1", memberships, cache))

    by_name = {r["roleName"]: r for r in roles}
    assert by_name["Global Reader"]["assignmentType"] == "active"
    assert by_name["Global Reader"]["source"] == "direct"
    assert by_name["Global Reader"]["sourceGroupId"] is None
    assert by_name["User Administrator"]["assignmentType"] == "eligible"
    assert by_name["User Administrator"]["source"] == "direct"
    # via-group active + eligible, attributed to the group
    assert by_name["Groups Administrator"]["source"] == "finance-admins"
    assert by_name["Groups Administrator"]["sourceGroupId"] == "g-1"
    assert by_name["Groups Administrator"]["assignmentType"] == "active"
    assert by_name["Helpdesk Admin"]["source"] == "finance-admins"
    assert by_name["Helpdesk Admin"]["assignmentType"] == "eligible"
    # the non-role-assignable group was never queried
    assert "g-2" not in counter


def test_group_schedules_fetched_once_across_sps_via_cache() -> None:
    counter: dict[str, int] = {}
    directory = FakeDirectory(
        assignments_by_principal={
            "g-1": [_schedule(UnifiedRoleAssignmentSchedule, "Groups Administrator")],
        },
        eligibilities_by_principal={},
        counter=counter,
    )
    client = cast(GraphServiceClient, FakeClient(directory))
    cache: SingleFlight[str, list] = SingleFlight()
    membership = [_membership("g-1", "shared-group", assignable=True)]

    async def scenario() -> None:
        # two different SPs both reach the same role-assignable group
        await collect_directory_roles(client, "sp-a", membership, cache)
        await collect_directory_roles(client, "sp-b", membership, cache)

    asyncio.run(scenario())

    # one cached fetch for the whole run = one active + one eligible call;
    # without the cache the second SP would double it to 4.
    assert counter["g-1"] == 2


def test_schedule_failure_propagates_for_sp_gap() -> None:
    class Boom:
        async def get(self, request_configuration=None):
            raise PermissionError("403 Forbidden")

        def with_url(self, url: str):  # pragma: no cover
            return self

    class BoomDirectory:
        role_assignment_schedules = Boom()
        role_eligibility_schedules = Boom()

    class BoomClient:
        role_management = type("RM", (), {"directory": BoomDirectory()})()

    client = cast(GraphServiceClient, BoomClient())
    cache: SingleFlight[str, list] = SingleFlight()

    try:
        asyncio.run(collect_directory_roles(client, "sp-1", [], cache))
    except PermissionError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected the schedule failure to propagate")
