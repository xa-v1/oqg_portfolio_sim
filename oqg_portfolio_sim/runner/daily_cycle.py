"""The GitHub Actions daily cycle (PROJECT_SPEC.md "Runner").

This module doesn't decide *when* to run -- the GH Actions cron does that,
firing on a fixed schedule regardless of the market calendar. Given an
invocation "now", this decides whether there's a new trading session to
process and, if so, does the full cycle: refresh EOD data, generate targets
per active strategy off yesterday's close (point-in-time), diff/fill/cost
via the simulation engine, append to the ledger, reconcile, and record
success or failure as a `runs` row.

Idempotency: state is always derived from the ledger (is_date_completed),
never from in-memory/process state, so re-invoking for an already-completed
date is a safe no-op, and a previously-errored date is safe to retry.

Not handled here (deliberately out of scope for the engine/runner split):
- Committing the ledger to git -- that's a step in the GH Actions workflow
  YAML, run with the CI bot's own credentials, not something this Python
  process does.
- Regenerating JSON for the website -- there is no website yet (Phase 6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from oqg_portfolio_sim.core import ledger
from oqg_portfolio_sim.core.models import load_instrument_specs
from oqg_portfolio_sim.engine import process_step
from oqg_portfolio_sim.ingestion import (
    FallbackSource,
    StooqSource,
    YFinanceSource,
    build_equity_panel,
    load_vix_futures_parquet,
    rank_panel,
    refresh_history,
)
from oqg_portfolio_sim.strategies.contracts import PricePanel, merge_panels

from .calendar import DEFAULT_CALENDAR, is_trading_day, previous_session
from .registry import StrategyRegistration, default_registry

# Generous enough for any strategy's rolling lookback (e.g. the rank
# butterfly's 252-day window) without hardcoding a per-strategy value here.
_EQUITY_HISTORY_DAYS = 400
_VIX_RANK_DEPTH = 8


class RunnerError(RuntimeError):
    """The daily cycle failed; the caller (CLI) should exit non-zero."""


@dataclass(frozen=True)
class RunOutcome:
    skipped: bool
    reason: str | None = None
    run_id: int | None = None
    fill_date: date | None = None
    signal_date: date | None = None
    strategy_notes: dict[str, str] | None = None


def _build_price_panel(
    registry: list[StrategyRegistration], through: date, calendar_name: str
) -> tuple[PricePanel, list[str]]:
    warnings: list[str] = []
    needed_asset_classes: dict[str, str] = {}
    for reg in registry:
        for spec in reg.strategy.required_instruments():
            needed_asset_classes[spec.instrument_id] = spec.asset_class

    panels: list[PricePanel] = []

    if any(asset_class == "futures" for asset_class in needed_asset_classes.values()):
        history = load_vix_futures_parquet()
        history, refresh_warnings = refresh_history(
            history, through, calendar_name=calendar_name)
        warnings.extend(refresh_warnings)
        panels.append(rank_panel(history, n_ranks=_VIX_RANK_DEPTH))

    equity_ids = sorted(
        instrument_id
        for instrument_id, asset_class in needed_asset_classes.items()
        if asset_class == "equity"
    )
    if equity_ids:
        source = FallbackSource([YFinanceSource(), StooqSource()])
        start = through - timedelta(days=_EQUITY_HISTORY_DAYS)
        panels.append(build_equity_panel(source, equity_ids, start, through))

    if not panels:
        return PricePanel(), warnings
    return merge_panels(*panels), warnings


def run_daily_cycle(
    db_path: str | Path,
    registry: list[StrategyRegistration] | None = None,
    *,
    today: date | None = None,
    calendar_name: str = DEFAULT_CALENDAR,
) -> RunOutcome:
    """Runs one daily cycle. Raises RunnerError if any strategy's step fails
    or fails to reconcile -- the run row is still written with status='error'
    before raising, so the failure is visible in the ledger, not just in logs.
    """

    registry = registry if registry is not None else default_registry()
    today = today or date.today()

    # Idempotent: creates the schema if this is a brand-new ledger file, a
    # no-op otherwise. Needed before the is_date_completed read below, since
    # that (unlike start_run/register_strategy) doesn't bootstrap itself.
    ledger.bootstrap_schema(db_path)

    if not is_trading_day(today, calendar_name):
        return RunOutcome(skipped=True, reason=f"{today} is not a {calendar_name} trading session")

    fill_date = today
    if ledger.is_date_completed(db_path, str(fill_date)):
        return RunOutcome(skipped=True, reason=f"{fill_date} already completed")

    # start_run happens before ANYTHING that can fail (ingestion included),
    # so every failure past this point -- not just a strategy's own step --
    # lands as a `runs` row with status='error', per PROJECT_SPEC.md's "on
    # any failure, record a runs row ... and exit non-zero."
    run_id = ledger.start_run(db_path, as_of_date=str(fill_date))

    strategy_notes: dict[str, str] = {}
    all_ok = True

    try:
        signal_date = previous_session(fill_date, calendar_name)
        panel, ingestion_warnings = _build_price_panel(
            registry, fill_date, calendar_name)
        if ingestion_warnings:
            strategy_notes["ingestion"] = "; ".join(ingestion_warnings)

        specs = {spec.instrument_id: spec for spec in load_instrument_specs()}

        for reg in registry:
            ledger.register_strategy(
                db_path,
                strategy_id=reg.strategy_id,
                name=reg.name,
                description=reg.description,
                owner=reg.owner,
                asset_class=reg.asset_class,
            )
            try:
                targets = reg.strategy.generate_targets(signal_date, panel)
                current_positions = ledger.get_current_positions(
                    db_path, reg.strategy_id)
                leg_ids = {t.instrument_id for t in targets} | set(
                    current_positions)

                fill_prices: dict[str, float] = {}
                prior_marks: dict[str, float] = {}
                for instrument_id in leg_ids:
                    price = panel.last_close_as_of(instrument_id, fill_date)
                    if price is None:
                        raise RunnerError(
                            f"no fill price for {instrument_id} as of {fill_date}")
                    fill_prices[instrument_id] = price

                    prior_price = panel.last_close_as_of(
                        instrument_id, signal_date)
                    if prior_price is not None:
                        prior_marks[instrument_id] = prior_price

                result = process_step(
                    db_path, run_id, reg.strategy_id, targets, fill_prices, prior_marks,
                    specs, reg.capital_base, reg.margin_fn,
                )
                if not result.reconciled:
                    all_ok = False
                    strategy_notes[reg.strategy_id] = f"reconciliation failed: {result.reconciliation_note}"
                    break  # halt further strategies per PROJECT_SPEC.md
            except Exception as exc:  # a strategy failure halts the whole run
                all_ok = False
                strategy_notes[reg.strategy_id] = str(exc)
                break
    except Exception as exc:  # ingestion/calendar failure before any strategy ran
        all_ok = False
        strategy_notes["cycle"] = str(exc)

    status = "completed" if all_ok else "error"
    reconciliation_status = "ok" if all_ok else "error"
    note_text = "; ".join(
        f"{key}: {note}" for key, note in strategy_notes.items()) or None
    ledger.finish_run(
        db_path, run_id, status=status, reconciliation_status=reconciliation_status,
        notes=note_text, reconciliation_note=note_text,
    )

    if not all_ok:
        raise RunnerError(
            f"daily cycle failed for {fill_date} (run_id={run_id}): {note_text}")

    return RunOutcome(
        skipped=False, run_id=run_id, fill_date=fill_date, signal_date=signal_date,
    )
