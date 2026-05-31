---
labels: [ready-for-agent]
---

# Service Principal Audit — Unified Entra + Azure RBAC Auditor

## Problem Statement

I administer a tenant with roughly 500 service principals across ~160
subscriptions under a single management group. Today I can answer only half of
"what can this identity do, and what's risky about it." The existing
`audit_rbac.py` tells me Azure RBAC role assignments, but Entra — which is the
actual hub of information about a service principal — is invisible: I can't see
its directory roles, group memberships, PIM eligibility, secrets and
certificates (and whether they've expired), API permissions, or who owns it. I
have to stitch that together by hand in the portal, one SP at a time, which
doesn't scale to 500 and gives me no durable, diff-able artifact.

On top of that, the RBAC tool I do have produces two known-wrong outputs:
management-group-scoped role assignments are mislabeled as "Resource Group" with
no subscription, and role names resolve to raw GUIDs (e.g.
`b24988ac-6180-42a0-ab88-20f7382dd24c`) instead of "Contributor".

## Solution

A single read-only CLI, `sp-audit`, that takes a set of Service Principals
(selected by Entra tag or by explicit object/app IDs) and, in **one run**,
gathers everything both the directory plane (Entra, via Microsoft Graph) and the
Azure RBAC plane (ARM, via Azure Resource Graph) know about each one, writing it
to a single JSON Audit Report. The same command can optionally render that report
to a self-contained HTML file.

For each Service Principal the report captures: identity and tags; the related
Application (or `null` for managed identities / multi-tenant / gallery apps);
direct and transitive group memberships; Directory Roles across all four
assignment paths (active/eligible × direct/via-group); PIM-for-Groups membership
status; Credentials (secrets and certificates from both the SP and the
Application, each with a derived validity status); application and delegated API
permissions; Owners of both the SP and the Application; and Azure Role
Assignments at every scope. The two known RBAC bugs (management-group scope
classification and GUID role names) are fixed as part of folding the RBAC
collector in.

The output is a self-describing artifact I can commit to git, attach to tickets,
and diff over time — and the HTML view surfaces the high-signal security surface
(directory roles, live/expiring credentials, Azure RBAC) at a glance.

## User Stories

1. As a tenant auditor, I want to select Service Principals by Entra tag, so that
   I can audit a managed fleet (e.g. all `terraform-iac` SPs) without listing IDs.
2. As a tenant auditor, I want to pass explicit Service Principal object IDs on
   the command line, so that I can audit a specific few without tagging them.
3. As a tenant auditor, I want to supply object IDs from a file (newline list or
   JSON array), so that I can audit hundreds of SPs without a 500-flag command.
4. As a tenant auditor, I want `--object-id` and `--ids-file` to combine into one
   set, so that I can mix ad-hoc IDs with a bulk file in a single run.
5. As a tenant auditor, I want an app (client) ID to still resolve when I pass it
   where an object ID was expected, so that I'm not blocked by which ID I have on
   hand.
6. As a tenant auditor, I want every selected SP to enter the audit with the same
   baseline fields regardless of how it was selected, so that tag-selected SPs
   never silently miss their SP-side credentials.
7. As a tenant auditor, I want a single run to cover both Entra and Azure RBAC, so
   that I get one complete picture per SP instead of reconciling two tools.
8. As a tenant auditor, I want each SP's identity, tags, appId, and objectId, so
   that I can unambiguously identify the audited subject.
9. As a tenant auditor, I want the related Application reported as a nullable
   attached object, so that I can tell managed identities and gallery apps (which
   have no Application) from SPs that own an app registration.
10. As a security reviewer, I want direct and transitive group memberships,
    labeled as such, so that I can see both assigned and inherited group context.
11. As a security reviewer, I want Directory Roles across all four paths
    (active/eligible × direct/via-group), so that I see standing privilege and
    dormant PIM eligibility alike.
12. As a security reviewer, I want a role inherited via a group attributed to the
    SP with the group named as its source, so that I know why the SP holds it.
13. As a security reviewer, I want via-group attribution to follow transitive
    membership regardless of intermediate non-role-assignable groups, so that no
    inherited role is missed because of nesting.
14. As a security reviewer, I want PIM-for-Groups membership status
    (assigned/eligible/none) per role-assignable group, so that I can tell a
    standing member from one who must activate first.
15. As a security reviewer, I want the raw facts of roles and memberships kept in
    separate cross-referenceable arrays, so that I can judge effective privilege
    myself rather than trust a brittle snapshot boolean.
16. As a security reviewer, I want every secret and certificate from both the SP
    and the Application in one Credentials array, so that I can see everything
    that can authenticate as the identity in one place.
17. As a security reviewer, I want each Credential tagged with which object owns
    it (application vs servicePrincipal) and whether it's a secret or
    certificate, so that I can reason about where a credential lives.
18. As a security reviewer, I want each Credential's status derived as
    active/expired/not-yet-valid from both its start and end dates, so that
    future-dated and expired credentials are never mistaken for active ones.
19. As a security reviewer, I want the raw start/end dates retained alongside the
    status, so that I can compute "expiring soon" myself at any threshold.
20. As a security reviewer, I want application permissions (appRoleAssignments)
    resolved to human-readable values like `User.Read.All`, so that I don't have
    to decode appRole GUIDs.
21. As a security reviewer, I want delegated permissions (oauth2PermissionGrants)
    with their scopes, consent type, and principal, so that I can spot delegated
    grants, which are rare but security-relevant for SPs.
22. As a security reviewer, I want the Owners of both the SP and the Application
    in one array, so that I can see who can modify the identity and mint
    credentials for it.
23. As a security reviewer, I want each Owner tagged with its type
    (user/servicePrincipal/group), so that an SP-owned-by-another-SP privilege
    chain is visible, not hidden among human owners.
24. As a tenant auditor, I want Azure Role Assignments at management group,
    subscription, resource group, and resource scope, so that I see the SP's full
    ARM-plane footprint.
25. As a tenant auditor, I want management-group-scoped assignments labeled
    "Management Group" (not mislabeled "Resource Group"), so that tenant-level
    grants are classified correctly.
26. As a tenant auditor, I want the management group id parsed out of such
    assignments, so that MG-scoped grants aren't just an opaque scope string.
27. As a tenant auditor, I want Azure role names resolved to friendly names like
    "Contributor" instead of GUIDs, so that the report is readable.
28. As a tenant auditor, I want the Azure RBAC query to always cover all
    management groups, so that I never have to remember to widen the scope.
29. As a tenant auditor, I want directory roles and Azure RBAC assignments kept in
    distinctly named fields, so that the two authorization planes are never
    conflated.
30. As a CI/automation user, I want the tool to always write JSON and exit 0 when
    the audit completes — even with noted gaps — so that scheduled runs don't
    fail on expected partial-permission cases.
31. As a CI/automation user, I want the tool to never prompt interactively, so
    that it runs unattended in pipelines.
32. As an operator, I want a per-SP failure (e.g. a 403 on a PIM call, a missing
    Application) recorded as an SP Gap in that SP's errors array, so that one bad
    SP doesn't abort the whole audit.
33. As an operator, I want a plane-wide failure (e.g. the Azure RBAC query
    failing) recorded as a Run Error in the report's meta, so that the other
    plane's good data still gets written.
34. As an operator, I want global preconditions (not logged in, no Graph token)
    to fail fast with a clear message before any collection, so that I fix auth
    up front rather than 500 SPs deep.
35. As an operator, I want a single up-front check that verifies both `az account
    show` and a live Graph token, so that a broken Graph path is caught early.
36. As an auditor, I want the report to be a self-describing object with meta
    (generatedAt, tenantId, selection, toolVersion, runErrors) plus the SP array,
    so that a committed file explains itself months later.
37. As an auditor, I want the signed-in user's UPN kept out of the report, so that
    a shared/committed artifact doesn't bake in personal PII.
38. As an auditor, I want the report sorted by display name, so that diffs between
    runs are stable and readable.
39. As an auditor, I want an optional self-contained HTML rendering of the report,
    so that I can open or share it as a single file with no external assets.
40. As an auditor, I want HTML rendering controlled by a flag on the same run (not
    an interactive prompt), so that it composes cleanly in scripts.
41. As a security reviewer, I want the HTML to foreground directory roles, live
    credentials (expired/expiring flagged), and Azure RBAC, so that the riskiest
    facts are visible without drilling in.
42. As a security reviewer, I want the long-tail sections shown as collapsible raw
    JSON in the HTML, so that nothing is hidden even before it's hand-styled.
43. As an auditor, I want the existing display-name search/filter in the HTML
    preserved, so that I can find an SP quickly in a large report.
44. As a large-tenant operator, I want SP processing bounded by a configurable
    concurrency limit (default conservative), so that I can dial load down when a
    throttling-prone tenant starts returning 429s.
45. As a large-tenant operator, I want shared lookups (role definitions, app
    roles, resources, groups, and per-group role schedules) fetched once and
    reused across SPs, so that a 500-SP run doesn't refetch the Microsoft Graph SP
    hundreds of times.
46. As a maintainer, I want type hints throughout, checked by `ty`, so that schema
    shapes are enforced rather than aspirational.
47. As a maintainer, I want formatting and linting enforced by ruff via a
    pre-commit hook, so that style and obvious bugs are caught before commit.
48. As a maintainer, I want the README rewritten to the new model and the old plan
    docs marked as historical, so that nobody mistakes stale docs for the spec.

## Implementation Decisions

### Architecture & transport (see ADR-0001, ADR-0002)

- Single async CLI program (`asyncio.run`). Entra plane uses `azure-identity`
  (`AzureCliCredential`) + `msgraph-sdk`, relying on the SDK's transparent paging
  and Retry-After/429 handling. The Azure RBAC plane keeps the working sync
  `az graph query` subprocess logic, wrapped in `asyncio.to_thread`. The former
  zero-dependency stance is intentionally abandoned.
- The Audit Report is an **object envelope**, not a bare array:
  `{ "meta": {...}, "servicePrincipals": [...] }`. `meta` carries `generatedAt`,
  `tenantId`, `selection`, `toolVersion`, and `runErrors`. No `signedInUser`, no
  `managementGroup` (scope is always full-tenant).
- **Two-tier failures.** SP Gaps → per-SP `errors[]`, run continues. Run Errors →
  `meta.runErrors`, other plane still written. Global preconditions fail fast with
  non-zero exit before collection. The process exits **0** whenever the audit
  completes and JSON is written, even with gaps/run-errors present.

### Packaging & layout

- Package renamed to `service-principal-audit`; console script `sp-audit`
  (`[project.scripts]`). Source under `src/sp_audit/`. Terraform input
  (`--state-file`, `parse_terraform_state`) is removed entirely.

### Modules

Pure / network-free (the bulk of the logic lives here so the I/O modules stay
thin):

- **`models`** — TypedDicts for the envelope, the per-SP record, and every
  sub-shape (group membership, directory role, credential, application/delegated
  permission, owner, azure role assignment, meta).
- **`single_flight`** — async single-flight cache (`dict[key, asyncio.Task]`) so
  the first miss starts a fetch and concurrent missers await the same task. Used
  for all five resolution caches (`roleDefinitionId→name`, `appRoleId→value`,
  `resourceId→displayName`, `groupId→displayName`, and `groupId→(active,eligible)
  role schedules`).
- **`arg_transform`** — pure transforms over raw ARG rows. Houses both RBAC bug
  fixes: (1) **scope classification** by scope-prefix, adding a "Management Group"
  scopeType and parsing `managementGroupId`; (2) **role-name resolution** via a
  GUID-normalized, de-duplicated join (no per-UUID lookup, no extra calls). The
  intended classifier shape:
  ```
  scopeType = case(
    scope startswith '/providers/Microsoft.Management/managementGroups/', 'Management Group',
    segments == 3, 'Subscription',
    segments == 5, 'Resource Group',
    'Resource')
  ```
  and the role-name join normalizes both sides to the trailing GUID, dedups role
  definitions before joining, and falls back to the GUID only for deleted roles.
- **`credentials`** — derives Credential status (`active`/`expired`/
  `not-yet-valid`) from both `startDateTime` and `endDateTime` against an injected
  timezone-aware UTC now; surfaces `credentialType` as `secret`/`certificate`.
- **`selection_parse`** — parses `--ids-file` (newline list or JSON array) and
  merges with repeated `--object-id` into one deduped set.
- **`report`** — merges both planes per SP, builds the `{ meta, servicePrincipals
  }` envelope, sorts SPs by display name.
- **`render`** — pure `report dict → self-contained HTML string`, lifted/rewritten
  from `render_html.py`; security-focused sections plus collapsible raw-JSON tail;
  preserves the display-name filter; reads the new envelope and the Management
  Group scope bucket.

I/O-bound (thin orchestration):

- **`auth`** — up-front precondition gating **both** `az account show` and a live
  `AzureCliCredential` Graph token acquisition; harvests `tenantId` for `meta`.
- **`entra`** — Graph collectors for: identity + SP-side credentials; direct &
  transitive group memberships; the four Directory Role paths; PIM-for-Groups
  status; Application credentials; application & delegated permissions; Owners of
  SP and Application. Applies Via-group attribution. Records SP Gaps on per-section
  failure.
- **`azure_rbac`** — the sync ARG collector (always full management-group scope),
  invoked via `to_thread`; failures become Run Errors, not `sys.exit`.
- **`cli`** — argparse (`--tag` xor (`--object-id` + `--ids-file`), `--output`,
  `--html`/`--html-output`, `--concurrency` default 5) + `asyncio.run`, wiring the
  modules.

### Selection semantics

- `--tag` is mutually exclusive with the ID inputs. `--object-id` (repeatable) and
  `--ids-file` combine. An object ID is resolved strictly via
  `GET /servicePrincipals/{id}`, falling back to `appId eq '{id}'` only on 404.
- **Unified `$select`** across both selection paths (`id, displayName, appId,
  tags, passwordCredentials, keyCredentials`) so SP-side credentials are never
  path-dependent.

### Schema vocabulary

- Identifier names: `appId` (not `applicationId`/"Client ID"), `objectId`.
- Role fields: `directoryRoles` (Entra plane) and `azureRoleAssignments` (RBAC
  plane) — never an unqualified `roleAssignments`.
- Directory roles report raw facts only (`assignmentType`, `source`,
  `sourceGroupId`); no computed `effective` field.
- `application` and per-section data degrade to `null`/empty with an SP Gap note
  rather than aborting.

### Concurrency & caching

- Bound **across SPs** with a single `asyncio.Semaphore` (`--concurrency`, default
  5); independent calls within one SP use `asyncio.gather`. Backoff is delegated
  to the SDK's Retry-After handler.
- All cross-SP lookups go through `single_flight` to preserve "one fetch serves
  many," including per-group directory-role schedules (deduped across every SP
  that transitively reaches the group).

