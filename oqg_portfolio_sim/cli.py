from __future__ import annotations

import argparse
from pathlib import Path

from oqg_portfolio_sim.core import bootstrap_run


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize the Phase 1 simulator ledger")
    parser.add_argument("--db", default="ledger.sqlite",
                        help="Path to the SQLite ledger database")
    parser.add_argument("--as-of", default="2026-08-03",
                        help="As-of date for the initial run")
    parser.add_argument("--strategy-id", default="placeholder",
                        help="Strategy identifier to seed")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser()
    run_id = bootstrap_run(db_path, as_of_date=args.as_of,
                           strategy_id=args.strategy_id)
    print(f"Created ledger at {db_path} with run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
