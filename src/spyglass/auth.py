"""Up-front precondition gating for a run.

A single check verifies *both* that the Azure CLI has an active login
(`az account show`) and that a live Microsoft Graph token can be acquired via
`AzureCliCredential`. It harvests `tenantId` for `meta`. This is a global
precondition (ADR-0002): if it fails the run aborts with a non-zero exit
*before* any collection starts.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from azure.identity.aio import AzureCliCredential

GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class PreconditionError(Exception):
    """A global precondition failed; the run must abort before collection."""


def _az_account_tenant_id() -> str:
    """Return the tenantId from `az account show`, or raise PreconditionError."""
    if shutil.which("az") is None:
        raise PreconditionError(
            "The Azure CLI ('az') is not installed or not on PATH. "
            "Install it and run 'az login'."
        )

    try:
        result = subprocess.run(
            ["az", "account", "show"],
            capture_output=True,
            text=True,
        )
    except OSError as exc:  # pragma: no cover - defensive
        raise PreconditionError(f"Could not invoke 'az account show': {exc}") from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or "no error output"
        raise PreconditionError(
            f"Not logged in to Azure CLI. Run 'az login' first. Details: {detail}"
        )

    try:
        account = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise PreconditionError(
            f"Could not parse 'az account show' output: {exc}"
        ) from exc

    tenant_id = account.get("tenantId")
    if not tenant_id:
        raise PreconditionError("'az account show' did not report a tenantId.")
    return str(tenant_id)


async def verify_preconditions() -> str:
    """Gate the run on CLI login and a live Graph token; return the tenantId.

    Raises:
        PreconditionError: if not logged in, or a Graph token cannot be acquired.
    """
    tenant_id = _az_account_tenant_id()

    credential = AzureCliCredential()
    try:
        await credential.get_token(GRAPH_SCOPE)
    except Exception as exc:  # noqa: BLE001 - any failure here is a hard gate
        raise PreconditionError(
            f"Could not acquire a Microsoft Graph token via Azure CLI: {exc}"
        ) from exc
    finally:
        await credential.close()

    return tenant_id
