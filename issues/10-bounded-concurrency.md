---
labels: [Implemented]
---

# Bounded concurrency across Service Principals

## What to build

Process Service Principals concurrently under a bound so a large fleet (~500 SPs)
completes without triggering tenant throttling. Bound **across SPs** with a single
`asyncio.Semaphore`, exposed as `--concurrency N` with a conservative default of 5.
Within one SP, its independent section calls run via `asyncio.gather` (naturally
limited by the SP-level bound). Delegate backoff to the msgraph-sdk Retry-After/429
handler rather than hand-rolling retry.

This slice validates that the shared single-flight caches behave correctly under
real concurrency (no stampede on the common Microsoft Graph resource SP or on
heavily-shared groups).

## Acceptance criteria

- [x] `--concurrency N` bounds the number of SPs processed at once; default is 5.
- [x] Independent per-SP section calls run concurrently within an SP.
- [x] Under concurrency, a shared lookup (e.g. the Microsoft Graph resource SP, a
      widely-shared group's role schedules) is still fetched once, not once per SP.
- [x] Backoff on 429/503 is handled via the SDK's Retry-After handler.
- [x] A run over many SPs completes and writes a complete report.

## Blocked by

- Issue 04 (group memberships + single-flight)