### Tooling

- ruff format + lint (rule set `E, F, I, UP, B`); `ty` in strict mode with a
  pinned pre-1.0 version range. Enforced via a pre-commit hook; because `ty` has
  no official pre-commit hook at this version, it runs as a `repo: local` hook
  invoking `uv run ty check`.

## Testing Decisions

Good tests here exercise **external behavior through a module's public interface**
— given raw input, assert the transformed output — never internal call sequences
or private helpers. The pure modules are designed precisely so this is possible
without mocking Graph or `az`. Time-dependent logic takes an injected "now" rather
than reading the clock, so tests are deterministic.

Tests will be written for **all pure modules**:

- **`arg_transform`** (highest priority — encodes the two known bugs): MG-scoped
  scope strings classify as "Management Group" and yield a parsed
  `managementGroupId`; subscription/RG/resource scopes classify correctly; a role
  assignment whose `roleDefinitionId` GUID matches a (differently-scoped) role
  definition resolves to the friendly name; duplicate role definitions across
  subscriptions don't fan out assignment rows; an unmatched GUID falls back to the
  GUID string.
- **`credentials`**: end in the past → `expired`; start in the future → `not-yet-
  valid`; start past & end future (or null end) → `active`; all computed against an
  injected UTC now; `secret`/`certificate` mapping from the two Graph collections.
