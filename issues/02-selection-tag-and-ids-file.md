---
labels: [ready-for-agent]
---

# Selection by tag and by ids-file

## What to build

Complete the SP selection surface. Add `--tag TAG` (mutually exclusive with the ID
inputs) which queries `GET /servicePrincipals?$filter=tags/any(c:c eq '{tag}')`
with OData single-quote escaping and full pagination. Add `--ids-file PATH` which
accepts either a newline list or a JSON array of object IDs, and have it merge and
dedup with repeated `--object-id` values into one selection set.

Crucially, normalize the `$select` across **both** selection paths
(`id, displayName, appId, tags, passwordCredentials, keyCredentials`) so every SP
enters the per-SP fan-out with an identical baseline and SP-side credentials are
never path-dependent.

Establishes the pure `selection_parse` module.

## Acceptance criteria

- [ ] `--tag` selects SPs via the tags filter with single-quote escaping and pages
      through all results.
- [ ] `--tag` is mutually exclusive with `--object-id`/`--ids-file`; `--object-id`
      and `--ids-file` combine into one deduped set.
- [ ] `--ids-file` parses both a newline list and a JSON array; blank lines are
      ignored.
- [ ] Both selection paths request the same unified `$select`, including
      `passwordCredentials`/`keyCredentials`/`tags`.
- [ ] Unit tests cover `selection_parse`: newline vs JSON-array parsing, blank-line
      handling, and merge/dedup with inline `--object-id` values.

## Blocked by

- Issue 01 (walking skeleton)
