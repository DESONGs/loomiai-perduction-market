from __future__ import annotations

import json
import os
from typing import Any

from app.schemas import ALLOWED_RUNTIME_ENV_VARS


ROLE_DEFAULTS = {
    "viewer": {
        "can_submit": False,
        "can_cleanup": False,
        "can_stop_any_run": False,
        "can_read_all_runs": False,
        "can_probe_model": False,
        "can_manage_workers": False,
    },
    "tenant_operator": {
        "can_submit": True,
        "can_cleanup": False,
        "can_stop_any_run": False,
        "can_read_all_runs": False,
        "can_probe_model": True,
        "can_manage_workers": False,
    },
    "tenant_admin": {
        "can_submit": True,
        "can_cleanup": False,
        "can_stop_any_run": False,
        "can_read_all_runs": True,
        "can_probe_model": True,
        "can_manage_workers": False,
    },
    "platform_admin": {
        "can_submit": True,
        "can_cleanup": True,
        "can_stop_any_run": True,
        "can_read_all_runs": True,
        "can_probe_model": True,
        "can_manage_workers": True,
    },
}


def _merge_role_permissions(roles: list[str]) -> dict[str, bool]:
    permissions = {
        "can_submit": True,
        "can_cleanup": False,
        "can_stop_any_run": False,
        "can_read_all_runs": False,
        "can_probe_model": True,
        "can_manage_workers": False,
    }
    if not roles:
        return permissions
    permissions = {key: False for key in permissions}
    for role in roles:
        defaults = ROLE_DEFAULTS.get(role, {})
        for key, value in defaults.items():
            permissions[key] = permissions.get(key, False) or bool(value)
    return permissions


def load_api_tokens() -> dict[str, dict[str, Any]]:
    raw = os.environ.get("TASK_API_TOKENS_JSON", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid TASK_API_TOKENS_JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("TASK_API_TOKENS_JSON must be a JSON object")

    normalized: dict[str, dict[str, Any]] = {}
    for token, config in payload.items():
        if not isinstance(token, str) or not token:
            continue
        if not isinstance(config, dict):
            continue
        roles = config.get("roles", [])
        if not isinstance(roles, list):
            roles = []
        normalized_roles = [str(item) for item in roles if isinstance(item, str) and item in ROLE_DEFAULTS]
        permissions = _merge_role_permissions(normalized_roles)
        allowed_env_refs = config.get("allowed_env_refs", [])
        if not isinstance(allowed_env_refs, list):
            allowed_env_refs = []
        allowed_secret_refs = config.get("allowed_secret_refs", [])
        if not isinstance(allowed_secret_refs, list):
            allowed_secret_refs = []
        normalized[token] = {
            "user_id": str(config.get("user_id", "") or ""),
            "tenant_id": str(config.get("tenant_id", "default") or "default"),
            "roles": normalized_roles,
            "allowed_env_refs": [item for item in allowed_env_refs if isinstance(item, str) and item in ALLOWED_RUNTIME_ENV_VARS],
            "allowed_secret_refs": [item for item in allowed_secret_refs if isinstance(item, str)],
            "can_cleanup": bool(config.get("can_cleanup", permissions["can_cleanup"])),
            "can_stop_any_run": bool(config.get("can_stop_any_run", permissions["can_stop_any_run"])),
            "can_submit": bool(config.get("can_submit", permissions["can_submit"])),
            "can_read_all_runs": bool(config.get("can_read_all_runs", permissions["can_read_all_runs"])),
            "can_probe_model": bool(config.get("can_probe_model", permissions["can_probe_model"])),
            "can_manage_workers": bool(config.get("can_manage_workers", permissions["can_manage_workers"])),
            "label": str(config.get("label", "") or ""),
        }
    return normalized


def parse_bearer_token(header_value: str) -> str:
    value = str(header_value or "").strip()
    if not value:
        return ""
    prefix = "Bearer "
    if value.startswith(prefix):
        return value[len(prefix):].strip()
    return value


def resolve_auth_context(header_value: str) -> dict[str, Any] | None:
    token = parse_bearer_token(header_value)
    if not token:
        return None
    config = load_api_tokens().get(token)
    if not config:
        return None
    return {"token": token, **config}