- **`single_flight`**: concurrent missers on the same key trigger exactly one
  underlying fetch and all receive the same result; distinct keys fetch
  independently; a failed fetch doesn't poison a subsequent retry.
- **`selection_parse`**: newline-list and JSON-array files both parse; blank lines
  ignored; `--object-id` values merge and dedup with file IDs.
- **`report`**: both planes merge into one per-SP record; SPs sort by display name;
  the envelope carries the expected `meta` keys; an SP with no assignments still
  appears.
- **`render`**: produces a single self-contained HTML string from a sample report;
  expired credentials are flagged; the Management Group scope bucket renders
  distinctly from subscriptions; output contains no external asset references;
  `</script>` in any field is safely escaped.
- **`models`**: exercised indirectly through the above (TypedDicts assert shape at
  the `ty` level rather than at runtime).

Prior art: the existing `audit_rbac.py` and `render_html.py` already use TypedDicts
and pure helper functions (`_sp_from_graph_payload`, the `render()` function) that
take data and return data — the new pure modules follow the same data-in/data-out
style, now isolated behind their own modules for direct unit testing.

## Out of Scope

- **Sign-in / usage activity.** Dormant-SP detection via sign-in or
  last-credential-usage signals is deferred — it is beta-only in Graph and would
  compromise the v1.0-endpoints-only guarantee.
