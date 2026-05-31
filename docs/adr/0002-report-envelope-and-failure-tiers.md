# Report is an object envelope; two-tier failure model

## Status

accepted

## Context

The two predecessor tools each emitted a bare JSON array (one entry per Service
Principal) and called `sys.exit(1)` on almost any failure. The merged tool runs
both planes (Entra + Azure RBAC) in one async run and fans out across many Graph
calls, where partial failures are normal: a Directory-Readers-only user gets 403
on PIM endpoints; managed identities have no Application object; the Azure RBAC
batch query can fail independently of the Entra data.

## Decision

The Audit Report is an **object envelope**, not a bare array:
`{ "meta": { ..., "runErrors": [...] }, "servicePrincipals": [...] }`. This lets
the report carry run metadata and plane-wide errors that have no per-SP home.
The HTML renderer is updated to read this shape.

Failures fall into **two tiers**:

- **SP Gaps** — per-SP, per-section failures (403 on a schedule call, missing
  Application object) are recorded in that SP's `errors[]`; the run continues.
- **Run Errors** — plane-wide failures (e.g. the Azure RBAC batch query failing)
  are recorded in `meta.runErrors`; the run still completes and writes the other
  plane's data. Only **global preconditions** (not logged in, cannot construct
  the Graph client) fail fast with a non-zero exit, before collection starts.

The process exits **0 whenever the audit ran to completion and JSON was
written**, even if SP Gaps or Run Errors are present — those are honestly
reported data, not run failures. CI that wants to gate on gaps inspects the
output (e.g. with `jq`), not the exit code. This deliberately reverses the
predecessor tools' `sys.exit(1)`-on-any-failure instinct, including the lifted
ARG collector's.
