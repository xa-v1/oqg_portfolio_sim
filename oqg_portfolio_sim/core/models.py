from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InstrumentSpec:
    """Configuration-backed trading instrument definition."""

    instrument_id: str
    name: str
    asset_class: str
    multiplier: float
    tick_size: float
    explicit_fee: float = 0.0
    spread: float = 0.0
    liquidity: str = "normal"
    margin_requirement: float | None = None
    fill_policy: str = "next_session_settle"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "InstrumentSpec":
        return cls(
            instrument_id=str(payload["instrument_id"]),
            name=str(payload["name"]),
            asset_class=str(payload["asset_class"]),
            multiplier=float(payload["multiplier"]),
            tick_size=float(payload["tick_size"]),
            explicit_fee=float(payload.get("explicit_fee", 0.0)),
            spread=float(payload.get("spread", 0.0)),
            liquidity=str(payload.get("liquidity", "normal")),
            margin_requirement=float(payload["margin_requirement"])
            if payload.get("margin_requirement") is not None
            else None,
            fill_policy=str(payload.get("fill_policy", "next_session_settle")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "name": self.name,
            "asset_class": self.asset_class,
            "multiplier": self.multiplier,
            "tick_size": self.tick_size,
            "explicit_fee": self.explicit_fee,
            "spread": self.spread,
            "liquidity": self.liquidity,
            "margin_requirement": self.margin_requirement,
            "fill_policy": self.fill_policy,
        }


def load_instrument_specs(path: str | Path | None = None) -> list[InstrumentSpec]:
    """Load instrument specs from a JSON config file."""

    config_path = Path(path or Path(__file__).resolve(
    ).parents[1] / "config" / "instruments.json")
    with config_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    instruments = payload.get("instruments", [])
    return [InstrumentSpec.from_dict(item) for item in instruments]


def load_spread_margins(path: str | Path | None = None) -> dict[tuple[str, str], float]:
    """Load approximate adjacent-leg spread margins from config.

    Keyed by (near, far) instrument id pairs, e.g. ("VX2", "VX3") -> 2170.0.
    This is explicitly an approximation (see PROJECT_SPEC.md's margin
    section), not a real broker margin calculation.
    """

    config_path = Path(path or Path(__file__).resolve(
    ).parents[1] / "config" / "spread_margins.json")
    with config_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    entries = payload.get("spread_margins", [])
    return {
        (str(entry["near"]), str(entry["far"])): float(entry["margin"])
        for entry in entries
    }
