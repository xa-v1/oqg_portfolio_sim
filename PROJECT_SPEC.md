# Quant Club — Forward Simulation & Track-Record Platform

## Read this first (context for the agent)

We are a quant club. We already have strategies that are **built and
backtested** (raw Python + pandas). We now want to run them **forward**
against real market data with a fake capital base, applying realistic
costs and slippage, and publish the resulting track record on a public
website so people can learn about the club.

We evaluated existing platforms (QuantConnect, Alpaca, Schwab API, IBKR)
and rejected them: no single free option covers all our asset classes
(equities, ETFs, options, **and CFE VIX futures**) while giving us a
controllable fill/cost model. So we are building our own **simulator**.

**Critical framing: this is a simulator, NOT a broker integration.**
We do not place real or paper orders with any broker. We ingest market
data, run strategies, simulate fills with our own cost model, record a
ledger, and render it. No brokerage API is involved.

This document is the spec. Build it in phases. Ask before inventing scope.

---

## Non-negotiable constraints (do not "improve" past these)

These are deliberate limits from a data/fidelity analysis. Respect them.

1. **Daily frequency, end-of-day (EOD) data only** in v1. No intraday,
   no real-time, no tick data. Free data = EOD only, and our first
   strategy (a VIX-futures rank butterfly) is daily and executes off
   settlement prices. Do not build streaming/websocket infrastructure.
2. **Fills are modeled, never claimed as real.** We do not have L2/order-
   book data. Every fill price is produced by an explicit, inspectable
   cost model. The system must never present a simulated fill as if it
   were a verified market fill.
3. **The ledger is the source of truth and must be tamper-evident.**
   The website reads the ledger only. Never backfill, silently reset,
   or edit past entries. Gaps (e.g. a missed run) must be recorded and
   rendered as gaps, not smoothed over. This is a credibility system;
   its integrity is the product.
4. **Everything runs on free infrastructure** (see Stack). No paid data,
   no paid hosting, no server to babysit.
5. **Options strategies are OUT OF SCOPE for v1.** Free EOD options data
   is scarce/unreliable. If a member submits an options strategy, the
   engine should reject it with a clear "unsupported asset class in v1"
   error, not silently mis-simulate it.

---

## What we're building (architecture)

```
                +------------------------+
   EOD data --> |  Data ingestion layer  |  (per-source adapters)
                +-----------+------------+
                            |  normalized price panel
                            v
                +------------------------+
   strategies->|   Strategy runner      |  (conforms every strategy
                |   (contract-based)     |   to one interface)
                +-----------+------------+
                            |  target positions / orders
                            v
                +------------------------+
                |  Simulation engine     |  fill model + cost model
                |  (fills, costs, margin)|  + per-instrument specs
                +-----------+------------+
                            |  fills, positions, P&L
                            v
                +------------------------+
                |   Ledger (append-only) |  SQLite or committed JSON
                +-----------+------------+
                            |
                            v
                +------------------------+
                |  Static website (read) |  charts from ledger
                +------------------------+
```

The whole daily cycle is triggered by a **scheduled GitHub Actions
workflow** that runs after US market close, executes one simulation
step, appends to the ledger, and commits.

---

## Stack (all free)

- **Language:** Python 3.11+ for engine/ingestion; the website is static
  (vanilla JS or a small React build — keep it dependency-light).
- **Scheduler / "always-on":** GitHub Actions `schedule:` cron. No VM.
- **Storage:** start with SQLite committed to the repo. If that gets
  awkward for the site to read, mirror the needed views to JSON on each
  run. (Decide with maintainer before switching.)
- **Website hosting:** GitHub Pages, reading committed ledger data.
- **Charting:** a lightweight client-side lib (e.g. uPlot or Chart.js).
- **Data sources (EOD only):**
  - Equities / ETFs / indices: yfinance (primary), with Stooq or Alpha
    Vantage as fallback. Abstract behind one interface so sources are
    swappable — free sources break often.
  - VIX futures: Cboe published daily **settlement** data (free). Our
    existing history lives in `vix_futures_combined.parquet` (columns
    include `Trade Date`, `Settle`, `days_to_settlement`); match that
    schema for continuity.
  - Options: none in v1 (see constraint 5).

---

## Strategy interface (the contract every member must conform to)

This is the long pole — members' code is idiosyncratic. Define ONE
contract and write adapters to conform existing notebooks to it. Do not
let strategies talk to data sources or the ledger directly; they receive
data and return intent.

```python
# Each strategy is a class or module exposing:

def required_instruments() -> list[InstrumentSpec]:
    """Which instruments this strategy needs data for."""

def generate_targets(as_of: date, history: PricePanel) -> list[TargetPosition]:
    """
    Given data available AS OF end-of-day `as_of` (no lookahead),
    return desired positions. The engine diffs targets vs current
    holdings to produce orders, and fills them at the NEXT session
    per the fill convention. Returning [] means 'flat'.
    """
```

- `history` must be strictly point-in-time: the engine passes only data
  dated `<= as_of`. Enforce this; lookahead is the #1 way a track record
  becomes a lie.
- `TargetPosition` carries instrument id + signed quantity (contracts /
  shares). For the VIX fly, quantities encode the butterfly legs across
  ranked expiries (e.g. -1/+2/-1 or the hedged 1-3-2 weighting).
- Provide a reference implementation that ports the existing rank-
  butterfly notebook to this interface, including its z-score entry
  (|z|>2 on a 252-day rolling window), revert/timeout exits, and
  next-bar execution (`shift(-1)` semantics).

---

