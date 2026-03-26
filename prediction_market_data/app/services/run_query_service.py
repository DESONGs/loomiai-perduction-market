from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator

from app.projections.run_projection import build_run_projection, default_orchestrator_state
from app.repositories.run_repository import (
    list_artifacts_for_run,
    list_run_roots,
    load_log_tail,
    load_manifest,
    load_projection,
    load_run_spec,
    projection_is_stale,
    run_exists,
    runtime_paths_for_run,
    safe_download_path,
)


def ensure_projection(run_id: str) -> dict[str, Any]:
    if projection_is_stale(run_id):
        return build_run_projection(run_id)
    projection = load_projection(run_id)
    return projection if projection else build_run_projection(run_id)


def build_run_detail(run_id: str) -> dict[str, Any]:
    paths = runtime_paths_for_run(run_id)
    manifest = load_manifest(run_id)
    spec = load_run_spec(run_id)
    projection = ensure_projection(run_id)
    summary = projection.get("summary", {}).get("data_summary", {})
    dataset = spec.get("dataset") or spec.get("data") or {}
    runtime = spec.get("runtime") or {}
    constraints = spec.get("constraints") or {}
    retention_policy = spec.get("retention_policy") or {
        "retention_hours": runtime.get("retention_hours", 168),
        "preserve_run": runtime.get("preserve_run", False),
    }
    return {
        "run_id": manifest.get("run_id") or spec.get("run_id") or run_id,
        "run_name": manifest.get("run_name") or spec.get("run_name") or run_id,
        "status": manifest.get("status") or "created",
        "created_at": manifest.get("created_at") or spec.get("created_at") or "",
        "started_at": manifest.get("started_at", ""),
        "finished_at": manifest.get("finished_at", ""),
        "updated_at": projection.get("updated_at")
        or manifest.get("finished_at")
        or manifest.get("started_at")
        or manifest.get("created_at")
        or "",
        "user_id": manifest.get("user_id") or spec.get("user_id") or "",
        "tenant_id": manifest.get("tenant_id") or spec.get("tenant_id") or "default",
        "error": manifest.get("error", ""),
        "pid": manifest.get("pid", 0),
        "worker_id": manifest.get("worker_id", ""),
        "dataset": dataset,
        "data": dataset,
        "runtime": runtime,
        "constraints": constraints,
        "harness_policy": spec.get("harness_policy") or {},
        "retention_policy": retention_policy,
        "summary": summary if isinstance(summary, dict) else {},
        "runtime_dir": str(paths["runtime"]),
        "schema_version": spec.get("schema_version", ""),
        "runtime_version": spec.get("runtime_version", ""),
        "paths": manifest.get("paths") or {"root": str(paths["root"]), "runtime": str(paths["runtime"])},
    }


def list_runs() -> list[dict[str, Any]]:
    return [build_run_detail(path.name) for path in list_run_roots()]


def find_run(run_id: str) -> dict[str, Any] | None:
    if not run_exists(run_id):
        return None
    return build_run_detail(run_id)


def get_results(run_id: str) -> list[dict[str, Any]]:
    return ensure_projection(run_id).get("results", [])


def get_iterations(run_id: str) -> list[dict[str, Any]]:
    return ensure_projection(run_id).get("iterations", [])


def get_orchestrator(run_id: str) -> dict[str, Any]:
    payload = ensure_projection(run_id).get("orchestrator")
    return payload if isinstance(payload, dict) and payload else default_orchestrator_state("idle")


def get_tokens(run_id: str) -> dict[str, Any]:
    payload = ensure_projection(run_id).get("tokens", {})
    return payload if isinstance(payload, dict) else {}


def get_run_summary(run_id: str) -> dict[str, Any]:
    projection = ensure_projection(run_id)
    detail = find_run(run_id)
    if not detail:
        return {"error": "run not found"}
    summary = projection.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    summary = dict(summary)
    summary["constraints"] = detail.get("constraints", {})
    summary["artifacts_root"] = detail.get("paths", {}).get("root", "")
    summary["error"] = detail.get("error", "")
    return summary


def get_log(run_id: str) -> dict[str, Any]:
    return {"lines": load_log_tail(run_id)}


def get_artifacts(run_id: str) -> list[dict[str, object]]:
    return list_artifacts_for_run(run_id)


def resolve_download_path(run_id: str, relative_path: str) -> Path:
    return safe_download_path(run_id, relative_path)


def stream_live_events(run_id: str) -> Iterator[str]:
    live_log = runtime_paths_for_run(run_id)["live_log"]
    last_pos = 0
    if live_log.exists():
        with live_log.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield f"data: {line}\n\n"
            last_pos = handle.tell()

    while True:
        try:
            if not live_log.exists():
                time.sleep(1)
                continue
            with live_log.open("r", encoding="utf-8") as handle:
                handle.seek(0, 2)
                current_size = handle.tell()
                if current_size < last_pos:
                    last_pos = 0
                    yield f"data: {json.dumps({'type': 'reset'})}\n\n"
                handle.seek(last_pos)
                new_lines = handle.readlines()
                if new_lines:
                    for line in new_lines:
                        line = line.strip()
                        if line:
                            yield f"data: {line}\n\n"
                    last_pos = handle.tell()
                else:
                    time.sleep(0.5)
        except Exception:
            time.sleep(1)
