from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from app.common import now_iso
from app.repositories.run_repository import (
    load_data_summary,
    load_iteration_details,
    load_live_events,
    load_manifest,
    load_orchestrator_state,
    load_results_rows,
    load_run_spec,
    load_runtime_events,
    runtime_paths_for_run,
    write_json,
)

PROJECTION_SCHEMA_VERSION = "run_projection.v1"

STATUS_MAP = {
    "accepted": "accepted",
    "keep": "accepted",
    "discard": "discard",
    "search_reject": "search_reject",
    "validation_reject": "validation_reject",
    "provisional": "provisional",
    "holdout_reject": "holdout_reject",
    "failed": "failed",
    "stopped": "stopped",
    "queued": "queued",
    "created": "created",
    "crash": "failed",
    "timeout": "failed",
}

RESULT_HEADERS = [
    "commit",
    "description",
    "status",
    "search_fitness",
    "validation_fitness",
    "holdout_fitness",
    "validation_pnl",
    "tokens",
    "decision_logic",
]


def parse_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_int(value: Any, default: int = 0) -> int:
    try:
        if value in ("", None):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def canonical_status(value: str) -> str:
    return STATUS_MAP.get(str(value or "").strip(), str(value or "unknown").strip() or "unknown")


def first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def choose_display_fitness(row: dict[str, Any]) -> float:
    return first_non_none(
        parse_float(row.get("validation_fitness"), None)
        if row.get("validation_fitness") not in (None, "")
        else None,
        parse_float(row.get("search_fitness"), None)
        if row.get("search_fitness") not in (None, "")
        else None,
        parse_float(row.get("holdout_fitness"), None)
        if row.get("holdout_fitness") not in (None, "")
        else None,
        parse_float(row.get("fitness"), 0.0),
    )


def normalize_result_row(row: dict[str, Any]) -> dict[str, Any]:
    status = canonical_status(str(row.get("status", "")))
    return {
        "commit": row.get("commit", ""),
        "description": row.get("description", ""),
        "status": status,
        "search_fitness": parse_float(row.get("search_fitness"), None),
        "validation_fitness": parse_float(row.get("validation_fitness"), None),
        "holdout_fitness": parse_float(row.get("holdout_fitness"), None),
        "fitness": choose_display_fitness(row),
        "validation_pnl": parse_float(row.get("validation_pnl"), None),
        "total_pnl": first_non_none(
            parse_float(row.get("validation_pnl"), None)
            if row.get("validation_pnl") not in (None, "")
            else None,
            parse_float(row.get("total_pnl"), None)
            if row.get("total_pnl") not in (None, "")
            else None,
            0.0,
        ),
        "accuracy": parse_float(row.get("accuracy"), None),
        "tokens": parse_int(row.get("tokens"), 0),
        "decision_logic": row.get("decision_logic", ""),
    }