- **Effective-privilege computation.** No derived `effective`/`effectiveReason`
  field; the report keeps raw, cross-referenceable facts. A derived view can be
  added later without schema disruption.
- **Remediation / writes.** The tool is strictly read-only; it never modifies the
  tenant.
- **Terraform state input.** Removed entirely.
- **`--management-group` scoping.** The Azure RBAC query always covers all
  management groups; there is no scoping flag.
- **`--expiring-within` threshold flag.** Not in v1; raw credential dates are
  retained so consumers (including the HTML) can compute "expiring soon"
  themselves.
- **Full hand-styled HTML parity for every section.** v1 HTML is security-focused
  with a raw-JSON tail; full per-section styling is a later iteration.
- **Pre-commit/CI beyond the local hook.** A hosted CI pipeline is a clean
  follow-up, not part of this iteration.

## Further Notes

- Domain language is defined in `CONTEXT.md`; architectural decisions in
  `docs/adr/0001-msgraph-sdk-async-with-sync-wrapped-arg.md` and
  `docs/adr/0002-report-envelope-and-failure-tiers.md`. The PRD uses that
  vocabulary throughout (Service Principal, Application, Plane, Directory Role,
  Azure Role Assignment, Credential, Owner, Audit Report, Run Error, SP Gap,
  Via-group attribution).
- Authentication is delegated: the tool runs as the signed-in `az login` user and
  inherits their directory role. **Global Reader** is the recommended role (covers
  directory reads plus role-management and PIM reads). **Directory Readers** alone
  will produce SP Gaps on the directory-role schedule and PIM endpoints — the
  tool degrades gracefully and the docs should steer users to Global Reader.
- The PIM-for-Groups schedule endpoints require a `$filter` on `principalId` (they
  return HTTP 400 otherwise) — always filter by the SP id, then read `groupId` and
  `accessId` from the results.
- The stale `example-audit.json` / `example-audit.html` describe the old RBAC-only
  schema and will be deleted, then regenerated from a real run once the tool
  exists. The README is fully rewritten; `implementation_plan.md` and
  `entra_audit_plan.md` are demoted to dated historical notes.
- All cited Graph endpoints are v1.0.
