---
labels: [ready-for-agent]
---

# Credentials — secrets and certificates with derived status

## What to build

Collect every Credential that can authenticate as the identity, from both the
Service Principal and its Application, flattened into one `credentials[]` array.
SP-side `passwordCredentials`/`keyCredentials` already arrive via the unified
`$select`; resolve the Application via `appId eq '{appId}'` (0 or 1 result) for its
credentials, emitting `application: null` and degrading gracefully (SP Gap) when
there is no local Application object (managed identities, gallery, cross-tenant).

Each entry carries `owner` (`application`/`servicePrincipal`), `credentialType`
(`secret` for password collections, `certificate` for key collections),
`displayName`, `keyId`, `startDateTime`, `endDateTime`, and a derived **Credential
status**: `active` | `expired` | `not-yet-valid`, computed in the pure
`credentials` module from both start and end dates against an injected
timezone-aware UTC now. Raw dates are retained so "expiring soon" stays a
consumer-side judgment.

## Acceptance criteria

- [ ] `credentials[]` includes secrets and certificates from both the SP and the
      Application, each tagged with `owner` and `credentialType`
      (`secret`/`certificate`).
- [ ] Each credential has a `status` of `active`/`expired`/`not-yet-valid` derived
      from both dates; raw `startDateTime`/`endDateTime` are retained.
- [ ] An SP with no Application object yields `application: null`, an SP Gap note,
      and still reports SP-side credentials.
- [ ] Status uses a timezone-aware UTC comparison.
- [ ] Unit tests cover `credentials`: end in past → `expired`; start in future →
      `not-yet-valid`; start past & end future/null → `active`; all against an
      injected now; secret/certificate mapping from the two collections.

## Blocked by

- Issue 01 (walking skeleton)
