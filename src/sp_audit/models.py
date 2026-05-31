"""Typed shapes for the Audit Report envelope and its sub-objects.

These TypedDicts are the single source of truth for the JSON the tool writes.
They are network-free and exist so `ty` enforces the schema at type-check time
rather than leaving it aspirational.
"""

from __future__ import annotations

from typing import TypedDict


class ApplicationRecord(TypedDict):
    """The Application related to a Service Principal via `appId`.

    Attached to a Service Principal as a nullable object — `null` for managed
    identities, multi-tenant apps, and gallery apps that have no Application.
    """

    objectId: str | None
    appId: str | None
    displayName: str | None


class ServicePrincipalRecord(TypedDict):
    """A single audited Service Principal: identity, tags, attached Application."""

    objectId: str
    appId: str | None
    displayName: str | None
    tags: list[str]
    application: ApplicationRecord | None


class Selection(TypedDict):
    """How the audited set was chosen for this run."""

    objectIds: list[str]


class Meta(TypedDict):
    """Run-scoped metadata carried at the top of the Audit Report.

    `runErrors` holds plane-wide / precondition failures (Run Errors). It is
    empty in the walking skeleton; collection-time failures populate it later.
    """

    generatedAt: str
    tenantId: str
    selection: Selection
    toolVersion: str
    runErrors: list[str]


class AuditReport(TypedDict):
    """The object envelope a run produces. Not a bare array (see ADR-0002)."""

    meta: Meta
    servicePrincipals: list[ServicePrincipalRecord]
