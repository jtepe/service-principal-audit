---
labels: [ready-for-agent]
---

# Docs rewrite and example artifact regeneration

## What to build

Bring the documentation in line with the shipped tool. Rewrite the README against
the new model: the `sp-audit` console command; selection via `--tag` /
`--object-id` / `--ids-file`; the `{ meta, servicePrincipals }` envelope; both
planes and their distinct fields; the four Directory Role paths; credentials,
owners, and permissions; Global Reader guidance (and the Directory-Readers gap that
produces SP Gaps on role/PIM endpoints); the `--html` flags; and the explicit
non-goals (sign-in activity, effective-privilege computation, Terraform,
`--management-group`, `--expiring-within`).

Demote `implementation_plan.md` and `entra_audit_plan.md` to historical design
notes with a one-line header pointing to the README and `CONTEXT.md` as the current
source of truth. Delete the stale `example-audit.json` / `example-audit.html`
(which describe the old RBAC-only schema) and regenerate both from a real `sp-audit`
run against synthetic/fake-GUID data so the examples match the final schema.

## Acceptance criteria

- [ ] README describes the `sp-audit` command, all selection flags, the envelope,
      both planes, every section, Global Reader guidance, the `--html` flags, and
      the documented non-goals.
- [ ] `implementation_plan.md` and `entra_audit_plan.md` carry a header marking them
      historical and pointing to README + `CONTEXT.md`.
- [ ] The stale example artifacts are removed and regenerated from a real run, with
      the current envelope and schema (including a Management Group scope example and
      a credential status example).
- [ ] No doc references the removed Terraform path or `--management-group`.

## Blocked by

- Issue 11 (HTML rendering — examples regenerate both JSON and HTML from a real run)
