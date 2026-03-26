from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.common import ROOT_DIR, RUNS_DIR, now_iso, parse_iso, read_json
from app.repositories.run_repository import load_run_spec
from app.runs import run_paths, update_manifest
from app.services.run_lifecycle_service import (
    attach_worker,
    mark_run_failed,
    mark_run_finished,
    mark_run_queued,
    mark_run_running,
    mark_run_stopped,
)


AUDIT_LOG = RUNS_DIR / "audit.log"
SUPPORTED_UPLOAD_SUFFIXES = {".json", ".csv"}
FINAL_STATUSES = {"finished", "failed", "stopped"}
WORKERS_DIR = RUNS_DIR / "_workers"


def audit_event(event: str, **payload: Any) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": now_iso(), "event": event, **payload}
    with AUDIT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def runtime_limits() -> dict[str, int]:
    return {
        "max_task_memory_mb": parse_env_int("MAX_TASK_MEMORY_MB", 4096),
        "max_task_cpu_seconds": parse_env_int("MAX_TASK_CPU_SECONDS", 7200),
    }


def build_preexec_limit_fn():
    limits = runtime_limits()
    if os.name != "posix":
        return None

    def _apply_limits():
        try:
            import resource

            memory_bytes = max(128, limits["max_task_memory_mb"]) * 1024 * 1024
            cpu_seconds = max(60, limits["max_task_cpu_seconds"])
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        except Exception:
            return None
        return None

    return _apply_limits


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def list_run_manifests() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not RUNS_DIR.exists():
        return items
    for path in RUNS_DIR.iterdir():
        if not path.is_dir() or path.name.startswith("_"):
            continue
        manifest_path = path / "run_manifest.json"
        if not manifest_path.exists():
            continue
        manifest = read_json(manifest_path, {})
        if not isinstance(manifest, dict):
            continue
        items.append(manifest)
    return sorted(items, key=lambda item: (item.get("created_at", ""), item.get("run_id", "")))


def queue_depth() -> int:
    return sum(1 for item in list_run_manifests() if item.get("status") == "queued")


def runs_for_user(user_id: str) -> int:
    return sum(1 for item in list_run_manifests() if item.get("user_id") == user_id)


def worker_heartbeat_path(worker_id: str) -> Path:
    return WORKERS_DIR / f"{worker_id}.json"


def run_lease_path(run_id: str) -> Path:
    return run_paths(run_id)["runtime"] / "worker_lease.json"


def heartbeat_worker(worker_id: str, status: str = "idle", current_run_id: str = "") -> dict[str, Any]:
    WORKERS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "worker_id": worker_id,
        "status": status,
        "current_run_id": current_run_id,
        "updated_at": now_iso(),
        "pid": os.getpid(),
    }
    path = worker_heartbeat_path(worker_id)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return payload


def discover_workers() -> list[dict[str, Any]]:
    WORKERS_DIR.mkdir(parents=True, exist_ok=True)
    stale_seconds = parse_env_int("WORKER_STALE_SECONDS", 30)
    now_dt = datetime.now(timezone.utc)
    workers: list[dict[str, Any]] = []
    for path in sorted(WORKERS_DIR.glob("*.json")):
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        updated_at = parse_iso(str(payload.get("updated_at", "")))
        stale = not updated_at or (now_dt - updated_at > timedelta(seconds=stale_seconds))
        payload["stale"] = stale
        workers.append(payload)
    return workers


def cleanup_expired_runs(force: bool = False) -> list[dict[str, Any]]:
    deleted: list[dict[str, Any]] = []
    manifests = list_run_manifests()
    default_retention_hours = parse_env_int("RUN_TTL_HOURS", 168)
    max_completed_runs = parse_env_int("MAX_COMPLETED_RUNS", 100)

    completed = [item for item in manifests if str(item.get("status", "")) in FINAL_STATUSES]
    completed_sorted = sorted(
        completed,
        key=lambda item: item.get("finished_at") or item.get("created_at") or "",
        reverse=True,
    )
    keep_run_ids = {item.get("run_id") for item in completed_sorted[:max_completed_runs]}
    now_dt = datetime.now(timezone.utc)

    for manifest in manifests:
        run_id = str(manifest.get("run_id", ""))
        status = str(manifest.get("status", ""))
        if status not in FINAL_STATUSES:
            continue
        spec = load_run_spec(run_id)
        retention_cfg = spec.get("retention_policy") if isinstance(spec.get("retention_policy"), dict) else {}
        runtime_cfg = spec.get("runtime") if isinstance(spec.get("runtime"), dict) else {}
        preserve_run = retention_cfg.get("preserve_run", runtime_cfg.get("preserve_run", False))
        if preserve_run and not force:
            continue

        retention_hours = int(retention_cfg.get("retention_hours") or runtime_cfg.get("retention_hours") or default_retention_hours)
        finished_at = parse_iso(str(manifest.get("finished_at") or manifest.get("created_at") or ""))
        expired = bool(finished_at and now_dt - finished_at >= timedelta(hours=retention_hours))
        over_limit = run_id not in keep_run_ids
        if not force and not expired and not over_limit:
            continue

        root = run_paths(run_id)["root"]
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        deleted.append({"run_id": run_id, "reason": "force" if force else "ttl_or_limit"})
        audit_event("run_cleaned", run_id=run_id, reason="force" if force else "ttl_or_limit")

    return deleted


