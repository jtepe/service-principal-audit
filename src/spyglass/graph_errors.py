"""Human-readable rendering for Microsoft Graph SDK errors.

The Graph SDK raises `kiota_abstractions.api_error.APIError` (in practice the
`ODataError` subclass), whose `str()` is a multi-line dump — and whose useful
detail (the real `errorCode` and message) is buried in a JSON blob inside
`error.message`. Recorded verbatim into an SP Gap, that produces unreadable
`errors[]` entries. `describe_graph_error` flattens it to a single, actionable
line such as:

    HTTP 403: PermissionScopeNotGranted: Authorization failed due to missing
    permission scope RoleManagement.Read.Directory,...

It is best-effort and total: any exception that does not match the expected
shape falls back to a stripped `str(exc)`.
"""

from __future__ import annotations

import json


def _parse_inner_message(message: str) -> tuple[str | None, str | None]:
    """Pull `(errorCode, message)` out of Graph's nested-JSON `error.message`.

    Graph frequently sets the top-level `code` to a generic `UnknownError` and
    stashes the real error in `error.message` as a JSON string. Returns
    `(None, None)` when the message is not such a JSON object.
    """
    try:
        data = json.loads(message)
    except ValueError, TypeError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    code = data.get("errorCode")
    inner = data.get("message")
    return (
        code if isinstance(code, str) else None,
        inner if isinstance(inner, str) else None,
    )


def describe_graph_error(exc: Exception) -> str:
    """Render a Graph SDK error as one concise, actionable line.

    Surfaces the HTTP status, the most specific error code available (preferring
    the nested `errorCode` over a generic top-level `UnknownError`), and the
    human-readable message. Falls back to a stripped `str(exc)` for anything that
    is not a recognizable Graph error.
    """
    status = getattr(exc, "response_status_code", None)
    main = getattr(exc, "error", None)

    code: str | None = getattr(main, "code", None) if main is not None else None
    detail: str | None = None
    raw_message = getattr(main, "message", None) if main is not None else None
    if isinstance(raw_message, str) and raw_message:
        inner_code, inner_message = _parse_inner_message(raw_message)
        code = inner_code or code
        detail = inner_message or raw_message

    parts: list[str] = []
    if status is not None:
        parts.append(f"HTTP {status}")
    # A generic UnknownError adds no signal once the nested code is unwrapped.
    if code and code != "UnknownError":
        parts.append(code)
    if detail:
        parts.append(detail)

    if parts:
        return ": ".join(parts)
    return str(exc).strip()
