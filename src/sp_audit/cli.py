"""`sp-audit` command-line entry point.

Wires the modules into one async vertical path: gate on preconditions (fail
fast, non-zero), then resolve the selected Service Principal, build the Audit
Report envelope, write JSON, and exit 0 (ADR-0002 two-tier failure model).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime

from azure.identity.aio import AzureCliCredential
from msgraph import GraphServiceClient

from .auth import GRAPH_SCOPE, PreconditionError, verify_preconditions
from .entra import collect_service_principal
from .models import Selection, ServicePrincipalRecord
from .report import build_report

DEFAULT_OUTPUT = "audit-report.json"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sp-audit",
        description=(
            "Audit an Entra service principal across the directory plane and "
            "write a JSON Audit Report."
        ),
    )
    parser.add_argument(
        "--object-id",
        required=True,
        metavar="ID",
        help=(
            "Object id of the service principal to audit "
            "(appId accepted as a fallback)."
        ),
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Path for the JSON Audit Report (default: {DEFAULT_OUTPUT}).",
    )
    return parser.parse_args(argv)


async def _run(object_id: str, output: str) -> int:
    # Global precondition: fail fast with a non-zero exit before any collection.
    try:
        tenant_id = await verify_preconditions()
    except PreconditionError as exc:
        print(f"sp-audit: precondition failed: {exc}", file=sys.stderr)
        return 2

    selection: Selection = {"objectIds": [object_id]}
    records: list[ServicePrincipalRecord] = []
    run_errors: list[str] = []

    # Collection has started: from here we always complete, write JSON, exit 0.
    credential = AzureCliCredential()
    try:
        client = GraphServiceClient(credentials=credential, scopes=[GRAPH_SCOPE])
        try:
            records.append(await collect_service_principal(client, object_id))
        except Exception as exc:  # noqa: BLE001 - degrade to a Run Error, never abort
            run_errors.append(f"Failed to collect '{object_id}': {exc}")
    finally:
        await credential.close()

    report = build_report(
        records,
        tenant_id=tenant_id,
        selection=selection,
        generated_at=datetime.now(UTC).isoformat(),
        run_errors=run_errors,
    )

    with open(output, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")

    print(
        f"sp-audit: wrote {len(report['servicePrincipals'])} service principal(s) "
        f"to {output}",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    exit_code = asyncio.run(_run(args.object_id, args.output))
    if argv is None:
        sys.exit(exit_code)
    return exit_code


if __name__ == "__main__":
    main()
