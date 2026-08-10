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

- Fills are ALWAYS modeled, never presented as verified market fills.
- NEVER mutate, reset, or backfill past ledger entries. Gaps render as gaps.
- Enforce point-in-time data: strategies receive only data dated <= as_of.
- Reconcile positions from fills every run; halt and mark error on mismatch.
- Daily/EOD only in v1. No intraday, streaming, or broker APIs.
- Options strategies rejected in v1, not silently simulated.

Use the .venv Virtual environment 

## Things to explicitly NOT do

- Do not add real-time / intraday / streaming anything in v1.
- Do not integrate a broker API.
- Do not simulate options strategies in v1.
- Do not ever mutate or backfill past ledger entries.
- Do not present modeled fills as verified fills.
- Do not let strategies read data or write the ledger directly.