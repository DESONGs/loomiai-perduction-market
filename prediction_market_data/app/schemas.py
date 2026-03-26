from __future__ import annotations

import copy
from typing import Any


SUPPORTED_AXES = [
    "CONFIDENCE_THRESHOLD",
    "BET_SIZING",
    "MAX_BET_FRACTION",
    "PROMPT_FACTORS",
]

ALLOWED_RUNTIME_ENV_VARS = [
    "API_BASE_URL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "MOONSHOT_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "MODEL_NAME",
    "SOURCE_AUTORESEARCH_DIR",
]

DEFAULT_RUN_SPEC = {
    "schema_version": "run_spec.v2",
    "runtime_version": "pm-autoresearch.v1",
    "run_name": "prediction-market-iteration",
    "user_id": "local",
    "tenant_id": "default",
    "dataset": {
        "adapter": "auto",
        "input_format": "auto",
    },
    "runtime": {
        "env_refs": [],
        "secret_refs": [],
        "real_execution": False,
        "retention_hours": 168,
        "preserve_run": False,
    },
    "constraints": {
        "max_iterations": 10,
        "eval_timeout": 900,
        "sample_size": 200,
        "per_eval_token_budget": 150000,
        "total_token_budget": 0,
        "max_completion_tokens": 1200,
        "allowed_axes": list(SUPPORTED_AXES),
    },
    "harness_policy": {
        "engine": "strategy_iteration_v2",
        "projection_contract": "run_projection.v1",
        "artifact_contract": "runtime_artifacts.v1",
        "allowed_axes": list(SUPPORTED_AXES),
    },
    "retention_policy": {
        "retention_hours": 168,
        "preserve_run": False,
    },
}


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def normalize_run_spec(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = merge_dict(DEFAULT_RUN_SPEC, payload)
    dataset_override = payload.get("dataset") if isinstance(payload.get("dataset"), dict) else None
    data_override = payload.get("data") if isinstance(payload.get("data"), dict) else None
    if dataset_override and data_override:
        normalized["dataset"] = merge_dict(normalized["dataset"], merge_dict(data_override, dataset_override))
    elif dataset_override:
        normalized["dataset"] = merge_dict(normalized["dataset"], dataset_override)
    elif data_override:
        normalized["dataset"] = merge_dict(normalized["dataset"], data_override)

    retention_override = payload.get("retention_policy") if isinstance(payload.get("retention_policy"), dict) else None
    runtime_override = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else None
    if retention_override:
        normalized["retention_policy"] = merge_dict(normalized["retention_policy"], retention_override)
    if runtime_override:
        if "retention_hours" in runtime_override:
            normalized["retention_policy"]["retention_hours"] = runtime_override["retention_hours"]
        if "preserve_run" in runtime_override:
            normalized["retention_policy"]["preserve_run"] = runtime_override["preserve_run"]

    normalized["runtime"]["retention_hours"] = normalized["retention_policy"]["retention_hours"]
    normalized["runtime"]["preserve_run"] = normalized["retention_policy"]["preserve_run"]
    normalized["harness_policy"]["allowed_axes"] = list(normalized["constraints"]["allowed_axes"])
    normalized["data"] = copy.deepcopy(normalized["dataset"])
    return normalized


def build_run_spec(override: dict[str, Any] | None = None) -> dict[str, Any]:
    if not override:
        return normalize_run_spec({})
    return normalize_run_spec(override)


def _require_type(errors: list[str], field: str, value: Any, expected_type: type) -> None:
    if not isinstance(value, expected_type):
        errors.append(f"{field} must be {expected_type.__name__}")


def validate_run_spec(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = build_run_spec(payload)
    errors: list[str] = []
    constraints = normalized.get("constraints", {})
    dataset = normalized.get("dataset", {})
    runtime = normalized.get("runtime", {})
    harness_policy = normalized.get("harness_policy", {})
    retention_policy = normalized.get("retention_policy", {})

    _require_type(errors, "schema_version", normalized.get("schema_version"), str)
    _require_type(errors, "runtime_version", normalized.get("runtime_version"), str)
    _require_type(errors, "run_name", normalized.get("run_name"), str)
    _require_type(errors, "user_id", normalized.get("user_id"), str)
    _require_type(errors, "tenant_id", normalized.get("tenant_id"), str)
    _require_type(errors, "dataset", dataset, dict)
    _require_type(errors, "runtime", runtime, dict)
    _require_type(errors, "constraints", constraints, dict)
    _require_type(errors, "harness_policy", harness_policy, dict)
    _require_type(errors, "retention_policy", retention_policy, dict)

    int_fields = {
        "max_iterations": (1, 200),
        "eval_timeout": (60, 7200),
        "sample_size": (1, 200),
        "per_eval_token_budget": (1, 5_000_000),
        "total_token_budget": (0, 50_000_000),
        "max_completion_tokens": (1, 32_000),
    }
    for field, (lower, upper) in int_fields.items():
        value = constraints.get(field)
        if not isinstance(value, int):
            errors.append(f"constraints.{field} must be int")
            continue
        if value < lower or value > upper:
            errors.append(f"constraints.{field} must be between {lower} and {upper}")

    allowed_axes = constraints.get("allowed_axes", [])
    if not isinstance(allowed_axes, list) or not allowed_axes or any(not isinstance(item, str) for item in allowed_axes):
        errors.append("constraints.allowed_axes must be a non-empty list[str]")
    else:
        invalid_axes = [axis for axis in allowed_axes if axis not in SUPPORTED_AXES]
        if invalid_axes:
            errors.append(f"constraints.allowed_axes contains unsupported axes: {invalid_axes}")

    adapter = dataset.get("adapter", "auto")
    input_format = dataset.get("input_format", "auto")
    if not isinstance(adapter, str):
        errors.append("dataset.adapter must be str")
    if not isinstance(input_format, str):
        errors.append("dataset.input_format must be str")

    env_refs = runtime.get("env_refs", [])
    if not isinstance(env_refs, list) or any(not isinstance(item, str) or not item.strip() for item in env_refs):
        errors.append("runtime.env_refs must be list[str]")
    else:
        invalid_env_refs = [item for item in env_refs if item not in ALLOWED_RUNTIME_ENV_VARS]
        if invalid_env_refs:
            errors.append(f"runtime.env_refs contains unsupported env vars: {invalid_env_refs}")

    secret_refs = runtime.get("secret_refs", [])
    if not isinstance(secret_refs, list) or any(not isinstance(item, str) or not item.strip() for item in secret_refs):
        errors.append("runtime.secret_refs must be list[str]")

    real_execution = runtime.get("real_execution", False)
    if not isinstance(real_execution, bool):
        errors.append("runtime.real_execution must be bool")

    retention_hours = retention_policy.get("retention_hours", 168)
    if not isinstance(retention_hours, int):
        errors.append("retention_policy.retention_hours must be int")
    elif retention_hours < 1 or retention_hours > 24 * 365:
        errors.append("retention_policy.retention_hours must be between 1 and 8760")

    preserve_run = retention_policy.get("preserve_run", False)
    if not isinstance(preserve_run, bool):
        errors.append("retention_policy.preserve_run must be bool")

    policy_axes = harness_policy.get("allowed_axes", [])
    if policy_axes != allowed_axes:
        errors.append("harness_policy.allowed_axes must match constraints.allowed_axes")
    raw_constraints = payload.get("constraints") if isinstance(payload.get("constraints"), dict) else {}
    raw_harness_policy = payload.get("harness_policy") if isinstance(payload.get("harness_policy"), dict) else {}
    if raw_constraints.get("allowed_axes") and raw_harness_policy.get("allowed_axes"):
        if list(raw_constraints["allowed_axes"]) != list(raw_harness_policy["allowed_axes"]):
            errors.append("input harness_policy.allowed_axes does not match input constraints.allowed_axes")

    if errors:
        raise ValueError("invalid run spec: " + "; ".join(errors))
    return normalized
