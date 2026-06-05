---
labels: [implemented]
---

# Group memberships + single-flight cache foundation

## What to build

Collect each SP's group memberships and introduce the shared async caching
primitive the directory-role and permission slices depend on.

Per SP, gather direct memberships (`memberOf`, kept where the entry is a group) and
transitive memberships (`transitiveMemberOf`), labeling each as `direct` or
`transitive`. Request `isAssignableToRole` and build the `groupId → displayName`
map used downstream. Each membership entry carries `groupId`, `displayName`,
`membershipType`, and `isAssignableToRole`.

Introduce the pure `single_flight` module — an async single-flight cache
(`dict[key, asyncio.Task]`) where the first miss starts the fetch and concurrent
missers await the same in-flight task. Back the `groupId → displayName` lookup with
it, establishing the pattern slices 5 and 8 reuse.

A per-section failure degrades to an SP Gap in that SP's `errors[]` rather than
aborting.

## Acceptance criteria

- [x] Each SP record carries `groupMemberships` with direct and transitive entries,
      labeled, including `isAssignableToRole`.
- [x] Both membership queries page fully.
- [x] `single_flight` exists and backs the `groupId → displayName` lookup; a repeat
      lookup for the same group does not refetch.
- [x] A 403 or error on a membership call records an SP Gap and the run continues.
- [x] Unit tests cover `single_flight`: concurrent missers on one key trigger
      exactly one underlying fetch and all receive the same result; distinct keys
      fetch independently; a failed fetch does not poison a later retry.

## Blocked by

- Issue 01 (walking skeleton)
