from __future__ import annotations

import ast
import json
from typing import Any


MIN_VOLUME = 5000
RESOLUTION_THRESHOLD = 0.9


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value not in ("", None) else default
    except (TypeError, ValueError):
        return default


def parse_json_list(value: str) -> list[Any] | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        try:
            payload = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return None
    return payload if isinstance(payload, list) else None


def determine_resolution(outcome_prices: list[float], outcomes: list[str]) -> tuple[str, int] | None:
    if not outcome_prices or not outcomes or len(outcome_prices) != len(outcomes):
        return None
    max_price = max(outcome_prices)
    if max_price < RESOLUTION_THRESHOLD:
        return None
    winner_index = outcome_prices.index(max_price)
    return outcomes[winner_index], winner_index


def process_polymarket_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("closed") != "True" or not row.get("resolvedBy"):
        return None
    volume = parse_float(row.get("volumeNum", ""))
    if volume < MIN_VOLUME:
        return None

    outcomes = parse_json_list(row.get("outcomes", ""))
    outcome_prices_raw = parse_json_list(row.get("outcomePrices", ""))
    if not outcomes or not outcome_prices_raw or len(outcomes) != 2:
        return None

    outcome_prices = [parse_float(value) for value in outcome_prices_raw]
    resolution = determine_resolution(outcome_prices, [str(item) for item in outcomes])
    if resolution is None:
        return None
    winning_outcome, winning_index = resolution
    return {
        "market_id": row.get("id", ""),
        "question": row.get("question", ""),
        "outcomes": [str(item) for item in outcomes],
        "outcome_prices": outcome_prices,
        "final_resolution": winning_outcome,
        "final_resolution_index": winning_index,
        "last_trade_price": parse_float(row.get("lastTradePrice", "")),
        "price_signals": {
            "1h_change": parse_float(row.get("oneHourPriceChange", "")),
            "1d_change": parse_float(row.get("oneDayPriceChange", "")),
            "1w_change": parse_float(row.get("oneWeekPriceChange", "")),
            "1m_change": parse_float(row.get("oneMonthPriceChange", "")),
            "1y_change": parse_float(row.get("oneYearPriceChange", "")),
        },
        "volume": volume,
        "context": {
            "category": row.get("category", "") or "unknown",
            "subcategory": row.get("subcategory", "") or "",
            "event_title": row.get("event_title", "") or "",
            "liquidity": parse_float(row.get("liquidityNum", "")),
            "neg_risk": row.get("negRisk", "") == "True",
        },
    }


ADAPTER_REGISTRY = {
    "canonical_json": {
        "name": "canonical_json",
        "input_format": "json",
        "description": "Canonical JSON dataset payload.",
    },
    "canonical_csv": {
        "name": "canonical_csv",
        "input_format": "csv",
        "description": "Flat canonical CSV input.",
    },
    "polymarket_csv": {
        "name": "polymarket_csv",
        "input_format": "csv",
        "description": "Legacy Polymarket CSV transformed through the adapter registry.",
        "row_processor": process_polymarket_row,
    },
}


def list_registered_adapters() -> list[dict[str, Any]]:
    return [dict(item) for item in ADAPTER_REGISTRY.values()]
