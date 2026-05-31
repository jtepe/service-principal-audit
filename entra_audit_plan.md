# Entra Service Principal Audit Plan

This document presents a technical proposal for auditing **Microsoft Entra ID
(Azure AD) directory-plane information** about a defined set of service principals.
For each service principal it gathers identity details, Entra group memberships,
Entra directory roles (including PIM eligible vs. active assignments and roles
inherited through groups), client secrets and certificates with their expiry,
service principal tags, and API permissions (application and delegated). The
consolidated result is written to a single JSON file.

This is a **research/design document only** — no runnable code is included. It is
written to be detailed enough to implement later.

## Scope & Non-Goals

**In scope** — read-only audit of the Entra directory plane for a set of service
principals:

- Identity: display name, Application (Client) ID, Object ID, tags.
- Group memberships (direct and transitive).
- Entra directory roles via all four assignment paths (active/eligible ×
  direct/via-group).
- PIM-for-Groups membership status.
- Client secrets and certificates (with expiry) from both the Application and the
  service principal objects.
- API permissions: application permissions (`appRoleAssignments`) and delegated
  permissions (`oauth2PermissionGrants`).

**Non-goals:**

- **No Azure RBAC.** Resource-/subscription-/management-group-scoped role
  assignments (the ARM plane) are already covered by the companion tool
  `audit_rbac.py` in this directory. This tool deliberately complements it on the
  Entra directory plane.
- **No remediation.** The tool reads and reports; it never writes to the tenant.
- **JSON only.** No HTML rendering in this iteration (see *Future Work*).

## Relationship to the Existing RBAC Tool

This tool is the Entra-plane sibling of `audit_rbac.py`:

| | `audit_rbac.py` (existing) | Entra SP auditor (this plan) |
|---|---|---|
| Plane | Azure RBAC / ARM (resources) | Entra ID / directory |
| Backend | Azure Resource Graph (`az graph query`) | Microsoft Graph (`msgraph-sdk`) |
| Answers | "What can this SP do to Azure resources?" | "What is this SP in the directory, and what directory privileges and credentials does it hold?" |

It intentionally **reuses the existing UX and reporting philosophy**: the same
mutually-exclusive selection model (tag vs. explicit IDs), OData single-quote
escaping (`'` → `''`), pagination handling, a typed report aggregated per service
principal, and a final list sorted by display name.

## Tech Choices & Dependency Departure

- **Python 3.14**, managed with **`uv`** (matching `pyproject.toml`'s
  `requires-python >= 3.14`).
- **`azure-identity`** (for `AzureCliCredential`) and **`msgraph-sdk`** (the
  official Microsoft Graph Python SDK).

> **Dependency departure.** The existing project has `dependencies = []` and shells
> out to the `az` CLI (`az rest`, `az graph query`) precisely to stay
> zero-dependency. This plan **intentionally breaks that convention** by adding
> `azure-identity` + `msgraph-sdk`. The justification: the Entra surface here is far
> richer than the RBAC tool's single Kusto query — it spans servicePrincipals,
> applications, group memberships, two directory-role schedule collections, two
> PIM-for-Groups schedule collections, appRoleAssignments and oauth2PermissionGrants,
> each with its own pagination, `$filter`/`$expand`/`$select` semantics, and
> throttling behavior. The SDK provides typed models, transparent paging, and a
> built-in retry/Retry-After handler that would otherwise have to be hand-rolled
> against raw `az rest` calls. This trade-off (one extra dependency group vs. a large
> amount of bespoke HTTP/paging code) should be revisited if zero-dependency becomes
> a hard requirement; an `az rest`-only implementation remains possible.
>
> **Async runtime.** `msgraph-sdk` is **async-only**. Unlike the synchronous,
> `subprocess`-based RBAC tool, the entire tool runs under `asyncio.run(main())`,
> and all Graph calls are awaited.

`pyproject.toml` would gain (e.g. via `uv add azure-identity msgraph-sdk`):

```toml
dependencies = [
    "azure-identity>=1.19",
    "msgraph-sdk>=1.0",
]
```

## Authentication & Authorization

Authentication is **delegated** — the tool runs as the signed-in user, inheriting
their directory roles, exactly like the RBAC tool's `az login` model. No service
principal or app registration is provisioned.

```python
from azure.identity import AzureCliCredential
from msgraph import GraphServiceClient

credential = AzureCliCredential()
graph_client = GraphServiceClient(
    credential, scopes=["https://graph.microsoft.com/.default"]
)
```

