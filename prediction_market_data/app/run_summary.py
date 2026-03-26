from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from app.common import read_json
from app.runs import run_paths


def parse_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def load_results_rows(run_id: str) -> list[dict[str, Any]]:
    results_path = run_paths(run_id)["root"] / "runtime" / "pm_results.tsv"
    if not results_path.exists():
        return []
    items: list[dict[str, Any]] = []
    with results_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            if not isinstance(row, dict):
                continue
            items.append(row)
    return items


def load_iterations(run_id: str) -> list[dict[str, Any]]:
    path = run_paths(run_id)["root"] / "runtime" / "iteration_details.json"
    payload = read_json(path, [])
    return payload if isinstance(payload, list) else []


def select_best_result(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None

    def rank(row: dict[str, Any]) -> tuple[float, float]:
        validation = parse_float(row.get("validation_fitness"), float("-inf"))
        fitness = parse_float(row.get("search_fitness"), float("-inf"))
        return (validation if validation is not None else float("-inf"), fitness if fitness is not None else float("-inf"))

    return sorted(rows, key=rank, reverse=True)[0]


def status_mix(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def build_run_summary(run_detail: dict[str, Any]) -> dict[str, Any]:
    run_id = str(run_detail["run_id"])
    rows = load_results_rows(run_id)
    iterations = load_iterations(run_id)
    best = select_best_result(rows) or {}
    latest_iteration = iterations[-1] if iterations else {}
    accepted_count = sum(1 for row in rows if row.get("status") == "accepted")
    summary = run_detail.get("summary") if isinstance(run_detail.get("summary"), dict) else {}

    headline_parts = [
        f"run={run_id}",
        f"status={run_detail.get('status', 'unknown')}",
        f"records={summary.get('num_records', 0)}",
        f"iterations={len(iterations)}",
        f"accepted={accepted_count}",
    ]
    if best:
        best_validation = parse_float(best.get("validation_fitness"), None)
        if best_validation is not None:
            headline_parts.append(f"best_validation={best_validation:.4f}")

    return {
        "run_id": run_id,
        "status": run_detail.get("status", "unknown"),
        "headline": " | ".join(headline_parts),
        "data_summary": summary,
        "constraints": run_detail.get("constraints") or {},
        "result_counts": status_mix(rows),
        "best_result": {
            "commit": best.get("commit", ""),
            "status": best.get("status", ""),
            "validation_fitness": parse_float(best.get("validation_fitness"), None),
            "search_fitness": parse_float(best.get("search_fitness"), None),
            "holdout_fitness": parse_float(best.get("holdout_fitness"), None),
            "validation_pnl": parse_float(best.get("validation_pnl"), None),
            "decision_logic": best.get("decision_logic", ""),
        } if best else {},
        "latest_iteration": {
            "iteration": latest_iteration.get("iteration"),
            "status": latest_iteration.get("status", ""),
            "change_axis": latest_iteration.get("change_axis", ""),
            "search_intent": latest_iteration.get("search_intent", ""),
            "patch_summary": latest_iteration.get("patch_summary", ""),
            "decision_logic": latest_iteration.get("decision_logic", ""),
        } if latest_iteration else {},
        "artifacts_root": run_detail.get("paths", {}).get("root", ""),
        "error": run_detail.get("error", ""),
    }
