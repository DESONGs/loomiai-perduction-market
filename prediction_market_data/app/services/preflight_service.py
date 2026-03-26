from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.adapters import adapt_dataset
from app.model_probe import probe_external_model
from app.schemas import ALLOWED_RUNTIME_ENV_VARS
from app.secrets import (
    build_secret_resolution_status,
    load_secret_sources,
    resolve_secret_refs,
)
from app.services.task_submission_service import build_run_spec_from_form, stage_upload
from app.task_manager import discover_workers, runtime_limits


def build_runtime_readiness(
    *,
    root_dir: Path,
    runs_dir: Path,
    source_autoresearch_dir: str,
    auth_enabled: bool,
) -> dict[str, Any]:
    required_paths = {
        "runs_dir": runs_dir,
        "source_autoresearch_dir": Path(source_autoresearch_dir),
        "strategy_experiment": root_dir / "demo" / "strategy_experiment.py",
        "codex_strategy_worker": root_dir / "demo" / "codex_strategy_worker.py",
    }
    path_status = {
        name: {
            "path": str(path),
            "exists": path.exists(),
            "writable": os.access(path, os.W_OK) if path.exists() else False,
        }
        for name, path in required_paths.items()
    }
    env_status = {name: bool(os.environ.get(name)) for name in ALLOWED_RUNTIME_ENV_VARS}
    secret_sources = load_secret_sources()
    model_key_ready = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("MOONSHOT_API_KEY"))
    return {
        "ok": all(item["exists"] for item in path_status.values()),
        "paths": path_status,
        "env": env_status,
        "secret_sources": {"count": len(secret_sources), "refs": sorted(secret_sources.keys())},
        "runtime_limits": runtime_limits(),
        "workers": discover_workers(),
        "auth_enabled": auth_enabled,
        "model_key_ready": model_key_ready,
    }


def build_task_preflight(
    *,
    form: Any,
    files: Any,
    auth: dict[str, Any] | None,
    upload_staging_dir: Path,
    source_autoresearch_dir: str,
) -> dict[str, Any]:
    spec = build_run_spec_from_form(form, auth)
    upload = files.get("dataset")
    dataset_report: dict[str, object] = {"ok": False, "error": "dataset file is required"}
    if upload is not None and upload.filename:
        staged_path = stage_upload(upload, upload_staging_dir)
        try:
            records, adapter_report = adapt_dataset(staged_path)
            dataset_report = {
                "ok": True,
                "adapter": adapter_report["adapter"],
                "input_format": adapter_report["input_format"],
                "summary": adapter_report["summary"],
                "num_records": len(records),
            }
        finally:
            staged_path.unlink(missing_ok=True)

    requested_env_refs = list(spec.get("runtime", {}).get("env_refs", []))
    resolved_env_refs = {name: bool(os.environ.get(name)) for name in requested_env_refs}
    requested_secret_refs = list(spec.get("runtime", {}).get("secret_refs", []))
    secret_status = build_secret_resolution_status(requested_secret_refs)
    real_execution = bool(spec.get("runtime", {}).get("real_execution", False)) or (
        (form.get("real_execution") or "").strip().lower() in {"1", "true", "yes", "on"}
    )
    probe_model = (form.get("probe_model") or "").strip().lower() in {"1", "true", "yes", "on"}
    model_key_present = any(
        resolved_env_refs.get(name, False) for name in ("OPENAI_API_KEY", "MOONSHOT_API_KEY")
    ) or any(
        item.get("inject_as") in {"OPENAI_API_KEY", "MOONSHOT_API_KEY"} for item in secret_status["resolved"]
    ) or (
        not requested_env_refs
        and not requested_secret_refs
        and bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("MOONSHOT_API_KEY"))
    )

    checks = {
        "run_spec_ok": True,
        "dataset_ok": bool(dataset_report.get("ok")),
        "source_ready": Path(source_autoresearch_dir).exists(),
        "env_refs_resolved": all(resolved_env_refs.values()) if requested_env_refs else True,
        "secret_refs_resolved": bool(secret_status["ok"]),
        "model_key_ready": model_key_present,
    }
    if real_execution and not checks["model_key_ready"]:
        checks["run_spec_ok"] = False

    probe = None
    if probe_model:
        launch_env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONUNBUFFERED": "1",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        }
        for name in requested_env_refs:
            if os.environ.get(name):
                launch_env[name] = os.environ[name]
        secret_report = resolve_secret_refs(requested_secret_refs)
        launch_env.update(secret_report["injected_env"])
        probe = probe_external_model(launch_env, source_dir=source_autoresearch_dir)
        checks["model_probe_ok"] = bool(probe.get("ok"))

    ok = (
        all(checks.values())
        if real_execution or probe_model
        else checks["run_spec_ok"]
        and checks["dataset_ok"]
        and checks["source_ready"]
        and checks["env_refs_resolved"]
        and checks["secret_refs_resolved"]
    )
    return {
        "ok": ok,
        "checks": checks,
        "dataset": dataset_report,
        "requested_env_refs": requested_env_refs,
        "resolved_env_refs": resolved_env_refs,
        "requested_secret_refs": requested_secret_refs,
        "secret_refs": secret_status,
        "real_execution": real_execution,
        "probe_model": probe_model,
        "probe": probe,
        "run_spec": spec,
    }
