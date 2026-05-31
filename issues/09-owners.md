---
labels: [ready-for-agent]
---

# Owners — who can modify the identity

## What to build

Collect the Owners of both the Service Principal and its Application, flattened into
one `owners[]` array, so the report answers "who can modify this identity and mint
credentials for it." Query `/servicePrincipals/{id}/owners` and (when an
Application object exists) `/applications/{id}/owners`, with `$select=id,
displayName` on the owner reads.

Each entry carries `owner` (`application`/`servicePrincipal` — which object is
owned, mirroring the credentials discriminator), `ownerType`
(`user`/`servicePrincipal`/`group`, from `@odata.type`), `id`, and `displayName`.
An SP-owned-by-another-SP entry is a privilege chain and must be visible, not
hidden among human owners. When there is no Application object, only SP-owners are
reported. A per-section failure degrades to an SP Gap.

## Acceptance criteria

- [ ] `owners[]` includes owners of both the SP and (when present) the Application,
      each tagged with `owner` and `ownerType`.
- [ ] `ownerType` distinguishes `user`, `servicePrincipal`, and `group` so non-human
      owners (privilege chains) are visible.
- [ ] An SP with no Application object reports only SP-side owners without error.
- [ ] Owner reads page fully.
- [ ] A failure on an owners call records an SP Gap and the run continues.

## Blocked by

- Issue 07 (credentials — establishes Application resolution and the owner/owned
  discriminator)
