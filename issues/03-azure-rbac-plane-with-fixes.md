---
labels: [ready-for-agent]
---

# Azure RBAC plane with scope-classification and role-name fixes

## What to build

Fold the Azure RBAC plane into the unified run and fix its two known bugs. Lift the
working `az graph query` ARG collector, keep it synchronous, and invoke it via
`asyncio.to_thread`. The query always covers all management groups (no scoping
flag). Each SP's record gains an `azureRoleAssignments` field. A failure of the ARG
batch query becomes a **Run Error** in `meta.runErrors` — never a `sys.exit` — so
the Entra-plane data still writes.

Extract all row-level logic into the pure `arg_transform` module and fix both bugs
there:

1. **Scope classification by prefix.** Management-group-scoped assignments
   (`/providers/Microsoft.Management/managementGroups/{mgId}`) currently collide
   with Resource Group on segment count and are mislabeled. Classify by scope
   prefix, add a `"Management Group"` scopeType, and parse out `managementGroupId`.
   From the design discussion, the intended shape:
   ```
   scopeType = case(
     scope startswith '/providers/Microsoft.Management/managementGroups/', 'Management Group',
     segments == 3, 'Subscription',
     segments == 5, 'Resource Group',
     'Resource')
   ```
2. **Role-name resolution.** Role names currently resolve to the raw GUID because
   the join matches on the full resource id (which differs by scope) instead of the
   role definition GUID. Normalize both sides to the trailing GUID, de-duplicate
   role definitions before joining (so duplicates across subscriptions don't fan
   out assignment rows), and fall back to the GUID only for a deleted role. This
   stays a single in-query resolution — no per-UUID lookups, no cache.

## Acceptance criteria

- [ ] A single `sp-audit` run populates `azureRoleAssignments` per SP from a full
      management-group-scoped ARG query.
- [ ] MG-scoped assignments classify as `"Management Group"` and carry a parsed
      `managementGroupId`; subscription/RG/resource scopes classify correctly.
- [ ] Azure role names resolve to friendly names (e.g. "Contributor"), not GUIDs;
      an unresolved (deleted) role falls back to the GUID.
- [ ] Duplicate role definitions across subscriptions do not multiply assignment
      rows.
- [ ] An ARG query failure is recorded in `meta.runErrors` and the run still writes
      the Entra-plane data and exits 0.
- [ ] `azureRoleAssignments` and `directoryRoles` are distinct fields.
- [ ] Unit tests cover `arg_transform`: all four scopeType classifications + parsed
      `managementGroupId`, GUID-normalized role-name resolution, dedup-no-fanout,
      and the deleted-role GUID fallback.

## Blocked by

- Issue 01 (walking skeleton)
