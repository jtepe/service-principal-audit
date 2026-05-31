---
labels: [ready-for-agent]
---

# PIM-for-Groups membership status

## What to build

Annotate how the SP holds each role-assignable group membership — standing
(`assigned`), `eligible`, or `none` — which is orthogonal to whether the group's
role is active/eligible. Query the PIM-for-Groups assignment schedules (active
membership) and eligibility schedules (eligible membership), filtered on the SP's
`principalId`.

> Gotcha: these endpoints return **HTTP 400** without a `$filter` on `principalId`
> (or `groupId`). Always filter by the SP id, then read `groupId` and `accessId`
> from the results.

Keep `accessId = member` for role-inheritance reasoning. Use the results to set
each entry's `groupMemberships[].pimMembership`. A per-section failure (e.g. a
Directory-Readers-only user hitting PIM) degrades to an SP Gap.

## Acceptance criteria

- [ ] Both PIM-for-Groups schedule calls are always issued with a `principalId`
      `$filter`.
- [ ] Each role-assignable group membership gets `pimMembership` set to
      `assigned`, `eligible`, or `none`.
- [ ] `member` vs `owner` `accessId` is handled so role-inheritance reasoning uses
      `member`.
- [ ] A 403/400 on a PIM call records an SP Gap and the run continues.

## Blocked by

- Issue 04 (group memberships + single-flight)
