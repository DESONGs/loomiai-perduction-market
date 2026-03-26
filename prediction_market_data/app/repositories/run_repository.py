from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from app.common import RUNS_DIR, now_iso, read_json
from app.runs import run_paths


def runtime_paths_for_run(run_id: str) -> dict[str, Path]:
    paths = run_paths(run_id)
    runtime_dir = paths["runtime"]
    return {
        **paths,
        "runtime_dir": runtime_dir,
        "live_log": runtime_dir / "pm_live.jsonl",
        "results_tsv": runtime_dir / "pm_results.tsv",
        "run_log": runtime_dir / "pm_run.log",
        "orchestrator_state": runtime_dir / "orchestrator_state.json",
        "iteration_details": runtime_dir / "iteration_details.json",
        "runtime_events": runtime_dir / "runtime_events.jsonl",
        "run_projection": runtime_dir / "run_projection.json",
    }


def list_run_roots() -> list[Path]:
    if not RUNS_DIR.exists():
        return []
    return sorted(
        [path for path in RUNS_DIR.iterdir() if path.is_dir() and not path.name.startswith("_")],
        key=lambda path: path.name,
        reverse=True,
    )


def run_exists(run_id: str) -> bool:
    return runtime_paths_for_run(run_id)["root"].exists()


def load_manifest(run_id: str) -> dict[str, Any]:
    return read_json(runtime_paths_for_run(run_id)["manifest"], {})


def load_run_spec(run_id: str) -> dict[str, Any]:
    return read_json(runtime_paths_for_run(run_id)["spec"], {})


def load_data_summary(run_id: str) -> dict[str, Any]:
    payload = read_json(runtime_paths_for_run(run_id)["data_summary"], {})
    return payload if isinstance(payload, dict) else {}


def load_projection(run_id: str) -> dict[str, Any]:
    payload = read_json(runtime_paths_for_run(run_id)["run_projection"], {})
    return payload if isinstance(payload, dict) else {}


def load_orchestrator_state(run_id: str) -> dict[str, Any]:
    payload = read_json(runtime_paths_for_run(run_id)["orchestrator_state"], {})
    return payload if isinstance(payload, dict) else {}


def load_iteration_details(run_id: str) -> list[dict[str, Any]]:
    payload = read_json(runtime_paths_for_run(run_id)["iteration_details"], [])
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def load_results_rows(run_id: str) -> list[dict[str, Any]]:
    path = runtime_paths_for_run(run_id)["results_tsv"]
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            if isinstance(row, dict):
                items.append(row)
    return items


def load_runtime_events(run_id: str) -> list[dict[str, Any]]:
    path = runtime_paths_for_run(run_id)["runtime_events"]
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
    return events


def load_live_events(run_id: str) -> list[dict[str, Any]]:
    path = runtime_paths_for_run(run_id)["live_log"]
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                items.append(payload)
    return items


def load_log_tail(run_id: str, limit: int = 50) -> list[str]:
    path = runtime_paths_for_run(run_id)["run_log"]
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [line.rstrip() for line in handle.readlines()[-limit:]]


def list_artifacts_for_run(run_id: str) -> list[dict[str, object]]:
    paths = runtime_paths_for_run(run_id)
    root = paths["root"]
    files: list[dict[str, object]] = []
    for relative in (
        "run_manifest.json",
        "run_spec.json",
        "data/summary.json",
        "data/eval_markets.json",
        "logs/launcher.log",
        "runtime/runtime_config.json",
        "runtime/runtime_events.jsonl",
        "runtime/run_projection.json",
        "runtime/orchestrator_state.json",
        "runtime/iteration_details.json",
        "runtime/pm_results.tsv",
        "runtime/pm_run.log",
        "runtime/pm_live.jsonl",
    ):
        target = root / relative
        if target.is_file():
            files.append({"path": relative, "size_bytes": target.stat().st_size})

    parent = root / "artifacts"
    if parent.exists():
        for target in sorted(parent.rglob("*")):
            if target.is_file():
                files.append({"path": target.relative_to(root).as_posix(), "size_bytes": target.stat().st_size})
    return files


def safe_download_path(run_id: str, relative_path: str) -> Path:
    root = runtime_paths_for_run(run_id)["root"].resolve()
    target = (root / relative_path).resolve()
    if root not in target.parents and target != root:
        raise FileNotFoundError(relative_path)
    if not target.is_file():
        raise FileNotFoundError(relative_path)
    return target


def projection_is_stale(run_id: str) -> bool:
    paths = runtime_paths_for_run(run_id)
    projection = paths["run_projection"]
    if not projection.exists():
        return True
    projection_mtime = projection.stat().st_mtime
    dependency_keys = ["manifest", "spec", "data_summary"]
    if paths["runtime_events"].exists():
        dependency_keys.append("runtime_events")
    else:
        dependency_keys.extend(["results_tsv", "iteration_details", "orchestrator_state", "live_log"])
    for dependency_key in dependency_keys:
        dependency = paths[dependency_key]
        if dependency.exists() and dependency.stat().st_mtime > projection_mtime:
            return True
    return False


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def build_runtime_event(event_type: str, **payload: Any) -> dict[str, Any]:
    return {"event": event_type, "timestamp": now_iso(), **payload}
