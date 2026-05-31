#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import shutil
from typing import Any, TypedDict, Optional


class ServicePrincipal(TypedDict):
    objectId: str
    applicationId: Optional[str]
    displayName: Optional[str]


class RoleAssignment(TypedDict):
    subscriptionName: Optional[str]
    subscriptionId: Optional[str]
    scopeType: Optional[str]
    scope: Optional[str]
    roleName: Optional[str]


class ReportEntry(TypedDict):
    displayName: Optional[str]
    applicationId: Optional[str]
    objectId: str
    roleAssignmentsCount: int
    roleAssignments: list[RoleAssignment]


def run_az_command(args: list[str], check: bool = True) -> Any:
    """Runs an Azure CLI command and returns the parsed JSON output.

    By default, prints a diagnostic and exits the process on failure. When
    ``check=False`` is passed, the function returns ``None`` instead, so the
    caller can recover from expected failures (e.g. permission denied during
    an optional Entra ID lookup).
    """
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        if not check:
            return None
        print(f"Error running command: {' '.join(args)}", file=sys.stderr)
        print(f"Exit code: {e.returncode}", file=sys.stderr)
        print(f"Error output: {e.stderr}", file=sys.stderr)
        sys.exit(1)

    if not result.stdout.strip():
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        if not check:
            return None
        print(
            f"Error decoding JSON response from command: {' '.join(args)}",
            file=sys.stderr,
        )
        print(f"Output was: {result.stdout}", file=sys.stderr)
        sys.exit(1)


