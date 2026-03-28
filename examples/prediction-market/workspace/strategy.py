from __future__ import annotations

CONFIDENCE_THRESHOLD = 0.75
BET_SIZING = "confidence_scaled"
MAX_BET_FRACTION = 0.15
PROMPT_FACTORS = []


def strategy_contract() -> dict[str, object]:
    return {
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "bet_sizing": BET_SIZING,
        "max_bet_fraction": MAX_BET_FRACTION,
        "prompt_factors": PROMPT_FACTORS,
    }
