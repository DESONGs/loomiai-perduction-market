#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.task_manager import (
    cleanup_expired_runs,
    discover_workers,
    heartbeat_worker,
    run_claimed_task,
    start_next_queued_run,
)


def main() -> None:
    worker_id = os.environ.get("WORKER_ID", "").strip() or f"worker-{os.getpid()}"
    source_dir = os.environ.get("SOURCE_AUTORESEARCH_DIR", "").strip()
    poll_seconds = max(1, int(os.environ.get("WORKER_POLL_SECONDS", "5") or "5"))

    while True:
        current_workers = discover_workers()
        current_run_id = ""
        for item in current_workers:
            if item.get("worker_id") == worker_id:
                current_run_id = str(item.get("current_run_id", "") or "")
                break
        heartbeat_worker(worker_id, status="idle", current_run_id=current_run_id)
        cleanup_expired_runs(force=False)
        claimed_run_id = start_next_queued_run(worker_id)
        if not claimed_run_id:
            time.sleep(poll_seconds)
            continue
        heartbeat_worker(worker_id, status="running", current_run_id=claimed_run_id)
        run_claimed_task(claimed_run_id, worker_id=worker_id, source_dir=source_dir)
        heartbeat_worker(worker_id, status="idle", current_run_id="")


if __name__ == "__main__":
    main()