def check_az_login() -> None:
    """Verifies that the az CLI is installed and the user is logged in."""
    if not shutil.which("az"):
        print(
            "Error: The Azure CLI ('az') is not installed or not in PATH.",
            file=sys.stderr,
        )
        print("Please install it and log in with 'az login'.", file=sys.stderr)
        sys.exit(1)

    print("Checking Azure CLI login status...")
    result = subprocess.run(["az", "account", "show"], capture_output=True, text=True)
    if result.returncode != 0:
        print(
            "Error: You are not logged in. Please run 'az login' first.",
            file=sys.stderr,
        )
        if result.stderr.strip():
            print(f"Details: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    try:
        account_info = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError as e:
        print(f"Error parsing 'az account show' output: {e}", file=sys.stderr)
        sys.exit(1)

    user = account_info.get("user") or {}
    user_name = user.get("name", "<unknown>")
    tenant_id = account_info.get("tenantId", "<unknown>")
    print(f"Logged in as: {user_name} (Tenant: {tenant_id})")


def parse_terraform_state(state_file_path: str) -> list[ServicePrincipal]:
    """Extracts service principal Object IDs, Application IDs, and names from a Terraform state file."""
    print(f"Reading service principals from Terraform state file: {state_file_path}")
    try:
        with open(state_file_path, "r") as f:
            state = json.load(f)
    except Exception as e:
        print(f"Error reading state file {state_file_path}: {e}", file=sys.stderr)
        sys.exit(1)

    sps: list[ServicePrincipal] = []
    # Terraform state can be v4 JSON format
    resources = state.get("resources", [])
    for res in resources:
        # Match service principal resources from azuread provider
        if res.get("type") == "azuread_service_principal":
            for instance in res.get("instances", []):
                attrs = instance.get("attributes", {})
                obj_id = attrs.get("object_id") or attrs.get("id")
                app_id = attrs.get("application_id") or attrs.get("client_id")
                display_name = (
                    attrs.get("display_name") or attrs.get("name") or res.get("name")
                )

                if obj_id:
                    sps.append(
                        {
                            "objectId": str(obj_id),
                            "applicationId": str(app_id) if app_id else None,
                            "displayName": str(display_name) if display_name else None,
                        }
                    )

    print(f"Found {len(sps)} service principals in Terraform state.")
    return sps


def _sp_from_graph_payload(sp: dict[str, Any]) -> Optional[ServicePrincipal]:
    """Converts a Microsoft Graph servicePrincipal object into a ServicePrincipal."""
    obj_id = sp.get("id")
    if not obj_id:
        return None
    return {
        "objectId": str(obj_id),
        "applicationId": str(sp.get("appId")) if sp.get("appId") else None,
        "displayName": str(sp.get("displayName")) if sp.get("displayName") else None,
    }


def fetch_sps_from_entra_by_tag(tag: str) -> list[ServicePrincipal]:
    """Queries Entra ID via Microsoft Graph API (using az rest) to find service principals by tag."""
    print(f"Querying Entra ID for service principals with tag: {tag}")
    sps: list[ServicePrincipal] = []
    # OData escapes single quotes inside string literals by doubling them.
    # Dollar signs are not escaped since we invoke az without a shell.
    escaped_tag = tag.replace("'", "''")
    uri: Optional[str] = (
        f"https://graph.microsoft.com/v1.0/servicePrincipals?$filter=tags/any(c:c eq '{escaped_tag}')&$select=id,displayName,appId"
    )

    while uri:
        # az rest returns JSON
        res = run_az_command(["az", "rest", "--method", "get", "--uri", uri])

        for sp in res.get("value", []):
            entry = _sp_from_graph_payload(sp)
            if entry is not None:
                sps.append(entry)

        # Get next page link if results are paged
        uri = res.get("@odata.nextLink")
        if uri:
            print("Fetching next page of service principals...")

    print(f"Found {len(sps)} service principals matching tag '{tag}' in Entra ID.")
    return sps


def lookup_sp_by_identifier(identifier: str) -> Optional[ServicePrincipal]:
    """Looks up a single service principal by Object ID or Application (Client) ID.

    Returns ``None`` if the lookup fails (e.g. caller lacks Entra read
    permission) or no service principal matches.
    """
    escaped = identifier.replace("'", "''")
    uri = (
        "https://graph.microsoft.com/v1.0/servicePrincipals"
        f"?$filter=id eq '{escaped}' or appId eq '{escaped}'"
        "&$select=id,displayName,appId"
    )
    res = run_az_command(["az", "rest", "--method", "get", "--uri", uri], check=False)
    if not isinstance(res, dict):
        return None
    matches = res.get("value") or []
    if not matches:
        return None
    if len(matches) > 1:
        print(
            f"Warning: Multiple service principals matched '{identifier}'. Using the first match.",
            file=sys.stderr,
        )
    return _sp_from_graph_payload(matches[0])


def resolve_sps_by_identifier(identifiers: list[str]) -> list[ServicePrincipal]:
    """Resolves service principals supplied directly on the command line.

    Each identifier may be an Object ID or an Application (Client) ID. The
    function attempts to enrich each entry with display name and application
    ID via Microsoft Graph. If the Entra lookup fails or returns nothing, the
    identifier is used as-is as an Object ID so the RBAC audit can still run.
    """
    print(
        f"Resolving {len(identifiers)} service principal(s) supplied on the command line..."
    )
    sps: list[ServicePrincipal] = []
    for identifier in identifiers:
        identifier = identifier.strip()
        if not identifier:
            continue
        resolved = lookup_sp_by_identifier(identifier)
        if resolved is None:
            print(
                f"Warning: Could not resolve '{identifier}' in Entra ID. "
                "Using it as a raw Object ID for the RBAC query.",
                file=sys.stderr,
            )
            sps.append(
                {
                    "objectId": identifier,
                    "applicationId": None,
                    "displayName": None,
                }
            )
        else:
            sps.append(resolved)
    return sps


def run_rbac_query(
    object_ids: list[str], management_group: Optional[str] = None
) -> list[dict[str, Any]]:
    """Runs the Azure Resource Graph query in chunks to find RBAC assignments."""
    chunk_size = 100
    # `az graph query --first` accepts values in the range 1-1000. For larger
    # result sets we paginate with --skip-token.
    page_size = 1000
    all_role_assignments: list[dict[str, Any]] = []

    # Ensure Object IDs are unique and lowercase for matching, with stable ordering
    unique_ids = sorted({oid.lower() for oid in object_ids if oid})
    total_ids = len(unique_ids)

    if total_ids == 0:
        print("No Service Principal Object IDs to query.")
        return []

    print(
        f"Querying Azure Resource Graph for RBAC assignments across {total_ids} principals..."
    )

    total_chunks = (total_ids - 1) // chunk_size + 1
    for i in range(0, total_ids, chunk_size):
        chunk = unique_ids[i : i + chunk_size]
        chunk_idx = i // chunk_size + 1
        print(f"  Processing chunk {chunk_idx} of {total_chunks} ({len(chunk)} IDs)...")

        # Build KQL query for this chunk
        ids_str = ", ".join(f"'{uid}'" for uid in chunk)
        kql_query = f"""
        authorizationresources
        | where type =~ 'microsoft.authorization/roleassignments'
        | extend principalId = tostring(properties.principalId)
        | extend roleDefinitionId = tostring(properties.roleDefinitionId)
        | extend scope = tostring(properties.scope)
        | where principalId in~ ({ids_str})
        | project subscriptionId, scope, principalId, roleDefinitionId
        | join kind=leftouter (
            authorizationresources
            | where type =~ 'microsoft.authorization/roledefinitions'
            | project roleDefinitionId = id, roleName = tostring(properties.roleName)
        ) on roleDefinitionId
        | join kind=leftouter (
            resourcecontainers
            | where type == 'microsoft.resources/subscriptions'
            | project subscriptionId, subscriptionName = name
        ) on subscriptionId
        | extend scopeSegments = split(scope, '/')
        | extend scopeType = iif(array_length(scopeSegments) == 3, 'Subscription',
                                iif(array_length(scopeSegments) == 5, 'Resource Group', 'Resource'))
        | project subscriptionName, subscriptionId, scopeType, scope, roleName = coalesce(roleName, split(roleDefinitionId, '/')[-1]), principalId
        """

        skip_token: Optional[str] = None
        page = 0
        while True:
            page += 1
            cmd = [
                "az",
                "graph",
                "query",
                "-q",
                kql_query,
                "--first",
                str(page_size),
            ]
            if management_group:
                cmd.extend(["--management-groups", management_group])
            if skip_token:
                cmd.extend(["--skip-token", skip_token])

            res = run_az_command(cmd)

            # Handle format: az graph query output can be a list or a dict containing "data"
            rows: list[dict[str, Any]] = []
            next_token: Optional[str] = None
            if isinstance(res, dict):
                rows = res.get("data", []) or []
                next_token = res.get("skip_token") or res.get("skipToken")
            elif isinstance(res, list):
                rows = res

            all_role_assignments.extend(rows)

            if not next_token:
                break
            skip_token = next_token
            print(f"    Fetching page {page + 1} for chunk {chunk_idx}...")

    print(f"Retrieved {len(all_role_assignments)} role assignment records in total.")
    return all_role_assignments


def generate_report(
    sps: list[ServicePrincipal],
    role_assignments: list[dict[str, Any]],
    output_file: str,
) -> None:
    """Aggregates service principal details and role assignments into a structured JSON report."""
    print("Formatting and aggregating results...")

    # Create empty template for every target Service Principal so they are all represented
    report: dict[str, ReportEntry] = {}
    for sp in sps:
        oid = sp["objectId"].lower()
        report[oid] = {
            "displayName": sp["displayName"],
            "applicationId": sp["applicationId"],
            "objectId": sp["objectId"],
            "roleAssignmentsCount": 0,
            "roleAssignments": [],
        }

    # Populate role assignments
    unknown_sps_count = 0
    for ra in role_assignments:
        p_id = str(ra.get("principalId", "")).lower()
        assignment: RoleAssignment = {
            "subscriptionName": ra.get("subscriptionName"),
            "subscriptionId": ra.get("subscriptionId"),
            "scopeType": ra.get("scopeType"),
            "scope": ra.get("scope"),
            "roleName": ra.get("roleName"),
        }

        if p_id in report:
            report[p_id]["roleAssignments"].append(assignment)
            report[p_id]["roleAssignmentsCount"] += 1
        else:
            # This should not normally happen since we filtered by these IDs, but handle just in case
            unknown_sps_count += 1

    # Convert report to list and sort by display name
    final_report: list[ReportEntry] = list(report.values())
    final_report.sort(key=lambda x: (x["displayName"] or "").lower())

    # Write to file
    try:
        with open(output_file, "w") as f:
            json.dump(final_report, f, indent=2)
        print(f"Successfully wrote audit results to: {output_file}")
    except Exception as e:
        print(f"Error writing output file {output_file}: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit Azure RBAC role assignments across subscriptions for target Service Principals."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--state-file",
        help="Path to a local Terraform state file (.tfstate JSON) containing 'azuread_service_principal' resources.",
    )
    group.add_argument(
        "--tag",
        help="Entra ID tag to query (finds service principals tagged with this in Entra).",
    )
    group.add_argument(
        "--service-principal",
        action="append",
        dest="service_principals",
        metavar="ID",
        help=(
            "Object ID or Application (Client) ID of a service principal to audit "
            "directly, skipping Terraform state and Entra tag lookup. "
            "Repeat the flag to audit multiple principals."
        ),
    )

    parser.add_argument(
        "--output",
        default="audit-results.json",
        help="Output path for the generated JSON report (default: audit-results.json)",
    )
    parser.add_argument(
        "--management-group",
        help="Optional: Scope the Resource Graph query to a specific Management Group ID.",
    )

    args = parser.parse_args()

    # Verify login
    check_az_login()

    # Retrieve Service Principals
    if args.state_file:
        sps = parse_terraform_state(args.state_file)
    elif args.tag:
        sps = fetch_sps_from_entra_by_tag(str(args.tag))
    else:
        sps = resolve_sps_by_identifier(list(args.service_principals or []))

    if not sps:
        print("No service principals found to audit. Exiting.")
        sys.exit(0)

    # Get role assignments
    object_ids = [sp["objectId"] for sp in sps]
    role_assignments = run_rbac_query(object_ids, args.management_group)

    # Format and write report
    generate_report(sps, role_assignments, args.output)


if __name__ == "__main__":
    main()
