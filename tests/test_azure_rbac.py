"""Tests for the Azure RBAC collector's pure boundaries (no `az` subprocess)."""

from __future__ import annotations

from sp_audit.azure_rbac import collect_azure_role_assignments


def test_empty_selection_makes_no_subprocess_call() -> None:
    # No object ids => nothing to query; must not shell out to `az`.
    assert collect_azure_role_assignments([]) == {}
