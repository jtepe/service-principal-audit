"""Entra (directory-plane) identity collector.

Resolves a Service Principal strictly by object id via
`GET /servicePrincipals/{id}`, falling back to an `appId eq` filter only on a
404, and attaches its related Application as a nullable object. The pure
mapping functions (Graph model -> record) are network-free so they can be
unit-tested without a live Graph client.
"""

from __future__ import annotations

from typing import Any, Literal

from kiota_abstractions.api_error import APIError
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph import GraphServiceClient
from msgraph.generated.applications.applications_request_builder import (
    ApplicationsRequestBuilder,
)
from msgraph.generated.groups.item.group_item_request_builder import (
    GroupItemRequestBuilder,
)
from msgraph.generated.models.application import Application
from msgraph.generated.models.group import Group
from msgraph.generated.models.service_principal import ServicePrincipal
from msgraph.generated.service_principals.item.member_of.member_of_request_builder import (  # noqa: E501
    MemberOfRequestBuilder,
)
from msgraph.generated.service_principals.item.service_principal_item_request_builder import (  # noqa: E501
    ServicePrincipalItemRequestBuilder,
)
from msgraph.generated.service_principals.item.transitive_member_of.transitive_member_of_request_builder import (  # noqa: E501
    TransitiveMemberOfRequestBuilder,
)
from msgraph.generated.service_principals.service_principals_request_builder import (
    ServicePrincipalsRequestBuilder,
)

from .models import (
    ApplicationRecord,
    GroupMembershipRecord,
    ServicePrincipalRecord,
)
from .single_flight import SingleFlight

# Unified $select so SP-side fields are never path-dependent across the
# selection routes (by object id, appId-eq fallback, and tag query). Every SP
# enters the per-SP fan-out with the same baseline, including the credential
# fields, so SP-side credentials are never path-dependent.
SP_SELECT = [
    "id",
    "displayName",
    "appId",
    "tags",
    "passwordCredentials",
    "keyCredentials",
]
APP_SELECT = ["id", "displayName", "appId"]
GROUP_SELECT = ["id", "displayName", "isAssignableToRole"]


def application_record_from_graph(app: Application) -> ApplicationRecord:
    """Map a Graph Application onto the nullable attached Application record."""
    return {
        "objectId": app.id,
        "appId": app.app_id,
        "displayName": app.display_name,
    }


def sp_record_from_graph(
    sp: ServicePrincipal, application: Application | None
) -> ServicePrincipalRecord:
    """Map a Graph servicePrincipal (+ optional Application) onto a record.

    Pure: no network, no clock. `objectId` must be present on a resolved SP;
    everything else degrades to `None`/empty.
    """
    if sp.id is None:
        raise ValueError("Resolved service principal has no object id")
    return {
        "objectId": sp.id,
        "appId": sp.app_id,
        "displayName": sp.display_name,
        "tags": list(sp.tags) if sp.tags else [],
        "application": (
            application_record_from_graph(application)
            if application is not None
            else None
        ),
        # Azure RBAC plane is folded in by the CLI after the ARG batch query;
        # every record starts with an empty (directory-plane-only) list.
        "azureRoleAssignments": [],
        "groupMemberships": [],
        "errors": [],
    }


def group_membership_from_graph(
    group: Group, membership_type: Literal["direct", "transitive"]
) -> GroupMembershipRecord:
    """Map a Graph Group onto a membership record, labeling how it is held.

    Pure: no network. `membership_type` is supplied by the caller — `member_of`
    yields `direct`, `transitiveMemberOf` yields `transitive`.
    """
    return {
        "groupId": group.id,
        "displayName": group.display_name,
        "membershipType": membership_type,
        "isAssignableToRole": group.is_assignable_to_role,
    }


async def _resolve_service_principal(
    client: GraphServiceClient, object_id: str
) -> ServicePrincipal:
    """Resolve by object id; fall back to `appId eq '{id}'` only on a 404."""
    item_config = RequestConfiguration(
        query_parameters=ServicePrincipalItemRequestBuilder.ServicePrincipalItemRequestBuilderGetQueryParameters(
            select=SP_SELECT,
        )
    )
    try:
        sp = await client.service_principals.by_service_principal_id(object_id).get(
            request_configuration=item_config
        )
    except APIError as exc:
        if exc.response_status_code != 404:
            raise
        sp = None

    if sp is not None:
        return sp

    # 404 on the object-id route: try resolving the value as an appId.
    escaped = object_id.replace("'", "''")
    list_config = RequestConfiguration(
        query_parameters=ServicePrincipalsRequestBuilder.ServicePrincipalsRequestBuilderGetQueryParameters(
            filter=f"appId eq '{escaped}'",
            select=SP_SELECT,
        )
    )
    page = await client.service_principals.get(request_configuration=list_config)
    matches = page.value if page is not None and page.value else []
    if not matches:
        raise LookupError(
            f"No service principal found by object id or appId '{object_id}'."
        )
    return matches[0]


async def _resolve_application(
    client: GraphServiceClient, app_id: str
) -> Application | None:
    """Fetch the related Application by `appId eq`; `None` if there is none."""
    escaped = app_id.replace("'", "''")
    config = RequestConfiguration(
        query_parameters=ApplicationsRequestBuilder.ApplicationsRequestBuilderGetQueryParameters(
            filter=f"appId eq '{escaped}'",
            select=APP_SELECT,
        )
    )
    page = await client.applications.get(request_configuration=config)
    matches = page.value if page is not None and page.value else []
    return matches[0] if matches else None


