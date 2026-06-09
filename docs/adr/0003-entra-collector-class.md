# Encapsulate the Entra collector state in a class; keep mapping pure-functional

## Status

accepted

## Context

The directory-plane logic in `entra.py` had grown into two kinds of code. The
pure mapping functions (`*_from_graph`, `apply_pim_membership`,
`resolve_app_role_value`, …) are network-free and carry the project's test
coverage. The network-bound collectors, however, threaded the same three
run-scoped collaborators — the Graph `client`, a `schedule_cache`, and a
`resource_cache` (all single-flight) — through nearly every signature. `client`
appeared in all fifteen async signatures; the caches were constructed in
`_collect_all` and passed down by hand (`_collect_for_service_principal(client,
sp, schedule_cache, resource_cache)` → `collect_directory_roles(..., schedule_cache)`
→ `_resolve_resource(client, resource_cache, ...)`). The run-scoped lifetime of
those caches lived only as a convention in a docstring.

## Decision

Encapsulate the network-bound collectors as methods on an `EntraCollector` that
owns the `client`, the two caches, the group-name cache, and the concurrency
`Semaphore` as instance state. The CLI constructs one collector per run and
calls `collect_by_object_ids` / `collect_by_tag` on it. The per-call signatures
now carry only genuinely per-call arguments (`object_id`, `sp`, `memberships`),
and the caches' run-scoped lifetime is made explicit as the instance lifetime:
one `EntraCollector` is one selection/run.

The pure mapping functions stay module-level free functions — they hold no
state, are unit-tested directly, and making them methods would only hide that
they need nothing from `self`. The stateless paging primitives (`_page_all`,
`_page_groups`, `_page_pim_schedules`) likewise stay free; they operate on a
passed builder. The caches accept constructor injection (defaulting to fresh
instances) so tests can pre-seed or inspect them without reintroducing per-call
threading.

The Azure RBAC collector (`azure_rbac.py`) is deliberately left functional: it
threads only `object_ids` and query strings, has no collaborator bundle and no
shared state, and its row-level logic already lives in the pure `arg_transform`
module. A class there would be ceremony with no payoff.

### Concurrency

asyncio is cooperatively scheduled on a single thread, so the shared instance
state (caches, semaphore) is as safe as the previous function-local caches —
`SingleFlight` already provides the stampede control, and no new locking is
needed. The semaphore moves from a `_collect_all` local onto the instance,
bounding fan-out exactly as before. The one rule the design depends on, now
documented on the class: an instance must not be reused across logically
separate runs, or its caches would serve stale data and grow unbounded.
