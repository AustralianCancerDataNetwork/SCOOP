"""Command-line entry point for SCOOP export utilities."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import sqlalchemy as sa

from .export.synthetic_cohort import SyntheticCohortOptions, export_synthetic_cohort


class _ProgressBar:
    def __init__(self) -> None:
        self.last_percent = -1
        self.started = False

    def update(self, completed: int, total: int) -> None:
        percent = int(completed * 100 / total)
        if percent == self.last_percent:
            return
        self.last_percent = percent
        filled = percent * 30 // 100
        bar = "=" * filled + " " * (30 - filled)
        sys.stderr.write(f"\r[{bar}] {percent:3d}% ({completed:,}/{total:,})")
        sys.stderr.flush()
        self.started = True
        if completed == total:
            self.finish()

    def finish(self) -> None:
        if self.started:
            sys.stderr.write("\n")
            sys.stderr.flush()
            self.started = False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onco-scoop")
    commands = parser.add_subparsers(dest="command", required=True)
    cohort = commands.add_parser(
        "export-synthetic-cohort",
        help="sample a routed profile and write a self-contained SQLite cohort",
    )
    cohort.add_argument(
        "--profile", default="nsclc", help="routed profile id (default: nsclc)"
    )
    cohort.add_argument(
        "--count", type=int, default=10_000, help="number of patients to sample"
    )
    cohort.add_argument(
        "--seed", type=int, default=0, help="first deterministic patient seed"
    )
    cohort.add_argument(
        "--max-cycles", type=int, default=35, help="maximum repeated treatment cycles"
    )
    cohort.add_argument(
        "--batch-size", type=int, default=200, help="patients per SQLite write batch"
    )
    cohort.add_argument(
        "--output", type=Path, required=True, help="new SQLite file to create"
    )
    cohort.add_argument(
        "--hemonc-snapshot",
        type=Path,
        default=os.environ.get("HEMONC_SNAPSHOT"),
        help="HemOnc SQLite snapshot to copy into the export (or HEMONC_SNAPSHOT)",
    )
    cohort.add_argument(
        "--database-url",
        default=os.environ.get("SCOOP_DATABASE_URL"),
        help="OMOP/HemOnc source database URL (or SCOOP_DATABASE_URL)",
    )
    cohort.add_argument(
        "--connect-options",
        default=os.environ.get(
            "SCOOP_DATABASE_CONNECT_OPTIONS", "-csearch_path=public,omop"
        ),
        help="SQLAlchemy driver connect options (default: PostgreSQL public,omop search path)",
    )
    cohort.add_argument(
        "--quiet",
        action="store_true",
        help="show only an in-place progress bar",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("set SCOOP_DATABASE_URL or pass --database-url")
    if args.hemonc_snapshot is None:
        parser.error("set HEMONC_SNAPSHOT or pass --hemonc-snapshot")
    progress = _ProgressBar() if args.quiet else None
    if args.quiet:
        logging.getLogger("scoop.schedule.bridge").setLevel(logging.WARNING)
    try:
        result = export_synthetic_cohort(
            SyntheticCohortOptions(
                database_url=args.database_url,
                hemonc_snapshot=args.hemonc_snapshot,
                output=args.output,
                profile_id=args.profile,
                count=args.count,
                seed=args.seed,
                max_cycles=args.max_cycles,
                batch_size=args.batch_size,
                connect_options=args.connect_options,
                progress_callback=progress.update if progress is not None else None,
            )
        )
    except (
        ImportError,
        OSError,
        RuntimeError,
        ValueError,
        LookupError,
        sa.exc.SQLAlchemyError,
    ) as exc:
        if progress is not None:
            progress.finish()
        print(f"onco-scoop: {exc}", file=sys.stderr)
        return 1
    if progress is not None:
        progress.finish()
    else:
        print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
