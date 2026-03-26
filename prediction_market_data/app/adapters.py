from __future__ import annotations

import ast
import csv
import json
from pathlib import Path
from statistics import median
from typing import Any

from app.adapter_registry import ADAPTER_REGISTRY


def _parse_jsonish(value: str, default: Any) -> Any:
    if value in ("", None):
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return default


def _parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _canonical_record_from_flat_row(row: dict[str, str], index: int) -> dict[str, Any]:
    context_payload = _parse_jsonish(row.get("context", ""), {})
    context = context_payload if isinstance(context_payload, dict) else {}
    context.update(
        {
            "category": row.get("category", context.get("category", "")) or "unknown",
            "subcategory": row.get("subcategory", context.get("subcategory", "")) or "",
            "event_title": row.get("event_title", context.get("event_title", "")) or "",
            "liquidity": _parse_float(row.get("liquidity", context.get("liquidity", 0.0))),
            "neg_risk": _parse_bool(row.get("neg_risk", context.get("neg_risk", False))),
        }
    )
    outcomes = _parse_jsonish(row.get("outcomes", ""), [])
    outcome_prices = _parse_jsonish(row.get("outcome_prices", ""), [])
    price_signals = _parse_jsonish(row.get("price_signals", ""), {})
    final_resolution_index = int(_parse_float(row.get("final_resolution_index", 0), 0))
    final_resolution = row.get("final_resolution", "")
    if not final_resolution and isinstance(outcomes, list) and 0 <= final_resolution_index < len(outcomes):
        final_resolution = str(outcomes[final_resolution_index])
    return {
        "market_id": row.get("market_id", "") or f"row-{index}",
        "question": row.get("question", ""),
        "outcomes": outcomes,
        "outcome_prices": outcome_prices if isinstance(outcome_prices, list) else [],
        "final_resolution": final_resolution,
        "final_resolution_index": final_resolution_index,
        "last_trade_price": _parse_float(row.get("last_trade_price", 0.0)),
        "price_signals": price_signals if isinstance(price_signals, dict) else {},
        "volume": _parse_float(row.get("volume", 0.0)),
        "context": context,
    }


def validate_canonical_records(records: list[Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    errors: list[str] = []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(records, start=1):
        if not isinstance(item, dict):
            errors.append(f"row {index}: record must be object")
            continue
        outcomes = item.get("outcomes")
        if not isinstance(outcomes, list) or len(outcomes) != 2 or any(not isinstance(val, str) for val in outcomes):
            errors.append(f"row {index}: outcomes must be list[str] of length 2")
            continue
        question = str(item.get("question", "")).strip()
        if not question:
            errors.append(f"row {index}: question is required")
            continue
        final_resolution_index = item.get("final_resolution_index")
        if final_resolution_index not in (0, 1):
            errors.append(f"row {index}: final_resolution_index must be 0 or 1")
            continue
        last_trade_price = _parse_float(item.get("last_trade_price", 0.0))
        if not 0.0 <= last_trade_price <= 1.0:
            errors.append(f"row {index}: last_trade_price must be between 0 and 1")
            continue
        context = item.get("context") if isinstance(item.get("context"), dict) else {}
        normalized.append(
            {
                "market_id": str(item.get("market_id", "") or f"row-{index}"),
                "question": question,
                "outcomes": [str(val) for val in outcomes],
                "outcome_prices": [
                    _parse_float(val) for val in item.get("outcome_prices", []) if isinstance(item.get("outcome_prices", []), list)
                ],
                "final_resolution": str(item.get("final_resolution", outcomes[final_resolution_index])),
                "final_resolution_index": int(final_resolution_index),
                "last_trade_price": last_trade_price,
                "price_signals": item.get("price_signals") if isinstance(item.get("price_signals"), dict) else {},
                "volume": max(0.0, _parse_float(item.get("volume", 0.0))),
                "context": {
                    "category": str(context.get("category", "unknown") or "unknown"),
                    "subcategory": str(context.get("subcategory", "") or ""),
                    "event_title": str(context.get("event_title", "") or ""),
                    "liquidity": max(0.0, _parse_float(context.get("liquidity", 0.0))),
                    "neg_risk": _parse_bool(context.get("neg_risk", False)),
                },
            }
        )

    if errors:
        raise ValueError("invalid canonical dataset: " + "; ".join(errors[:25]))

    categories: dict[str, int] = {}
    volumes = []
    liquidities = []
    for item in normalized:
        category = item["context"]["category"]
        categories[category] = categories.get(category, 0) + 1
        volumes.append(item["volume"])
        liquidities.append(item["context"]["liquidity"])

    summary = {
        "num_records": len(normalized),
        "categories": dict(sorted(categories.items(), key=lambda pair: (-pair[1], pair[0]))),
        "volume_min": round(min(volumes), 2) if volumes else 0.0,
        "volume_median": round(median(volumes), 2) if volumes else 0.0,
        "volume_max": round(max(volumes), 2) if volumes else 0.0,
        "liquidity_median": round(median(liquidities), 2) if liquidities else 0.0,
    }
    return normalized, summary


def adapt_dataset(input_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    suffix = input_path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("records"), list):
            payload = payload["records"]
        if not isinstance(payload, list):
            raise ValueError("JSON input must be a list of canonical records or {\"records\": [...]}")
        records, summary = validate_canonical_records(payload)
        return records, {"adapter": "canonical_json", "input_format": "json", "summary": summary}

    if suffix != ".csv":
        raise ValueError("unsupported input file type; expected .json or .csv")

    with input_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    polymarket_columns = {"closed", "resolvedBy", "outcomes", "outcomePrices", "volumeNum"}
    if polymarket_columns.issubset(set(fieldnames)):
        row_processor = ADAPTER_REGISTRY["polymarket_csv"]["row_processor"]
        records = []
        skipped = 0
        for row in rows:
            item = row_processor(row)
            if item:
                records.append(item)
            else:
                skipped += 1
        records, summary = validate_canonical_records(records)
        summary["source_rows"] = len(rows)
        summary["skipped_rows"] = skipped
        return records, {"adapter": "polymarket_csv", "input_format": "csv", "summary": summary}

    flat_records = [_canonical_record_from_flat_row(row, index) for index, row in enumerate(rows, start=1)]
    records, summary = validate_canonical_records(flat_records)
    summary["source_rows"] = len(rows)
    return records, {"adapter": "canonical_csv", "input_format": "csv", "summary": summary}
