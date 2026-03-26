from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.adapters import adapt_dataset
from app.common import RUNS_DIR, atomic_write_json, build_run_id, ensure_dir, now_iso
from app.schemas import validate_run_spec


def run_paths(run_id: str) -> dict[str, Path]:
    root = RUNS_DIR / run_id
    return {
        "root": root,
        "input": root / "input",
        "data": root / "data",
        "runtime": root / "runtime",
        "logs": root / "logs",
        "artifacts": root / "artifacts",
        "manifest": root / "run_manifest.json",
        "spec": root / "run_spec.json",
        "data_summary": root / "data" / "summary.json",
        "canonical_eval": root / "data" / "eval_markets.json",
        "launcher_log": root / "logs" / "launcher.log",
        "runtime_config": root / "runtime" / "runtime_config.json",
    }


def create_run(
    *,
    input_path: Path,
    run_spec_payload: dict[str, Any],
    run_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Path]]:
    run_spec = validate_run_spec(run_spec_payload)
    resolved_run_id = run_id or build_run_id(str(run_spec.get("run_name", "")))
    paths = run_paths(resolved_run_id)
    if paths["root"].exists():
        raise FileExistsError(f"run already exists: {resolved_run_id}")

    for key in ("root", "input", "data", "runtime", "logs", "artifacts"):
        ensure_dir(paths[key])

    copied_input = paths["input"] / f"upload{input_path.suffix.lower()}"
    shutil.copy2(input_path, copied_input)
    records, adapter_report = adapt_dataset(copied_input)
    atomic_write_json(paths["canonical_eval"], records)
    atomic_write_json(paths["data_summary"], adapter_report)

    run_spec["run_id"] = resolved_run_id
    run_spec["created_at"] = now_iso()
    run_spec["data"]["input_path"] = str(copied_input)
    run_spec["data"]["canonical_eval_path"] = str(paths["canonical_eval"])
    run_spec["data"]["adapter"] = adapter_report["adapter"]
    run_spec["data"]["input_format"] = adapter_report["input_format"]
    run_spec["data"]["summary"] = adapter_report["summary"]
    atomic_write_json(paths["spec"], run_spec)
    atomic_write_json(
        paths["runtime_config"],
        {
            "env_refs": run_spec.get("runtime", {}).get("env_refs", []),
            "secret_refs": run_spec.get("runtime", {}).get("secret_refs", []),
            "real_execution": run_spec.get("runtime", {}).get("real_execution", False),
            "retention_hours": run_spec.get("runtime", {}).get("retention_hours", 168),
            "preserve_run": run_spec.get("runtime", {}).get("preserve_run", False),
            "resolved_at": run_spec["created_at"],
        },
    )

    manifest = {
        "run_id": resolved_run_id,
        "run_name": run_spec.get("run_name", ""),
        "user_id": run_spec.get("user_id", ""),
        "tenant_id": run_spec.get("tenant_id", ""),
        "status": "created",
        "created_at": run_spec["created_at"],
        "started_at": "",
        "finished_at": "",
        "error": "",
        "worker_id": "",
        "pid": 0,
        "schema_version": run_spec.get("schema_version", ""),
        "runtime_version": run_spec.get("runtime_version", ""),
        "paths": {
            "root": str(paths["root"]),
            "runtime": str(paths["runtime"]),
            "launcher_log": str(paths["launcher_log"]),
        },
    }
    atomic_write_json(paths["manifest"], manifest)
    return manifest, paths


def update_manifest(paths: dict[str, Path], updates: dict[str, Any]) -> dict[str, Any]:
    current = {}
    if paths["manifest"].exists():
        current = __import__("json").loads(paths["manifest"].read_text(encoding="utf-8"))
    current.update(updates)
    atomic_write_json(paths["manifest"], current)
    return current
