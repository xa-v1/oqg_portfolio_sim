"""Reconciliation (PROJECT_SPEC.md: "recompute positions from fills and
assert they match the stored position snapshot. On mismatch, mark the run
status = error and halt -- never publish a run that failed reconciliation.")
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, NamedTuple


class FillRecord(NamedTuple):
    """The minimal shape reconciliation needs from a stored fill row."""

    instrument_id: str
    qty: float


def recompute_positions_from_fills(fills: Iterable[FillRecord]) -> dict[str, float]:
    """Independently derives current holdings by summing every fill ever recorded."""

    positions: dict[str, float] = defaultdict(float)
    for fill in fills:
        positions[fill.instrument_id] += fill.qty
    return dict(positions)


def reconcile(
    recomputed: dict[str, float],
    stored: dict[str, float],
    tolerance: float = 1e-6,
) -> tuple[bool, str | None]:
    """Compares recomputed-from-fills positions against the stored snapshot.

    Both dicts are treated as sparse (a missing key means flat/0), matching
    the convention that only nonzero positions get a ledger row. Returns
    (ok, note); note is None when reconciled, otherwise a description of
    every mismatched instrument.
    """

    all_ids = set(recomputed) | set(stored)
    mismatches = []
    for instrument_id in sorted(all_ids):
        expected = recomputed.get(instrument_id, 0.0)
        actual = stored.get(instrument_id, 0.0)
        if abs(expected - actual) > tolerance:
            mismatches.append(
                f"{instrument_id}: fills sum to {expected}, stored position is {actual}"
            )

    if mismatches:
        return False, "; ".join(mismatches)
    return True, None
