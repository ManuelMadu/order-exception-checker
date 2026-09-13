#!/usr/bin/env python3
"""Command line entry point for the Order Exception Checker.

    python main.py                       # run against the bundled sample data
    python main.py --now "2026-03-20 09:00"
    python main.py --data-dir data --output output/exception_report.csv
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src import checker, config
from src.loader import DataValidationError

ROOT = Path(__file__).resolve().parent
PREVIEW_ROWS = 8


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconcile marketplace, warehouse and carrier data.")
    parser.add_argument("--data-dir", default=str(ROOT / "data"),
                        help="Directory holding the three source CSV files.")
    parser.add_argument("--output", default=str(ROOT / config.REPORT_FILE),
                        help="Path of the exception report to write.")
    parser.add_argument("--now", default=None,
                        help="Reference 'current' time (default: the fixed demo time in src/config.py).")
    parser.add_argument("--quiet", action="store_true", help="Suppress the report preview.")
    return parser.parse_args(argv)


def resolve_now(raw: str | None) -> datetime:
    if raw is None:
        return config.REFERENCE_NOW
    if raw.lower() == "system":
        # Naive UTC, to match the timestamps in the source CSVs.
        return datetime.now(timezone.utc).replace(tzinfo=None)
    parsed = pd.to_datetime(raw, errors="coerce")
    if pd.isna(parsed):
        raise SystemExit(f"Could not parse --now value: {raw!r}")
    # An offset like "+01:00" parses fine but cannot be compared with the naive
    # timestamps in the CSVs, so convert it to UTC and drop the tzinfo.
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert("UTC").tz_localize(None)
    return parsed.to_pydatetime()


def print_summary(result: checker.CheckResult, report_path: Path, quiet: bool) -> None:
    print()
    print(f"Reference time: {result.reference_now:%Y-%m-%d %H:%M} (UTC)")
    print(f"{result.orders_analysed} orders analysed")
    print(f"{result.exception_count} exceptions detected")
    print()

    for category, count in result.counts_by_category().items():
        print(f"  {category + ':':<20} {count}")

    print()
    print("  By severity")
    for severity, count in result.counts_by_severity().items():
        if count:
            print(f"    {severity + ':':<12} {count}")

    print()
    print("  By escalation owner")
    for owner, count in sorted(result.counts_by_owner().items(), key=lambda item: -item[1]):
        print(f"    {owner + ':':<22} {count}")

    if not quiet and not result.report.empty:
        preview = result.report.head(PREVIEW_ROWS)[
            ["order_id", "marketplace", "exception_type", "severity", "escalation_owner", "age_hours"]
        ]
        print()
        print(f"Top {len(preview)} exceptions by severity:")
        print(preview.to_string(index=False))

    print()
    print(f"Report written to {report_path}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    data_dir = Path(args.data_dir)

    try:
        result = checker.run_checks(
            marketplace_path=data_dir / "marketplace_orders.csv",
            warehouse_path=data_dir / "warehouse_status.csv",
            tracking_path=data_dir / "carrier_tracking.csv",
            now=resolve_now(args.now),
        )
    except DataValidationError as error:
        print(f"Data error: {error}", file=sys.stderr)
        return 1

    report_path = checker.write_report(result.report, args.output)
    print_summary(result, report_path, args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
