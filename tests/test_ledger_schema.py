import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from oqg_portfolio_sim.core.ledger import (
    bootstrap_run,
    bootstrap_schema,
    list_runs_for_as_of,
)


class LedgerSchemaTest(unittest.TestCase):
    def test_bootstrap_schema_enforces_append_only_and_reconciliation_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "ledger.sqlite"
            bootstrap_schema(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute(
                    "INSERT INTO strategies(strategy_id, name, description, owner, active, asset_class) VALUES (?, ?, ?, ?, ?, ?)",
                    ("strategy-1", "Test strategy", "desc", "club", 1, "futures"),
                )
                conn.execute(
                    "INSERT INTO runs(as_of_date, started_at, completed_at, status, notes, reconciliation_status, reconciliation_note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("2026-08-03", "2026-08-03T16:00:00Z",
                     None, "completed", "ok", "ok", None),
                )
                conn.execute(
                    "INSERT INTO fills(run_id, strategy_id, instrument_id, qty, fill_price, explicit_cost, implicit_cost, fill_confidence, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (1, "strategy-1", "VX1", 1.0, 10.0, 1.0, 0.5,
                     "normal", "test", "2026-08-03T16:00:00Z"),
                )
                conn.commit()

                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("UPDATE fills SET qty = 2 WHERE fill_id = 1")
                    conn.commit()

    def test_completed_runs_are_unique_per_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "ledger.sqlite"
            bootstrap_schema(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute(
                    "INSERT INTO runs(as_of_date, started_at, completed_at, status, notes, reconciliation_status, reconciliation_note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("2026-08-03", "2026-08-03T16:00:00Z",
                     "2026-08-03T16:05:00Z", "completed", "ok", "ok", None),
                )
                conn.commit()

                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO runs(as_of_date, started_at, completed_at, status, notes, reconciliation_status, reconciliation_note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        ("2026-08-03", "2026-08-03T16:10:00Z",
                         "2026-08-03T16:15:00Z", "completed", "retry", "ok", None),
                    )
                    conn.commit()

    def test_point_in_time_access_filters_out_future_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "ledger.sqlite"
            bootstrap_schema(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "INSERT INTO runs(as_of_date, started_at, completed_at, status, notes, reconciliation_status, reconciliation_note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("2026-08-03", "2026-08-03T16:00:00Z",
                     None, "pending", "ok", "pending", None),
                )
                conn.execute(
                    "INSERT INTO runs(as_of_date, started_at, completed_at, status, notes, reconciliation_status, reconciliation_note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("2026-08-04", "2026-08-04T16:00:00Z", None,
                     "pending", "future", "pending", None),
                )
                conn.commit()

            rows = list_runs_for_as_of(db_path, "2026-08-03")
            self.assertEqual([row[1] for row in rows], ["2026-08-03"])

    def test_bootstrap_run_inserts_initial_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "ledger.sqlite"
            run_id = bootstrap_run(db_path, as_of_date="2026-08-03")

            self.assertGreater(run_id, 0)
            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute(
                    "SELECT status, reconciliation_status FROM runs WHERE run_id = ?", (
                        run_id,)
                ).fetchone()
                self.assertEqual(row, ("pending", "pending"))


if __name__ == "__main__":
    unittest.main()
