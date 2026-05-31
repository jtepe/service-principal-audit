---
labels: [ready-for-agent]
---

# API permissions — application and delegated

## What to build

Collect the SP's API permissions on both planes of Graph consent.

- **Application permissions** (`appRoleAssignments`): each has `resourceId`,
  `resourceDisplayName`, and an `appRoleId` GUID. Resolve `appRoleId` to its
  human-readable value (e.g. `User.Read.All`) by fetching the resource SP's
  `appRoles` once and mapping. The all-zero GUID means "default access," not a
  specific role.
- **Delegated permissions** (`oauth2PermissionGrants`): each has `resourceId`,
  `scope` (space-delimited), `consentType` (`AllPrincipals`/`Principal`), and
  `principalId`. Surface scopes as a list.

Resolve `resourceId → displayName` and `appRoleId → value` through single-flight
caches keyed by `resourceId`, so the Microsoft Graph resource SP (targeted by most
assignments) is fetched once and reused across all SPs. A per-section failure
degrades to an SP Gap.

## Acceptance criteria

- [ ] `applicationPermissions[]` lists each assignment with `resourceId`,
      `resourceDisplayName`, `appRoleId`, and the resolved permission value.
- [ ] `delegatedPermissions[]` lists each grant with `resourceId`,
      `resourceDisplayName`, scopes (as a list), `consentType`, and `principalId`.
- [ ] Resource SP and appRole lookups go through single-flight caches keyed by
      `resourceId`; the Microsoft Graph SP is fetched once for a multi-SP run.
- [ ] The all-zero appRole GUID is handled as "default access," not a named role.
- [ ] A failure on either collection records an SP Gap and the run continues.

## Blocked by

- Issue 04 (group memberships + single-flight)
