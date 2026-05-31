# Use msgraph-sdk (async) for Entra, keep the ARG collector sync-wrapped

## Status

accepted

## Context

The tool reports two planes per Service Principal: the directory plane (Entra,
via Microsoft Graph) and the Azure RBAC plane (ARM, via Azure Resource Graph).

The original RBAC tool was deliberately zero-dependency, shelling out to `az`
(`az rest`, `az graph query`) for a single Kusto query. The Entra surface is far
larger — roughly eight Graph collections (servicePrincipals, applications,
group memberships, two directory-role schedule collections, two PIM-for-Groups
schedule collections, appRoleAssignments, oauth2PermissionGrants), each with its
own pagination, `$filter`/`$expand`/`$select` semantics, and throttling. The
workload fans out heavily (SPs × groups × four schedule calls).

## Decision

Adopt `azure-identity` (`AzureCliCredential`) and `msgraph-sdk` for the Entra
plane, making the program async end-to-end (`asyncio.run(main())`) so we get the
SDK's transparent paging (`PageIterator`) and built-in Retry-After/429 handling
instead of hand-rolling them against raw `az rest`.

The existing Azure Resource Graph collector keeps its working synchronous
`az graph query` subprocess logic and is wrapped in `asyncio.to_thread`. ARG is
a single bounded query with no comparably clean SDK path, so rewriting it buys
nothing.

This intentionally abandons the project's former zero-dependency stance. That
purity paid off for one Kusto query; it stops paying at this surface area.
