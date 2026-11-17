"""Lakehouse CLI.

    python -m s2_pipelines demo-load                       write the demo asset register to main
    python -m s2_pipelines branch create assets.commercial_drone preview
    python -m s2_pipelines demo-rollout --branch preview   a firmware upgrade, on the branch
    python -m s2_pipelines sync CommercialDrone --branch preview --dry-run
    python -m s2_pipelines branch promote assets.commercial_drone preview
    python -m s2_pipelines sync CommercialDrone

Inside the Compose stack:  make lakehouse ARGS="sync CommercialDrone --dry-run"
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from s2_pipelines import demo
from s2_pipelines.engine import EngineClient, EngineError
from s2_pipelines.lakehouse import MAIN, Lakehouse, LakehouseError
from s2_pipelines.settings import get_pipeline_settings
from s2_pipelines.sync import Plan, Syncer, SyncError


def _lakehouse() -> Lakehouse:
    lakehouse = Lakehouse.from_settings(get_pipeline_settings())
    lakehouse.ensure_warehouse()
    return lakehouse


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str))


def _plan_details(the_plan: Plan) -> dict[str, Any]:
    return {
        **the_plan.summary(),
        "creates": the_plan.creates,
        "updates": the_plan.updates,
        "rejected": the_plan.rejected,
        "absent": the_plan.absent,
    }


def cmd_demo_load(args: argparse.Namespace) -> None:
    snapshot = _lakehouse().write(demo.DATASET, demo.register(), properties={"s2.source": "demo-load"})
    print(f"{demo.DATASET}@main = snapshot {snapshot} ({len(demo.REGISTER)} rows)")


def cmd_demo_rollout(args: argparse.Namespace) -> None:
    rows = demo.firmware_rollout(args.version)
    snapshot = _lakehouse().write(
        demo.DATASET, rows, branch=args.branch, properties={"s2.source": "demo-rollout"}
    )
    print(f"{demo.DATASET}@{args.branch} = snapshot {snapshot} (firmware {args.version} on DRN-0003)")


def cmd_branch(args: argparse.Namespace) -> None:
    lakehouse = _lakehouse()
    if args.action == "list":
        _print(lakehouse.branches(args.dataset))
    elif args.action == "create":
        snapshot = lakehouse.create_branch(args.dataset, args.name)
        print(f"{args.dataset}@{args.name} created at snapshot {snapshot}")
    elif args.action == "promote":
        print(f"{args.dataset}@main = snapshot {lakehouse.promote(args.dataset, args.name)}")
    elif args.action == "drop":
        lakehouse.drop_branch(args.dataset, args.name)
        print(f"{args.dataset}@{args.name} dropped")


def cmd_sync(args: argparse.Namespace) -> None:
    settings = get_pipeline_settings()
    syncer = Syncer(_lakehouse(), EngineClient.from_settings(settings))
    the_plan = syncer.plan(args.object_type, branch=args.branch)
    if args.dry_run:
        _print({"dry_run": True, **_plan_details(the_plan)})
        return
    if args.branch != MAIN and not args.allow_branch:
        sys.exit(
            f"refusing to write the ontology from branch {args.branch!r}: preview it with --dry-run, "
            "promote it, then sync main (or pass --allow-branch)"
        )
    result = syncer.apply(the_plan, delete_absent=args.delete_absent, force=args.force)
    _print({**result.summary(), "rejected": result.rejected})
    if result.rejected:
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="s2-pipelines", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    load = commands.add_parser("demo-load", help="write the demo asset register to main")
    load.set_defaults(run=cmd_demo_load)

    rollout = commands.add_parser("demo-rollout", help="write a firmware upgrade to a branch")
    rollout.add_argument("--branch", required=True)
    rollout.add_argument("--version", default="4.2.0")
    rollout.set_defaults(run=cmd_demo_rollout)

    branch = commands.add_parser("branch", help="list, create, promote or drop a dataset branch")
    branch.add_argument("action", choices=["list", "create", "promote", "drop"])
    branch.add_argument("dataset")
    branch.add_argument("name", nargs="?")
    branch.set_defaults(run=cmd_branch)

    sync = commands.add_parser("sync", help="make an object type agree with its dataset")
    sync.add_argument("object_type")
    sync.add_argument("--branch", default=MAIN)
    sync.add_argument("--dry-run", action="store_true", help="show what would change; write nothing")
    sync.add_argument("--allow-branch", action="store_true", help="write the ontology from a branch")
    sync.add_argument("--delete-absent", action="store_true", help="delete objects that have no row")
    sync.add_argument("--force", action="store_true", help="with --delete-absent: lift the 20%% limit")
    sync.set_defaults(run=cmd_sync)

    args = parser.parse_args(argv)
    if args.command == "branch" and args.action != "list" and not args.name:
        parser.error(f"branch {args.action} needs a branch name")
    try:
        args.run(args)
    except (LakehouseError, SyncError, EngineError) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
