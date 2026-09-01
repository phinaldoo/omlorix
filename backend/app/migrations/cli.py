from __future__ import annotations

import argparse
import json
import logging


logger = logging.getLogger(__name__)


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        from app.migrations.runner import run_all_migrations

        executed = run_all_migrations(use_lock=not args.no_lock)
        print(json.dumps({"executed": bool(executed)}))
        return 0
    except Exception as exc:
        logger.error("Database migration failed: %s", exc)
        return 1


def _cmd_repair_version_state(args: argparse.Namespace) -> int:
    try:
        from app.migrations.runner import repair_version_state

        stamped = repair_version_state(config_file=args.config, use_lock=not args.no_lock)
        print(json.dumps({"stamped": bool(stamped)}))
        return 0
    except Exception as exc:
        logger.error("Alembic version state repair failed: %s", exc)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Omlorix database migration CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="Run app + audit schema Alembic migrations")
    run_cmd.add_argument("--no-lock", action="store_true", help="Disable DB advisory migration lock")
    run_cmd.set_defaults(func=_cmd_run)

    repair_cmd = sub.add_parser(
        "repair-version-state",
        help="Validate schema metadata and explicitly stamp Alembic heads when version rows are stale",
    )
    repair_cmd.add_argument(
        "--config",
        choices=("alembic_main.ini", "alembic_audit.ini"),
        help="Repair only one Alembic config instead of both",
    )
    repair_cmd.add_argument("--no-lock", action="store_true", help="Disable DB advisory migration lock")
    repair_cmd.set_defaults(func=_cmd_repair_version_state)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
