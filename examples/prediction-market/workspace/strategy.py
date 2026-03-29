from __future__ import annotations

from typing import Any


CONFIDENCE_THRESHOLD = 0.75
BET_SIZING = "confidence_scaled"
MAX_BET_FRACTION = 0.15
PROMPT_FACTORS = []


def resolve_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    base = {
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "bet_sizing": BET_SIZING,
        "max_bet_fraction": MAX_BET_FRACTION,
        "prompt_factors": list(PROMPT_FACTORS),
    }
    if not config:
        return base
    merged = dict(base)
    merged.update(config)
    return merged


def _position_size(confidence: float, config: dict[str, Any]) -> float:
    max_fraction = float(config.get("max_bet_fraction", MAX_BET_FRACTION) or MAX_BET_FRACTION)
    sizing = str(config.get("bet_sizing", BET_SIZING) or BET_SIZING)
    if sizing == "fixed":
        return max_fraction
    if sizing == "kelly":
        return max_fraction * max(0.1, confidence * confidence)
    return max_fraction * max(0.25, confidence)


def strategy(record: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    active = resolve_config(config)
    threshold = float(active.get("confidence_threshold", CONFIDENCE_THRESHOLD) or CONFIDENCE_THRESHOLD)
    price = float(record.get("last_trade_price", 0.0) or 0.0)
    predicted = 0 if price >= threshold else 1
    confidence = round(min(1.0, max(0.0, abs(price - 0.5) * 2)), 4)
    return {
        "action": "buy",
        "outcome_index": predicted,
        "size": round(_position_size(confidence, active), 4),
        "prediction": predicted,
        "confidence": confidence,
    }
