---
labels: [ready-for-agent]
---

# Directory Roles — four paths with via-group attribution

## What to build

Collect Directory Roles across all four assignment paths and attribute group-borne
roles back to the SP. The paths are active/eligible × direct/via-group, using
`roleAssignmentSchedules` (active) and `roleEligibilitySchedules` (eligible),
filtered on `principalId`, with `$expand=roleDefinition` to resolve role display
names.

- **Direct (a/b):** filter on the SP's id; `source = "direct"`, `sourceGroupId =
  null`.
- **Via-group (c/d):** for each role-assignable group the SP is a transitive member
  of, filter on the group id; attribute any returned role to the SP with
  `source = <group displayName>` and `sourceGroupId = <group id>`.

Apply **Via-group attribution** per the glossary: transitive membership in a
role-assignable group that holds role R credits R to the SP regardless of the path,
including intermediate non-role-assignable groups.

Add a per-group role-schedule single-flight cache (`groupId → (active, eligible)
schedules`) so a group reached by many SPs is fetched once, not once per SP.

Report **raw facts only** — `assignmentType`, `source`, `sourceGroupId`,
`directoryScopeId`, and the schedule dates. No computed `effective` field. A
per-section failure degrades to an SP Gap.

## Acceptance criteria

- [ ] Each SP record carries `directoryRoles` populated from all four paths, with
      resolved role display names.
- [ ] Direct roles are labeled `source = "direct"`; via-group roles carry the
      group's display name as `source` and its id as `sourceGroupId`.
- [ ] Only role-assignable groups are queried for via-group paths; attribution is
      path-insensitive across transitive membership.
- [ ] A group's role schedules are fetched once and reused across all SPs that
      reach it (via the single-flight cache).
- [ ] No `effective`/`effectiveReason` field is emitted; roles and memberships
      remain separate cross-referenceable arrays.
- [ ] A 403 on a schedule call records an SP Gap and the run continues.

## Blocked by

- Issue 04 (group memberships + single-flight)
