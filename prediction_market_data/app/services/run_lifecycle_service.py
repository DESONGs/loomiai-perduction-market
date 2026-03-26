from __future__ import annotations

from typing import Any

from app.common import now_iso, read_json
from app.repositories.run_repository import append_jsonl, build_runtime_event, runtime_paths_for_run
from app.runs import run_paths, update_manifest

LIFECYCLE_KEYS = {
    "run_id",
    "run_name",
    "user_id",
    "tenant_id",
    "status",
    "created_at",
    "started_at",
    "finished_at",
    "error",
    "worker_id",
    "pid",
    "paths",
    "schema_version",
    "runtime_version",
}


def _write_lifecycle_event(run_id: str, event_type: str, manifest: dict[str, Any]) -> None:
    paths = runtime_paths_for_run(run_id)
    append_jsonl(
        paths["runtime_events"],
        build_runtime_event(
            event_type,
            manifest_status=manifest.get("status", ""),
            manifest={
                key: manifest.get(key)
                for key in (
                    "run_id",
                    "status",
                    "created_at",
                    "started_at",
                    "finished_at",
                    "error",
                    "worker_id",
                    "pid",
                )
            },
        ),
    )


def update_run_lifecycle(run_id: str, updates: dict[str, Any], *, event_type: str | None = None) -> dict[str, Any]:
    paths = run_paths(run_id)
    current = read_json(paths["manifest"], {})
    payload = {key: current.get(key) for key in current.keys() if key in LIFECYCLE_KEYS}
    payload.update(updates)
    manifest = update_manifest(paths, payload)
    if event_type:
        _write_lifecycle_event(run_id, event_type, manifest)
    return manifest


def mark_run_queued(run_id: str) -> dict[str, Any]:
    return update_run_lifecycle(
        run_id,
        {"status": "queued", "error": "", "pid": 0, "worker_id": ""},
        event_type="run_queued",
    )


def mark_run_running(run_id: str, *, pid: int = 0, worker_id: str = "", started_at: str | None = None) -> dict[str, Any]:
    updates = {"status": "running", "pid": pid, "error": ""}
    if worker_id:
        updates["worker_id"] = worker_id
    if started_at or not read_json(run_paths(run_id)["manifest"], {}).get("started_at"):
        updates["started_at"] = started_at or now_iso()
        updates["finished_at"] = ""
    return update_run_lifecycle(run_id, updates, event_type="run_started")


def mark_run_finished(run_id: str, *, worker_id: str = "") -> dict[str, Any]:
    updates = {"status": "finished", "finished_at": now_iso(), "pid": 0, "error": ""}
    if worker_id:
        updates["worker_id"] = worker_id
    return update_run_lifecycle(run_id, updates, event_type="run_finished")


def mark_run_failed(run_id: str, *, error: str, worker_id: str = "") -> dict[str, Any]:
    updates = {"status": "failed", "finished_at": now_iso(), "pid": 0, "error": error[:1000]}
    if worker_id:
        updates["worker_id"] = worker_id
    return update_run_lifecycle(run_id, updates, event_type="run_failed")


def mark_run_stopped(run_id: str, *, worker_id: str = "") -> dict[str, Any]:
    updates = {"status": "stopped", "finished_at": now_iso(), "pid": 0, "error": "stopped by user"}
    if worker_id:
        updates["worker_id"] = worker_id
    return update_run_lifecycle(run_id, updates, event_type="run_stopped")


def attach_worker(run_id: str, worker_id: str) -> dict[str, Any]:
    return update_run_lifecycle(run_id, {"worker_id": worker_id}, event_type="run_claimed")
