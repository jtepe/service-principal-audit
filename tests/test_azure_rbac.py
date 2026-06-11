"""Tests for the Azure RBAC collector's pure boundaries (no `az` subprocess)."""

from __future__ import annotations

from types import SimpleNamespace

from spyglass.azure_rbac import collect_azure_role_assignments


def test_empty_selection_makes_no_subprocess_call() -> None:
    # No object ids => nothing to query; must not shell out to `az`.
    assert collect_azure_role_assignments([]) == {}


def test_run_arg_query_follows_skip_token_across_pages(monkeypatch) -> None:
    from spyglass import azure_rbac

    pages = [
        # First page returns a continuation token...
        '{"data": [{"id": "a"}], "skip_token": "TOK"}',
        # ...second page exhausts it (no skip_token).
        '{"data": [{"id": "b"}], "skip_token": null}',
    ]
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        stdout = pages[len(calls) - 1]
        return SimpleNamespace(stdout=stdout, returncode=0)

    monkeypatch.setattr(azure_rbac.subprocess, "run", fake_run)

    rows = azure_rbac._run_arg_query("Resources")

    assert rows == [{"id": "a"}, {"id": "b"}]
    # Page one carries no token; page two passes the token from page one.
    assert "--skip-token" not in calls[0]
    assert calls[1][calls[1].index("--skip-token") + 1] == "TOK"
