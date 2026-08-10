"""Margin approximation (PROJECT_SPEC.md "Margin / buying power").

Explicitly approximate, per the spec: "Label margin as an approximation in
any public display." Two calculations are provided:

- `flat_margin_used`: sums each instrument's configured flat
  `margin_requirement`. Correct for standalone/outright positions; None
  for spread-margined instruments (they have no single-instrument number).
- `calendar_butterfly_margin`: the notebook's own decomposition for a
  3-leg calendar butterfly (near/mid/far ranked futures) into two adjacent
  calendar-spread units, priced off configured spread margins (e.g.
  ~$2170 for the M2-M3 spread, ~$1220 for M3-M4). This assumes
  mid_qty == -near_qty - far_qty (a properly offsetting butterfly); a
  position that doesn't decompose that way will be understated by this
  approximation.
"""

from __future__ import annotations

from oqg_portfolio_sim.core.models import InstrumentSpec


def flat_margin_used(positions: dict[str, float], specs: dict[str, InstrumentSpec]) -> float:
    """Sum of |qty| * margin_requirement for instruments with a flat margin configured.

    Instruments with margin_requirement=None (e.g. spread-margined futures
    legs, or cash equities) contribute nothing here -- use a more specific
    margin calculation for those.
    """

    total = 0.0
    for instrument_id, qty in positions.items():
        spec = specs.get(instrument_id)
        if spec is not None and spec.margin_requirement is not None:
            total += abs(qty) * spec.margin_requirement
    return total


def calendar_butterfly_margin(
    positions: dict[str, float],
    near_id: str,
    mid_id: str,
    far_id: str,
    spread_margins: dict[tuple[str, str], float],
) -> float:
    """Approximate margin for one 3-leg calendar butterfly.

    Decomposes into q1 units of the (near, mid) spread and q2 units of the
    (mid, far) spread, where q1 = -near_qty and q2 = -far_qty.
    """

    near_qty = positions.get(near_id, 0.0)
    far_qty = positions.get(far_id, 0.0)
    q1 = -near_qty
    q2 = -far_qty

    margin_near_mid = spread_margins.get((near_id, mid_id))
    margin_mid_far = spread_margins.get((mid_id, far_id))
    if margin_near_mid is None or margin_mid_far is None:
        raise KeyError(
            f"missing spread margin config for ({near_id}, {mid_id}) or "
            f"({mid_id}, {far_id})"
        )

    return abs(q1) * margin_near_mid + abs(q2) * margin_mid_far


def margin_exceeded(margin_used: float, capital_base: float) -> bool:
    """True if approximate margin usage exceeds the fake capital base."""

    return margin_used > capital_base
