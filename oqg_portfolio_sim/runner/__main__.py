"""CLI entrypoint for the daily runner: `python -m oqg_portfolio_sim.runner`.

Called once per weekday, after US market close, by the GitHub Actions
workflow. Exits non-zero on any failure so the workflow (and whoever
watches it) gets notified -- see PROJECT_SPEC.md's runner failure mode.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .daily_cycle import RunnerError, run_daily_cycle


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one daily simulation cycle")
    parser.add_argument("--db", default="ledger.sqlite",
                         help="Path to the SQLite ledger database")
    parser.add_argument(
        "--as-of", default=None,
        help="Process this date instead of today (YYYY-MM-DD). For manual "
             "backfill/testing only -- the normal cron invocation omits this.",
    )
    args = parser.parse_args()

    db_path = Path(args.db).expanduser()
    today = None
    if args.as_of:
        from datetime import date
        today = date.fromisoformat(args.as_of)

    try:
        outcome = run_daily_cycle(db_path, today=today)
    except RunnerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if outcome.skipped:
        print(f"Skipped: {outcome.reason}")
        return 0

    print(
        f"Completed run_id={outcome.run_id} "
        f"signal_date={outcome.signal_date} fill_date={outcome.fill_date}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
