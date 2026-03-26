from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path
from typing import Any

from openai import OpenAI


def load_model_defaults(source_dir: str = "") -> dict[str, Any]:
    pm_config_path = Path(source_dir).resolve() / "pm_config.py" if source_dir else None
    defaults = {
        "api_base_url": "",
        "model_name": "",
        "temperature": 0.0,
        "max_tokens": 8,
    }
    if not pm_config_path or not pm_config_path.exists():
        return defaults

    spec = importlib.util.spec_from_file_location("pm_config_probe", pm_config_path)
    if not spec or not spec.loader:
        return defaults
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    defaults["api_base_url"] = str(getattr(module, "API_BASE_URL", "") or "")
    defaults["model_name"] = str(getattr(module, "MODEL_NAME", "") or "")
    defaults["temperature"] = float(getattr(module, "TEMPERATURE", 0.0) or 0.0)
    defaults["max_tokens"] = int(getattr(module, "MAX_TOKENS", 8) or 8)
    return defaults


def build_probe_config(env: dict[str, str], source_dir: str = "") -> dict[str, Any]:
    defaults = load_model_defaults(source_dir=source_dir)
    api_key = env.get("OPENAI_API_KEY") or env.get("MOONSHOT_API_KEY") or ""
    provider = "openai" if env.get("OPENAI_API_KEY") else "moonshot" if env.get("MOONSHOT_API_KEY") else "unknown"
    api_base_url = env.get("OPENAI_BASE_URL") or env.get("API_BASE_URL") or defaults["api_base_url"]
    model_name = env.get("MODEL_NAME") or defaults["model_name"]
    return {
        "provider": provider,
        "api_key": api_key,
        "api_base_url": api_base_url,
        "model_name": model_name,
        "temperature": defaults["temperature"],
        "max_tokens": defaults["max_tokens"],
    }


def probe_external_model(env: dict[str, str], source_dir: str = "") -> dict[str, Any]:
    config = build_probe_config(env, source_dir=source_dir)
    if not config["api_key"]:
        return {
            "ok": False,
            "error": "model api key is missing",
            "provider": config["provider"],
            "model_name": config["model_name"],
            "api_base_url": config["api_base_url"],
        }
    if not config["model_name"]:
        return {
            "ok": False,
            "error": "model name is missing",
            "provider": config["provider"],
            "model_name": "",
            "api_base_url": config["api_base_url"],
        }

    started = time.time()
    try:
        client = OpenAI(api_key=config["api_key"], base_url=config["api_base_url"] or None)
        response = client.chat.completions.create(
            model=config["model_name"],
            messages=[{"role": "user", "content": "Return exactly OK."}],
            temperature=0,
            max_tokens=min(int(config["max_tokens"] or 8), 8),
        )
        elapsed_ms = int((time.time() - started) * 1000)
        usage = getattr(response, "usage", None)
        content = ""
        if getattr(response, "choices", None):
            message = response.choices[0].message
            content = str(getattr(message, "content", "") or "").strip()
        return {
            "ok": True,
            "provider": config["provider"],
            "model_name": config["model_name"],
            "api_base_url": config["api_base_url"],
            "latency_ms": elapsed_ms,
            "response_preview": content[:80],
            "usage": {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "provider": config["provider"],
            "model_name": config["model_name"],
            "api_base_url": config["api_base_url"],
            "latency_ms": int((time.time() - started) * 1000),
        }
