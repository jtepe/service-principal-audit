"""Pure transforms over raw Azure Resource Graph (ARG) rows.

All row-level logic for the Azure RBAC plane lives here so it is network-free
and unit-testable without an `az graph query` subprocess. This module houses the
two lifted bug fixes:

1. **Scope classification by prefix.** Management-group-scoped assignments are
   classified by their `/providers/Microsoft.Management/managementGroups/` prefix
   (previously they collided with Resource Group on segment count), and the
   `managementGroupId` is parsed out.
2. **Role-name resolution.** Both sides of the role-definition join are
   normalized to the trailing GUID, role definitions are de-duplicated before
   joining (so duplicates across subscriptions do not fan out assignment rows),
   and the raw GUID is used only as a fallback for a deleted role.
"""

from __future__ import annotations

from .models import AzureRoleAssignment

_MG_PREFIX = "/providers/Microsoft.Management/managementGroups/"


def _trailing_guid(resource_id: str) -> str:
    """Normalize a role-definition resource id to its trailing GUID segment."""
    return resource_id.rstrip("/").rsplit("/", 1)[-1]


def classify_scope(scope: str) -> tuple[str, str | None]:
    """Classify an Azure RBAC scope and parse a management-group id.

    Returns `(scopeType, managementGroupId)`. `managementGroupId` is the parsed
    id for a Management Group scope and `None` for every other scope type.
    """
    if scope.startswith(_MG_PREFIX):
        return "Management Group", scope[len(_MG_PREFIX) :].split("/")[0]
    segments = scope.split("/")
    if len(segments) == 3:
        return "Subscription", None
    if len(segments) == 5:
        return "Resource Group", None
    return "Resource", None


def transform_assignments(
    assignment_rows: list[dict],
    role_definition_rows: list[dict],
    subscription_rows: list[dict],
) -> dict[str, list[AzureRoleAssignment]]:
    """Transform raw ARG rows into per-principal Azure Role Assignments.

    `assignment_rows` carry `principalId`, `roleDefinitionId`, `scope`, and
    `subscriptionId`. `role_definition_rows` carry the role definition `id` and
    `roleName`. `subscription_rows` carry `subscriptionId` and `subscriptionName`.

    Role names are resolved by normalizing both the assignment's
    `roleDefinitionId` and each definition's `id` to their trailing GUID, after
    de-duplicating definitions by that GUID (so duplicate definitions across
    subscriptions do not fan out assignment rows). An unresolved (deleted) role
    falls back to the raw GUID. Output is keyed by `principalId`.
    """
    role_names: dict[str, str] = {}
    for row in role_definition_rows:
        guid = _trailing_guid(row["id"])
        # First definition wins; de-dup keeps the join from fanning out rows.
        role_names.setdefault(guid, row["roleName"])

    subscription_names: dict[str, str] = {
        row["subscriptionId"]: row["subscriptionName"] for row in subscription_rows
    }

    by_principal: dict[str, list[AzureRoleAssignment]] = {}
    for row in assignment_rows:
        guid = _trailing_guid(row["roleDefinitionId"])
        scope_type, mg_id = classify_scope(row["scope"])
        subscription_id = None if mg_id is not None else row.get("subscriptionId")
        assignment: AzureRoleAssignment = {
            "roleName": role_names.get(guid, guid),
            "scopeType": scope_type,
            "scope": row["scope"],
            "subscriptionId": subscription_id,
            "subscriptionName": (
                subscription_names.get(subscription_id)
                if subscription_id is not None
                else None
            ),
            "managementGroupId": mg_id,
        }
        by_principal.setdefault(row["principalId"], []).append(assignment)
    return by_principal