async def _record_with_application(
    client: GraphServiceClient, sp: ServicePrincipal
) -> ServicePrincipalRecord:
    """Attach the related Application (if any) and map to a record."""
    application: Application | None = None
    if sp.app_id:
        application = await _resolve_application(client, sp.app_id)
    return sp_record_from_graph(sp, application)


async def _page_groups(builder: Any, config: RequestConfiguration) -> list[Group]:
    """Follow `@odata.nextLink` over a membership builder, keeping only groups.

    `memberOf`/`transitiveMemberOf` return mixed directory objects (groups,
    directory roles, administrative units); only `Group` entries are memberships
    in the sense this audit cares about.
    """
    groups: list[Group] = []
    page = await builder.get(request_configuration=config)
    while page is not None:
        for obj in page.value or []:
            if isinstance(obj, Group):
                groups.append(obj)
        next_link = page.odata_next_link
        if not next_link:
            break
        page = await builder.with_url(next_link).get()
    return groups


async def collect_group_memberships(
    client: GraphServiceClient, object_id: str
) -> list[GroupMembershipRecord]:
    """Collect a Service Principal's group memberships, labeled by how held.

    Pages `memberOf` (labeled `direct`) and `transitiveMemberOf` (labeled
    `transitive`), requesting `isAssignableToRole` so downstream via-group
    attribution can decide which groups actually confer a directory role.
    """
    sp_item = client.service_principals.by_service_principal_id(object_id)
    direct_config = RequestConfiguration(
        query_parameters=MemberOfRequestBuilder.MemberOfRequestBuilderGetQueryParameters(
            select=GROUP_SELECT,
        )
    )
    transitive_config = RequestConfiguration(
        query_parameters=TransitiveMemberOfRequestBuilder.TransitiveMemberOfRequestBuilderGetQueryParameters(
            select=GROUP_SELECT,
        )
    )
    memberships = [
        group_membership_from_graph(group, "direct")
        for group in await _page_groups(sp_item.member_of, direct_config)
    ]
    memberships.extend(
        group_membership_from_graph(group, "transitive")
        for group in await _page_groups(sp_item.transitive_member_of, transitive_config)
    )
    return memberships


async def resolve_group_name(
    client: GraphServiceClient,
    single_flight: SingleFlight[str, str | None],
    group_id: str,
) -> str | None:
    """Resolve a group's display name, fetching at most once per group id.

    Backed by `single_flight`: concurrent or repeat lookups for the same group
    share one `GET /groups/{id}` instead of refetching. This is the pattern
    slices 5 and 8 reuse for their own per-group lookups.
    """

    async def fetch() -> str | None:
        config = RequestConfiguration(
            query_parameters=GroupItemRequestBuilder.GroupItemRequestBuilderGetQueryParameters(
                select=["id", "displayName"],
            )
        )
        group = await client.groups.by_group_id(group_id).get(
            request_configuration=config
        )
        return group.display_name if group is not None else None

    return await single_flight.do(group_id, fetch)


async def _collect_for_service_principal(
    client: GraphServiceClient, sp: ServicePrincipal
) -> ServicePrincipalRecord:
    """Build a record for an already-resolved SP, including group memberships.

    A failure gathering group memberships degrades to an SP Gap in the record's
    `errors[]` rather than aborting the whole SP (ADR-0002 two-tier failures).
    """
    record = await _record_with_application(client, sp)
    try:
        record["groupMemberships"] = await collect_group_memberships(
            client, record["objectId"]
        )
    except Exception as exc:  # noqa: BLE001 - degrade to an SP Gap, never abort
        record["errors"].append(f"Failed to collect group memberships: {exc}")
    return record


async def collect_service_principal(
    client: GraphServiceClient, object_id: str
) -> ServicePrincipalRecord:
    """Collect one Service Principal's identity, Application, and memberships."""
    sp = await _resolve_service_principal(client, object_id)
    return await _collect_for_service_principal(client, sp)


async def select_by_tag(client: GraphServiceClient, tag: str) -> list[ServicePrincipal]:
    """Select Service Principals by tag, paging through all results.

    Queries `tags/any(c:c eq '{tag}')` with OData single-quote escaping and
    follows `@odata.nextLink` until the result set is exhausted. Requests the
    unified `$select` so tag-selected SPs share the by-id baseline.
    """
    escaped = tag.replace("'", "''")
    config = RequestConfiguration(
        query_parameters=ServicePrincipalsRequestBuilder.ServicePrincipalsRequestBuilderGetQueryParameters(
            filter=f"tags/any(c:c eq '{escaped}')",
            select=SP_SELECT,
        )
    )
    selected: list[ServicePrincipal] = []
    page = await client.service_principals.get(request_configuration=config)
    while page is not None:
        if page.value:
            selected.extend(page.value)
        next_link = page.odata_next_link
        if not next_link:
            break
        page = await client.service_principals.with_url(next_link).get()
    return selected


async def collect_by_tag(
    client: GraphServiceClient, tag: str
) -> list[ServicePrincipalRecord]:
    """Collect every Service Principal carrying `tag` into records."""
    return [
        await _collect_for_service_principal(client, sp)
        for sp in await select_by_tag(client, tag)
    ]
