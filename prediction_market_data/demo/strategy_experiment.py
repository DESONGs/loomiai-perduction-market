"""
Experiment-grade self-iteration loop for prediction market strategy.

V2 闭环:
- 复制 autoresearch 工作区到 demo_runtime/strategy_workspace
- 受控 target: strategy_workspace/pm_train.py
- coding worker 只输出 patch
- orchestrator 校验并应用 patch
- 固定总样本上限后拆成 search / validation / holdout
- 重复评估 search / validation，holdout 做最终确认
- 多阶段 gate 决定 accepted / rejected / stop
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.experiment.event_sink import RuntimeEventSink
from app.experiment.gates import (
    GatePolicy,
    evaluate_holdout_gate,
    evaluate_search_gate,
    evaluate_validation_gate,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
SOURCE_AUTORESEARCH_DIR = Path(
    os.environ.get("SOURCE_AUTORESEARCH_DIR", str(ROOT_DIR.parent / "autoresearch"))
).resolve()
RUNTIME_DIR = Path(os.environ.get("AUTORESEARCH_DIR", str(ROOT_DIR / "demo_runtime"))).resolve()
LIVE_LOG = RUNTIME_DIR / "pm_live.jsonl"
RESULTS_TSV = RUNTIME_DIR / "pm_results.tsv"
RUN_LOG = RUNTIME_DIR / "pm_run.log"
STATE_JSON = RUNTIME_DIR / "orchestrator_state.json"
RUNTIME_EVENTS_JSON = RUNTIME_DIR / "runtime_events.jsonl"
WORKER_DIR = RUNTIME_DIR / "workers"
ARTIFACT_DIR = RUNTIME_DIR / "artifacts"
POOL_DIR = RUNTIME_DIR / "eval_pools"
WORKSPACE_DIR = RUNTIME_DIR / "strategy_workspace"
MANAGED_STRATEGY_FILE = WORKSPACE_DIR / "pm_train.py"
MANAGED_PATCH_FILE = ARTIFACT_DIR / "pm_train.patch"
ITERATIONS_JSON = RUNTIME_DIR / "iteration_details.json"
WORKSPACE_EVAL_DATA = WORKSPACE_DIR / "eval_markets.json"
EVAL_DATA_SOURCE = Path(os.environ.get("EVAL_DATA_PATH", str(SOURCE_AUTORESEARCH_DIR / "eval_markets.json"))).resolve()
RUN_ID = os.environ.get("RUN_ID", "")
RUN_SPEC_PATH = Path(os.environ["RUN_SPEC_PATH"]).resolve() if os.environ.get("RUN_SPEC_PATH") else None

DEFAULT_PER_EVAL_TOKEN_BUDGET = 150_000
DEFAULT_TOTAL_TOKEN_BUDGET = 0
DEFAULT_MAX_COMPLETION_TOKENS = 1_200

POOL_RATIOS = {
    "search": 0.50,
    "validation": 0.30,
    "holdout": 0.20,
}
PHASE_SETTINGS = {
    "search": {"repeats": 2, "sample_ratio": 0.80, "seeds": [11, 17]},
    "validation": {"repeats": 2, "sample_ratio": 0.80, "seeds": [23, 29]},
    "holdout": {"repeats": 1, "sample_ratio": 1.00, "seeds": [37]},
}
SEARCH_MIN_DELTA_FITNESS = 0.10
VALIDATION_MIN_DELTA_FITNESS = 0.15
VALIDATION_STD_MULTIPLIER = 0.50
MAX_DRAWDOWN_DETERIORATION = 0.02
HOLDOUT_MAX_DRAWDOWN_DETERIORATION = 0.015
MAX_TOKEN_PER_MARKET_RATIO = 1.25
HIGH_TOKEN_VALIDATION_DELTA = 0.35
TRADE_RATIO_BAND = (0.70, 1.30)
VALIDATED_NO_EDGE_PATIENCE = 4
RUNTIME_FAILURE_BREAKER = 3
RECOVERY_TRIGGER = 2
TOTAL_REPEAT_RUNS_PER_ITERATION = sum(settings["repeats"] for settings in PHASE_SETTINGS.values())
SEARCH_CANDIDATES_PER_ITERATION = 3
SEARCH_MODE_TARGET_RATIOS = {
    "exploit_local": 0.60,
    "structured_exploration": 0.25,
    "recovery": 0.15,
}
SEARCH_MODE_PRIORITY = {
    "exploit_local": 3,
    "structured_exploration": 2,
    "recovery": 1,
}
PROMPT_FACTOR_ORDER = [
    "extreme_price_skepticism",
    "evidence_balance",
    "volume_awareness",
    "event_type_branching",
]

SUPPORTED_ALLOWED_AXES = {
    "CONFIDENCE_THRESHOLD",
    "BET_SIZING",
    "MAX_BET_FRACTION",
    "PROMPT_FACTORS",
}


def configure_runtime_paths(runtime_dir: Path) -> None:
    global RUNTIME_DIR, LIVE_LOG, RESULTS_TSV, RUN_LOG, STATE_JSON, RUNTIME_EVENTS_JSON, WORKER_DIR, ARTIFACT_DIR
    global POOL_DIR, WORKSPACE_DIR, MANAGED_STRATEGY_FILE, MANAGED_PATCH_FILE, ITERATIONS_JSON
    global WORKSPACE_EVAL_DATA
    RUNTIME_DIR = runtime_dir.resolve()
    LIVE_LOG = RUNTIME_DIR / "pm_live.jsonl"
    RESULTS_TSV = RUNTIME_DIR / "pm_results.tsv"
    RUN_LOG = RUNTIME_DIR / "pm_run.log"
    STATE_JSON = RUNTIME_DIR / "orchestrator_state.json"
    RUNTIME_EVENTS_JSON = RUNTIME_DIR / "runtime_events.jsonl"
    WORKER_DIR = RUNTIME_DIR / "workers"
    ARTIFACT_DIR = RUNTIME_DIR / "artifacts"
    POOL_DIR = RUNTIME_DIR / "eval_pools"
    WORKSPACE_DIR = RUNTIME_DIR / "strategy_workspace"
    MANAGED_STRATEGY_FILE = WORKSPACE_DIR / "pm_train.py"
    MANAGED_PATCH_FILE = ARTIFACT_DIR / "pm_train.patch"
    ITERATIONS_JSON = RUNTIME_DIR / "iteration_details.json"
    WORKSPACE_EVAL_DATA = WORKSPACE_DIR / "eval_markets.json"


def configure_source_paths(source_dir: Path | None, eval_data_path: Path | None) -> None:
    global SOURCE_AUTORESEARCH_DIR, EVAL_DATA_SOURCE
    if source_dir is not None:
        SOURCE_AUTORESEARCH_DIR = source_dir.resolve()
    if eval_data_path is not None:
        EVAL_DATA_SOURCE = eval_data_path.resolve()


def configure_run_context(run_id: str = "", run_spec_path: Path | None = None) -> None:
    global RUN_ID, RUN_SPEC_PATH
    if run_id:
        RUN_ID = run_id
    if run_spec_path is not None:
        RUN_SPEC_PATH = run_spec_path.resolve()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    for path in (RUNTIME_DIR, WORKER_DIR, ARTIFACT_DIR, POOL_DIR, WORKSPACE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def reset_runtime() -> None:
    if RUNTIME_DIR.exists():
        shutil.rmtree(RUNTIME_DIR)
    ensure_dirs()


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def append_log(message: str) -> None:
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{now_iso()}] {message}\n")


def write_results(row: dict[str, Any]) -> None:
    headers = [
        "commit",
        "description",
        "status",
        "search_fitness",
        "validation_fitness",
        "holdout_fitness",
        "validation_pnl",
        "tokens",
        "decision_logic",
    ]
    exists = RESULTS_TSV.exists()
    with RESULTS_TSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter="\t")
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def apply_unified_patch(old_text: str, patch_text: str, file_label: str) -> str:
    lines = patch_text.splitlines(keepends=True)
    if len(lines) < 2:
        raise ValueError("patch is too short")
    if not lines[0].startswith(f"--- {file_label}") or not lines[1].startswith(f"+++ {file_label}"):
        raise ValueError("patch headers must target managed strategy file")

    old_lines = old_text.splitlines(keepends=True)
    result: list[str] = []
    old_index = 0
    i = 2

    while i < len(lines):
        header = lines[i]
        if not header.startswith("@@"):
            raise ValueError("unexpected patch line before hunk header")
        match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
        if not match:
            raise ValueError("invalid hunk header")
        old_start = int(match.group(1)) - 1
        result.extend(old_lines[old_index:old_start])
        old_index = old_start
        i += 1
        while i < len(lines) and not lines[i].startswith("@@"):
            line = lines[i]
            if line.startswith("\\"):
                i += 1
                continue
            prefix = line[:1]
            content = line[1:]
            if prefix == " ":
                if old_index >= len(old_lines) or old_lines[old_index] != content:
                    raise ValueError("patch context mismatch")
                result.append(content)
                old_index += 1
            elif prefix == "-":
                if old_index >= len(old_lines) or old_lines[old_index] != content:
                    raise ValueError("patch deletion mismatch")
                old_index += 1
            elif prefix == "+":
                result.append(content)
            else:
                raise ValueError("invalid patch line")
            i += 1
    result.extend(old_lines[old_index:])
    return "".join(result)


def summarize_prompt(text: str) -> str:
    compact = " ".join(line.strip() for line in text.strip().splitlines() if line.strip())
    return compact[:220]


def infer_prompt_factors(system_prompt: str, user_prompt: str) -> list[str]:
    combined = f"{system_prompt}\n{user_prompt}"
    factors = []
    if "extreme 0.0 or 1.0 prices" in combined or "Treat extreme prices" in combined:
        factors.append("extreme_price_skepticism")
    if "disconfirming evidence" in combined or "evidence could make the market wrong" in combined:
        factors.append("evidence_balance")
    if "low-volume markets as noisier" in combined or "volume as a reliability signal" in combined:
        factors.append("volume_awareness")
    if "market category" in combined or "event type" in combined:
        factors.append("event_type_branching")
    return sorted(factors)


def prompt_profile_label(factors: list[str]) -> str:
    return "baseline" if not factors else "+".join(sorted(factors))


def normalize_prompt_factors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized = [str(item) for item in value if isinstance(item, str)]
    allowed = set(PROMPT_FACTOR_ORDER)
    return [factor for factor in PROMPT_FACTOR_ORDER if factor in set(normalized) and factor in allowed]


def build_prompt_text(factors: list[str]) -> tuple[str, str]:
    factors = normalize_prompt_factors(factors)
    if not factors:
        return (
            """You are a prediction market analyst. For each market, you must:
