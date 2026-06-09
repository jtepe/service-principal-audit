"""Tests for the Graph SDK error renderer (`describe_graph_error`)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from spyglass.graph_errors import describe_graph_error


class _FakeODataError(Exception):
    """A minimal stand-in for ODataError: top-level status + nested MainError."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__("graph error")
        self.response_status_code = status
        self.error = SimpleNamespace(code=code, message=message)


def _odata_error(status: int, code: str, message: str) -> _FakeODataError:
    return _FakeODataError(status, code, message)


def test_permission_scope_not_granted_renders_one_actionable_line() -> None:
    inner = json.dumps(
        {
            "errorCode": "PermissionScopeNotGranted",
            "message": (
                "Authorization failed due to missing permission scope "
                "RoleManagement.Read.Directory,RoleManagement.Read.All."
            ),
            "instanceAnnotations": [],
        }
    )
    exc = _odata_error(403, "UnknownError", inner)

    described = describe_graph_error(exc)

    assert described == (
        "HTTP 403: PermissionScopeNotGranted: Authorization failed due to "
        "missing permission scope RoleManagement.Read.Directory,"
        "RoleManagement.Read.All."
    )
    # The generic top-level code and the multi-line dump are both gone.
    assert "UnknownError" not in described
    assert "\n" not in described


def test_plain_message_without_nested_json_is_used_verbatim() -> None:
    exc = _odata_error(404, "Request_ResourceNotFound", "Resource does not exist.")

    assert describe_graph_error(exc) == (
        "HTTP 404: Request_ResourceNotFound: Resource does not exist."
    )


def test_non_graph_exception_falls_back_to_str() -> None:
    assert describe_graph_error(ValueError("  boom  ")) == "boom"
