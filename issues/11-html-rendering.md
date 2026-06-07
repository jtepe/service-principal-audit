---
labels: [Implemented]
---

# HTML rendering — security-focused self-contained report

## What to build

Render the Audit Report to a single self-contained HTML file (data + CSS + JS
embedded, no external assets), controlled by flags on the same run — `--html` and
optional `--html-output PATH` (default: the JSON path with an `.html` suffix). No
interactive prompt. JSON is always written; `--html` additionally renders.

The view is **security-focused**: foreground Directory Roles, Credentials (with
`expired` flagged, and "expiring soon" computable from the retained raw dates), and
Azure Role Assignments. Show the long-tail sections (group memberships, API
permissions, owners, raw identity) as collapsible raw JSON so nothing is hidden.
Adapt to the `{ meta, servicePrincipals }` envelope (render a meta header and any
`runErrors`/per-SP `errors`), and give Management-Group-scoped Azure assignments
their own bucket instead of forcing them under a subscription. Preserve the
existing sticky display-name search/filter UX.

Establishes the pure `render` module (`report dict → HTML string`).

This is a **HITL** slice: the visual layout and what counts as "high-signal" needs
human review before it's locked in.

## Acceptance criteria

- [x] `sp-audit --html` writes both JSON and a single self-contained HTML file with
      no external asset references; `--html-output` overrides the path.
- [x] No interactive prompt; default (no `--html`) writes JSON only.
- [x] Directory roles, credentials, and Azure RBAC are foregrounded; expired
      credentials are visibly flagged.
- [x] Long-tail sections render as collapsible raw JSON.
- [x] Management-Group-scoped assignments render in a distinct bucket, not under a
      subscription.
- [x] The meta header and any run/SP errors are shown; the display-name filter
      works.
- [x] `</script>` or similar in any field is safely escaped.
- [x] Unit tests cover `render`: self-contained output from a sample report,
      expired-credential flagging, MG-scope bucket, no external assets, and
      injection-safe escaping.
- [x] A human has reviewed the rendered layout and signed off.

## Blocked by

- Issue 03 (Azure RBAC plane — needs azureRoleAssignments + MG scope bucket)
- Issue 05 (Directory Roles)
- Issue 07 (Credentials)
