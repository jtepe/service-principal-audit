"""Tests for Graph-plane credential selection (construction only, no network)."""

from __future__ import annotations

import pytest
from azure.identity.aio import (
    AzureCliCredential,
    ClientSecretCredential,
    ManagedIdentityCredential,
)

from spyglass.auth import (
    GraphAuthConfig,
    PreconditionError,
    build_graph_credential,
    resolve_graph_auth_config,
)

_AZURE_ENV = ("AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID")


@pytest.fixture(autouse=True)
def _clear_azure_env(monkeypatch):
    # Keep the host's AZURE_* vars from leaking into resolution under test.
    for name in _AZURE_ENV:
        monkeypatch.delenv(name, raising=False)


def test_no_inputs_falls_back_to_az_login():
    credential = build_graph_credential(GraphAuthConfig())

    assert isinstance(credential, AzureCliCredential)


def test_full_service_principal_selects_client_secret():
    config = GraphAuthConfig(
        client_id="app-id", client_secret="shh", tenant_id="tenant"
    )

    credential = build_graph_credential(config)

    assert isinstance(credential, ClientSecretCredential)


def test_managed_identity_system_assigned():
    credential = build_graph_credential(GraphAuthConfig(managed_identity=True))

    assert isinstance(credential, ManagedIdentityCredential)


def test_managed_identity_user_assigned_uses_client_id():
    credential = build_graph_credential(
        GraphAuthConfig(managed_identity=True, client_id="mi-id")
    )

    assert isinstance(credential, ManagedIdentityCredential)


def test_partial_service_principal_config_is_rejected():
    # A secret without the matching client/tenant id is a misconfiguration.
    with pytest.raises(PreconditionError, match="client id, client secret, and tenant"):
        build_graph_credential(GraphAuthConfig(client_secret="shh"))


def test_managed_identity_with_secret_is_mutually_exclusive():
    with pytest.raises(PreconditionError, match="cannot be combined"):
        build_graph_credential(
            GraphAuthConfig(managed_identity=True, client_secret="shh")
        )


def test_resolve_prefers_cli_over_env(monkeypatch):
    monkeypatch.setenv("AZURE_CLIENT_ID", "env-id")
    monkeypatch.setenv("AZURE_TENANT_ID", "env-tenant")

    config = resolve_graph_auth_config(
        client_id="cli-id",
        client_secret=None,
        tenant_id=None,
        managed_identity=False,
    )

    # CLI value wins; the unset secret/tenant fall back to the environment.
    assert config.client_id == "cli-id"
    assert config.tenant_id == "env-tenant"
    assert config.client_secret is None


def test_resolve_reads_env_when_cli_absent(monkeypatch):
    monkeypatch.setenv("AZURE_CLIENT_ID", "env-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "env-secret")
    monkeypatch.setenv("AZURE_TENANT_ID", "env-tenant")

    config = resolve_graph_auth_config(
        client_id=None,
        client_secret=None,
        tenant_id=None,
        managed_identity=False,
    )

    assert isinstance(build_graph_credential(config), ClientSecretCredential)
