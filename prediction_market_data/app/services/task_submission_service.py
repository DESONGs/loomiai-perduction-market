from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from werkzeug.utils import secure_filename

from app.runs import create_run
from app.schemas import build_run_spec
from app.task_manager import enforce_submission_limits, enqueue_run, validate_upload_constraints


def form_int(form: Any, name: str) -> int | None:
    raw = (form.get(name) or "").strip()
    if not raw:
        return None
    return int(raw)


def parse_requested_allowed_axes(form: Any) -> list[str]:
    values: list[str] = []
    getlist = getattr(form, "getlist", None)
    if callable(getlist):
        for item in form.getlist("allowed_axes"):
            values.extend([part.strip() for part in str(item).split(",") if part.strip()])
    if not values:
        raw = (form.get("allowed_axes") or "").strip()
        if raw:
            values = [item.strip() for item in raw.split(",") if item.strip()]
    return values


def build_run_spec_from_form(form: Any, auth: dict[str, Any] | None = None) -> dict[str, Any]:
    raw_spec = (form.get("run_spec_json") or "").strip()
    if raw_spec:
        payload = json.loads(raw_spec)
        if not isinstance(payload, dict):
            raise ValueError("run_spec_json must be a JSON object")
        return build_run_spec(payload)

    override: dict[str, Any] = {}
    run_name = (form.get("run_name") or "").strip()
    user_id = (form.get("user_id") or "").strip()
    tenant_id = (form.get("tenant_id") or "").strip()
    if auth and auth.get("user_id"):
        user_id = str(auth["user_id"])
    if auth and auth.get("tenant_id"):
        tenant_id = str(auth["tenant_id"])

    if run_name:
        override["run_name"] = run_name
    if user_id:
        override["user_id"] = user_id
    if tenant_id:
        override["tenant_id"] = tenant_id

    constraints: dict[str, object] = {}
    for field in (
        "max_iterations",
        "eval_timeout",
        "sample_size",
        "per_eval_token_budget",
        "total_token_budget",
        "max_completion_tokens",
    ):
        value = form_int(form, field)
        if value is not None:
            constraints[field] = value

    axes = parse_requested_allowed_axes(form)
    if axes:
        constraints["allowed_axes"] = axes
    if constraints:
        override["constraints"] = constraints

    runtime: dict[str, object] = {}
    env_refs = [item.strip() for item in (form.get("env_refs") or "").split(",") if item.strip()]
    if auth and env_refs:
        allowed = set(auth.get("allowed_env_refs", []))
        requested = set(env_refs)
        if not requested.issubset(allowed):
            raise ValueError(f"requested env_refs exceed token allowlist: {sorted(requested - allowed)}")
    if env_refs:
        runtime["env_refs"] = env_refs

    secret_refs = [item.strip() for item in (form.get("secret_refs") or "").split(",") if item.strip()]
    if auth and secret_refs:
        allowed = set(auth.get("allowed_secret_refs", []))
        requested = set(secret_refs)
        if not requested.issubset(allowed):
            raise ValueError(f"requested secret_refs exceed token allowlist: {sorted(requested - allowed)}")
    if secret_refs:
        runtime["secret_refs"] = secret_refs

    real_execution = (form.get("real_execution") or "").strip().lower()
    if real_execution:
        runtime["real_execution"] = real_execution in {"1", "true", "yes", "on"}

    retention_policy: dict[str, object] = {}
    retention_hours = form_int(form, "retention_hours")
    if retention_hours is not None:
        retention_policy["retention_hours"] = retention_hours
        runtime["retention_hours"] = retention_hours
    preserve_run = (form.get("preserve_run") or "").strip().lower()
    if preserve_run:
        retention_policy["preserve_run"] = preserve_run in {"1", "true", "yes", "on"}
        runtime["preserve_run"] = retention_policy["preserve_run"]
    if retention_policy:
        override["retention_policy"] = retention_policy
    if runtime:
        override["runtime"] = runtime
    return build_run_spec(override)


def stage_upload(upload: Any, upload_staging_dir: Path) -> Path:
    filename = secure_filename(upload.filename or "dataset")
    payload = upload.read()
    validate_upload_constraints(filename, len(payload))
    upload_staging_dir.mkdir(parents=True, exist_ok=True)
    temp_path = upload_staging_dir / f"{int(time.time() * 1000)}-{filename}"
    temp_path.write_bytes(payload)
    return temp_path


def create_task(
    *,
    form: Any,
    files: Any,
    auth: dict[str, Any] | None,
    upload_staging_dir: Path,
) -> dict[str, Any]:
    upload = files.get("dataset")
    if upload is None or not upload.filename:
        raise ValueError("dataset file is required")

    user_id = (form.get("user_id") or "local").strip() or "local"
    tenant_id = (form.get("tenant_id") or "default").strip() or "default"
    if auth and auth.get("user_id"):
        user_id = str(auth["user_id"])
    if auth and auth.get("tenant_id"):
        tenant_id = str(auth["tenant_id"])

    enforce_submission_limits(user_id)
    spec = build_run_spec_from_form(form, auth)
    if not spec.get("user_id"):
        spec["user_id"] = user_id
    if not spec.get("tenant_id"):
        spec["tenant_id"] = tenant_id

    staged_path = stage_upload(upload, upload_staging_dir)
    try:
        manifest, _paths = create_run(
            input_path=staged_path,
            run_spec_payload=spec,
            run_id=(form.get("run_id") or "").strip() or None,
        )
    finally:
        staged_path.unlink(missing_ok=True)
    enqueue_run(manifest["run_id"])
    return manifest