def normalize_phase_summary(phase_name: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload or {}
    return {
        "phase": phase_name,
        "fitness_median": parse_float(payload.get("fitness_median"), None),
        "fitness_mean": parse_float(payload.get("fitness_mean"), None),
        "fitness_std": parse_float(payload.get("fitness_std"), None),
        "search_rank_score": parse_float(payload.get("search_rank_score"), None),
        "total_pnl_mean": parse_float(payload.get("total_pnl_mean"), None),
        "accuracy_mean": parse_float(payload.get("accuracy_mean"), None),
        "max_drawdown_max": parse_float(payload.get("max_drawdown_max"), None),
        "num_trades_mean": parse_float(payload.get("num_trades_mean"), None),
        "num_skipped_mean": parse_float(payload.get("num_skipped_mean"), None),
        "token_per_market_mean": parse_float(payload.get("token_per_market_mean"), None),
        "total_tokens": parse_int(payload.get("total_tokens"), 0),
        "prompt_tokens": parse_int(payload.get("prompt_tokens"), 0),
        "completion_tokens": parse_int(payload.get("completion_tokens"), 0),
        "api_calls": parse_int(payload.get("api_calls"), 0),
        "api_errors": parse_int(payload.get("api_errors"), 0),
        "pool_size": parse_int(payload.get("pool_size"), 0),
        "sample_size": parse_int(payload.get("sample_size"), 0),
        "repeats": parse_int(payload.get("repeats"), 0),
        "category_breakdown": payload.get("category_breakdown") if isinstance(payload.get("category_breakdown"), dict) else {},
        "liquidity_breakdown": payload.get("liquidity_breakdown") if isinstance(payload.get("liquidity_breakdown"), dict) else {},
    }


def normalize_phase_results(item: dict[str, Any]) -> dict[str, Any]:
    phase_results = item.get("phase_results")
    if isinstance(phase_results, dict) and phase_results:
        return {name: normalize_phase_summary(name, payload) for name, payload in phase_results.items()}

    eval_results = item.get("eval_results", {}) or {}
    token_summary = item.get("token_summary", {}) or {}
    if not eval_results and not token_summary:
        return {}
    return {
        "search": normalize_phase_summary(
            "search",
            {
                "fitness_median": eval_results.get("fitness"),
                "fitness_mean": eval_results.get("fitness"),
                "fitness_std": 0.0,
                "total_pnl_mean": eval_results.get("total_pnl"),
                "accuracy_mean": eval_results.get("accuracy"),
                "max_drawdown_max": eval_results.get("max_drawdown"),
                "num_trades_mean": eval_results.get("num_trades"),
                "num_skipped_mean": eval_results.get("num_skipped"),
                "total_tokens": token_summary.get("total_tokens"),
                "prompt_tokens": token_summary.get("prompt_tokens"),
                "completion_tokens": token_summary.get("completion_tokens"),
                "api_calls": token_summary.get("api_calls"),
                "api_errors": token_summary.get("api_errors"),
            },
        )
    }


def normalize_iteration_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    normalized["status"] = canonical_status(str(item.get("status", "")))
    normalized["phase_results"] = normalize_phase_results(item)
    normalized["prompt_change"] = item.get("prompt_change") or {
        "changed": False,
        "summary": "No prompt changes recorded.",
        "details": "",
        "before_system": "",
        "after_system": "",
        "before_user": "",
        "after_user": "",
        "before_factors": [],
        "after_factors": [],
    }
    normalized["change_axis"] = item.get("change_axis") or "unknown"
    normalized["search_intent"] = item.get("search_intent") or "unknown"
    normalized["step_size"] = item.get("step_size") or "n/a"
    normalized["current_mode"] = item.get("current_mode") or ""
    normalized["search_reject_streak"] = parse_int(item.get("search_reject_streak"), 0)
    normalized["validated_no_edge_streak"] = parse_int(item.get("validated_no_edge_streak"), 0)
    normalized["search_shortlist"] = item.get("search_shortlist") if isinstance(item.get("search_shortlist"), list) else []
    return normalized


def normalize_orchestrator_state(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload or {}
    main_agent = payload.get("main_agent") or {}
    return {
        "goal": payload.get("goal", ""),
        "status": payload.get("status", "idle"),
        "updated_at": payload.get("updated_at", ""),
        "main_agent": {
            "goal": main_agent.get("goal", ""),
            "constraints": main_agent.get("constraints") or [],
            "completed": main_agent.get("completed") or [],
            "in_progress": main_agent.get("in_progress") or [],
            "pending": main_agent.get("pending") or [],
            "notes": main_agent.get("notes") or [],
            "feedback": main_agent.get("feedback") or [],
            "next_dispatch_reasoning": main_agent.get("next_dispatch_reasoning", ""),
        },
        "workers": payload.get("workers") or [],
    }


def default_orchestrator_state(status: str = "idle") -> dict[str, Any]:
    return normalize_orchestrator_state(
        {
            "goal": "",
            "status": status,
            "updated_at": "",
            "main_agent": {},
            "workers": [],
        }
    )


def tokens_from_live_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    result = {
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "api_calls": 0,
        "api_errors": 0,
        "progress": "0/0",
        "status": "idle",
    }
    if not events:
        return result

    last_inference = None
    last_entry = None
    start_entry = None
    for entry in events:
        last_entry = entry
        if entry.get("type") == "start":
            start_entry = entry
        if entry.get("type") == "inference":
            last_inference = entry

    if last_inference:
        result["total_tokens"] = parse_int(last_inference.get("cumulative_tokens"), 0)
        result["prompt_tokens"] = parse_int(last_inference.get("prompt_tokens"), 0)
        result["completion_tokens"] = parse_int(last_inference.get("completion_tokens"), 0)
        result["api_calls"] = parse_int(last_inference.get("api_calls"), 0)
        result["api_errors"] = parse_int(last_inference.get("api_errors"), 0)
        result["progress"] = last_inference.get("progress", "0/0")

    if last_entry:
        entry_type = last_entry.get("type", "")
        if entry_type == "start":
            result["status"] = "running"
            result["budget_limit"] = last_entry.get("total_budget_limit") or last_entry.get("budget_limit", 0)
            result["model"] = last_entry.get("model", "")
        elif entry_type == "inference":
            result["status"] = "running"
        elif entry_type == "finish":
            result["status"] = "finished"
            result["results"] = last_entry.get("results", {})
            result["token_summary"] = last_entry.get("token_summary", {})

    if start_entry:
        result["budget_limit"] = start_entry.get("total_budget_limit") or start_entry.get("budget_limit", 0)
        result["per_eval_budget_limit"] = start_entry.get("budget_limit", 0)
        result["total_budget_limit"] = start_entry.get("total_budget_limit", 0)
    return result


def status_mix(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def select_best_result(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None

    def rank(row: dict[str, Any]) -> tuple[float, float]:
        validation = parse_float(row.get("validation_fitness"), float("-inf"))
        search = parse_float(row.get("search_fitness"), float("-inf"))
        return (
            validation if validation is not None else float("-inf"),
            search if search is not None else float("-inf"),
        )

    return sorted(rows, key=rank, reverse=True)[0]


def build_run_summary_projection(
    *,
    run_id: str,
    manifest: dict[str, Any],
    data_summary: dict[str, Any],
    results: list[dict[str, Any]],
    iterations: list[dict[str, Any]],
) -> dict[str, Any]:
    best = select_best_result(results) or {}
    latest_iteration = iterations[-1] if iterations else {}
    accepted_count = sum(1 for row in results if row.get("status") == "accepted")
    headline_parts = [
        f"run={run_id}",
        f"status={manifest.get('status', 'unknown')}",
        f"records={data_summary.get('summary', {}).get('num_records', data_summary.get('num_records', 0))}",
        f"iterations={len(iterations)}",
        f"accepted={accepted_count}",
    ]
    best_validation = parse_float(best.get("validation_fitness"), None)
    if best_validation is not None:
        headline_parts.append(f"best_validation={best_validation:.4f}")
    return {
        "run_id": run_id,
        "status": manifest.get("status", "unknown"),
        "headline": " | ".join(headline_parts),
        "data_summary": data_summary.get("summary") if isinstance(data_summary.get("summary"), dict) else data_summary,
        "result_counts": status_mix(results),
        "best_result": {
            "commit": best.get("commit", ""),
            "status": best.get("status", ""),
            "validation_fitness": parse_float(best.get("validation_fitness"), None),
            "search_fitness": parse_float(best.get("search_fitness"), None),
            "holdout_fitness": parse_float(best.get("holdout_fitness"), None),
            "validation_pnl": parse_float(best.get("validation_pnl"), None),
            "decision_logic": best.get("decision_logic", ""),
        }
        if best
        else {},
        "latest_iteration": {
            "iteration": latest_iteration.get("iteration"),
            "status": latest_iteration.get("status", ""),
            "change_axis": latest_iteration.get("change_axis", ""),
            "search_intent": latest_iteration.get("search_intent", ""),
            "patch_summary": latest_iteration.get("patch_summary", ""),
            "decision_logic": latest_iteration.get("decision_logic", ""),
        }
        if latest_iteration
        else {},
        "error": manifest.get("error", ""),
    }


def _write_results_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_HEADERS, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "commit": row.get("commit", ""),
                    "description": row.get("description", ""),
                    "status": row.get("status", ""),
                    "search_fitness": row.get("search_fitness", ""),
                    "validation_fitness": row.get("validation_fitness", ""),
                    "holdout_fitness": row.get("holdout_fitness", ""),
                    "validation_pnl": row.get("validation_pnl", ""),
                    "tokens": row.get("tokens", 0),
                    "decision_logic": row.get("decision_logic", ""),
                }
            )


def _projection_from_events(run_id: str, events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    results: list[dict[str, Any]] = []
    iterations: list[dict[str, Any]] = []
    orchestrator = default_orchestrator_state("idle")
    tokens = {
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "api_calls": 0,
        "api_errors": 0,
        "progress": "0/0",
        "status": "idle",
    }
    for event in events:
        event_type = event.get("event")
        if event_type == "state_synced":
            orchestrator = normalize_orchestrator_state(event.get("orchestrator"))
        elif event_type == "iteration_recorded":
            detail = event.get("detail")
            result = event.get("result")
            token_snapshot = event.get("token_snapshot")
            if isinstance(detail, dict):
                iterations.append(normalize_iteration_item(detail))
            if isinstance(result, dict) and result:
                results.append(normalize_result_row(result))
            if isinstance(token_snapshot, dict):
                tokens.update(token_snapshot)
                tokens["status"] = "running"
        elif event_type == "run_started":
            start_payload = event.get("stream")
            if isinstance(start_payload, dict):
                tokens.update(tokens_from_live_events([start_payload]))
            tokens["status"] = "running"
        elif event_type == "run_finished":
            finish_payload = event.get("stream")
            if isinstance(finish_payload, dict):
                tokens.update(tokens_from_live_events([finish_payload]))
            tokens.update(event.get("token_snapshot") if isinstance(event.get("token_snapshot"), dict) else {})
            tokens["status"] = "finished"
    return results, iterations, orchestrator, tokens


def _projection_from_legacy(run_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    results = [normalize_result_row(row) for row in load_results_rows(run_id)]
    iterations = [normalize_iteration_item(item) for item in load_iteration_details(run_id)]
    orchestrator = normalize_orchestrator_state(load_orchestrator_state(run_id))
    tokens = tokens_from_live_events(load_live_events(run_id))
    return results, iterations, orchestrator, tokens


def build_run_projection(run_id: str) -> dict[str, Any]:
    manifest = load_manifest(run_id)
    spec = load_run_spec(run_id)
    data_summary = load_data_summary(run_id)
    events = load_runtime_events(run_id)
    if events:
        results, iterations, orchestrator, tokens = _projection_from_events(run_id, events)
        source = "runtime_events"
    else:
        results, iterations, orchestrator, tokens = _projection_from_legacy(run_id)
        source = "legacy_backfill"

    projection = {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "run_id": manifest.get("run_id") or spec.get("run_id") or run_id,
        "runtime_version": spec.get("runtime_version") or spec.get("runtime", {}).get("runtime_version") or "pm-autoresearch.v1",
        "source": source,
        "updated_at": now_iso(),
        "status": manifest.get("status", "created"),
        "results": results,
        "iterations": iterations,
        "orchestrator": orchestrator,
        "tokens": tokens,
        "summary": build_run_summary_projection(
            run_id=run_id,
            manifest=manifest,
            data_summary=data_summary if isinstance(data_summary, dict) else {},
            results=results,
            iterations=iterations,
        ),
    }
    paths = runtime_paths_for_run(run_id)
    write_json(paths["run_projection"], projection)
    write_json(paths["iteration_details"], iterations)
    _write_results_tsv(paths["results_tsv"], results)
    return projection
