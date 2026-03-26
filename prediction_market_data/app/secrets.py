from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.schemas import ALLOWED_RUNTIME_ENV_VARS


def _load_raw_secret_sources() -> dict[str, Any]:
    raw = os.environ.get("TASK_SECRET_SOURCES_JSON", "").strip()
    file_path = os.environ.get("TASK_SECRET_SOURCES_FILE", "").strip()
    if raw:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("TASK_SECRET_SOURCES_JSON must be a JSON object")
        return payload
    if file_path:
        payload = json.loads(Path(file_path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("TASK_SECRET_SOURCES_FILE must contain a JSON object")
        return payload
    return {}


def load_secret_sources() -> dict[str, dict[str, str]]:
    payload = _load_raw_secret_sources()
    normalized: dict[str, dict[str, str]] = {}
    for ref_name, config in payload.items():
        if not isinstance(ref_name, str) or not ref_name or not isinstance(config, dict):
            continue
        provider = str(config.get("provider", "env") or "env")
        inject_as = str(config.get("inject_as", "") or "").strip()
        if inject_as not in ALLOWED_RUNTIME_ENV_VARS:
            continue
        record = {
            "provider": provider,
            "inject_as": inject_as,
            "description": str(config.get("description", "") or ""),
        }
        if provider == "env":
            env_var = str(config.get("env_var", "") or "").strip()
            if not env_var:
                continue
            record["env_var"] = env_var
        elif provider == "file":
            path = str(config.get("path", "") or "").strip()
            if not path:
                continue
            record["path"] = path
        else:
            continue
        normalized[ref_name] = record
    return normalized


def resolve_secret_refs(secret_refs: list[str]) -> dict[str, Any]:
    sources = load_secret_sources()
    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    unsupported: list[str] = []
    values: dict[str, str] = {}

    for ref_name in secret_refs:
        source = sources.get(ref_name)
        if not source:
            missing.append(ref_name)
            continue
        provider = source["provider"]
        inject_as = source["inject_as"]
        if provider == "env":
            env_var = source["env_var"]
            secret_value = os.environ.get(env_var, "")
            if not secret_value:
                missing.append(ref_name)
                continue
            values[inject_as] = secret_value
            resolved.append(
                {
                    "ref": ref_name,
                    "provider": provider,
                    "inject_as": inject_as,
                    "source": env_var,
                    "resolved": True,
                }
            )
            continue
        if provider == "file":
            path = Path(source["path"])
            if not path.exists():
                missing.append(ref_name)
                continue
            secret_value = path.read_text(encoding="utf-8").strip()
            if not secret_value:
                missing.append(ref_name)
                continue
            values[inject_as] = secret_value
            resolved.append(
                {
                    "ref": ref_name,
                    "provider": provider,
                    "inject_as": inject_as,
                    "source": str(path),
                    "resolved": True,
                }
            )
            continue
        unsupported.append(ref_name)

    return {
        "resolved": resolved,
        "missing": missing,
        "unsupported": unsupported,
        "injected_env": values,
        "available_refs": sorted(sources.keys()),
    }


def build_secret_resolution_status(secret_refs: list[str]) -> dict[str, Any]:
    report = resolve_secret_refs(secret_refs)
    return {
        "requested": list(secret_refs),
        "resolved": report["resolved"],
        "missing": report["missing"],
        "unsupported": report["unsupported"],
        "available_refs": report["available_refs"],
        "ok": not report["missing"] and not report["unsupported"],
    }