1. Briefly explain your thinking process (2-3 sentences)
2. Make a prediction

Respond ONLY with valid JSON, no other text.""",
            """Analyze this prediction market:

Question: {question}
Outcomes: {outcomes}
Last trade price (probability of outcome[0]): {last_trade_price}
Volume: {volume_usd}
Category: {category}
Event: {event_title}
{price_signals_text}

Reply with JSON only:
{{"prediction": 0 or 1, "confidence": 0.0 to 1.0, "thinking": "2-3 sentences on your analysis logic", "reasoning": "one-line conclusion"}}""",
        )

    system_lines = [
        "You are a prediction market analyst focused on calibrated, risk-adjusted edge.",
        "For each market:",
        "1. Briefly explain your thinking process (2-3 sentences).",
        "2. Compare the market-implied probability with plausible real-world uncertainty.",
        "3. Explicitly consider why the market might be wrong before making a prediction.",
    ]
    user_focus_lines = []

    if "extreme_price_skepticism" in factors:
        system_lines.append("4. Treat extreme 0.0 or 1.0 prices with skepticism unless supporting evidence is strong.")
        user_focus_lines.append("Treat extreme prices as potentially stale or overconfident if evidence is weak.")
    if "evidence_balance" in factors:
        system_lines.append("5. Balance supporting evidence with disconfirming evidence before locking the final view.")
        user_focus_lines.append("Balance supporting and disconfirming evidence before finalizing the prediction.")
    if "volume_awareness" in factors:
        system_lines.append("6. Treat low-volume markets as noisier and use volume as a reliability signal.")
        user_focus_lines.append("Treat low-volume markets as noisier and use volume as a reliability signal.")
    if "event_type_branching" in factors:
        system_lines.append("7. Adjust emphasis based on the market category and event type before assigning confidence.")
        user_focus_lines.append("Use the market category and event type as a branching cue for what evidence matters most.")

    system_lines.append("")
    system_lines.append("Respond ONLY with valid JSON, no other text.")

    user_lines = [
        "Analyze this prediction market:",
        "",
        "Question: {question}",
        "Outcomes: {outcomes}",
        "Last trade price (probability of outcome[0]): {last_trade_price}",
        "Volume: {volume_usd}",
        "Category: {category}",
        "Event: {event_title}",
        "{price_signals_text}",
        "",
    ]
    if user_focus_lines:
        user_lines.append("Focus checklist:")
        for line in user_focus_lines:
            user_lines.append(f"- {line}")
        user_lines.append("")
    user_lines.append("Reply with JSON only:")
    user_lines.append(
        '{{"prediction": 0 or 1, "confidence": 0.0 to 1.0, "thinking": "2-3 sentences on your analysis logic", "reasoning": "one-line conclusion"}}'
    )
    return "\n".join(system_lines), "\n".join(user_lines)


def parse_strategy_config(text: str) -> dict[str, Any]:
    threshold_match = re.search(r"^CONFIDENCE_THRESHOLD = ([0-9.]+)$", text, flags=re.MULTILINE)
    sizing_match = re.search(r'^BET_SIZING = "([^"]+)"$', text, flags=re.MULTILINE)
    max_bet_match = re.search(r"^MAX_BET_FRACTION = ([0-9.]+)$", text, flags=re.MULTILINE)
    prompt_factors_match = re.search(r"^PROMPT_FACTORS = (\[[^\n]*\])$", text, flags=re.MULTILINE)
    system_match = re.search(r'SYSTEM_PROMPT = """(.*?)"""', text, flags=re.MULTILINE | re.DOTALL)
    user_match = re.search(r'USER_PROMPT_TEMPLATE = """(.*?)"""', text, flags=re.MULTILINE | re.DOTALL)
    if prompt_factors_match:
        try:
            factors = normalize_prompt_factors(ast.literal_eval(prompt_factors_match.group(1)))
        except (ValueError, SyntaxError):
            factors = []
        system_prompt, user_prompt = build_prompt_text(factors)
    else:
        system_prompt = system_match.group(1) if system_match else ""
        user_prompt = user_match.group(1) if user_match else ""
        factors = infer_prompt_factors(system_prompt, user_prompt)
    return {
        "CONFIDENCE_THRESHOLD": float(threshold_match.group(1)) if threshold_match else None,
        "BET_SIZING": sizing_match.group(1) if sizing_match else None,
        "MAX_BET_FRACTION": float(max_bet_match.group(1)) if max_bet_match else None,
        "PROMPT_FACTORS": factors,
        "PROMPT_PROFILE": prompt_profile_label(factors),
        "SYSTEM_PROMPT_TEXT": system_prompt or None,
        "USER_PROMPT_TEMPLATE_TEXT": user_prompt or None,
        "SYSTEM_PROMPT_SUMMARY": summarize_prompt(system_prompt) if system_match else None,
        "USER_PROMPT_SUMMARY": summarize_prompt(user_prompt) if user_match else None,
    }


def compact_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "CONFIDENCE_THRESHOLD": config.get("CONFIDENCE_THRESHOLD"),
        "BET_SIZING": config.get("BET_SIZING"),
        "MAX_BET_FRACTION": config.get("MAX_BET_FRACTION"),
        "PROMPT_FACTORS": list(config.get("PROMPT_FACTORS", [])),
        "PROMPT_PROFILE": config.get("PROMPT_PROFILE"),
    }


def build_prompt_change(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_factors = before.get("PROMPT_FACTORS", [])
    after_factors = after.get("PROMPT_FACTORS", [])
    before_system_text = before.get("SYSTEM_PROMPT_TEXT", "")
    after_system_text = after.get("SYSTEM_PROMPT_TEXT", "")
    before_user_text = before.get("USER_PROMPT_TEMPLATE_TEXT", "")
    after_user_text = after.get("USER_PROMPT_TEMPLATE_TEXT", "")
    changed = before_factors != after_factors or before_system_text != after_system_text or before_user_text != after_user_text
    if not changed:
        return {
            "changed": False,
            "summary": "No prompt changes in this iteration.",
            "details": "Current patch kept the same system prompt and user prompt template.",
            "before_system": before_system_text,
            "after_system": after_system_text,
            "before_user": before_user_text,
            "after_user": after_user_text,
            "before_factors": before_factors,
            "after_factors": after_factors,
        }
    return {
        "changed": True,
        "summary": f"Prompt factors changed: {prompt_profile_label(before_factors)} -> {prompt_profile_label(after_factors)}",
        "details": (
            f"System prompt: {before.get('SYSTEM_PROMPT_SUMMARY')} -> {after.get('SYSTEM_PROMPT_SUMMARY')} | "
            f"User prompt: {before.get('USER_PROMPT_SUMMARY')} -> {after.get('USER_PROMPT_SUMMARY')}"
        ),
        "before_system": before_system_text,
        "after_system": after_system_text,
        "before_user": before_user_text,
        "after_user": after_user_text,
        "before_factors": before_factors,
        "after_factors": after_factors,
    }


def format_adjustment_basis(
    before_cfg: dict[str, Any],
    after_cfg: dict[str, Any],
    worker_result: dict[str, Any],
    status: str,
) -> str:
    status_lines = {
        "accepted": "评估结论: 候选通过 search / validation / holdout，升级为新的 champion。",
        "search_reject": "评估结论: 候选未通过 search gate，直接丢弃，不进入 validation。",
        "validation_reject": "评估结论: 候选通过了 search，但没通过 validation，记为 validated no-edge。",
        "provisional": "评估结论: 候选通过了 validation，已进入 provisional，等待 holdout 最终确认。",
        "holdout_reject": "评估结论: 候选通过了 validation，但 holdout 未确认，因此不升级。",
        "failed": "评估结论: 本轮执行失败，实验已回退到上一版已接受策略。",
    }
    lines = [
        f"本轮目标: {worker_result.get('summary', '受控策略改动')}",
        f"搜索意图: {worker_result.get('search_intent', 'unknown')}；变更主轴: {worker_result.get('change_axis', 'unknown')}",
        (
            "参数调整: "
            f"置信度阈值 {before_cfg.get('CONFIDENCE_THRESHOLD')} -> {after_cfg.get('CONFIDENCE_THRESHOLD')}；"
            f"下注策略 {before_cfg.get('BET_SIZING')} -> {after_cfg.get('BET_SIZING')}；"
            f"最大下注比例 {before_cfg.get('MAX_BET_FRACTION')} -> {after_cfg.get('MAX_BET_FRACTION')}"
        ),
        f"提示词因子: {before_cfg.get('PROMPT_PROFILE')} -> {after_cfg.get('PROMPT_PROFILE')}",
        status_lines.get(status, "评估结论: 本轮完成。"),
    ]
    return "\n".join(lines)


def dict_diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    keys = sorted(set(before) | set(after))
    diffs = []
    for key in keys:
        if before.get(key) != after.get(key):
            diffs.append({"field": key, "before": before.get(key), "after": after.get(key)})
    return diffs


def load_finish_entry(path: Path) -> dict[str, Any]:
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for entry in reversed(entries):
        if entry.get("type") == "finish":
            return entry
    raise RuntimeError("finish entry not found")


def replace_eval_sampling(text: str, sample_size: int, seed: int) -> str:
    updated, count = re.subn(
        r"markets = sample_eval_markets\([^)]*\)",
        f"markets = sample_eval_markets(n={sample_size}, seed={seed})",
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("failed to configure workspace: sample_eval_markets snippet not found")
    return updated


def managed_prompt_factory_snippet() -> str:
    return '''# ---------------------------------------------------------------------------
# 提示词模板（agent 核心优化目标之一）
# ---------------------------------------------------------------------------

PROMPT_FACTORS = []

def build_system_prompt(prompt_factors):
    factors = sorted(prompt_factors)
    if not factors:
        return """You are a prediction market analyst. For each market, you must:
1. Briefly explain your thinking process (2-3 sentences)
2. Make a prediction

Respond ONLY with valid JSON, no other text."""

    system_lines = [
        "You are a prediction market analyst focused on calibrated, risk-adjusted edge.",
        "For each market:",
        "1. Briefly explain your thinking process (2-3 sentences).",
        "2. Compare the market-implied probability with plausible real-world uncertainty.",
        "3. Explicitly consider why the market might be wrong before making a prediction.",
    ]
    if "extreme_price_skepticism" in factors:
        system_lines.append("4. Treat extreme 0.0 or 1.0 prices with skepticism unless supporting evidence is strong.")
    if "evidence_balance" in factors:
        system_lines.append("5. Balance supporting evidence with disconfirming evidence before locking the final view.")
    if "volume_awareness" in factors:
        system_lines.append("6. Treat low-volume markets as noisier and use volume as a reliability signal.")
    if "event_type_branching" in factors:
        system_lines.append("7. Adjust emphasis based on the market category and event type before assigning confidence.")
    system_lines.append("")
    system_lines.append("Respond ONLY with valid JSON, no other text.")
    return "\\n".join(system_lines)


def build_user_prompt_template(prompt_factors):
    factors = sorted(prompt_factors)
    if not factors:
        return """Analyze this prediction market:

