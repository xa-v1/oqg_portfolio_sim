# oqg_portfolio_sim

Phase 1 scaffold for the quant club forward-simulation project.

## Quick start

1. Activate the virtual environment:

   ```bash
   source .venv/bin/activate
   ```

2. Create a ledger database:

   ```bash
   python -m oqg_portfolio_sim.cli --db ledger.sqlite --as-of 2026-08-03
   ```

3. Inspect the SQLite database with any SQLite client, for example:

   ```bash
   sqlite3 ledger.sqlite "SELECT run_id, as_of_date, status, reconciliation_status FROM runs;"
   ```

## What is included

- Instrument-spec config via JSON in [oqg_portfolio_sim/config/instruments.json](oqg_portfolio_sim/config/instruments.json)
- Append-only SQLite ledger schema in [oqg_portfolio_sim/core/ledger.py](oqg_portfolio_sim/core/ledger.py)
- Strategy contract (`PricePanel`, `TargetPosition`) in
  [oqg_portfolio_sim/strategies/](oqg_portfolio_sim/strategies/), plus two
  reference strategies:
  - `SmaCrossoverStrategy`: a dumb SMA-crossover on SPY, for exercising the
    pipeline end to end.
  - `RankButterflyStrategy`: a faithful port of the club's constant-rank VIX
    futures butterfly notebook (`rank_butterflies.ipynb`). Trades a butterfly
    across three ranked VIX expiries (default M2/M3/M4), entering on
    |z-score| > 2 against a 252-day rolling mean/std, exiting on reversion
    or a 20-day timeout. Supports the notebook's two leg-weight presets,
    `PLAIN_1_2_1` (-1/+2/-1) and `HEDGED_1_3_2` (-1/+3/-2). Verified against
    the notebook's own printed train-period backtest stats (21 trades,
    mean_pnl=0.5407, win_rate=0.8095, mean_days=13.33 — all match exactly);
    see `tests/test_rank_butterfly_strategy.py`.
  - `MaBreakoutHoldStrategy`: also deliberately naive, but *live* (in the
    default registry) rather than a smoke test. Long SPY when price crosses
    above its 200-day moving average, held for a fixed 5 sessions
    regardless of price action in between (no early exit on a dip, no
    re-entry mid-hold), then closed flat. Exists as a pipeline canary: the
    rank butterfly only trades on rare signals, which makes it a poor way
    to eyeball "is this thing actually alive" — this trades often enough
    that the site's trade log answers that on its own.
- EOD data ingestion layer in [oqg_portfolio_sim/ingestion/](oqg_portfolio_sim/ingestion/):
  - Equities/ETFs: `YFinanceSource` (primary) with `StooqSource` fallback,
    chained via `FallbackSource`.
  - VIX futures: `load_vix_futures_parquet()` normalizes the seed history in
    `data/vix_futures_combined.parquet` (the club's original, immutable
    backtest data); `CboeSettlementSource` pulls daily settlement snapshots
    from Cboe's public feed; `rank_panel()` turns per-contract rows into
    continuous M1/M2/M3/M4-style series (`VX1`..`VXN`) that roll across
    contract expiries. **The runner itself calls `load_vix_futures_history()`
    /`save_vix_futures_history()`, not the seed loader directly** —
    these read/write a separate growing file, `data/vix_futures_live.parquet`,
    so each day only has to fetch what's missing since the *last successful
    run*, not since the seed file's fixed date. Skipping the save step
    silently reintroduces a real production bug from the first soak week:
    every run re-fetched the entire gap from the seed's fixed 2026-07-20
    from scratch, so the gap grew by one session per day until it
    permanently exceeded `refresh_history`'s `max_backfill_sessions` cap —
    see `tests/test_ingestion_vix_futures.py::VixFuturesHistoryPersistenceTest`.