With `AzureCliCredential` and the `.default` scope, the token carries whatever
delegated permissions the Azure CLI's first-party client application has already
been consented for in the tenant. You therefore do **not** pass a per-resource scope
list. What actually gates whether each call returns data is the **signed-in user's
directory role**.

### Required delegated scopes and the read-only roles that satisfy them

| Capability | Least-privilege delegated scope | Read-only role that satisfies it |
|---|---|---|
| SP / app / group reads, memberships, oauth2 grants, appRoleAssignments | `Directory.Read.All` (or `Application.Read.All` + `Group.Read.All`) | Global Reader, or Directory Readers |
| Directory role schedules — active | `RoleAssignmentSchedule.Read.Directory` (or `RoleManagement.Read.Directory` / `RoleManagement.Read.All`) | Global Reader |
| Directory role schedules — eligible | `RoleEligibilitySchedule.Read.Directory` (or `RoleManagement.Read.Directory`) | Global Reader |
| PIM for Groups schedules | `PrivilegedAccess.Read.AzureADGroup` | Global Reader |
| App credentials (passwordCredentials / keyCredentials) | `Application.Read.All` (covered by `Directory.Read.All`) | Global Reader / Directory Readers |

**Recommended role: Global Reader.** It covers every capability above, including
role-management and PIM reads.

> **Directory Readers gap.** The **Directory Readers** role grants directory object
> reads (SPs, apps, groups, memberships) but does **not** grant role-management or
> PIM reads. A user with only Directory Readers will get `403` on the directory-role
> schedule and PIM-for-Groups endpoints. The tool must degrade gracefully (see
> *Cross-Cutting Concerns*), and the docs should steer users to Global Reader.

> **CLI client consent caveat.** Because the token comes from the Azure CLI's
> first-party app, the relevant Graph permissions must be consented on that app. In
> most tenants the broad first-party CLI app already carries them; if a schedule call
> returns `403` despite the user holding Global Reader, missing consent on the CLI
> client app is the likely cause.

## Input Selection

Service principal selection mirrors `audit_rbac.py`'s mutually-exclusive argparse
group. Exactly one selection mode is chosen per run:

- `--ids-file PATH` — a file of Object IDs, either one ID per line or a JSON array
  of IDs. Scales well for large sets.
- `--object-id ID` — a single Object ID; repeatable to pass several inline. May be
  combined with `--ids-file` (both feed the same ID list).
- `--tag TAG` — query all service principals carrying the given Entra tag (mutually
  exclusive with the ID inputs).
- `--output PATH` — output JSON path (default e.g. `entra-audit-results.json`).

`--object-id` is treated **strictly as an Object ID**: the tool issues a direct
`GET /servicePrincipals/{id}`. Only if that returns `404` does it fall back to an
`appId eq '{id}'` filter, so an Application (Client) ID still resolves — matching the
RBAC tool's best-effort behavior without sacrificing the common case.

Tag values are OData-escaped (`'` → `''`) exactly as in `fetch_sps_from_entra_by_tag`
in `audit_rbac.py`.

## Data Model — What We Collect (with exact Graph endpoints)

All endpoints below are **Microsoft Graph v1.0**. SDK chains assume the
`graph_client` constructed above.

### 1. Identity / selection

- **By tag:** `GET /servicePrincipals?$filter=tags/any(c:c eq '{tag}')&$select=id,displayName,appId,tags`
- **By Object ID:**
  `service_principals.by_service_principal_id(oid).get(...)` with
  `$select=id,displayName,appId,tags,passwordCredentials,keyCredentials`
  (selecting the credential collections here avoids a second round-trip for SP-side
  credentials in section 5).

### 2. Group memberships (label direct vs. transitive)

- **Direct:** `service_principals.by_service_principal_id(oid).member_of.get()` —
  keep entries whose `@odata.type == #microsoft.graph.group`; label
  `membershipType = "direct"`.
- **Transitive:** `service_principals.by_service_principal_id(oid).transitive_member_of.get()` —
  label `membershipType = "transitive"`.
- Request `$select=id,displayName,isAssignableToRole` on both.
- Build a `groupId → displayName` map here. The **transitive** set is what drives the
  per-group directory-role and PIM-for-Groups queries below (a role may target a
  nested group the SP only reaches transitively).

`memberOf` returns only directly-assigned groups; `transitiveMemberOf` includes
nested groups. Both are reported so the direct/inherited distinction is preserved.

### 3. Directory roles — all four paths

