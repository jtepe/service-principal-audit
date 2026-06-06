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
from .azure_rbac import collect_azure_rbac
from .entra import collect_by_tag, collect_service_principal
from .models import DirectoryRoleRecord, Selection, ServicePrincipalRecord
from .report import build_report
from .selection_parse import merge_object_ids, parse_ids_file
from .single_flight import SingleFlight

DEFAULT_OUTPUT = "audit-report.json"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sp-audit",
        description=(
            "Audit Entra service principals across the directory plane and "
            "write a JSON Audit Report."
        ),
    )
    parser.add_argument(
        "--object-id",
        action="append",
        dest="object_ids",
        default=[],
        metavar="ID",
        help=(
            "Object id of a service principal to audit (appId accepted as a "
            "fallback). May be repeated and combined with --ids-file."
        ),
    )
    parser.add_argument(
        "--ids-file",
        metavar="PATH",
        help=(
            "Path to a file of object ids, either a newline list or a JSON "
            "array. Merged and deduped with any --object-id values."
        ),
    )
    parser.add_argument(
        "--tag",
        metavar="TAG",
        help=(
            "Select service principals carrying this tag. Mutually exclusive "
            "with --object-id/--ids-file."
        ),
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Path for the JSON Audit Report (default: {DEFAULT_OUTPUT}).",
    )
    args = parser.parse_args(argv)

    if args.tag is not None and (args.object_ids or args.ids_file):
        parser.error("--tag is mutually exclusive with --object-id/--ids-file")
    if args.tag is None and not args.object_ids and not args.ids_file:
        parser.error("one of --object-id, --ids-file, or --tag is required")
    return args


async def _run(args: argparse.Namespace) -> int:
    output: str = args.output

    # Global precondition: fail fast with a non-zero exit before any collection.
    try:
        tenant_id = await verify_preconditions()
    except PreconditionError as exc:
        print(f"sp-audit: precondition failed: {exc}", file=sys.stderr)
        return 2

    file_ids: list[str] = []
    if args.ids_file:
        try:
            with open(args.ids_file, encoding="utf-8") as fh:
                file_ids = parse_ids_file(fh.read())
        except (OSError, ValueError) as exc:
            print(f"sp-audit: failed to read --ids-file: {exc}", file=sys.stderr)
            return 2

    selection: Selection
    records: list[ServicePrincipalRecord] = []
    run_errors: list[str] = []

    # Collection has started: from here we always complete, write JSON, exit 0.
    credential = AzureCliCredential()
    try:
        client = GraphServiceClient(credentials=credential, scopes=[GRAPH_SCOPE])
        if args.tag is not None:
            selection = {"objectIds": [], "tag": args.tag}
            try:
                records = await collect_by_tag(client, args.tag)
            except Exception as exc:  # noqa: BLE001 - degrade to Run Error, never abort
                run_errors.append(f"Failed to select by tag '{args.tag}': {exc}")
            selection["objectIds"] = [r["objectId"] for r in records]
        else:
            object_ids = merge_object_ids(args.object_ids, file_ids)
            selection = {"objectIds": object_ids}
            # One shared per-group schedule cache across the whole id selection
            # so a group reached by many SPs is fetched once for the run.
            schedule_cache: SingleFlight[str, list[DirectoryRoleRecord]] = (
                SingleFlight()
            )
            for object_id in object_ids:
                try:
                    records.append(
                        await collect_service_principal(
                            client, object_id, schedule_cache
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - degrade to a Run Error
                    run_errors.append(f"Failed to collect '{object_id}': {exc}")
    finally:
        await credential.close()

    # Azure RBAC plane: a single full management-group-scoped ARG batch. A
    # failure here is a Run Error (ADR-0002) — the Entra-plane data still writes.
    try:
        assignments_by_principal = await collect_azure_rbac(
            [r["objectId"] for r in records]
        )
    except Exception as exc:  # noqa: BLE001 - degrade to a Run Error, never abort
        run_errors.append(f"Azure RBAC query failed: {exc}")
    else:
        for record in records:
            record["azureRoleAssignments"] = assignments_by_principal.get(
                record["objectId"], []
            )

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
    exit_code = asyncio.run(_run(args))
    if argv is None:
        sys.exit(exit_code)
    return exit_code


if __name__ == "__main__":
    main()
