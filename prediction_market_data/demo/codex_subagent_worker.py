#!/usr/bin/env python3
"""
External Codex worker executor with strict single-file patch output.

Invocation:
  python3 demo/codex_subagent_worker.py <input_json> <output_json>

Input JSON contract:
  {
    "task": {...},
    "prior_contexts": [...],
    "runtime_dir": "...",
    "artifact_dir": "...",
    "managed_target_file": "...",
    "managed_patch_file": "..."
  }

Output JSON contract:
  {
    "summary": "...",
    "deliverables": ["managed_target.patch"],
    "tool_summary": ["write: managed_target.patch"],
    "feedback": [...],
    "next_action": "...",
    "details": [...],
    "artifacts": ["<absolute patch path>"],
    "prompt_tokens": 0,
    "completion_tokens": 0
  }
"""

from __future__ import annotations

import difflib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_unified_patch(old_text: str, new_text: str, file_label: str) -> str:
    return "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=file_label,
            tofile=file_label,
        )
    )


def summarize_contexts(prior_contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for ctx in prior_contexts[-3:]:
        task = ctx.get("task", {})
        execution = ctx.get("execution", {})
        summary.append(
            {
                "task_id": task.get("task_id", ""),
                "role": task.get("role", ""),
                "title": task.get("title", ""),
                "summary": execution.get("summary", ""),
                "deliverables": execution.get("deliverables", []),
                "feedback": execution.get("feedback", []),
            }
        )
    return summary


def expect_type(name: str, value: Any, expected_type: type) -> None:
    if not isinstance(value, expected_type):
        raise ValueError(f"{name} must be {expected_type.__name__}")


def expect_list_of_str(name: str, value: Any) -> list[str]:
    expect_type(name, value, list)
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} must contain only strings")
    return value


def validate_input(payload: dict[str, Any]) -> dict[str, Any]:
    required = ["task", "prior_contexts", "runtime_dir", "artifact_dir", "managed_target_file", "managed_patch_file"]
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"missing input fields: {', '.join(missing)}")
    expect_type("task", payload["task"], dict)
    expect_type("prior_contexts", payload["prior_contexts"], list)
    expect_type("runtime_dir", payload["runtime_dir"], str)
    expect_type("artifact_dir", payload["artifact_dir"], str)
    expect_type("managed_target_file", payload["managed_target_file"], str)
    expect_type("managed_patch_file", payload["managed_patch_file"], str)
    return payload


def validate_output(payload: dict[str, Any], managed_patch_file: Path) -> dict[str, Any]:
    required = [
        "summary",
        "deliverables",
        "tool_summary",
        "feedback",
        "next_action",
        "details",
        "artifacts",
        "prompt_tokens",
        "completion_tokens",
    ]
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"missing output fields: {', '.join(missing)}")
    expect_type("summary", payload["summary"], str)
    expect_list_of_str("deliverables", payload["deliverables"])
    expect_list_of_str("tool_summary", payload["tool_summary"])
    expect_list_of_str("feedback", payload["feedback"])
    expect_type("next_action", payload["next_action"], str)
    expect_list_of_str("details", payload["details"])
    artifacts = expect_list_of_str("artifacts", payload["artifacts"])
    expect_type("prompt_tokens", payload["prompt_tokens"], int)
    expect_type("completion_tokens", payload["completion_tokens"], int)
    if payload["deliverables"] != ["managed_target.patch"]:
        raise ValueError("deliverables must be ['managed_target.patch']")
    if payload["tool_summary"] != ["write: managed_target.patch"]:
        raise ValueError("tool_summary must be ['write: managed_target.patch']")
    if artifacts != [str(managed_patch_file)]:
        raise ValueError("artifacts must contain only the managed patch file")
    return payload


def build_target_content(task: dict[str, Any], prior_contexts: list[dict[str, Any]]) -> str:
    context_summary = summarize_contexts(prior_contexts)
    md_lines = [
        "# Managed Target",
        "",
        f"- generated_at: {now_iso()}",
        f"- task_id: {task.get('task_id', '')}",
        f"- role: {task.get('role', '')}",
        f"- title: {task.get('title', '')}",
        "",
        "## Objective",
        task.get("objective", ""),
        "",
        "## Context Summary",
    ]
    if context_summary:
        for item in context_summary:
            md_lines.append(f"- {item['task_id']}: {item['summary']}")
    else:
        md_lines.append("- none")
    md_lines.extend(
        [
            "",
            "## Contract",
            "- this is the only file the external coding worker may modify",
            "- main agent keeps compressed summary only",
            "- worker context stays in worker-owned storage",
        ]
    )
    return "\n".join(md_lines) + "\n"


def estimate_tokens(*texts: str) -> int:
    chars = sum(len(text) for text in texts)
    return max(1, chars // 4)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: python3 demo/codex_subagent_worker.py <input_json> <output_json>", file=sys.stderr)
        return 2

    input_path = Path(sys.argv[1]).expanduser().resolve()
    output_path = Path(sys.argv[2]).expanduser().resolve()
    payload = validate_input(load_json(input_path))

    task = payload["task"]
    prior_contexts = payload["prior_contexts"]
    managed_target_file = Path(payload["managed_target_file"]).expanduser().resolve()
    managed_patch_file = Path(payload["managed_patch_file"]).expanduser().resolve()

    current_text = managed_target_file.read_text(encoding="utf-8") if managed_target_file.exists() else ""
    updated_text = build_target_content(task, prior_contexts)
    patch_text = build_unified_patch(current_text, updated_text, managed_target_file.name)
    write_text(managed_patch_file, patch_text)

    summary = f"Generated managed patch for {task.get('task_id', 'unknown task')}"
    response_preview = {
        "summary": summary,
        "tool_summary": ["write: managed_target.patch"],
        "feedback": ["External Codex worker protocol executed successfully."],
    }

    output = {
        "summary": summary,
        "deliverables": ["managed_target.patch"],
        "tool_summary": ["write: managed_target.patch"],
        "feedback": ["External Codex worker protocol executed successfully."],
        "next_action": "MainAgent can validate and apply the managed patch.",
        "details": [
            f"input_path={input_path}",
            f"managed_target_file={managed_target_file}",
            f"managed_patch_file={managed_patch_file}",
            f"prior_context_count={len(prior_contexts)}",
        ],
        "artifacts": [str(managed_patch_file)],
        "prompt_tokens": estimate_tokens(
            json.dumps(task, ensure_ascii=False),
            json.dumps(prior_contexts, ensure_ascii=False),
        ),
        "completion_tokens": estimate_tokens(summary, json.dumps(response_preview, ensure_ascii=False)),
    }

    atomic_write_json(output_path, validate_output(output, managed_patch_file))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