Resolve `roleDefinitionId → displayName` via `$expand=roleDefinition` on each query
(cheaper than a separate lookup); optionally cache a one-time
`GET /roleManagement/directory/roleDefinitions` map as a fallback.

| # | Path | Endpoint (SDK: `role_management.directory.…`) | Labels |
|---|---|---|---|
| a | Active direct | `role_assignment_schedules` `?$filter=principalId eq '{spId}'&$expand=roleDefinition` | `assignmentType=active`, `source=direct` |
| b | Eligible direct (PIM) | `role_eligibility_schedules` `?$filter=principalId eq '{spId}'&$expand=roleDefinition` | `assignmentType=eligible`, `source=direct` |
| c | Active via group | `role_assignment_schedules` `?$filter=principalId eq '{groupId}'&$expand=roleDefinition` (per transitive, role-assignable group) | `assignmentType=active`, `source=<group displayName>` |
| d | Eligible via group | `role_eligibility_schedules` `?$filter=principalId eq '{groupId}'&$expand=roleDefinition` | `assignmentType=eligible`, `source=<group displayName>` |

Notes:

- `roleAssignmentSchedules` is the unified view that includes both standing
  assignments and currently-active PIM activations; it is preferred over the plain
  `roleAssignments` collection so active state is captured consistently with eligible
  state. `roleEligibilitySchedules` holds the eligible (PIM) assignments.
- `$filter eq` on `principalId` is supported on both schedule collections.
- A role assignment whose `principalId` is a **group** only takes effect if that
  group is `isAssignableToRole = true`. Only iterate role-assignable groups for paths
  (c)/(d); annotate each group with that flag.

### 4. PIM for Groups (how the SP holds the membership — orthogonal to #3)

This answers a different question from #3: for each role-assignable group, is the SP
a **standing (assigned)** or only an **eligible** member? An SP that is merely
*eligible* for a role-assignable group holds the group's directory role only after
activating that membership.

- **Eligible membership:**
  `identity_governance.privileged_access.group.eligibility_schedules`
  `?$filter=principalId eq '{spId}'`
- **Active membership:**
  `identity_governance.privileged_access.group.assignment_schedules`
  `?$filter=principalId eq '{spId}'`

> **Gotcha (verified): `$filter` is required.** These PIM-for-Groups schedule
> endpoints return **HTTP 400** if queried without a `$filter` on `principalId` (or
> `groupId`). Always filter by the SP's id, then read `groupId` and `accessId` from
> the results.

- `accessId` is `member` or `owner`. Keep `member` for role-inheritance reasoning;
  `owner` may be recorded separately if useful.
- Use this to set each group's `pimMembership` field (`assigned` / `eligible` /
  `none`) so the report distinguishes "SP is actively in the role-assignable group"
  from "SP is eligible to activate into it."

### 5. Credentials — secrets and certificates (resolve SP → Application)

- **Application object:**
  `GET /applications?$filter=appId eq '{appId}'&$select=id,displayName,passwordCredentials,keyCredentials`
  — returns 0 or 1 result.
  > **Graceful degradation:** managed identities, gallery (SaaS) apps, and apps homed
  > in another tenant have **no local Application object**. When the filter returns
  > empty, emit `application: null` and still report any SP-side credentials.
- **ServicePrincipal object:** `passwordCredentials` / `keyCredentials` already
  selected in section 1 (or fetched via a dedicated `$select`).
- For **each** credential record, from both the application and the SP:
  - `owner`: `"application"` or `"servicePrincipal"`
  - `credentialType`: `"password"` (secret) or `"key"` (certificate)
  - `displayName`, `keyId`, `startDateTime`, `endDateTime`
  - `expired = endDateTime < datetime.now(timezone.utc)` — compute against a
    timezone-aware UTC now; Graph returns ISO-8601 `Z` timestamps.

### 6. API permissions

- **Application permissions (`appRoleAssignments`):**
  `service_principals.by_service_principal_id(oid).app_role_assignments.get()` — each
  entry has `resourceId`, `resourceDisplayName`, and `appRoleId` (a GUID).
  - **Resolve `appRoleId` → human-readable value** (e.g. `User.Read.All`): fetch the
    **resource** SP once
    (`by_service_principal_id(resourceId).get($select=appId,displayName,appRoles)`),
    build an `appRoleId → appRole.value` map, and look up. Cache resource SPs by
    `resourceId` — most assignments target the Microsoft Graph SP, so one fetch
    serves many. The all-zero GUID (`00000000-0000-0000-0000-000000000000`) means
    "default access," not a specific role.