def _is_stale_lease(payload: dict[str, Any]) -> bool:
    lease_seconds = parse_env_int("WORKER_LEASE_SECONDS", 120)
    claimed_at = parse_iso(str(payload.get("claimed_at", "")))
    if not claimed_at:
        return True
    return datetime.now(timezone.utc) - claimed_at > timedelta(seconds=lease_seconds)


def claim_run(run_id: str, worker_id: str) -> bool:
    lease_path = run_lease_path(run_id)
    lease_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = read_json(run_paths(run_id)["manifest"], {})
    if str(manifest.get("status", "")) != "queued":
        return False
    payload = {
        "run_id": run_id,
        "worker_id": worker_id,
        "claimed_at": now_iso(),
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(str(lease_path), flags)
    except FileExistsError:
        existing = read_json(lease_path, {})
        if isinstance(existing, dict) and _is_stale_lease(existing):
            lease_path.unlink(missing_ok=True)
            return claim_run(run_id, worker_id)
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
    except Exception:
        lease_path.unlink(missing_ok=True)
        raise
    attach_worker(run_id, worker_id)
    audit_event("run_claimed", run_id=run_id, worker_id=worker_id)
    return True


def release_run_claim(run_id: str, worker_id: str = "") -> None:
    lease_path = run_lease_path(run_id)
    if not lease_path.exists():
        return
    payload = read_json(lease_path, {})
    if worker_id and isinstance(payload, dict) and payload.get("worker_id") not in ("", worker_id):
        return
    lease_path.unlink(missing_ok=True)


def start_next_queued_run(worker_id: str) -> str | None:
    for manifest in list_run_manifests():
        if str(manifest.get("status", "")) != "queued":
            continue
        run_id = str(manifest.get("run_id", ""))
        if not run_id:
            continue
        if claim_run(run_id, worker_id):
            return run_id
    return None


def run_claimed_task(run_id: str, worker_id: str, source_dir: str = "") -> dict[str, Any]:
    paths = run_paths(run_id)
    cmd = [sys.executable, "app/run_task.py", "--existing-run-id", run_id]
    if source_dir:
        cmd.extend(["--source-dir", str(Path(source_dir).resolve())])

    paths["launcher_log"].parent.mkdir(parents=True, exist_ok=True)
    with paths["launcher_log"].open("a", encoding="utf-8") as handle:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT_DIR),
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            preexec_fn=build_preexec_limit_fn(),
        )
        mark_run_running(run_id, pid=proc.pid, worker_id=worker_id)
        audit_event("run_started", run_id=run_id, pid=proc.pid, worker_id=worker_id)
        return_code = proc.wait()

    release_run_claim(run_id, worker_id=worker_id)
    manifest = read_json(paths["manifest"], {})
    if str(manifest.get("status", "")) == "running":
        if return_code == 0:
            mark_run_finished(run_id, worker_id=worker_id)
        else:
            mark_run_failed(run_id, worker_id=worker_id, error=f"exit code {return_code}")
    audit_event("run_finished", run_id=run_id, worker_id=worker_id, return_code=return_code)
    return read_json(paths["manifest"], {})


def enqueue_run(run_id: str) -> dict[str, Any]:
    manifest = mark_run_queued(run_id)
    audit_event("run_queued", run_id=run_id, user_id=manifest.get("user_id", ""), tenant_id=manifest.get("tenant_id", ""))
    return manifest


def stop_run(run_id: str) -> dict[str, Any]:
    paths = run_paths(run_id)
    manifest = read_json(paths["manifest"], {})
    if not manifest:
        raise FileNotFoundError(f"run manifest not found: {run_id}")
    pid = int(manifest.get("pid") or 0)
    status = str(manifest.get("status", ""))

    if status == "queued":
        release_run_claim(run_id)
        updated = mark_run_stopped(run_id, worker_id=manifest.get("worker_id", ""))
        audit_event("run_stopped", run_id=run_id, pid=0, status="queued")
        return updated

    if status in FINAL_STATUSES:
        return manifest

    if pid and pid_is_alive(pid):
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            pass
    release_run_claim(run_id)
    updated = mark_run_stopped(run_id, worker_id=manifest.get("worker_id", ""))
    audit_event("run_stopped", run_id=run_id, pid=pid, status=status)
    return updated


def validate_upload_constraints(filename: str, size_bytes: int) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
        raise ValueError("unsupported upload file type; expected .json or .csv")
    max_upload_bytes = parse_env_int("MAX_UPLOAD_BYTES", 25 * 1024 * 1024)
    if size_bytes > max_upload_bytes:
        raise ValueError(f"upload exceeds MAX_UPLOAD_BYTES={max_upload_bytes}")


def enforce_submission_limits(user_id: str) -> None:
    max_queued_runs = parse_env_int("MAX_QUEUED_RUNS", 20)
    max_runs_per_user = parse_env_int("MAX_RUNS_PER_USER", 50)
    if queue_depth() >= max_queued_runs:
        raise ValueError(f"queue is full (MAX_QUEUED_RUNS={max_queued_runs})")
    if runs_for_user(user_id) >= max_runs_per_user:
        raise ValueError(f"user has reached MAX_RUNS_PER_USER={max_runs_per_user}")