Question: {question}
Outcomes: {outcomes}
Last trade price (probability of outcome[0]): {last_trade_price}
Volume: {volume_usd}
Category: {category}
Event: {event_title}
{price_signals_text}

Reply with JSON only:
{{"prediction": 0 or 1, "confidence": 0.0 to 1.0, "thinking": "2-3 sentences on your analysis logic", "reasoning": "one-line conclusion"}}"""

    user_lines = [
        "Analyze this prediction market:",
        "",
        "Question: {question}",
        "Outcomes: {outcomes}",
        "Last trade price (probability of outcome[0]): {last_trade_price}",
        "Volume: {volume_usd}",
        "Category: {category}",
        "Event: {event_title}",
        "{price_signals_text}",
        "",
    ]
    focus_lines = []
    if "extreme_price_skepticism" in factors:
        focus_lines.append("Treat extreme prices as potentially stale or overconfident if evidence is weak.")
    if "evidence_balance" in factors:
        focus_lines.append("Balance supporting and disconfirming evidence before finalizing the prediction.")
    if "volume_awareness" in factors:
        focus_lines.append("Treat low-volume markets as noisier and use volume as a reliability signal.")
    if "event_type_branching" in factors:
        focus_lines.append("Use the market category and event type as a branching cue for what evidence matters most.")
    if focus_lines:
        user_lines.append("Focus checklist:")
        for line in focus_lines:
            user_lines.append(f"- {line}")
        user_lines.append("")
    user_lines.append("Reply with JSON only:")
    user_lines.append(
        '{{"prediction": 0 or 1, "confidence": 0.0 to 1.0, "thinking": "2-3 sentences on your analysis logic", "reasoning": "one-line conclusion"}}'
    )
    return "\\n".join(user_lines)


SYSTEM_PROMPT = build_system_prompt(PROMPT_FACTORS)
USER_PROMPT_TEMPLATE = build_user_prompt_template(PROMPT_FACTORS)'''


def managed_prepare_breakdown_snippet() -> str:
    return '''# ---------------------------------------------------------------------------
# 评估细分归因（runtime 注入）
# ---------------------------------------------------------------------------

def liquidity_bucket(volume, lower_cut, upper_cut):
    if volume >= upper_cut:
        return "high"
    if volume >= lower_cut:
        return "mid"
    return "low"


def register_breakdown(store, key, *, traded, correct, pnl):
    bucket = store.setdefault(
        key,
        {"num_markets": 0, "num_trades": 0, "num_skipped": 0, "correct": 0, "total_pnl": 0.0},
    )
    bucket["num_markets"] += 1
    if traded:
        bucket["num_trades"] += 1
        bucket["correct"] += 1 if correct else 0
        bucket["total_pnl"] += pnl
    else:
        bucket["num_skipped"] += 1


def finalize_breakdown(store):
    summary = {}
    for key, stats in store.items():
        trades = stats["num_trades"]
        total_pnl = stats["total_pnl"]
        summary[key] = {
            "num_markets": stats["num_markets"],
            "num_trades": trades,
            "num_skipped": stats["num_skipped"],
            "accuracy": round(stats["correct"] / max(1, trades), 4),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl_per_trade": round(total_pnl / max(1, trades), 2),
        }
    return summary


'''


def prepare_workspace(per_eval_token_budget: int, max_completion_tokens: int) -> None:
    for name in ("pm_train.py", "pm_prepare.py", "pm_config.py"):
        shutil.copy2(SOURCE_AUTORESEARCH_DIR / name, WORKSPACE_DIR / name)
    shutil.copy2(EVAL_DATA_SOURCE, WORKSPACE_DIR / "eval_markets.json")

    strategy_text = MANAGED_STRATEGY_FILE.read_text(encoding="utf-8")
    strategy_text, strategy_budget_count = re.subn(
        r"token_monitor = TokenMonitor\(budget_limit=\d[\d_]*\)",
        f"token_monitor = TokenMonitor(budget_limit={per_eval_token_budget:_})",
        strategy_text,
        count=1,
    )
    if strategy_budget_count != 1:
        raise RuntimeError("failed to prepare workspace: token monitor budget snippet not found")
    strategy_text, prompt_factory_count = re.subn(
        r"# ---------------------------------------------------------------------------\n# 提示词模板（agent 核心优化目标之一）\n# ---------------------------------------------------------------------------\n\nSYSTEM_PROMPT = \"\"\".*?\"\"\"\n\nUSER_PROMPT_TEMPLATE = \"\"\".*?\"\"\"",
        lambda _: managed_prompt_factory_snippet(),
        strategy_text,
        count=1,
        flags=re.DOTALL,
    )
    if prompt_factory_count != 1:
        raise RuntimeError("failed to prepare workspace: prompt factory snippet not found")
    strategy_text, prompt_snapshot_count = re.subn(
        r'("max_bet_fraction": MAX_BET_FRACTION,\n)',
        '\\1        "prompt_factors": PROMPT_FACTORS,\n',
        strategy_text,
        count=1,
    )
    if prompt_snapshot_count != 1:
        raise RuntimeError("failed to prepare workspace: params snapshot snippet not found")
    write_text(MANAGED_STRATEGY_FILE, strategy_text)

    prepare_path = WORKSPACE_DIR / "pm_prepare.py"
    prepare_text = prepare_path.read_text(encoding="utf-8")
    prepare_text, helper_count = re.subn(
        r"# ---------------------------------------------------------------------------\n# 评估函数（固定，不可修改）\n# ---------------------------------------------------------------------------\n",
        lambda _: (
            managed_prepare_breakdown_snippet()
            + "# ---------------------------------------------------------------------------\n# 评估函数（固定，不可修改）\n# ---------------------------------------------------------------------------\n"
        ),
        prepare_text,
        count=1,
    )
    if helper_count != 1:
        raise RuntimeError("failed to prepare workspace: pm_prepare helper injection point not found")
    prepare_text, breakdown_init_count = re.subn(
        r"    pnl_list = \[\]\n    correct = 0\n    total_trades = 0\n    skipped = 0\n",
        "    pnl_list = []\n    correct = 0\n    total_trades = 0\n    skipped = 0\n    category_breakdown = {}\n    liquidity_breakdown = {}\n    volumes = sorted(float(item.get(\"volume\", 0.0) or 0.0) for item in markets)\n    lower_cut = volumes[max(0, len(volumes) // 3 - 1)] if volumes else 0.0\n    upper_cut = volumes[max(0, (2 * len(volumes)) // 3 - 1)] if volumes else 0.0\n",
        prepare_text,
        count=1,
    )
    if breakdown_init_count != 1:
        raise RuntimeError("failed to prepare workspace: pm_prepare breakdown init snippet not found")
    prepare_text, skip_count = re.subn(
        r'        if bet\["action"\] == "skip":\n            skipped \+= 1\n            continue\n',
        '        category_key = str(market.get("context", {}).get("category", "unknown"))\n        volume = float(market.get("volume", 0.0) or 0.0)\n        liquidity_key = liquidity_bucket(volume, lower_cut, upper_cut)\n\n        if bet["action"] == "skip":\n            skipped += 1\n            register_breakdown(category_breakdown, category_key, traded=False, correct=False, pnl=0.0)\n            register_breakdown(liquidity_breakdown, liquidity_key, traded=False, correct=False, pnl=0.0)\n            continue\n',
        prepare_text,
        count=1,
    )
    if skip_count != 1:
        raise RuntimeError("failed to prepare workspace: pm_prepare skip snippet not found")
    prepare_text, trade_count = re.subn(
        r"        if pnl > 0:\n            correct \+= 1\n",
        '        trade_won = pnl > 0\n        if trade_won:\n            correct += 1\n        register_breakdown(category_breakdown, category_key, traded=True, correct=trade_won, pnl=pnl)\n        register_breakdown(liquidity_breakdown, liquidity_key, traded=True, correct=trade_won, pnl=pnl)\n',
        prepare_text,
        count=1,
    )
    if trade_count != 1:
        raise RuntimeError("failed to prepare workspace: pm_prepare trade snippet not found")
    prepare_text, result_count = re.subn(
        r'        "final_bankroll": round\(bankroll, 2\),\n',
        '        "final_bankroll": round(bankroll, 2),\n        "category_breakdown": finalize_breakdown(category_breakdown),\n        "liquidity_breakdown": finalize_breakdown(liquidity_breakdown),\n',
        prepare_text,
        count=1,
    )
    if result_count != 1:
        raise RuntimeError("failed to prepare workspace: pm_prepare result snippet not found")
    write_text(prepare_path, prepare_text)

    pm_config_path = WORKSPACE_DIR / "pm_config.py"
    config_text = pm_config_path.read_text(encoding="utf-8")
    config_text, max_tokens_count = re.subn(
        r"^MAX_TOKENS = \d+$",
        f"MAX_TOKENS = {max_completion_tokens}",
        config_text,
        count=1,
        flags=re.MULTILINE,
    )
    if max_tokens_count != 1:
        raise RuntimeError("failed to prepare workspace: pm_config MAX_TOKENS not found")
    write_text(pm_config_path, config_text)


def run_strategy_eval(
    strategy_text: str,
    eval_markets: list[dict[str, Any]],
    sample_size: int,
    seed: int,
    timeout: int,
) -> dict[str, Any]:
    eval_strategy_text = replace_eval_sampling(strategy_text, sample_size=sample_size, seed=seed)
    write_text(MANAGED_STRATEGY_FILE, eval_strategy_text)
    write_json(WORKSPACE_EVAL_DATA, eval_markets)
    try:
        proc = subprocess.run(
            [sys.executable, "pm_train.py"],
            cwd=str(WORKSPACE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    finally:
        write_text(MANAGED_STRATEGY_FILE, strategy_text)
    if proc.returncode != 0:
        raise RuntimeError(f"pm_train.py failed: {proc.stderr.strip()[:400]}")
    finish = load_finish_entry(WORKSPACE_DIR / "pm_live.jsonl")
    results = finish.get("results", {})
    token_summary = finish.get("token_summary", {})
    return {
        "results": results,
        "token_summary": token_summary,
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-20:]),
        "sample_size": sample_size,
        "seed": seed,
    }


def allocate_counts(total_size: int, ratios: dict[str, float]) -> dict[str, int]:
    names = list(ratios.keys())
    if total_size <= 0:
        return {name: 0 for name in names}
    raw = {name: total_size * ratios[name] for name in names}
    counts = {name: int(raw[name]) for name in names}
    assigned = sum(counts.values())
    remainders = sorted(names, key=lambda name: (raw[name] - counts[name]), reverse=True)
    remainder_index = 0
    while assigned < total_size:
        counts[remainders[remainder_index % len(remainders)]] += 1
        assigned += 1
        remainder_index += 1
    if total_size >= len(names):
        for name in names:
            if counts[name] > 0:
                continue
            donor = max(names, key=lambda key: counts[key])
            if counts[donor] > 1:
                counts[donor] -= 1
                counts[name] = 1
    return counts


def market_identity(market: dict[str, Any]) -> str:
    market_id = market.get("market_id")
    if market_id is not None:
        return str(market_id)
    return str(market.get("question", ""))[:160]


def build_strata_groups(markets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    markets_sorted = sorted(markets, key=lambda item: float(item.get("volume", 0.0) or 0.0), reverse=True)
    total = max(1, len(markets_sorted))
    tier_by_id: dict[str, str] = {}
    for rank, market in enumerate(markets_sorted):
        if rank < total / 3:
            tier = "high"
        elif rank < 2 * total / 3:
            tier = "mid"
        else:
            tier = "low"
        tier_by_id[market_identity(market)] = tier

    groups: dict[str, list[dict[str, Any]]] = {}
    for market in markets:
        category = str(market.get("context", {}).get("category", "unknown"))
        tier = tier_by_id.get(market_identity(market), "mid")
        key = f"{category}::{tier}"
        groups.setdefault(key, []).append(market)
    return groups


def stratified_round_robin(markets: list[dict[str, Any]], limit: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    groups = build_strata_groups(markets)
    group_keys = list(groups.keys())
    for items in groups.values():
        rng.shuffle(items)
    rng.shuffle(group_keys)

    selected: list[dict[str, Any]] = []
    while len(selected) < limit:
        progressed = False
        rng.shuffle(group_keys)
        for key in group_keys:
            if len(selected) >= limit:
                break
            if groups[key]:
                selected.append(groups[key].pop())
                progressed = True
        if not progressed:
            break
    rng.shuffle(selected)
    return selected


def choose_pool(pools: dict[str, list[dict[str, Any]]], targets: dict[str, int]) -> str | None:
    candidates = [name for name, target in targets.items() if len(pools[name]) < target]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda name: (
            len(pools[name]) / max(targets[name], 1),
            -targets[name],
            name,
        ),
    )


def build_eval_pools(markets: list[dict[str, Any]], sample_cap: int, seed: int) -> dict[str, list[dict[str, Any]]]:
    capped = stratified_round_robin(markets, limit=min(sample_cap, len(markets)), seed=seed)
    targets = allocate_counts(len(capped), POOL_RATIOS)
    ordered = stratified_round_robin(capped, limit=len(capped), seed=seed + 1)
    pools = {name: [] for name in POOL_RATIOS}
    for market in ordered:
        pool_name = choose_pool(pools, targets)
        if pool_name is None:
            break
        pools[pool_name].append(market)
    return pools


def safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 1.0 if numerator <= 0 else float("inf")
    return numerator / denominator


def round_metric(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


def aggregate_breakdown(runs: list[dict[str, Any]], key: str) -> dict[str, dict[str, float]]:
    aggregates: dict[str, dict[str, list[float]]] = {}
    for run in runs:
        breakdown = run.get("results", {}).get(key, {})
        if not isinstance(breakdown, dict):
            continue
        for bucket, stats in breakdown.items():
            if not isinstance(stats, dict):
                continue
            target = aggregates.setdefault(
                str(bucket),
                {
                    "num_markets": [],
                    "num_trades": [],
                    "num_skipped": [],
                    "accuracy": [],
                    "total_pnl": [],
                    "avg_pnl_per_trade": [],
                },
            )
            for field in target:
                target[field].append(float(stats.get(field, 0.0) or 0.0))
    summary: dict[str, dict[str, float]] = {}
    for bucket, stats in aggregates.items():
        summary[bucket] = {
            field: round_metric(statistics.mean(values) if values else 0.0, 4 if field == "accuracy" else 2)
            for field, values in stats.items()
        }
    return summary


def summarize_phase_runs(phase: str, pool_size: int, runs: list[dict[str, Any]]) -> dict[str, Any]:
    fitness_values = [float(run["results"].get("fitness", 0.0) or 0.0) for run in runs]
    accuracy_values = [float(run["results"].get("accuracy", 0.0) or 0.0) for run in runs]
    pnl_values = [float(run["results"].get("total_pnl", 0.0) or 0.0) for run in runs]
    drawdowns = [float(run["results"].get("max_drawdown", 0.0) or 0.0) for run in runs]
    trades = [float(run["results"].get("num_trades", 0) or 0) for run in runs]
    skipped = [float(run["results"].get("num_skipped", 0) or 0) for run in runs]
    token_per_market = [
        float(run["token_summary"].get("total_tokens", 0) or 0) / max(1, int(run.get("sample_size", 1)))
        for run in runs
    ]
    total_tokens = sum(int(run["token_summary"].get("total_tokens", 0) or 0) for run in runs)
    prompt_tokens_total = sum(int(run["token_summary"].get("prompt_tokens", 0) or 0) for run in runs)
    completion_tokens_total = sum(int(run["token_summary"].get("completion_tokens", 0) or 0) for run in runs)
    api_calls_total = sum(int(run["token_summary"].get("api_calls", 0) or 0) for run in runs)
    api_errors_total = sum(int(run["token_summary"].get("api_errors", 0) or 0) for run in runs)
    return {
        "phase": phase,
        "pool_size": pool_size,
        "sample_size": int(runs[0].get("sample_size", pool_size)) if runs else 0,
        "repeats": len(runs),
        "runs": runs,
        "fitness_mean": round_metric(statistics.mean(fitness_values) if fitness_values else 0.0),
        "fitness_median": round_metric(statistics.median(fitness_values) if fitness_values else 0.0),
        "fitness_std": round_metric(statistics.stdev(fitness_values) if len(fitness_values) > 1 else 0.0),
        "accuracy_mean": round_metric(statistics.mean(accuracy_values) if accuracy_values else 0.0, 4),
        "total_pnl_mean": round_metric(statistics.mean(pnl_values) if pnl_values else 0.0, 2),
        "max_drawdown_max": round_metric(max(drawdowns) if drawdowns else 0.0, 4),
        "num_trades_mean": round_metric(statistics.mean(trades) if trades else 0.0, 2),
        "num_skipped_mean": round_metric(statistics.mean(skipped) if skipped else 0.0, 2),
        "token_per_market_mean": round_metric(statistics.mean(token_per_market) if token_per_market else 0.0, 2),
        "total_tokens": total_tokens,
        "prompt_tokens": prompt_tokens_total,
        "completion_tokens": completion_tokens_total,
        "api_calls": api_calls_total,
        "api_errors": api_errors_total,
        "search_rank_score": round_metric(
            (statistics.median(fitness_values) if fitness_values else 0.0)
            - 0.35 * (statistics.stdev(fitness_values) if len(fitness_values) > 1 else 0.0)
        ),
        "category_breakdown": aggregate_breakdown(runs, "category_breakdown"),
        "liquidity_breakdown": aggregate_breakdown(runs, "liquidity_breakdown"),
    }


def summarize_gate_metrics(candidate: dict[str, Any], champion: dict[str, Any]) -> dict[str, float]:
    return {
        "delta_fitness": round_metric(candidate.get("fitness_median", 0.0) - champion.get("fitness_median", 0.0)),
        "delta_search_rank_score": round_metric(
            candidate.get("search_rank_score", 0.0) - champion.get("search_rank_score", 0.0)
        ),
        "delta_pnl": round_metric(candidate.get("total_pnl_mean", 0.0) - champion.get("total_pnl_mean", 0.0), 2),
        "delta_drawdown": round_metric(candidate.get("max_drawdown_max", 0.0) - champion.get("max_drawdown_max", 0.0), 4),
        "trade_ratio": round_metric(
            safe_ratio(candidate.get("num_trades_mean", 0.0), champion.get("num_trades_mean", 0.0)),
            4,
        ),
        "token_ratio": round_metric(
            safe_ratio(candidate.get("token_per_market_mean", 0.0), champion.get("token_per_market_mean", 0.0)),
            4,
        ),
    }


def within_trade_band(value: float) -> bool:
    return TRADE_RATIO_BAND[0] <= value <= TRADE_RATIO_BAND[1]


@dataclass
class ExperimentState:
    iteration: int = 0
    accepted_fitness: float | None = None
    best_fitness: float | None = None
    search_reject_streak: int = 0
    validated_no_edge_streak: int = 0
    failure_streak: int = 0
    total_eval_tokens: int = 0
    accepted_strategy_text: str = ""
    accepted_config: dict[str, Any] = field(default_factory=dict)
    champion_phase_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    baseline_validation_std: float = 0.0
    dataset_pools: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    current_mode: str = "exploit_local"
    mode_usage: dict[str, int] = field(
        default_factory=lambda: {
            "exploit_local": 0,
            "structured_exploration": 0,
            "recovery": 0,
        }
    )
    provisional_iteration: int | None = None
    provisional_config: dict[str, Any] = field(default_factory=dict)
    provisional_phase_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    tried_configs: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)


class StrategyExperiment:
    def __init__(
        self,
        max_iterations: int,
        eval_timeout: int,
        codex_worker_cmd: str,
        sample_size: int,
        per_eval_token_budget: int,
        total_token_budget: int,
        max_completion_tokens: int,
        allowed_search_axes: list[str],
    ) -> None:
        self.max_iterations = max_iterations
        self.eval_timeout = eval_timeout
        self.codex_worker_cmd = codex_worker_cmd
        self.sample_size = sample_size
        self.per_eval_token_budget = per_eval_token_budget
        self.total_token_budget = total_token_budget
        self.max_completion_tokens = max_completion_tokens
        self.allowed_search_axes = [axis for axis in allowed_search_axes if axis in SUPPORTED_ALLOWED_AXES]
        self.state = ExperimentState()
        self.event_sink = RuntimeEventSink(RUNTIME_EVENTS_JSON)
        self.gate_policy = GatePolicy(
            search_min_delta_fitness=SEARCH_MIN_DELTA_FITNESS,
            validation_min_delta_fitness=VALIDATION_MIN_DELTA_FITNESS,
            validation_std_multiplier=VALIDATION_STD_MULTIPLIER,
            max_drawdown_deterioration=MAX_DRAWDOWN_DETERIORATION,
            holdout_max_drawdown_deterioration=HOLDOUT_MAX_DRAWDOWN_DETERIORATION,
            max_token_per_market_ratio=MAX_TOKEN_PER_MARKET_RATIO,
            high_token_validation_delta=HIGH_TOKEN_VALIDATION_DELTA,
            trade_ratio_band=TRADE_RATIO_BAND,
        )

    def total_budget_label(self) -> str:
        return "disabled" if self.total_token_budget <= 0 else str(self.total_token_budget)

    def estimated_iteration_cost(self) -> int:
        max_phase_evals = (
            SEARCH_CANDIDATES_PER_ITERATION * PHASE_SETTINGS["search"]["repeats"]
            + PHASE_SETTINGS["validation"]["repeats"]
            + PHASE_SETTINGS["holdout"]["repeats"]
        )
        return self.per_eval_token_budget * max_phase_evals

    def build_token_snapshot(
        self,
        phase_summaries: dict[str, dict[str, Any]],
        *,
        status: str,
        iteration: int | None = None,
    ) -> dict[str, Any]:
        total_tokens = sum(int(item.get("total_tokens", 0) or 0) for item in phase_summaries.values())
        prompt_tokens = sum(int(item.get("prompt_tokens", 0) or 0) for item in phase_summaries.values())
        completion_tokens = sum(int(item.get("completion_tokens", 0) or 0) for item in phase_summaries.values())
        api_calls = sum(int(item.get("api_calls", 0) or 0) for item in phase_summaries.values())
        api_errors = sum(int(item.get("api_errors", 0) or 0) for item in phase_summaries.values())
        return {
            "total_tokens": self.state.total_eval_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "api_calls": api_calls,
            "api_errors": api_errors,
            "progress": f"{iteration or self.state.iteration}/{self.max_iterations}",
            "status": status,
            "budget_limit": self.total_token_budget,
            "per_eval_budget_limit": self.per_eval_token_budget,
            "last_iteration_tokens": total_tokens,
        }

    def sync_state(self, status: str, note: str = "") -> None:
        pool_split = {name: len(items) for name, items in self.state.dataset_pools.items()}
        provisional_state = (
            {
                "iteration": self.state.provisional_iteration,
                "config": self.state.provisional_config,
                "phase_keys": sorted(self.state.provisional_phase_summaries.keys()),
            }
            if self.state.provisional_iteration is not None
            else None
        )
        payload = {
            "goal": "预测市场策略自我迭代实验",
            "status": status,
            "updated_at": now_iso(),
            "run_id": RUN_ID,
            "run_spec_path": str(RUN_SPEC_PATH) if RUN_SPEC_PATH else "",
            "main_agent": {
                "goal": (
                    "在总样本 <= 200、受控 token 预算下，用 search / validation / holdout "
                    "三层 gate 做稳定型策略自我迭代。"
                ),
                "constraints": [
                    "sample size <= 200",
                    f"active sample size = {self.sample_size}",
                    f"pool split = {pool_split}",
                    f"per-eval token budget = {self.per_eval_token_budget}",
                    f"estimated iteration cost = {self.estimated_iteration_cost()}",
                    f"total token budget = {self.total_budget_label()}",
                    f"max completion tokens = {self.max_completion_tokens}",
                    f"search candidates per iteration = {SEARCH_CANDIDATES_PER_ITERATION}",
                    f"allowed search axes = {self.allowed_search_axes}",
                    f"validated no-edge patience = {VALIDATED_NO_EDGE_PATIENCE}",
                    f"runtime failure breaker = {RUNTIME_FAILURE_BREAKER}",
                    f"search mode target ratios = {SEARCH_MODE_TARGET_RATIOS}",
                ],
                "completed": [f"iteration_{item['iteration']}" for item in self.state.history],
                "in_progress": [f"iteration_{self.state.iteration + 1}"] if status == "running" else [],
                "pending": [],
                "notes": [
                    f"run_id={RUN_ID or 'standalone'}",
                    f"current_mode={self.state.current_mode}",
                    f"mode_usage={self.state.mode_usage}",
                    f"search_reject_streak={self.state.search_reject_streak}",
                    f"validated_no_edge_streak={self.state.validated_no_edge_streak}",
                    f"failure_streak={self.state.failure_streak}",
                    f"total_eval_tokens={self.state.total_eval_tokens}",
                    f"baseline_validation_std={self.state.baseline_validation_std}",
                ] + ([note] if note else []),
                "feedback": [
                    f"accepted_validation_median={self.state.accepted_fitness}",
                    f"best_validation_median={self.state.best_fitness}",
                    f"accepted_config={self.state.accepted_config}",
                    f"provisional_state={provisional_state}",
                ],
                "next_dispatch_reasoning": (
                    "继续按 60/25/15 scheduler 生成单主轴候选 shortlist，用 search_rank_score 排序，"
                    "再通过 search / validation / provisional / holdout gate 决定 reject / accept。"
                ),
            },
            "workers": [],
        }
        atomic_write_json(STATE_JSON, payload)
        self.event_sink.append_state_synced(payload)

    def append_iteration_detail(self, detail: dict[str, Any]) -> dict[str, Any]:
        details = read_json(ITERATIONS_JSON, [])
        details.append(detail)
        atomic_write_json(ITERATIONS_JSON, details)
        return detail

    def write_stream_start(self) -> None:
        payload = {
            "type": "start",
            "started_at": now_iso(),
            "budget_limit": self.per_eval_token_budget,
            "total_budget_limit": self.total_token_budget,
            "model": "strategy-experiment-v2 + codex-patch-worker",
            "params": {
                "run_id": RUN_ID,
                "model": "pm_train.py self-iteration",
                "temperature": "workspace-managed",
                "max_tokens": self.max_completion_tokens,
                "dataset_split": POOL_RATIOS,
                "phase_settings": PHASE_SETTINGS,
                "search_candidates_per_iteration": SEARCH_CANDIDATES_PER_ITERATION,
                "search_rank_score": "fitness_median - 0.35 * fitness_std",
                "accept_thresholds": {
                    "search_min_delta_fitness": SEARCH_MIN_DELTA_FITNESS,
                    "validation_min_delta_fitness": VALIDATION_MIN_DELTA_FITNESS,
                    "max_drawdown_deterioration": MAX_DRAWDOWN_DETERIORATION,
                    "max_token_per_market_ratio": MAX_TOKEN_PER_MARKET_RATIO,
                    "trade_ratio_band": TRADE_RATIO_BAND,
                },
                "allowed_axes": self.allowed_search_axes,
            },
        }
        append_jsonl(LIVE_LOG, payload)
        self.event_sink.append_run_started(payload)

    def append_iteration_event(
        self,
        iteration: int,
        summary: str,
        phase_summaries: dict[str, dict[str, Any]],
        status: str,
    ) -> dict[str, Any]:
        reference = phase_summaries.get("validation") or phase_summaries.get("search") or phase_summaries.get("holdout") or {}
        total_tokens = sum(int(item.get("total_tokens", 0) or 0) for item in phase_summaries.values())
        prompt_tokens = sum(int(item.get("prompt_tokens", 0) or 0) for item in phase_summaries.values())
        completion_tokens = sum(int(item.get("completion_tokens", 0) or 0) for item in phase_summaries.values())
        api_calls = sum(int(item.get("api_calls", 0) or 0) for item in phase_summaries.values())
        api_errors = sum(int(item.get("api_errors", 0) or 0) for item in phase_summaries.values())
        payload = {
            "type": "inference",
            "index": iteration,
            "progress": f"{iteration}/{self.max_iterations}",
            "question": summary,
            "thinking": (
                f"search={phase_summaries.get('search', {}).get('fitness_median')} "
                f"validation={phase_summaries.get('validation', {}).get('fitness_median')} "
                f"holdout={phase_summaries.get('holdout', {}).get('fitness_median')}"
            ),
            "reasoning": status,
            "raw_response": json.dumps(phase_summaries, ensure_ascii=False)[:1000],
            "prediction": 0,
            "confidence": 0.9,
            "outcomes": ["accepted", "rejected"],
            "final_resolution": status,
            "bet_action": "buy" if status == "accepted" else "skip",
            "is_correct": status == "accepted",
            "bet_pnl": float(reference.get("total_pnl_mean", 0.0) or 0.0),
            "running_pnl": float(reference.get("total_pnl_mean", 0.0) or 0.0),
            "bankroll": 10000.0 + float(reference.get("total_pnl_mean", 0.0) or 0.0),
            "wins": len([h for h in self.state.history if h["status"] == "accepted"]),
            "losses": len([h for h in self.state.history if h["status"] != "accepted"]),
            "win_rate": len([h for h in self.state.history if h["status"] == "accepted"]) / max(1, len(self.state.history)),
            "volume": sum(len(items) for items in self.state.dataset_pools.values()),
            "last_trade_price": 1.0,
            "call_tokens": total_tokens,
            "cumulative_tokens": self.state.total_eval_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "api_calls": api_calls,
            "api_errors": api_errors,
        }
        append_jsonl(LIVE_LOG, payload)
        return payload

    def append_finish(self, fitness: float, stop_reason: str) -> dict[str, Any]:
        payload = {
            "type": "finish",
            "finished_at": now_iso(),
            "results": {"fitness": fitness, "stop_reason": stop_reason},
            "token_summary": {
                "total_tokens": self.state.total_eval_tokens,
                "api_calls": sum(item.get("phase_evals", 0) for item in self.state.history),
                "api_errors": self.state.failure_streak,
            },
        }
        append_jsonl(LIVE_LOG, payload)
        self.event_sink.append_run_finished(
            stream_payload=payload,
            token_snapshot={
                "total_tokens": self.state.total_eval_tokens,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "api_calls": sum(item.get("phase_evals", 0) for item in self.state.history),
                "api_errors": self.state.failure_streak,
                "progress": f"{self.state.iteration}/{self.max_iterations}",
                "status": "finished",
            },
        )
        return payload

    def record_history(
        self,
        iteration: int,
        patch_summary: str,
        phase_summaries: dict[str, dict[str, Any]],
        status: str,
        config: dict[str, Any] | None,
        decision_logic: str,
        worker_result: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        search_summary = phase_summaries.get("search", {})
        validation_summary = phase_summaries.get("validation", {})
        holdout_summary = phase_summaries.get("holdout", {})
        entry = {
            "iteration": iteration,
            "status": status,
            "patch_summary": patch_summary,
            "search_fitness": search_summary.get("fitness_median"),
            "validation_fitness": validation_summary.get("fitness_median"),
            "holdout_fitness": holdout_summary.get("fitness_median"),
            "tokens": sum(int(item.get("total_tokens", 0) or 0) for item in phase_summaries.values()),
            "config": config or {},
            "change_axis": (worker_result or {}).get("change_axis"),
            "search_intent": (worker_result or {}).get("search_intent"),
            "phase_evals": sum(item.get("repeats", 0) for item in phase_summaries.values()),
            "decision_logic": decision_logic,
        }
        self.state.history.append(entry)
        result_row = {
            "commit": f"iteration-{iteration}",
            "description": patch_summary,
            "status": status,
            "search_fitness": str(search_summary.get("fitness_median", "")),
            "validation_fitness": str(validation_summary.get("fitness_median", "")),
            "holdout_fitness": str(holdout_summary.get("fitness_median", "")),
            "validation_pnl": str(validation_summary.get("total_pnl_mean", search_summary.get("total_pnl_mean", ""))),
            "tokens": str(sum(int(item.get("total_tokens", 0) or 0) for item in phase_summaries.values())),
            "decision_logic": decision_logic,
        }
        write_results(result_row)
        return entry, result_row

    def call_codex_worker(
        self,
        iteration: int,
        candidate_slot: int,
        requested_mode: str,
        excluded_configs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        input_path = ARTIFACT_DIR / f"iteration_{iteration}_candidate_{candidate_slot}_worker_input.json"
        output_path = ARTIFACT_DIR / f"iteration_{iteration}_candidate_{candidate_slot}_worker_output.json"
        patch_path = ARTIFACT_DIR / f"iteration_{iteration}_candidate_{candidate_slot}.patch"
        payload = {
            "task": {
                "task_id": f"coding_artifact_{iteration}_{candidate_slot}",
                "role": "coding",
                "title": "Patch managed pm_train.py",
                "objective": "Modify controlled strategy constants to search for a better validation score.",
            },
            "managed_target_file": str(MANAGED_STRATEGY_FILE),
            "managed_patch_file": str(patch_path),
            "experiment_state": {
                "run_id": RUN_ID,
                "iteration": iteration,
                "candidate_slot": candidate_slot,
                "accepted_fitness": self.state.accepted_fitness,
                "best_fitness": self.state.best_fitness,
                "validated_no_edge_streak": self.state.validated_no_edge_streak,
                "search_reject_streak": self.state.search_reject_streak,
                "failure_streak": self.state.failure_streak,
                "current_mode": self.state.current_mode,
                "requested_mode": requested_mode,
                "baseline_validation_std": self.state.baseline_validation_std,
                "accepted_config": self.state.accepted_config,
                "tried_configs": self.state.tried_configs,
                "excluded_configs": excluded_configs,
                "mode_usage": self.state.mode_usage,
                "allowed_axes": self.allowed_search_axes,
                "history": self.state.history[-12:],
            },
        }
        atomic_write_json(input_path, payload)
        cmd = [sys.executable, "demo/codex_strategy_worker.py", str(input_path), str(output_path)]
        if self.codex_worker_cmd:
            cmd = self.codex_worker_cmd.split() + [str(input_path), str(output_path)]
        proc = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"codex worker failed: {proc.stderr.strip()[:300]}")
        result = json.loads(output_path.read_text(encoding="utf-8"))
        result["_managed_patch_file"] = str(patch_path)
        result["_candidate_slot"] = candidate_slot
        return result

    def apply_strategy_patch(self, patch_path: Path) -> tuple[str, dict[str, Any], dict[str, Any], str]:
        old_text = MANAGED_STRATEGY_FILE.read_text(encoding="utf-8")
        patch_text = patch_path.read_text(encoding="utf-8")
        new_text = apply_unified_patch(old_text, patch_text, MANAGED_STRATEGY_FILE.name)
        write_text(MANAGED_STRATEGY_FILE, new_text)
        before_cfg = parse_strategy_config(old_text)
        after_cfg = parse_strategy_config(new_text)
        return new_text, before_cfg, after_cfg, patch_text

    def clear_provisional_state(self) -> None:
        self.state.provisional_iteration = None
        self.state.provisional_config = {}
        self.state.provisional_phase_summaries = {}

    def mark_provisional(
        self,
        iteration: int,
        candidate_cfg: dict[str, Any],
        phase_summaries: dict[str, dict[str, Any]],
    ) -> None:
        self.state.provisional_iteration = iteration
        self.state.provisional_config = compact_config(candidate_cfg)
        self.state.provisional_phase_summaries = {
            key: dict(value)
            for key, value in phase_summaries.items()
        }

    def select_search_mode(self) -> str:
        allowed_modes = ["exploit_local", "structured_exploration"]
        if self.state.search_reject_streak >= RECOVERY_TRIGGER:
            allowed_modes.append("recovery")
        total_usage = sum(self.state.mode_usage.get(mode, 0) for mode in SEARCH_MODE_TARGET_RATIOS)
        selected = max(
            allowed_modes,
            key=lambda mode: (
                SEARCH_MODE_TARGET_RATIOS[mode] * (total_usage + 1) - self.state.mode_usage.get(mode, 0),
                SEARCH_MODE_PRIORITY[mode],
                -self.state.mode_usage.get(mode, 0),
            ),
        )
        self.state.current_mode = selected
        self.state.mode_usage[selected] = self.state.mode_usage.get(selected, 0) + 1
        return selected

    def build_search_shortlist(self, iteration: int, requested_mode: str) -> list[dict[str, Any]]:
        proposals: list[dict[str, Any]] = []
        excluded_configs = list(self.state.tried_configs)
        for candidate_slot in range(1, SEARCH_CANDIDATES_PER_ITERATION + 1):
            worker_result = self.call_codex_worker(
                iteration=iteration,
                candidate_slot=candidate_slot,
                requested_mode=requested_mode,
                excluded_configs=excluded_configs,
            )
            patch_path = Path(worker_result["_managed_patch_file"]).resolve()
            candidate_text, before_cfg, candidate_cfg, patch_text = self.apply_strategy_patch(patch_path)
            candidate_compact = compact_config(candidate_cfg)
            excluded_configs.append(candidate_compact)
            self.state.tried_configs.append(candidate_compact)
            try:
                search_summary = self.run_phase_bundle(candidate_text, "search")
            finally:
                write_text(MANAGED_STRATEGY_FILE, self.state.accepted_strategy_text)
            search_passed, search_metrics, search_logic = self.search_gate(search_summary)
            proposals.append(
                {
                    "candidate_slot": candidate_slot,
                    "worker_result": worker_result,
                    "patch_path": str(patch_path),
                    "patch_text": patch_text,
                    "candidate_text": candidate_text,
                    "before_cfg": before_cfg,
                    "candidate_cfg": candidate_cfg,
                    "candidate_compact": candidate_compact,
                    "search_summary": search_summary,
                    "search_passed": search_passed,
                    "search_metrics": search_metrics,
                    "search_logic": search_logic,
                }
            )
        return proposals

    def serialize_search_shortlist(self, proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        serialized = []
        for proposal in proposals:
            serialized.append(
                {
                    "candidate_slot": proposal["candidate_slot"],
                    "status": "search_pass" if proposal["search_passed"] else "search_reject",
                    "patch_summary": proposal["worker_result"]["summary"],
                    "change_axis": proposal["worker_result"].get("change_axis"),
                    "search_intent": proposal["worker_result"].get("search_intent"),
                    "step_size": proposal["worker_result"].get("step_size"),
                    "patch_path": proposal["patch_path"],
                    "candidate_config": proposal["candidate_compact"],
                    "search_rank_score": proposal["search_summary"].get("search_rank_score"),
                    "search_metrics": proposal["search_metrics"],
                    "decision_logic": proposal["search_logic"],
                }
            )
        return serialized

    def choose_ranked_search_candidate(self, proposals: list[dict[str, Any]]) -> dict[str, Any]:
        passing = [proposal for proposal in proposals if proposal["search_passed"]]
        candidate_pool = passing if passing else proposals
        return max(
            candidate_pool,
            key=lambda proposal: (
                float(proposal["search_summary"].get("search_rank_score", 0.0) or 0.0),
                float(proposal["search_metrics"].get("delta_fitness", 0.0) or 0.0),
                -float(proposal["search_metrics"].get("delta_drawdown", 0.0) or 0.0),
                -float(proposal["search_metrics"].get("token_ratio", 0.0) or 0.0),
            ),
        )

    def remaining_budget_allows_iteration(self) -> bool:
        if self.total_token_budget <= 0:
            return True
        return self.state.total_eval_tokens + self.estimated_iteration_cost() <= self.total_token_budget

    def build_dataset_pools(self) -> None:
        full_markets = read_json(WORKSPACE_EVAL_DATA, [])
        if not isinstance(full_markets, list) or not full_markets:
            raise RuntimeError("failed to load evaluation markets for dataset split")
        pools = build_eval_pools(full_markets, sample_cap=self.sample_size, seed=42)
        self.state.dataset_pools = pools
        for name, items in pools.items():
            write_json(POOL_DIR / f"{name}.json", items)

    def run_phase_bundle(self, strategy_text: str, phase: str) -> dict[str, Any]:
        pool = self.state.dataset_pools.get(phase, [])
        if not pool:
            raise RuntimeError(f"evaluation pool is empty: {phase}")
        settings = PHASE_SETTINGS[phase]
        if phase == "holdout":
            sample_size = len(pool)
        else:
            sample_size = min(len(pool), max(1, int(round(len(pool) * settings["sample_ratio"]))))
        runs = []
        for repeat_index in range(settings["repeats"]):
            seed = settings["seeds"][repeat_index]
            eval_data = run_strategy_eval(
                strategy_text=strategy_text,
                eval_markets=pool,
                sample_size=sample_size,
                seed=seed,
                timeout=self.eval_timeout,
            )
            self.state.total_eval_tokens += int(eval_data["token_summary"].get("total_tokens", 0) or 0)
            runs.append(eval_data)
        return summarize_phase_runs(phase=phase, pool_size=len(pool), runs=runs)

    def search_gate(self, candidate_summary: dict[str, Any]) -> tuple[bool, dict[str, float], str]:
        return evaluate_search_gate(candidate_summary, self.state.champion_phase_summaries["search"], self.gate_policy)

    def validation_gate(self, candidate_summary: dict[str, Any]) -> tuple[bool, dict[str, float], str]:
        return evaluate_validation_gate(
            candidate_summary,
            self.state.champion_phase_summaries["validation"],
            self.gate_policy,
            self.state.baseline_validation_std,
        )

    def holdout_gate(self, candidate_summary: dict[str, Any]) -> tuple[bool, dict[str, float], str]:
        return evaluate_holdout_gate(candidate_summary, self.state.champion_phase_summaries["holdout"], self.gate_policy)

    def record_provisional_transition(
        self,
        iteration: int,
        worker_result: dict[str, Any],
        before_cfg: dict[str, Any],
        candidate_cfg: dict[str, Any],
        patch_text: str,
        patch_path: str,
        phase_summaries: dict[str, dict[str, Any]],
        decision_logic: str,
        search_shortlist: list[dict[str, Any]],
    ) -> None:
        detail = self.append_iteration_detail(
            {
                "iteration": iteration,
                "status": "provisional",
                "kind": "gate_transition",
                "patch_summary": worker_result["summary"],
                "reasoning": format_adjustment_basis(before_cfg, candidate_cfg, worker_result, "provisional"),
                "raw_reasoning": " | ".join(worker_result.get("details", [])),
                "worker_feedback": worker_result.get("feedback", []),
                "change_axis": worker_result.get("change_axis"),
                "search_intent": worker_result.get("search_intent"),
                "step_size": worker_result.get("step_size"),
                "adjustment_scope": [
                    "CONFIDENCE_THRESHOLD",
                    "BET_SIZING",
                    "MAX_BET_FRACTION",
                    "PROMPT_FACTORS",
                    "SYSTEM_PROMPT",
                    "USER_PROMPT_TEMPLATE",
                ],
                "prompt_change": build_prompt_change(before_cfg, candidate_cfg),
                "config_before": before_cfg,
                "config_after": candidate_cfg,
                "config_diff": dict_diff(before_cfg, candidate_cfg),
                "patch_path": patch_path,
                "patch_excerpt": patch_text[:4000],
                "phase_results": phase_summaries,
                "decision_logic": decision_logic,
                "search_shortlist": search_shortlist,
                "current_mode": self.state.current_mode,
                "search_reject_streak": self.state.search_reject_streak,
                "validated_no_edge_streak": self.state.validated_no_edge_streak,
            }
        )
        stream_payload = self.append_iteration_event(iteration, worker_result["summary"], phase_summaries, "provisional")
        self.event_sink.append_iteration_recorded(
            detail=detail,
            result={},
            token_snapshot=self.build_token_snapshot(phase_summaries, status="running", iteration=iteration),
            stream_payload=stream_payload,
        )

    def record_iteration_outcome(
        self,
        iteration: int,
        status: str,
        worker_result: dict[str, Any],
        before_cfg: dict[str, Any],
        candidate_cfg: dict[str, Any],
        patch_text: str,
        patch_path: str,
        phase_summaries: dict[str, dict[str, Any]],
        decision_logic: str,
        search_shortlist: list[dict[str, Any]] | None = None,
        raw_reasoning: str = "",
    ) -> None:
        _entry, result_row = self.record_history(
            iteration=iteration,
            patch_summary=worker_result["summary"],
            phase_summaries=phase_summaries,
            status=status,
            config=compact_config(candidate_cfg),
            decision_logic=decision_logic,
            worker_result=worker_result,
        )
        detail = self.append_iteration_detail(
            {
                "iteration": iteration,
                "status": status,
                "kind": "patch_eval",
                "patch_summary": worker_result["summary"],
                "reasoning": format_adjustment_basis(before_cfg, candidate_cfg, worker_result, status),
                "raw_reasoning": raw_reasoning or " | ".join(worker_result.get("details", [])),
                "worker_feedback": worker_result.get("feedback", []),
                "change_axis": worker_result.get("change_axis"),
                "search_intent": worker_result.get("search_intent"),
                "step_size": worker_result.get("step_size"),
                "adjustment_scope": [
                    "CONFIDENCE_THRESHOLD",
                    "BET_SIZING",
                    "MAX_BET_FRACTION",
                    "PROMPT_FACTORS",
                    "SYSTEM_PROMPT",
                    "USER_PROMPT_TEMPLATE",
                ],
                "prompt_change": build_prompt_change(before_cfg, candidate_cfg),
                "config_before": before_cfg,
                "config_after": candidate_cfg,
                "config_diff": dict_diff(before_cfg, candidate_cfg),
                "patch_path": patch_path,
                "patch_excerpt": patch_text[:4000],
                "phase_results": phase_summaries,
                "decision_logic": decision_logic,
                "search_shortlist": search_shortlist or [],
                "current_mode": self.state.current_mode,
                "search_reject_streak": self.state.search_reject_streak,
                "validated_no_edge_streak": self.state.validated_no_edge_streak,
            }
        )
        stream_payload = self.append_iteration_event(iteration, worker_result["summary"], phase_summaries, status)
        self.event_sink.append_iteration_recorded(
            detail=detail,
            result=result_row,
            token_snapshot=self.build_token_snapshot(phase_summaries, status="running", iteration=iteration),
            stream_payload=stream_payload,
        )

    def calibrate_baseline(self) -> None:
        self.build_dataset_pools()
        baseline_text = self.state.accepted_strategy_text
        phase_summaries = {
            "search": self.run_phase_bundle(baseline_text, "search"),
            "validation": self.run_phase_bundle(baseline_text, "validation"),
            "holdout": self.run_phase_bundle(baseline_text, "holdout"),
        }
        baseline_validation = phase_summaries["validation"]
        self.state.accepted_fitness = float(baseline_validation["fitness_median"])
        self.state.best_fitness = float(baseline_validation["fitness_median"])
        self.state.baseline_validation_std = float(baseline_validation["fitness_std"])
        self.state.champion_phase_summaries = phase_summaries
        self.state.accepted_config = compact_config(parse_strategy_config(baseline_text))
        _entry, result_row = self.record_history(
            iteration=0,
            patch_summary="baseline",
            phase_summaries=phase_summaries,
            status="accepted",
            config=self.state.accepted_config,
            decision_logic="Baseline calibration completed and becomes the initial champion.",
            worker_result=None,
        )
        detail = self.append_iteration_detail(
            {
                "iteration": 0,
                "status": "accepted",
                "kind": "baseline",
                "patch_summary": "baseline",
                "reasoning": "Initial baseline calibration with repeated search / validation and holdout confirmation.",
                "change_axis": "none",
                "search_intent": "baseline_calibration",
                "step_size": "n/a",
                "adjustment_scope": [
                    "CONFIDENCE_THRESHOLD",
                    "BET_SIZING",
                    "MAX_BET_FRACTION",
                    "SYSTEM_PROMPT",
                    "USER_PROMPT_TEMPLATE",
                ],
                "prompt_change": {
                    "changed": False,
                    "summary": "No prompt changes in baseline calibration.",
                    "details": "Baseline is evaluated multiple times before any worker patch.",
                },
                "config_before": parse_strategy_config(baseline_text),
                "config_after": parse_strategy_config(baseline_text),
                "config_diff": [],
                "patch_path": "",
                "patch_excerpt": "",
                "phase_results": phase_summaries,
                "decision_logic": "Baseline becomes the initial champion after calibration.",
                "current_mode": self.state.current_mode,
                "baseline_validation_std": self.state.baseline_validation_std,
            }
        )
        stream_payload = self.append_iteration_event(0, "baseline_calibration", phase_summaries, "accepted")
        self.event_sink.append_iteration_recorded(
            detail=detail,
            result=result_row,
            token_snapshot=self.build_token_snapshot(phase_summaries, status="running", iteration=0),
            stream_payload=stream_payload,
        )

    def run(self) -> None:
        reset_runtime()
        prepare_workspace(self.per_eval_token_budget, self.max_completion_tokens)
        self.state.accepted_strategy_text = MANAGED_STRATEGY_FILE.read_text(encoding="utf-8")
        self.clear_provisional_state()
        self.sync_state("running", "baseline calibration")
        self.write_stream_start()
        append_log("strategy experiment v2 started")

        self.calibrate_baseline()

        stop_reason = "max_iterations_reached"
        while self.state.iteration < self.max_iterations:
            if self.state.failure_streak >= RUNTIME_FAILURE_BREAKER:
                stop_reason = "runtime_failure_breaker"
                break
            if self.state.validated_no_edge_streak >= VALIDATED_NO_EDGE_PATIENCE:
                stop_reason = "validated_no_edge_patience"
                break
            if not self.remaining_budget_allows_iteration():
                stop_reason = "global_token_budget_exhausted"
                append_log(
                    "global token budget exhausted before next iteration: "
                    f"total_eval_tokens={self.state.total_eval_tokens} "
                    f"estimated_iteration_cost={self.estimated_iteration_cost()} "
                    f"total_budget={self.total_token_budget}"
                )
                break

            self.state.iteration += 1
            scheduled_mode = self.select_search_mode()
            self.sync_state("running", f"iteration={self.state.iteration} mode={scheduled_mode}")
            append_log(
                f"iteration {self.state.iteration} patch generation started "
                f"mode={scheduled_mode} shortlist_size={SEARCH_CANDIDATES_PER_ITERATION}"
            )
            try:
                proposals = self.build_search_shortlist(self.state.iteration, scheduled_mode)
                search_shortlist = self.serialize_search_shortlist(proposals)
                selected = self.choose_ranked_search_candidate(proposals)
                write_text(MANAGED_PATCH_FILE, selected["patch_text"])

                worker_result = selected["worker_result"]
                candidate_text = selected["candidate_text"]
                before_cfg = selected["before_cfg"]
                candidate_cfg = selected["candidate_cfg"]
                patch_text = selected["patch_text"]
                patch_path = selected["patch_path"]
                phase_summaries: dict[str, dict[str, Any]] = {"search": selected["search_summary"]}
                passed_search_count = len([proposal for proposal in proposals if proposal["search_passed"]])
                search_logic = (
                    f"{selected['search_logic']} | "
                    f"selected_search_rank_score={selected['search_summary'].get('search_rank_score', 0.0):.6f} | "
                    f"search_passes={passed_search_count}/{len(proposals)}"
                )

                if passed_search_count == 0:
                    write_text(MANAGED_STRATEGY_FILE, self.state.accepted_strategy_text)
                    self.clear_provisional_state()
                    self.state.search_reject_streak += 1
                    self.state.failure_streak = 0
                    self.record_iteration_outcome(
                        iteration=self.state.iteration,
                        status="search_reject",
                        worker_result=worker_result,
                        before_cfg=before_cfg,
                        candidate_cfg=candidate_cfg,
                        patch_text=patch_text,
                        patch_path=patch_path,
                        phase_summaries=phase_summaries,
                        decision_logic=search_logic,
                        search_shortlist=search_shortlist,
                    )
                    self.sync_state("running", f"iteration={self.state.iteration} status=search_reject")
                    append_log(
                        f"iteration {self.state.iteration} finished with status=search_reject "
                        f"fitness={selected['search_summary']['fitness_median']} "
                        f"rank_score={selected['search_summary'].get('search_rank_score', 0.0):.6f}"
                    )
                    continue

                validation_summary = self.run_phase_bundle(candidate_text, "validation")
                phase_summaries["validation"] = validation_summary
                validation_passed, _validation_metrics, validation_logic = self.validation_gate(validation_summary)
                if not validation_passed:
                    write_text(MANAGED_STRATEGY_FILE, self.state.accepted_strategy_text)
                    self.state.search_reject_streak = 0
                    self.state.validated_no_edge_streak += 1
                    self.state.failure_streak = 0
                    self.clear_provisional_state()
                    self.record_iteration_outcome(
                        iteration=self.state.iteration,
                        status="validation_reject",
                        worker_result=worker_result,
                        before_cfg=before_cfg,
                        candidate_cfg=candidate_cfg,
                        patch_text=patch_text,
                        patch_path=patch_path,
                        phase_summaries=phase_summaries,
                        decision_logic=validation_logic,
                        search_shortlist=search_shortlist,
                    )
                    self.sync_state("running", f"iteration={self.state.iteration} status=validation_reject")
                    append_log(
                        f"iteration {self.state.iteration} finished with status=validation_reject "
                        f"fitness={validation_summary['fitness_median']}"
                    )
                    continue

                self.mark_provisional(self.state.iteration, candidate_cfg, phase_summaries)
                provisional_logic = f"{search_logic} | {validation_logic}"
                self.record_provisional_transition(
                    iteration=self.state.iteration,
                    worker_result=worker_result,
                    before_cfg=before_cfg,
                    candidate_cfg=candidate_cfg,
                    patch_text=patch_text,
                    patch_path=patch_path,
                    phase_summaries=phase_summaries,
                    decision_logic=provisional_logic,
                    search_shortlist=search_shortlist,
                )
                self.sync_state("running", f"iteration={self.state.iteration} status=provisional")

                holdout_summary = self.run_phase_bundle(candidate_text, "holdout")
                phase_summaries["holdout"] = holdout_summary
                holdout_passed, _holdout_metrics, holdout_logic = self.holdout_gate(holdout_summary)
                if not holdout_passed:
                    write_text(MANAGED_STRATEGY_FILE, self.state.accepted_strategy_text)
                    self.state.search_reject_streak = 0
                    self.state.failure_streak = 0
                    self.clear_provisional_state()
                    self.record_iteration_outcome(
                        iteration=self.state.iteration,
                        status="holdout_reject",
                        worker_result=worker_result,
                        before_cfg=before_cfg,
                        candidate_cfg=candidate_cfg,
                        patch_text=patch_text,
                        patch_path=patch_path,
                        phase_summaries=phase_summaries,
                        decision_logic=holdout_logic,
                        search_shortlist=search_shortlist,
                    )
                    self.sync_state("running", f"iteration={self.state.iteration} status=holdout_reject")
                    append_log(
                        f"iteration {self.state.iteration} finished with status=holdout_reject "
                        f"fitness={holdout_summary['fitness_median']}"
                    )
                    continue

                self.state.accepted_strategy_text = candidate_text
                self.state.accepted_config = compact_config(candidate_cfg)
                self.state.champion_phase_summaries = phase_summaries
                self.state.accepted_fitness = float(validation_summary["fitness_median"])
                self.state.best_fitness = max(float(self.state.best_fitness or 0.0), float(self.state.accepted_fitness))
                self.state.search_reject_streak = 0
                self.state.validated_no_edge_streak = 0
                self.state.failure_streak = 0
                self.clear_provisional_state()
                self.record_iteration_outcome(
                    iteration=self.state.iteration,
                    status="accepted",
                    worker_result=worker_result,
                    before_cfg=before_cfg,
                    candidate_cfg=candidate_cfg,
                    patch_text=patch_text,
                    patch_path=patch_path,
                    phase_summaries=phase_summaries,
                    decision_logic=(
                        f"{search_logic} | {validation_logic} | {holdout_logic} | "
                        f"accepted_validation_fitness={validation_summary['fitness_median']:.6f}"
                    ),
                    search_shortlist=search_shortlist,
                )
                self.sync_state("running", f"iteration={self.state.iteration} status=accepted")
                append_log(
                    f"iteration {self.state.iteration} finished with status=accepted "
                    f"validation_fitness={validation_summary['fitness_median']}"
                )
            except Exception as exc:
                write_text(MANAGED_STRATEGY_FILE, self.state.accepted_strategy_text)
                self.clear_provisional_state()
                self.state.failure_streak += 1
                append_log(f"iteration {self.state.iteration} failed: {str(exc)[:500]}")
                fail_phase_summaries: dict[str, dict[str, Any]] = {}
                fail_worker_result = {
                    "summary": "iteration_failed",
                    "feedback": [str(exc)],
                    "details": [str(exc)],
                    "change_axis": "unknown",
                    "search_intent": self.state.current_mode,
                }
                accepted_cfg = parse_strategy_config(self.state.accepted_strategy_text)
                _entry, result_row = self.record_history(
                    iteration=self.state.iteration,
                    patch_summary="iteration_failed",
                    phase_summaries=fail_phase_summaries,
                    status="failed",
                    config=compact_config(accepted_cfg),
                    decision_logic=f"failure_streak={self.state.failure_streak}: {str(exc)[:200]}",
                    worker_result=fail_worker_result,
                )
                detail = self.append_iteration_detail(
                    {
                        "iteration": self.state.iteration,
                        "status": "failed",
                        "kind": "patch_eval",
                        "patch_summary": "iteration_failed",
                        "reasoning": "本轮执行失败，实验已回退到上一版已接受策略。",
                        "change_axis": "unknown",
                        "search_intent": self.state.current_mode,
                        "step_size": "n/a",
                        "raw_reasoning": str(exc),
                        "worker_feedback": [str(exc)],
                        "adjustment_scope": [
                            "CONFIDENCE_THRESHOLD",
                            "BET_SIZING",
                            "MAX_BET_FRACTION",
                            "PROMPT_FACTORS",
                            "SYSTEM_PROMPT",
                            "USER_PROMPT_TEMPLATE",
                        ],
                        "prompt_change": {
                            "changed": False,
                            "summary": "Iteration failed before prompt comparison completed.",
                            "details": "Failure path reverted to the accepted strategy state.",
                        },
                        "config_before": accepted_cfg,
                        "config_after": accepted_cfg,
                        "config_diff": [],
                        "patch_path": str(MANAGED_PATCH_FILE) if MANAGED_PATCH_FILE.exists() else "",
                        "patch_excerpt": MANAGED_PATCH_FILE.read_text(encoding="utf-8")[:4000] if MANAGED_PATCH_FILE.exists() else "",
                        "phase_results": {},
                        "decision_logic": f"failure_streak={self.state.failure_streak}",
                        "search_shortlist": [],
                        "current_mode": self.state.current_mode,
                    }
                )
                self.sync_state("running", f"iteration={self.state.iteration} status=failed")
                stream_payload = self.append_iteration_event(self.state.iteration, "iteration_failed", {}, "failed")
                self.event_sink.append_iteration_recorded(
                    detail=detail,
                    result=result_row,
                    token_snapshot=self.build_token_snapshot({}, status="running", iteration=self.state.iteration),
                    stream_payload=stream_payload,
                )
                if self.state.failure_streak >= RUNTIME_FAILURE_BREAKER:
                    stop_reason = "runtime_failure_breaker"
                    break

        self.sync_state("finished", stop_reason)
        self.append_finish(float(self.state.best_fitness or 0.0), stop_reason)
        append_log(f"strategy experiment finished stop_reason={stop_reason}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--eval-timeout", type=int, default=900)
    parser.add_argument("--sample-size", type=int, default=200)
    parser.add_argument("--per-eval-token-budget", type=int, default=DEFAULT_PER_EVAL_TOKEN_BUDGET)
    parser.add_argument("--total-token-budget", type=int, default=DEFAULT_TOTAL_TOKEN_BUDGET)
    parser.add_argument("--max-completion-tokens", type=int, default=DEFAULT_MAX_COMPLETION_TOKENS)
    parser.add_argument("--codex-worker-cmd", default="", help="optional override command")
    parser.add_argument("--runtime-dir", default="", help="task runtime directory")
    parser.add_argument("--source-dir", default="", help="source autoresearch directory")
    parser.add_argument("--eval-data-path", default="", help="canonical evaluation dataset for this run")
    parser.add_argument("--run-id", default="", help="task run_id")
    parser.add_argument("--run-spec-path", default="", help="path to task run_spec.json")
    parser.add_argument(
        "--allowed-axes",
        default="",
        help="comma-separated allowed search axes: CONFIDENCE_THRESHOLD,BET_SIZING,MAX_BET_FRACTION,PROMPT_FACTORS",
    )
    args = parser.parse_args()

    if args.runtime_dir:
        configure_runtime_paths(Path(args.runtime_dir))
    configure_source_paths(
        Path(args.source_dir) if args.source_dir else None,
        Path(args.eval_data_path) if args.eval_data_path else None,
    )
    configure_run_context(
        run_id=args.run_id,
        run_spec_path=Path(args.run_spec_path) if args.run_spec_path else None,
    )
    allowed_search_axes = sorted(SUPPORTED_ALLOWED_AXES)
    if args.allowed_axes.strip():
        requested_axes = [item.strip() for item in args.allowed_axes.split(",") if item.strip()]
        allowed_search_axes = [axis for axis in requested_axes if axis in SUPPORTED_ALLOWED_AXES]
        if not allowed_search_axes:
            raise ValueError("allowed_axes resolved to empty set")

    experiment = StrategyExperiment(
        max_iterations=max(args.max_iterations, 1),
        eval_timeout=max(args.eval_timeout, 60),
        codex_worker_cmd=args.codex_worker_cmd,
        sample_size=min(max(args.sample_size, 1), 200),
        per_eval_token_budget=max(args.per_eval_token_budget, 1),
        total_token_budget=max(args.total_token_budget, 0),
        max_completion_tokens=max(args.max_completion_tokens, 1),
        allowed_search_axes=allowed_search_axes,
    )
    experiment.run()
    print(f"strategy experiment runtime written to {RUNTIME_DIR}")


if __name__ == "__main__":
    main()