## Simulation engine

### Instrument specs (per-instrument, config-driven)
Each tradable instrument carries: `multiplier` (VIX futures = 1000),
`tick_size`, `asset_class`, explicit fees, and a margin rule. Store as
config, not hardcoded in strategy logic.

### Fill model (explicit and honest)
- Default convention: fill at the **next session's settle/close** for
  the instrument, offset by a modeled spread cost. Make the convention a
  named, swappable policy so we can show exactly what we assumed.
- Apply an **implicit cost** = half-spread × quantity, per leg, per side.
  For the VIX fly, default spread = 0.10 vol points per contract (carry
  over the notebook's assumption; make it per-instrument configurable).
- **Do not** assume perfect fills on illiquid legs silently. Add a
  liquidity flag per instrument; when a strategy trades a flagged-illiquid
  instrument, record a `fill_confidence: low` marker on those fills so the
  website can surface it. We are honest about back-month VIX futures.

### Cost model (explicit)
Per-instrument explicit fees, mirroring the notebook's structure:
CFE exchange fee, OCC clearing, NFA fee, assumed broker fee. Sum explicit
+ implicit into a per-trade cost and record both components separately in
the ledger (never just a net number).

### Margin / buying power
Approximate. For futures use configured spread-margin numbers (the
notebook uses ~$2170 for M2-M3, ~$1220 for M3-M4). Track margin usage vs
the fake capital base and flag if a strategy would exceed it. Label
margin as an approximation in any public display.

---

## Ledger schema (append-only)

Design so nothing is ever mutated. Suggested tables:

- `runs(run_id, as_of_date, started_at, status, notes)` — one row per
  daily execution, including failed/partial runs so gaps are visible.
- `fills(fill_id, run_id, strategy_id, instrument_id, qty, fill_price,
  explicit_cost, implicit_cost, fill_confidence, reason)`.
- `positions(run_id, strategy_id, instrument_id, qty)` — end-of-day
  snapshot.
- `equity(run_id, strategy_id, gross_pnl, net_pnl, cum_net_pnl,
  capital_base, margin_used)` — daily mark per strategy.
- `strategies(strategy_id, name, description, owner, active, asset_class)`.

Per-strategy attribution is first-class (this gives us a **leaderboard**).
A **combined book** view (netted across strategies) is a derived, optional
read on top — build the per-strategy ledger first; combined can come later.

**Reconciliation:** each run, recompute positions from fills and assert
they match the stored position snapshot. On mismatch, mark the run
`status = error` and halt — never publish a run that failed reconciliation.

---

## Runner (GitHub Actions)

- Scheduled workflow, once per weekday after US close (account for the
  market calendar — skip holidays/weekends; use `pandas_market_calendars`
  or an equivalent free calendar).
- Steps: pull latest EOD data → for each active strategy, call
  `generate_targets` for the new `as_of` date → diff vs current positions
  → simulate fills + costs → append fills/positions/equity → reconcile →
  commit ledger (and regenerate any JSON the site reads).
- **Idempotency:** if a date was already processed, do not double-apply.
  On startup, derive current state from the ledger, not from memory.
- On any failure, record a `runs` row with `status = error` and a note,
  commit that, and exit non-zero so we get notified. Do not partially
  write.

---

## Website (read-only, static)

- Reads committed ledger data only. No secrets, no API calls to brokers,
  no server.
- Shows, per strategy and combined: equity curve, key stats (net return,
  Sharpe-like, max drawdown, win rate), and a trade log.
- **Mandatory honesty UI:**
  - A persistent banner: results are **simulated paper trading**, not
    real or investable, for education.
  - Note the data basis (EOD, delayed) and the fill assumptions
    (modeled spread, next-session execution).
  - Render run gaps as visible gaps in the curve.
  - Surface `fill_confidence: low` markers on affected trades.
- Keep it clean and legible; this is a public face for the club. (If you
  want design direction, follow a distinctive, non-templated aesthetic.)

---

## Build phases

1. **Skeleton + instrument specs + ledger schema.** Get the empty
   pipeline and DB in place. No strategies yet.
2. **Data ingestion layer** with swappable EOD sources; load the VIX
   futures history from the existing parquet and wire the Cboe settlement
   feed for daily updates.
3. **Strategy contract + port the rank butterfly** as the reference
   strategy. Enforce point-in-time data. Unit-test against the notebook's
   backtested numbers to confirm the port is faithful.
4. **Simulation engine:** fill model, cost model, margin, reconciliation.
5. **Runner** as a GitHub Actions cron; prove one clean end-to-end daily
   step that commits a ledger row.
6. **Website** reading the ledger, with all honesty UI.
7. **Soak:** let it run live-forward across real sessions before any
   public launch; watch for gaps, source breakage, reconciliation fails.
8. **Combined-book view** (optional) once per-strategy is solid.

---

## Open questions to resolve with the maintainer (ask, don't assume)

- Ledger: SQLite-in-repo vs JSON-in-repo vs both? (Affects how the
  static site reads it.)
- Capital base: single shared fake capital across strategies, or a
  separate notional per strategy? (Affects margin checks and the
  combined view.)
- Leaderboard only, or combined book too, at launch?
- How are new member strategies submitted and reviewed before going
  live on the public site?

---

## Things to explicitly NOT do

- Do not add real-time / intraday / streaming anything in v1.
- Do not integrate a broker API.
- Do not simulate options strategies in v1.
- Do not ever mutate or backfill past ledger entries.
- Do not present modeled fills as verified fills.
- Do not let strategies read data or write the ledger directly.