- **Delegated permissions (`oauth2PermissionGrants`):**
  `service_principals.by_service_principal_id(oid).oauth2_permission_grants.get()` —
  each entry has `resourceId`, `scope` (space-delimited list of delegated
  permissions), `consentType` (`AllPrincipals` vs. `Principal`), and `principalId`.
  Resolve `resourceId → displayName` via the same cached resource-SP map. Delegated
  grants are uncommon for service principals but security-relevant when present.

## The Four Directory-Role Paths (summary)

|  | Direct (principal = SP) | Via group (principal = role-assignable group the SP is a transitive member of) |
|---|---|---|
| **Active** | `roleAssignmentSchedules?$filter=principalId eq '{spId}'` | `roleAssignmentSchedules?$filter=principalId eq '{groupId}'` |
| **Eligible** | `roleEligibilitySchedules?$filter=principalId eq '{spId}'` | `roleEligibilitySchedules?$filter=principalId eq '{groupId}'` |

The "via group" mapping back to the SP is: enumerate the SP's `transitiveMemberOf`
groups → keep those with `isAssignableToRole = true` → query both schedule
collections with `principalId = groupId` → attribute any returned roles to the SP,
tagging `source` with the group's display name and `sourceGroupId` with its id.

## PIM for Groups vs. Role-Assignable Groups

These two concepts are **orthogonal** and both must be reported:

- **`isAssignableToRole`** is a static property of a group: *can this group hold a
  directory role at all?* Only role-assignable groups can.
- **PIM for Groups** governs *how the SP holds its membership* in a group: standing
  (`assigned`) vs. `eligible`. `accessId` further distinguishes `member` vs. `owner`.

A complete picture needs both dimensions: a role inherited via a group is only
*currently effective* if (a) the group is role-assignable, (b) the role assignment is
active (not just eligible), and (c) the SP's membership in that group is active (not
just eligible).

## ID Resolution & Caching

To minimize Graph calls across many SPs:

- **`roleDefinitionId → displayName`** — prefer `$expand=roleDefinition`; fall back to
  a cached `roleDefinitions` map. Built-in roles also expose a stable `templateId`;
  `roleDefinitionId` may be the templateId GUID for built-ins.
- **`appRoleId → value`** — per-resource-SP `appRoles` map, cached by `resourceId`.
- **`resourceId → displayName`** — from the cached resource-SP lookups.
- **`groupId → displayName`** — built once from the membership queries and reused for
  role `source` labels and the per-group PIM queries.

## Cross-Cutting Concerns

- **Pagination.** SDK responses expose `odata_next_link`. Use the SDK's
  `PageIterator`, or a manual `while response.odata_next_link:` loop calling
  `.with_url(next_link).get()`. Memberships, oauth2 grants, and schedule collections
  can all page — never assume a single page.
- **Throttling / 429.** `msgraph-sdk` ships a retry handler that honors
  `Retry-After`. Because the workload fans out heavily (many SPs × many groups × four
  schedule calls), add **bounded concurrency** and your own backoff on `429`/`503`,
  and use `$select` to shrink payloads. Graph PIM endpoints are comparatively
  rate-limited.
- **Graceful degradation.** Wrap each per-SP, per-section call so a `403` (e.g.
  Directory-Readers-only user hitting PIM endpoints) or a missing Application object
  records a note in the SP's `errors` array rather than aborting the whole run.
- **Timezone-aware expiry.** Compare credential `endDateTime` against
  `datetime.now(timezone.utc)`.

## Output Schema

A single JSON file: an array of per-SP objects, **sorted by `displayName`** (mirroring
the RBAC tool). Each object:

```json
[
  {
    "objectId": "...",
    "appId": "...",
    "displayName": "...",
    "tags": ["..."],
    "application": { "objectId": "...", "displayName": "..." },
    "groupMemberships": [
      {
        "groupId": "...",
        "displayName": "...",
        "membershipType": "direct|transitive",
        "isAssignableToRole": true,
        "pimMembership": "assigned|eligible|none"
      }
    ],
    "directoryRoles": [
      {
        "roleDefinitionId": "...",
        "roleDisplayName": "Global Reader",
        "assignmentType": "active|eligible",
        "source": "direct|<group displayName>",
        "sourceGroupId": null,
        "directoryScopeId": "/",
        "startDateTime": "...",
        "endDateTime": null
      }
    ],
    "credentials": [
      {
        "owner": "application|servicePrincipal",
        "credentialType": "password|key",
        "displayName": "...",
        "keyId": "...",
        "startDateTime": "...",
        "endDateTime": "...",
        "expired": false
      }
    ],
    "applicationPermissions": [
      {
        "resourceId": "...",
        "resourceDisplayName": "Microsoft Graph",
        "appRoleId": "...",
        "permission": "User.Read.All",
        "assignmentId": "..."
      }
    ],
    "delegatedPermissions": [
      {
        "resourceId": "...",
        "resourceDisplayName": "Microsoft Graph",
        "scopes": ["User.Read"],
        "consentType": "AllPrincipals|Principal",
        "principalId": null
      }
    ],
    "errors": ["partial-failure notes, e.g. 403 on a schedule call"]
  }
]
```

