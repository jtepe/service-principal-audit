"""Entra (directory-plane) identity collector.

Resolves a Service Principal strictly by object id via
`GET /servicePrincipals/{id}`, falling back to an `appId eq` filter only on a
404, and attaches its related Application as a nullable object. The pure
mapping functions (Graph model -> record) are network-free so they can be
unit-tested without a live Graph client.
"""

from __future__ import annotations

from kiota_abstractions.api_error import APIError
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph import GraphServiceClient
from msgraph.generated.applications.applications_request_builder import (
    ApplicationsRequestBuilder,
)
from msgraph.generated.models.application import Application
from msgraph.generated.models.service_principal import ServicePrincipal
from msgraph.generated.service_principals.item.service_principal_item_request_builder import (  # noqa: E501
    ServicePrincipalItemRequestBuilder,
)
from msgraph.generated.service_principals.service_principals_request_builder import (
    ServicePrincipalsRequestBuilder,
)

from .models import ApplicationRecord, ServicePrincipalRecord

# Unified $select so SP-side fields are never path-dependent across the two
# resolution routes (by id vs appId-eq fallback).
SP_SELECT = ["id", "displayName", "appId", "tags"]
APP_SELECT = ["id", "displayName", "appId"]


def application_record_from_graph(app: Application) -> ApplicationRecord:
    """Map a Graph Application onto the nullable attached Application record."""
    return {
        "objectId": app.id,
        "appId": app.app_id,
        "displayName": app.display_name,
    }


def sp_record_from_graph(
    sp: ServicePrincipal, application: Application | None
) -> ServicePrincipalRecord:
    """Map a Graph servicePrincipal (+ optional Application) onto a record.

    Pure: no network, no clock. `objectId` must be present on a resolved SP;
    everything else degrades to `None`/empty.
    """
    if sp.id is None:
        raise ValueError("Resolved service principal has no object id")
    return {
        "objectId": sp.id,
        "appId": sp.app_id,
        "displayName": sp.display_name,
        "tags": list(sp.tags) if sp.tags else [],
        "application": (
            application_record_from_graph(application)
            if application is not None
            else None
        ),
    }


async def _resolve_service_principal(
    client: GraphServiceClient, object_id: str
) -> ServicePrincipal:
    """Resolve by object id; fall back to `appId eq '{id}'` only on a 404."""
    item_config = RequestConfiguration(
        query_parameters=ServicePrincipalItemRequestBuilder.ServicePrincipalItemRequestBuilderGetQueryParameters(
            select=SP_SELECT,
        )
    )
    try:
        sp = await client.service_principals.by_service_principal_id(object_id).get(
            request_configuration=item_config
        )
    except APIError as exc:
        if exc.response_status_code != 404:
            raise
        sp = None

    if sp is not None:
        return sp

    # 404 on the object-id route: try resolving the value as an appId.
    escaped = object_id.replace("'", "''")
    list_config = RequestConfiguration(
        query_parameters=ServicePrincipalsRequestBuilder.ServicePrincipalsRequestBuilderGetQueryParameters(
            filter=f"appId eq '{escaped}'",
            select=SP_SELECT,
        )
    )
    page = await client.service_principals.get(request_configuration=list_config)
    matches = page.value if page is not None and page.value else []
    if not matches:
        raise LookupError(
            f"No service principal found by object id or appId '{object_id}'."
        )
    return matches[0]


async def _resolve_application(
    client: GraphServiceClient, app_id: str
) -> Application | None:
    """Fetch the related Application by `appId eq`; `None` if there is none."""
    escaped = app_id.replace("'", "''")
    config = RequestConfiguration(
        query_parameters=ApplicationsRequestBuilder.ApplicationsRequestBuilderGetQueryParameters(
            filter=f"appId eq '{escaped}'",
            select=APP_SELECT,
        )
    )
    page = await client.applications.get(request_configuration=config)
    matches = page.value if page is not None and page.value else []
    return matches[0] if matches else None


async def collect_service_principal(
    client: GraphServiceClient, object_id: str
) -> ServicePrincipalRecord:
    """Collect one Service Principal's identity and attached Application."""
    sp = await _resolve_service_principal(client, object_id)

    application: Application | None = None
    if sp.app_id:
        application = await _resolve_application(client, sp.app_id)

    return sp_record_from_graph(sp, application)