- Simulation engine in [oqg_portfolio_sim/engine/](oqg_portfolio_sim/engine/):
  fill model + cost model (`simulate_step`, `compute_costs`: explicit fees
  + half-spread implicit cost, both recorded separately, never netted),
  margin approximation (`calendar_butterfly_margin`, matching the notebook's
  M2-M3/M3-M4 spread-margin numbers via `config/spread_margins.json`;
  `flat_margin_used` for simple per-instrument margin), daily mark-to-market
  P&L (`gross_pnl`/`net_pnl`), and reconciliation (`recompute_positions_from_fills`
  vs the stored snapshot — a mismatch means halt, per PROJECT_SPEC.md).
  `process_step()` ties all of it together for one strategy's daily step and
  writes fills/positions/equity to the ledger. `core/ledger.py` now has the
  actual writer functions (Phase 1 only had the schema) plus
  `get_current_positions`, which is careful not to resurrect stale holdings
  when a strategy has gone fully flat (flat = no position row, not a zero
  row — see `tests/test_ledger_writers.py`).
  This is the engine only, not the daily runner: it takes already-computed
  targets and already-resolved fill prices as input. Deciding *which*
  as_of/fill dates to process each day is the runner (below).
- Daily runner in [oqg_portfolio_sim/runner/](oqg_portfolio_sim/runner/), run via
  `python -m oqg_portfolio_sim.runner --db ledger.sqlite` (or `--as-of
  YYYY-MM-DD` for manual backfill/testing) and scheduled by
  [.github/workflows/daily-run.yml](.github/workflows/daily-run.yml) once per
  weekday after US close:
  - `runner/calendar.py`: CFE market-calendar awareness (`pandas_market_calendars`)
    -- non-trading days are skipped cleanly, not treated as errors.
  - `runner/registry.py`: which strategies are live; each carries its own
    capital base and margin calculation. `SmaCrossoverStrategy` is
    deliberately excluded (it's a pipeline smoke test, not a real strategy).
  - `runner/daily_cycle.py`: refreshes EOD data (`ingestion.refresh_history`
    pulls any missing VIX settlement sessions, capped at 30 sessions per
    call so a large gap fails fast with a clear error instead of hanging on
    hundreds of sequential requests), computes each active strategy's
    target off yesterday's close, fills at today's close via the engine,
    and writes one `runs` row per day. Idempotent (`ledger.is_date_completed`
    -- state is always read from the ledger, never assumed), and halts
    immediately on any strategy's failure or reconciliation mismatch,
    still recording that failure as `status='error'` before raising (so a
    data-ingestion failure is just as visible in the ledger as a strategy
    bug — not only strategy errors get recorded).
  - The workflow YAML (not the Python) handles committing/pushing the
    ledger, including on failure, so an errored run is visible in the
    public repo, not just in CI logs.
  - The schedule fires twice on the same UTC day (21:30 and 23:30 UTC) —
    the second is a same-day retry safety net for a transient source
    failure or a scheduled trigger GitHub Actions silently dropped (both
    observed for real during the first soak week). Deliberately kept on
    the same UTC calendar day: the runner targets `date.today()`, so a
    trigger that slips past midnight UTC would target the *next* trading
    day rather than retry the current one. Safe as a no-op on a normal day
    thanks to `is_date_completed`.
- Static website in [docs/](docs/), read by [oqg_portfolio_sim/webexport.py](oqg_portfolio_sim/webexport.py)'s
  JSON, regenerated by every daily run (successful or not) and served by
  GitHub Pages once enabled (repo Settings → Pages → source: `/docs`):
  - `index.html`: leaderboard of active strategies, persistent honesty
    banner (simulated paper trading, EOD/delayed data, modeled fills), and
    a gaps/errors panel.
  - `strategy.html?id=...`: one template for any strategy, driven by its
    JSON — stat tiles (net return, Sharpe-like, max drawdown, daily win
    rate), a Chart.js equity curve where missed/errored sessions render as
    real breaks in the line (not smoothed over), and a trade log with a
    "low confidence" badge on fills against illiquid instruments.
  - `webexport.py` only ever includes data from runs with `status='completed'
    AND reconciliation_status='ok'` — "never publish a run that failed
    reconciliation" is enforced at export time, not left to the frontend.
    Gaps/errors are computed and shown regardless, since those are meant to
    be visible.
  - `run_daily_cycle(..., site_output_dir=..., vix_live_path=...)` lets
    tests point both the site export and the VIX ingestion cache at temp
    paths instead of the real `docs/data` and `data/vix_futures_live.parquet`
    — worth knowing since the default with no argument is the real thing;
    both have bitten the test suite once already (see git history).
- Tests in [tests/](tests/)