Field notes:

- `application` is `null` when the SP has no owned Application object.
- `directoryRoles[].source` is `"direct"` for paths a/b and the group display name for
  paths c/d; `sourceGroupId` is `null` for direct, the group's id otherwise.
- `directoryScopeId` is typically `/` (tenant-wide) but may be an administrative-unit
  or app scope.
- `errors` lets partial-permission runs surface gaps per SP instead of failing the
  entire audit.

Mirror `audit_rbac.py`'s `TypedDict` definitions for each of these shapes
(`ReportEntry`, plus `GroupMembership`, `DirectoryRole`, `Credential`,
`ApplicationPermission`, `DelegatedPermission`).

## Execution Workflow

A local async tool (e.g. `audit_entra.py`):

1. **Check login** — verify `az login` (mirror `check_az_login`); construct the
   `GraphServiceClient` with `AzureCliCredential`.
2. **Resolve service principals** — from `--ids-file`/`--object-id` (strict Object ID,
   `appId` fallback on 404) or `--tag`.
3. **Per-SP fan-out (bounded concurrency)** — for each SP gather: identity + SP-side
   credentials (§1), direct & transitive groups (§2), the four directory-role paths
   (§3), PIM-for-Groups status (§4), Application credentials (§5), and API permissions
   (§6), populating caches as it goes.
4. **Aggregate & write** — build the per-SP report objects, sort by `displayName`,
   and write the JSON to `--output`.

```bash
# By explicit Object IDs
uv run audit_entra.py \
  --object-id 22222222-2222-2222-2222-222222222222 \
  --object-id 33333333-3333-3333-3333-333333333333 \
  --output entra-audit-results.json

# By Object IDs from a file (newline list or JSON array)
uv run audit_entra.py --ids-file sp-object-ids.txt --output entra-audit-results.json

# By Entra tag
uv run audit_entra.py --tag terraform-iac --output entra-audit-results.json
```

## Permissions & Verification Plan

After implementation, verify the output against the Entra portal:

- **Identity & tags** — Enterprise applications → the SP → Properties / Object ID /
  Application ID; tags.
- **Group memberships** — the SP's group memberships; cross-check a nested group to
  confirm the direct/transitive labeling.
- **Directory roles** — Roles & administrators (active) and **PIM → Microsoft Entra
  roles** (eligible) for the SP, plus roles inherited via a role-assignable group.
- **PIM for Groups** — PIM → Groups → the role-assignable group → eligible vs. active
  members, to confirm `pimMembership`.
- **Credentials** — App registrations → the app → Certificates & secrets; confirm
  expiry dates and the `expired` flag, and that SP-side credentials are also captured.
- **API permissions** — Enterprise applications → the SP → Permissions (application
  vs. delegated); confirm `appRoleId` resolves to the right permission value.

Also confirm: both selection modes work; the JSON schema covers every requested
field; cited endpoints are v1.0; and the `$filter`-required PIM-for-Groups behavior is
handled.

## Limitations / Future Work

- **Beta-only fields.** Everything here is available in Graph **v1.0**; drop to
  `beta` only if a future field is needed.
- **Ownership chains.** This plan reads group *membership*; SP/app/group *ownership*
  graphs are out of scope.
- **Expiring-soon thresholds.** A `--expiring-within DAYS` flag could flag credentials
  nearing expiry, not just already-expired ones.
- **HTML renderer.** The JSON-first design means a companion renderer mirroring
  `render_html.py` could later present the report as a single self-contained HTML
  file.
- **Integrate Azure RBAC** The companion app in `audit_rbac.py` will be integrated
  and provide Azure RBAC information per service principal. The data model can be
  extended with the output defined by the `audit_rbac.py` tool.
