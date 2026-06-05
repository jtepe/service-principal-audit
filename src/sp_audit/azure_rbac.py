"""Azure RBAC (resource-plane) collector.

Lifts the working synchronous `az graph query` Azure Resource Graph (ARG)
collector. The query always covers all management groups (no scoping flag).
Row-level logic — scope classification and role-name resolution, including both
bug fixes — lives in the pure `arg_transform` module; this module only runs the
bounded ARG batch and hands the raw rows over.

Per ADR-0001 the collector stays synchronous and is invoked from the async CLI
via `asyncio.to_thread`. Per ADR-0002 a failure of the ARG batch is surfaced by
the caller as a **Run Error** (`meta.runErrors`) — never a `sys.exit` — so the
Entra-plane data still writes.
"""

from __future__ import annotations

import asyncio
import json
import subprocess

from .arg_transform import transform_assignments
from .models import AzureRoleAssignment

_PAGE = 1000

# Role assignments held by the selected principals, with the raw fields the
# transform expects. `{principals}` is substituted with the OData-escaped,
# comma-separated id list.
_ASSIGNMENTS_QUERY = """
authorizationresources
| where type =~ 'microsoft.authorization/roleassignments'
| extend principalId = tostring(properties.principalId)
| where principalId in~ ({principals})
| project
    principalId,
    roleDefinitionId = tostring(properties.roleDefinitionId),
    scope = tostring(properties.scope),
    subscriptionId
"""

# Every role definition in reach. Built-in roles repeat once per subscription;
# the transform de-duplicates by trailing GUID so they do not fan out rows.
_ROLE_DEFINITIONS_QUERY = """
authorizationresources
| where type =~ 'microsoft.authorization/roledefinitions'
| project id, roleName = tostring(properties.roleName)
"""

# Subscription display names, joined in by id for friendly scope labels.
_SUBSCRIPTIONS_QUERY = """
resourcecontainers
| where type =~ 'microsoft.resources/subscriptions'
| project subscriptionId, subscriptionName = name
"""


def _run_arg_query(query: str) -> list[dict]:
    """Run one ARG query via `az graph query`, paging until exhausted."""
    rows: list[dict] = []
    skip = 0
    while True:
        completed = subprocess.run(
            [
                "az",
                "graph",
                "query",
                "-q",
                query,
                "--first",
                str(_PAGE),
                "--skip",
                str(skip),
                "-o",
                "json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(completed.stdout)
        batch = payload.get("data", []) if isinstance(payload, dict) else payload
        rows.extend(batch)
        if len(batch) < _PAGE:
            break
        skip += _PAGE
    return rows


def collect_azure_role_assignments(
    object_ids: list[str],
) -> dict[str, list[AzureRoleAssignment]]:
    """Collect Azure Role Assignments for `object_ids`, keyed by principal id.

    Runs the bounded ARG batch (assignments + role definitions + subscriptions)
    and delegates all row-level logic to `transform_assignments`. Returns an
    empty mapping without shelling out when the selection is empty. Any
    subprocess failure propagates to the caller, which records it as a Run Error.
    """
    if not object_ids:
        return {}
    quoted = ("'" + oid.replace("'", "''") + "'" for oid in object_ids)
    principals = ", ".join(quoted)
    assignment_rows = _run_arg_query(_ASSIGNMENTS_QUERY.format(principals=principals))
    role_definition_rows = _run_arg_query(_ROLE_DEFINITIONS_QUERY)
    subscription_rows = _run_arg_query(_SUBSCRIPTIONS_QUERY)
    return transform_assignments(
        assignment_rows, role_definition_rows, subscription_rows
    )


async def collect_azure_rbac(
    object_ids: list[str],
) -> dict[str, list[AzureRoleAssignment]]:
    """Async wrapper: run the sync ARG collector off the event loop thread."""
    return await asyncio.to_thread(collect_azure_role_assignments, object_ids)
