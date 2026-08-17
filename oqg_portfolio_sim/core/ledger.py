from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS strategies (
    strategy_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    owner TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    asset_class TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of_date TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    notes TEXT,
    reconciliation_status TEXT NOT NULL DEFAULT 'pending',
    reconciliation_note TEXT
);

CREATE TABLE IF NOT EXISTS fills (
    fill_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    strategy_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    qty REAL NOT NULL,
    fill_price REAL NOT NULL,
    explicit_cost REAL NOT NULL DEFAULT 0.0,
    implicit_cost REAL NOT NULL DEFAULT 0.0,
    fill_confidence TEXT NOT NULL DEFAULT 'normal',
    reason TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id),
    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id)
);

CREATE TABLE IF NOT EXISTS positions (
    run_id INTEGER NOT NULL,
    strategy_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    qty REAL NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, strategy_id, instrument_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id),
    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id)
);

CREATE TABLE IF NOT EXISTS equity (
    run_id INTEGER NOT NULL,
    strategy_id TEXT NOT NULL,
    gross_pnl REAL NOT NULL DEFAULT 0.0,
    net_pnl REAL NOT NULL DEFAULT 0.0,
    cum_net_pnl REAL NOT NULL DEFAULT 0.0,
    capital_base REAL NOT NULL DEFAULT 0.0,
    margin_used REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, strategy_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id),
    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id)
);

CREATE INDEX IF NOT EXISTS idx_fills_run_id ON fills(run_id);
CREATE INDEX IF NOT EXISTS idx_positions_run_id ON positions(run_id);
CREATE INDEX IF NOT EXISTS idx_equity_run_id ON equity(run_id);
CREATE INDEX IF NOT EXISTS idx_strategies_active ON strategies(active);
CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_one_completed ON runs(as_of_date) WHERE status = 'completed';

