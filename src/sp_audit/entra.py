"""Entra (directory-plane) identity collector.

Resolves a Service Principal strictly by object id via
`GET /servicePrincipals/{id}`, falling back to an `appId eq` filter only on a
404, and attaches its related Application as a nullable object. The pure
mapping functions (Graph model -> record) are network-free so they can be
unit-tested without a live Graph client.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
from msgraph.generated.models.unified_role_assignment_schedule import (
    UnifiedRoleAssignmentSchedule,
)
from msgraph.generated.models.unified_role_eligibility_schedule import (
    UnifiedRoleEligibilitySchedule,
)
from msgraph.generated.role_management.directory.role_assignment_schedules.role_assignment_schedules_request_builder import (  # noqa: E501
    RoleAssignmentSchedulesRequestBuilder,
)
from msgraph.generated.role_management.directory.role_eligibility_schedules.role_eligibility_schedules_request_builder import (  # noqa: E501
    RoleEligibilitySchedulesRequestBuilder,
)
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

from .credentials import map_credentials
from .models import (
    ApplicationRecord,
    DirectoryRoleRecord,
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
APP_SELECT = [
    "id",
    "displayName",
    "appId",
    "passwordCredentials",
    "keyCredentials",
]
GROUP_SELECT = ["id", "displayName", "isAssignableToRole"]

# Both directory-role schedule kinds share the same readable shape
# (`roleDefinition`, `directoryScopeId`, `scheduleInfo`); `scheduleInfo` lives on
# the concrete subclasses rather than `UnifiedRoleScheduleBase`, so the collector
# types against the union.
type RoleSchedule = UnifiedRoleAssignmentSchedule | UnifiedRoleEligibilitySchedule


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
        "directoryRoles": [],
        "credentials": [],
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


def directory_role_from_schedule(
    schedule: RoleSchedule,
    assignment_type: Literal["active", "eligible"],
    source: str,
    source_group_id: str | None,
) -> DirectoryRoleRecord:
    """Map a Graph role schedule onto a Directory Role record.

    Pure: no network, no clock. `assignment_type` is supplied by the caller —
    `roleAssignmentSchedules` yield `active`, `roleEligibilitySchedules` yield
    `eligible`. `source`/`source_group_id` carry the Via-group attribution:
    `"direct"`/`None` for a role targeting the SP itself, or the group's display
    name/id for one reached through a role-assignable group. Raw facts only.
    """
    role_definition = schedule.role_definition
    role_name = role_definition.display_name if role_definition is not None else None
    schedule_info = schedule.schedule_info
    start = schedule_info.start_date_time if schedule_info is not None else None
    expiration = schedule_info.expiration if schedule_info is not None else None
    end = expiration.end_date_time if expiration is not None else None
    return {
        "roleName": role_name,
        "assignmentType": assignment_type,
        "source": source,
        "sourceGroupId": source_group_id,
        "directoryScopeId": schedule.directory_scope_id,
        "startDateTime": start.isoformat() if start is not None else None,
        "endDateTime": end.isoformat() if end is not None else None,
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

    The cache key is namespaced by the Graph resource path (`/groups/{id}`) so a
    shared `single_flight` can hold other resource kinds (e.g. role definitions)
    without bare-id collisions across namespaces.
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

    return await single_flight.do(f"/groups/{group_id}", fetch)


async def _page_schedules(
    builder: Any, config: RequestConfiguration
) -> list[RoleSchedule]:
    """Follow `@odata.nextLink` over a role-schedule builder, collecting items.

    Shared by the active (`roleAssignmentSchedules`) and eligible
    (`roleEligibilitySchedules`) endpoints, whose collection responses share the
    `RoleSchedule` shape (`roleDefinition`, `directoryScopeId`,
    `scheduleInfo`).
    """
    schedules: list[RoleSchedule] = []
    page = await builder.get(request_configuration=config)
    while page is not None:
        schedules.extend(page.value or [])
        next_link = page.odata_next_link
        if not next_link:
            break
        page = await builder.with_url(next_link).get()
    return schedules


async def _principal_schedules(
    client: GraphServiceClient, principal_id: str
) -> tuple[list[RoleSchedule], list[RoleSchedule]]:
    """Fetch (active, eligible) directory-role schedules for one principal id.

    The principal may be the SP itself (direct paths) or a role-assignable group
    (via-group paths); both are filtered by `principalId` with
    `$expand=roleDefinition` so role display names resolve in the same call.
    """
    escaped = principal_id.replace("'", "''")
    active_config = RequestConfiguration(
        query_parameters=RoleAssignmentSchedulesRequestBuilder.RoleAssignmentSchedulesRequestBuilderGetQueryParameters(
            filter=f"principalId eq '{escaped}'",
            expand=["roleDefinition"],
        )
    )
    eligible_config = RequestConfiguration(
        query_parameters=RoleEligibilitySchedulesRequestBuilder.RoleEligibilitySchedulesRequestBuilderGetQueryParameters(
            filter=f"principalId eq '{escaped}'",
            expand=["roleDefinition"],
        )
    )
    directory = client.role_management.directory
    active = await _page_schedules(directory.role_assignment_schedules, active_config)
    eligible = await _page_schedules(
        directory.role_eligibility_schedules, eligible_config
    )
    return active, eligible


async def collect_directory_roles(
    client: GraphServiceClient,
    object_id: str,
    memberships: list[GroupMembershipRecord],
    schedule_cache: SingleFlight[str, list[DirectoryRoleRecord]],
) -> list[DirectoryRoleRecord]:
    """Collect Directory Roles across all four paths, with Via-group attribution.

    Direct paths filter the SP's own id (`source = "direct"`). Via-group paths
    query, for each role-assignable group the SP is a transitive member of, the
    group's schedules and attribute every returned role to the SP with the
    group's display name as `source` and its id as `sourceGroupId` — regardless
    of intermediate non-role-assignable groups, since `memberships` already holds
    the transitive closure.

    A group's schedules are fetched at most once across every SP that reaches it
    via `schedule_cache`. Its key is namespaced by the Graph resource path so the
    cache never collides with other single-flight users (e.g. group-name lookups)
    keyed by bare id.
    """
    roles: list[DirectoryRoleRecord] = []

    active, eligible = await _principal_schedules(client, object_id)
    roles.extend(
        directory_role_from_schedule(s, "active", "direct", None) for s in active
    )
    roles.extend(
        directory_role_from_schedule(s, "eligible", "direct", None) for s in eligible
    )

    seen_groups: set[str] = set()
    for membership in memberships:
        group_id = membership["groupId"]
        if not membership["isAssignableToRole"] or group_id is None:
            continue
        if group_id in seen_groups:
            continue
        seen_groups.add(group_id)
        source = membership["displayName"] or group_id

        async def fetch(
            gid: str = group_id, src: str = source
        ) -> list[DirectoryRoleRecord]:
            g_active, g_eligible = await _principal_schedules(client, gid)
            return [
                directory_role_from_schedule(s, "active", src, gid) for s in g_active
            ] + [
                directory_role_from_schedule(s, "eligible", src, gid)
                for s in g_eligible
            ]

        roles.extend(
            await schedule_cache.do(
                f"/roleManagement/directory/schedules/{group_id}", fetch
            )
        )
    return roles


async def _collect_for_service_principal(
    client: GraphServiceClient,
    sp: ServicePrincipal,
    schedule_cache: SingleFlight[str, list[DirectoryRoleRecord]],
) -> ServicePrincipalRecord:
    """Build a record for an already-resolved SP: Application, memberships, roles.

    Every section degrades to an SP Gap in the record's `errors[]` rather than
    aborting the whole SP (ADR-0002 two-tier failures), so this never raises: the
    base record is mapped first from the already-resolved SP, then the Application
    and each collected section are attached independently. Directory-role
    attribution depends on the collected memberships, so it runs after them.
    """
    record = sp_record_from_graph(sp, None)
    # One injected "now" derives every Credential status for this SP, keeping the
    # SP-side and Application-side flattening consistent within the record.
    now = datetime.now(UTC)
    record["credentials"] = map_credentials(
        "servicePrincipal", sp.password_credentials, sp.key_credentials, now
    )
    if sp.app_id:
        try:
            application = await _resolve_application(client, sp.app_id)
        except Exception as exc:  # noqa: BLE001 - degrade to an SP Gap, never abort
            record["errors"].append(f"Failed to resolve application: {exc}")
        else:
            if application is not None:
                record["application"] = application_record_from_graph(application)
                record["credentials"].extend(
                    map_credentials(
                        "application",
                        application.password_credentials,
                        application.key_credentials,
                        now,
                    )
                )
            else:
                # No local Application (managed identity, gallery, cross-tenant):
                # application stays null, SP-side credentials still report.
                record["errors"].append(
                    f"No Application object found for appId '{sp.app_id}' (SP Gap)"
                )
    try:
        record["groupMemberships"] = await collect_group_memberships(
            client, record["objectId"]
        )
    except Exception as exc:  # noqa: BLE001 - degrade to an SP Gap, never abort
        record["errors"].append(f"Failed to collect group memberships: {exc}")
    try:
        record["directoryRoles"] = await collect_directory_roles(
            client, record["objectId"], record["groupMemberships"], schedule_cache
        )
    except Exception as exc:  # noqa: BLE001 - degrade to an SP Gap, never abort
        record["errors"].append(f"Failed to collect directory roles: {exc}")
    return record


async def _collect_all(
    client: GraphServiceClient, service_principals: list[ServicePrincipal]
) -> tuple[list[ServicePrincipalRecord], list[str]]:
    """Collect a list of already-resolved SPs into records, isolating failures.

    Shared by both selection paths so a per-SP failure never aborts the batch.
    One `schedule_cache` is shared across the whole selection so a group reached
    by many SPs has its directory-role schedules fetched once for the run. Per-SP
    sections already degrade to SP Gaps; an unexpected collection failure here
    degrades to a Run Error rather than dropping every other SP.
    """
    schedule_cache: SingleFlight[str, list[DirectoryRoleRecord]] = SingleFlight()
    records: list[ServicePrincipalRecord] = []
    run_errors: list[str] = []
    for sp in service_principals:
        try:
            records.append(
                await _collect_for_service_principal(client, sp, schedule_cache)
            )
        except Exception as exc:  # noqa: BLE001 - degrade to a Run Error, never abort
            run_errors.append(f"Failed to collect '{sp.id}': {exc}")
    return records, run_errors


async def collect_by_object_ids(
    client: GraphServiceClient, object_ids: list[str]
) -> tuple[list[ServicePrincipalRecord], list[str]]:
    """Collect records for an explicit set of object ids.

    Each id is resolved independently, so an unresolvable id degrades to a Run
    Error rather than aborting the run; the rest are then collected via the
    shared `_collect_all` path. Returns the records plus all Run Errors.
    """
    service_principals: list[ServicePrincipal] = []
    run_errors: list[str] = []
    for object_id in object_ids:
        try:
            service_principals.append(
                await _resolve_service_principal(client, object_id)
            )
        except Exception as exc:  # noqa: BLE001 - degrade to a Run Error, never abort
            run_errors.append(f"Failed to resolve '{object_id}': {exc}")
    records, collect_errors = await _collect_all(client, service_principals)
    return records, run_errors + collect_errors


async def _select_by_tag(
    client: GraphServiceClient, tag: str
) -> list[ServicePrincipal]:
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
) -> tuple[list[ServicePrincipalRecord], list[str]]:
    """Collect every Service Principal carrying `tag` into records.

    Tag selection is a single Graph query: if it fails the whole selection is a
    Run Error. The selected SPs are then collected via the shared `_collect_all`
    path, so one SP's failure no longer drops the rest. Returns the records plus
    all Run Errors.
    """
    try:
        service_principals = await _select_by_tag(client, tag)
    except Exception as exc:  # noqa: BLE001 - degrade to a Run Error, never abort
        return [], [f"Failed to select by tag '{tag}': {exc}"]
    return await _collect_all(client, service_principals)
