# Spyglass

A read-only spyglass for Microsoft Entra service principals: a CLI that, for a
selected set of Entra **Service Principals**, gathers
everything the directory (Entra) and Azure RBAC planes know about them and writes
it to a single JSON **Audit Report**, optionally rendered to a self-contained HTML
view.

The tool runs locally as the user who ran `az login`, inheriting that user's
permissions. It needs no dedicated service principal or managed identity, and it
never writes to the tenant. Domain terms used throughout (Service Principal,
Application, Directory Role, Azure Role Assignment, Plane, Credential, Owner,
Audit Report, Run Error, SP Gap, Via-group attribution) are defined in
[`CONTEXT.md`](CONTEXT.md).

## Prerequisites

1. **Azure CLI** — install `az` and ensure it is on your `PATH`.
2. **Log in** — run `az login`. The audit runs as the signed-in user.
3. **Permissions** — see [Required permissions](#required-permissions) below.
   In short: **Global Reader** in Entra plus a **Reader** role over the
   management group hierarchy you want covered.

The project is managed with [uv](https://docs.astral.sh/uv/). Installing it
exposes the `spyglass` console command:

```bash
uv sync
uv run spyglass --help
```

## Usage

`spyglass` always writes a JSON Audit Report. You choose the set of Service
Principals to audit with exactly one selection method.

### Select by object id

Pass one or more Service Principal `objectId`s (an `appId` is accepted as a
fallback and resolved to its Service Principal). The flag repeats:

```bash
uv run spyglass \
  --object-id 22222222-2222-2222-2222-222222222222 \
  --object-id 99999999-9999-9999-9999-999999999999 \
  --output audit-report.json
```

### Select from an ids file

Point `--ids-file` at a file of object ids — either a newline-separated list or
a JSON array. Values are merged and de-duplicated with any `--object-id` flags:

```bash
uv run spyglass --ids-file ./sp-ids.txt --output audit-report.json
```

### Select by tag

Audit every Service Principal carrying a given Entra tag. This is mutually
exclusive with `--object-id` / `--ids-file`:

```bash
uv run spyglass --tag terraform-iac --output audit-report.json
```

### Rendering HTML

Add `--html` to additionally render a single self-contained HTML file (data, CSS
and JS embedded — no external assets), suitable for sharing as one file. JSON is
always written; `--html` only adds the HTML view. `--html-output` overrides the
path (and implies `--html`); by default the HTML path is the JSON path with an
`.html` suffix.

```bash
uv run spyglass --tag terraform-iac --html
# writes audit-report.json and audit-report.html

uv run spyglass --tag terraform-iac --html-output report.html
```

The HTML view is **security-focused**: it foregrounds Directory Roles,
Credentials (flagging `expired`, with raw dates kept so "expiring soon" is a
consumer-side judgment) and Azure Role Assignments, while showing group
memberships, API permissions, owners and raw identity as collapsible JSON.
Management-Group-scoped assignments get their own bucket rather than being folded
under a subscription. A sticky search box filters Service Principals by display
name (space-separated tokens matched in order; press `/` to focus, `Esc` to
clear).

### Other flags

- `--output PATH` — path for the JSON Audit Report (default `audit-report.json`).
- `--concurrency N` — maximum Service Principals processed at once (default 8).
  Lower it if a throttling-prone tenant starts returning HTTP 429s.

## The Audit Report

The output is a single JSON **object** (an envelope) — not a bare array:

```json
{
  "meta": { "...": "run-scoped metadata, including runErrors" },
  "servicePrincipals": [ { "...": "one entry per audited SP" } ]
}
```

`meta` carries the run context — `generatedAt`, `tenantId`, the `selection`
(resolved `objectIds`, plus `tag` when selected that way), `toolVersion`, and
`runErrors`. Each entry in `servicePrincipals` (sorted by display name) reports
**both planes** in separately-named fields:

| Field | Plane | What it holds |
| --- | --- | --- |
| `objectId`, `appId`, `displayName`, `tags`, `application` | directory | Identity of the SP and its nullable attached Application (`null` for managed identities, multi-tenant and gallery apps). |
| `directoryRoles` | directory | Directory Roles from all four paths: `assignmentType` (`active`/`eligible`) × direct or via a role-assignable group. `source` is `"direct"` or the group's display name, with `sourceGroupId`. |
| `groupMemberships` | directory | Direct and transitive group memberships, with `isAssignableToRole` and PIM-for-Groups status (`pimMembership`). |
| `credentials` | directory | Secrets and certificates flattened across both the SP and its Application, each with a derived `status` (`active`/`expired`/`not-yet-valid`) and the raw dates. |
| `applicationPermissions`, `delegatedPermissions` | directory | API permissions: application (`appRoleAssignment`) and delegated (`oauth2PermissionGrant`). |
| `owners` | directory | Principals that can modify the identity (and mint Credentials), flattened across SP and Application, tagged with `owner` and `ownerType`. |
| `azureRoleAssignments` | Azure RBAC | Role assignments at management group, subscription, resource group or resource scope. MG-scoped entries carry `managementGroupId` (with `subscription*` null). |
| `errors` | — | Per-SP gaps (SP Gaps): a section that failed for this SP (e.g. a 403 on a PIM call, a missing Application). |

### Two-tier failure model

The run distinguishes two kinds of failure (see
[`docs/adr/0002-report-envelope-and-failure-tiers.md`](docs/adr/0002-report-envelope-and-failure-tiers.md)):

- **Run Errors** — plane-wide or precondition failures (e.g. the Azure RBAC batch
  query failed) recorded in top-level `meta.runErrors`. The rest of the report
  still writes.
- **SP Gaps** — a per-SP, per-section failure recorded in that SP's `errors[]`.
  The run still completes and **exits 0**; gaps are data, not run failures.

A failed precondition (not logged in, no Graph token) is the one case that aborts
before collection with a non-zero exit.

See [`example-audit.json`](example-audit.json) for a full report on synthetic
data — including a Management-Group-scoped assignment, all three credential
statuses, an active and an eligible Directory Role, a managed identity (`null`
Application) with SP Gaps, and a `runErrors` entry — and
[`example-audit.html`](example-audit.html) for the rendered view.

## Required permissions

The tool authenticates as your delegated `az login` user token across both
planes — it is not an app registration; every Graph call is made on behalf of
your signed-in user.

- **Directory plane (Entra).** A call succeeds only when **both** hold: your
  user has a directory role that can read the data (**Global Reader** is enough —
  it covers directory reads, the role-management reads, and PIM-for-Groups), and
  the Graph token actually carries the matching delegated scope.

  The Azure CLI sign-in does not include the privileged role-management / PIM
  scopes by default, so the directory-role schedule and PIM-for-Groups endpoints
  return `403 PermissionScopeNotGranted` until those scopes are consented for the
  `az` sign-in. Grant them by signing in with the scopes (admin consent is
  required — they are admin-restricted):

  ```bash
  az login --scope "https://graph.microsoft.com/RoleManagement.Read.All https://graph.microsoft.com/PrivilegedAccess.Read.AzureADGroup"
  ```

  After consent the tool's `.default` token picks the scopes up automatically —
  no flag on `sp-audit` is needed. Until then those two sections degrade to SP
  Gaps (the affected `errors[]` entry names the exact missing scopes) and the run
  still completes and exits 0. **Directory Readers** alone is not enough even
  once the scopes are consented: it cannot read those objects.
- **Azure RBAC plane (ARM).** A **Reader** (or equivalent) role over the
  management group hierarchy you want covered, so the Azure Resource Graph query
  can resolve assignments at every scope.

## Non-goals

The following are explicitly out of scope for this tool:

- **Sign-in / usage activity** — no dormant-SP detection from sign-in or
  last-credential-usage signals.
- **Effective-privilege computation** — the report keeps raw, cross-referenceable
  facts; there is no derived `effective`/`effectiveReason` field.
- **Terraform state input** — there is no `--state-file` / state parsing; selection
  is by object id, ids file, or tag only.
- **`--management-group` scoping** — the Azure RBAC query always covers the full
  management group hierarchy; there is no scoping flag.
- **`--expiring-within` threshold flag** — raw credential dates are retained so
  "expiring soon" stays a consumer-side judgment.

## Development

```bash
uv run pytest        # tests
uv run ruff check .  # lint
uv run ruff format . # format
uv run ty check      # type-check
```

Design history lives in [`implementation_plan.md`](implementation_plan.md) and
[`entra_audit_plan.md`](entra_audit_plan.md) (historical notes — the current
sources of truth are this README and [`CONTEXT.md`](CONTEXT.md)). Architectural
decisions are in [`docs/adr/`](docs/adr).