CREATE TRIGGER IF NOT EXISTS prevent_fill_update
BEFORE UPDATE ON fills
BEGIN
    SELECT RAISE(ABORT, 'fills are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_fill_delete
BEFORE DELETE ON fills
BEGIN
    SELECT RAISE(ABORT, 'fills are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_position_update
BEFORE UPDATE ON positions
BEGIN
    SELECT RAISE(ABORT, 'positions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_position_delete
BEFORE DELETE ON positions
BEGIN
    SELECT RAISE(ABORT, 'positions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_equity_update
BEFORE UPDATE ON equity
BEGIN
    SELECT RAISE(ABORT, 'equity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_equity_delete
BEFORE DELETE ON equity
BEGIN
    SELECT RAISE(ABORT, 'equity is immutable');
END;
"""


def bootstrap_schema(db_path: str | Path) -> Path:
    """Create the append-only SQLite schema if it does not already exist.

    The immutable audit trail lives in fills, positions, and equity. Runs are a mutable state
    machine whose status and reconciliation flags may change as failures are retried or resolved.
    """

    target_path = Path(db_path).expanduser()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with closing(sqlite3.connect(target_path)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(SCHEMA_SQL)
            conn.commit()
        return target_path
    except sqlite3.OperationalError:
        raise


def bootstrap_run(db_path: str | Path, *, as_of_date: str, strategy_id: str = "placeholder") -> int:
    """Create the database and initialize one pending run row."""

    target_path = bootstrap_schema(db_path)
    with closing(sqlite3.connect(target_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT OR IGNORE INTO strategies(strategy_id, name, description, owner, active, asset_class) VALUES (?, ?, ?, ?, ?, ?)",
            (
                strategy_id,
                "Placeholder strategy",
                "Initial placeholder strategy entry for Phase 1",
                "club",
                1,
                "futures",
            ),
        )
        cursor = conn.execute(
            """
            INSERT INTO runs(as_of_date, started_at, status, notes, reconciliation_status, reconciliation_note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                as_of_date,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "pending",
                "Bootstrap run created by Phase 1 scaffold",
                "pending",
                "Pending initial reconciliation",
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def register_strategy(
    db_path: str | Path,
    *,
    strategy_id: str,
    name: str,
    description: str = "",
    owner: str = "",
    active: bool = True,
    asset_class: str,
) -> None:
    """Registers a strategy if it isn't already known. A no-op otherwise."""

    target_path = bootstrap_schema(db_path)
    with closing(sqlite3.connect(target_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT OR IGNORE INTO strategies(strategy_id, name, description, owner, active, asset_class) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (strategy_id, name, description, owner, int(active), asset_class),
        )
        conn.commit()


def start_run(db_path: str | Path, *, as_of_date: str, notes: str = "") -> int:
    """Creates a new pending run row, covering all strategies processed that day."""

    target_path = bootstrap_schema(db_path)
    with closing(sqlite3.connect(target_path)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO runs(as_of_date, started_at, status, notes, reconciliation_status, reconciliation_note)
            VALUES (?, ?, 'pending', ?, 'pending', NULL)
            """,
            (as_of_date, datetime.now(
                timezone.utc).isoformat(timespec="seconds"), notes),
        )
        conn.commit()
        return int(cursor.lastrowid)


def finish_run(
    db_path: str | Path,
    run_id: int,
    *,
    status: str,
    reconciliation_status: str,
    notes: str | None = None,
    reconciliation_note: str | None = None,
) -> None:
    """Updates a run's status/reconciliation outcome. `runs` is the one mutable
    state-machine table; fills/positions/equity remain append-only."""

    target_path = Path(db_path).expanduser()
    completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(sqlite3.connect(target_path)) as conn:
        conn.execute(
            """
            UPDATE runs
            SET status = ?, reconciliation_status = ?, completed_at = ?,
                notes = COALESCE(?, notes),
                reconciliation_note = COALESCE(?, reconciliation_note)
            WHERE run_id = ?
            """,
            (status, reconciliation_status, completed_at,
             notes, reconciliation_note, run_id),
        )
        conn.commit()


def insert_fills(db_path: str | Path, run_id: int, strategy_id: str, fills) -> None:
    """Appends fill rows. `fills` are objects with instrument_id, qty, fill_price,
    explicit_cost, implicit_cost, fill_confidence, reason attributes (see
    engine.fills.Fill)."""

    if not fills:
        return

    target_path = Path(db_path).expanduser()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(sqlite3.connect(target_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executemany(
            """
            INSERT INTO fills(run_id, strategy_id, instrument_id, qty, fill_price,
                               explicit_cost, implicit_cost, fill_confidence, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id, strategy_id, fill.instrument_id, fill.qty, fill.fill_price,
                    fill.explicit_cost, fill.implicit_cost, fill.fill_confidence, fill.reason, now,
                )
                for fill in fills
            ],
        )
        conn.commit()


def insert_positions(
    db_path: str | Path,
    run_id: int,
    strategy_id: str,
    positions: dict[str, float],
    *,
    tolerance: float = 1e-9,
) -> None:
    """Records the end-of-day position snapshot. Only nonzero positions get a
    row -- flat is represented by absence, not a zero row -- so callers must
    pass the FULL current book (not just this run's diff), or a flat
    instrument carried from a prior run will silently disappear from the
    snapshot instead of correctly showing as still-flat.
    """

    nonzero = {instrument_id: qty for instrument_id,
               qty in positions.items() if abs(qty) > tolerance}
    if not nonzero:
        return

    target_path = Path(db_path).expanduser()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(sqlite3.connect(target_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executemany(
            "INSERT INTO positions(run_id, strategy_id, instrument_id, qty, created_at) VALUES (?, ?, ?, ?, ?)",
            [(run_id, strategy_id, instrument_id, qty, now)
             for instrument_id, qty in nonzero.items()],
        )
        conn.commit()


def insert_equity(
    db_path: str | Path,
    run_id: int,
    strategy_id: str,
    *,
    gross_pnl: float,
    net_pnl: float,
    cum_net_pnl: float,
    capital_base: float,
    margin_used: float,
) -> None:
    target_path = Path(db_path).expanduser()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(sqlite3.connect(target_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO equity(run_id, strategy_id, gross_pnl, net_pnl, cum_net_pnl,
                                capital_base, margin_used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, strategy_id, gross_pnl, net_pnl,
             cum_net_pnl, capital_base, margin_used, now),
        )
        conn.commit()


def get_latest_completed_run_id(db_path: str | Path) -> int | None:
    target_path = Path(db_path).expanduser()
    with closing(sqlite3.connect(target_path)) as conn:
        row = conn.execute(
            "SELECT MAX(run_id) FROM runs WHERE status = 'completed'").fetchone()
    return row[0] if row else None


def get_positions_for_run(db_path: str | Path, run_id: int, strategy_id: str) -> dict[str, float]:
    target_path = Path(db_path).expanduser()
    with closing(sqlite3.connect(target_path)) as conn:
        rows = conn.execute(
            "SELECT instrument_id, qty FROM positions WHERE run_id = ? AND strategy_id = ?",
            (run_id, strategy_id),
        ).fetchall()
    return {instrument_id: qty for instrument_id, qty in rows}


def get_current_positions(db_path: str | Path, strategy_id: str) -> dict[str, float]:
    """Current holdings for a strategy: the snapshot from the most recently
    completed run. {} if there is no completed run yet, OR if the strategy
    was fully flat as of that run -- deliberately does NOT fall back to an
    earlier run's positions, since that would resurrect stale holdings for
    a strategy that has since gone flat.
    """

    latest_run_id = get_latest_completed_run_id(db_path)
    if latest_run_id is None:
        return {}
    return get_positions_for_run(db_path, latest_run_id, strategy_id)


def get_cumulative_net_pnl(db_path: str | Path, strategy_id: str) -> float:
    """Most recent cum_net_pnl recorded for a strategy, or 0.0 if none yet."""

    target_path = Path(db_path).expanduser()
    with closing(sqlite3.connect(target_path)) as conn:
        row = conn.execute(
            """
            SELECT cum_net_pnl FROM equity
            WHERE strategy_id = ?
            ORDER BY run_id DESC LIMIT 1
            """,
            (strategy_id,),
        ).fetchone()
    return float(row[0]) if row else 0.0


def get_all_fills(db_path: str | Path, strategy_id: str) -> list[tuple[str, float]]:
    """Every fill ever recorded for a strategy, as (instrument_id, qty) pairs --
    the raw input to reconciliation's recompute_positions_from_fills."""

    target_path = Path(db_path).expanduser()
    with closing(sqlite3.connect(target_path)) as conn:
        rows = conn.execute(
            "SELECT instrument_id, qty FROM fills WHERE strategy_id = ? ORDER BY fill_id",
            (strategy_id,),
        ).fetchall()
    return [(instrument_id, qty) for instrument_id, qty in rows]


def list_strategies(db_path: str | Path) -> list[dict]:
    """All registered strategies (active or not)."""

    target_path = Path(db_path).expanduser()
    with closing(sqlite3.connect(target_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT strategy_id, name, description, owner, active, asset_class "
            "FROM strategies ORDER BY strategy_id"
        ).fetchall()
    return [dict(row) for row in rows]


def get_equity_curve(db_path: str | Path, strategy_id: str) -> list[dict]:
    """Daily equity marks for a strategy, oldest first.

    Only pulls from runs with status='completed' AND reconciliation_status='ok'
    -- per PROJECT_SPEC.md, "never publish a run that failed reconciliation."
    """

    target_path = Path(db_path).expanduser()
    with closing(sqlite3.connect(target_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT runs.as_of_date AS date, equity.gross_pnl, equity.net_pnl,
                   equity.cum_net_pnl, equity.capital_base, equity.margin_used
            FROM equity
            JOIN runs ON runs.run_id = equity.run_id
            WHERE equity.strategy_id = ?
              AND runs.status = 'completed'
              AND runs.reconciliation_status = 'ok'
            ORDER BY runs.as_of_date
            """,
            (strategy_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_trade_log(db_path: str | Path, strategy_id: str) -> list[dict]:
    """Every fill for a strategy, oldest first, from reconciled/completed runs only."""

    target_path = Path(db_path).expanduser()
    with closing(sqlite3.connect(target_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT runs.as_of_date AS date, fills.instrument_id, fills.qty,
                   fills.fill_price, fills.explicit_cost, fills.implicit_cost,
                   fills.fill_confidence, fills.reason
            FROM fills
            JOIN runs ON runs.run_id = fills.run_id
            WHERE fills.strategy_id = ?
              AND runs.status = 'completed'
              AND runs.reconciliation_status = 'ok'
            ORDER BY runs.as_of_date, fills.fill_id
            """,
            (strategy_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_completed_run_dates(db_path: str | Path) -> list[str]:
    """as_of_date for every completed, reconciled run, oldest first."""

    target_path = Path(db_path).expanduser()
    with closing(sqlite3.connect(target_path)) as conn:
        rows = conn.execute(
            "SELECT as_of_date FROM runs WHERE status = 'completed' "
            "AND reconciliation_status = 'ok' ORDER BY as_of_date"
        ).fetchall()
    return [row[0] for row in rows]


def list_all_runs(db_path: str | Path) -> list[dict]:
    """Every run ever recorded (including errors), oldest first -- the raw
    material for rendering gaps/failures, not just successes."""

    target_path = Path(db_path).expanduser()
    with closing(sqlite3.connect(target_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT run_id, as_of_date, status, reconciliation_status, notes "
            "FROM runs ORDER BY as_of_date"
        ).fetchall()
    return [dict(row) for row in rows]


def is_date_completed(db_path: str | Path, as_of_date: str) -> bool:
    """True if `as_of_date` already has a completed run.

    The `idx_runs_one_completed` unique index guarantees there's at most
    one, so this is the idempotency check the runner uses to decide
    whether a date needs (re)processing: an 'error' run for the same date
    does NOT count as completed, so a fixed-and-retried date can still
    succeed without being blocked by its own earlier failure.
    """

    target_path = Path(db_path).expanduser()
    with closing(sqlite3.connect(target_path)) as conn:
        row = conn.execute(
            "SELECT 1 FROM runs WHERE as_of_date = ? AND status = 'completed' LIMIT 1",
            (as_of_date,),
        ).fetchone()
    return row is not None


def list_runs_for_as_of(db_path: str | Path, as_of: str) -> list[tuple[int, str, str]]:
    """Return runs that are point-in-time visible as of the supplied date."""

    target_path = Path(db_path).expanduser()
    with closing(sqlite3.connect(target_path)) as conn:
        rows = conn.execute(
            "SELECT run_id, as_of_date, status FROM runs WHERE as_of_date <= ? ORDER BY as_of_date, run_id",
            (as_of,),
        ).fetchall()
    return rows
