#!/usr/bin/env python3
"""
External coding worker for controlled pm_train.py patch generation.

Invocation:
  python3 demo/codex_strategy_worker.py <input_json> <output_json>
"""

from __future__ import annotations

import ast
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any


THRESHOLD_MIN = 0.55
THRESHOLD_MAX = 0.85
THRESHOLD_STEP = 0.02
THRESHOLD_RECOVERY_STEP = 0.01
MAX_BET_MIN = 0.05
MAX_BET_MAX = 0.20
MAX_BET_STEP = 0.01
MAX_BET_RECOVERY_STEP = 0.005
BET_SIZING_OPTIONS = ["fixed", "confidence_scaled", "kelly"]
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


def expect_type(name: str, value: Any, expected_type: type) -> None:
    if not isinstance(value, expected_type):
        raise ValueError(f"{name} must be {expected_type.__name__}")


def expect_list_of_str(name: str, value: Any) -> list[str]:
    expect_type(name, value, list)
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} must contain only strings")
    return value


def validate_input(payload: dict[str, Any]) -> dict[str, Any]:
    required = ["task", "managed_target_file", "managed_patch_file", "experiment_state"]
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"missing input fields: {', '.join(missing)}")
    expect_type("task", payload["task"], dict)
    expect_type("managed_target_file", payload["managed_target_file"], str)
    expect_type("managed_patch_file", payload["managed_patch_file"], str)
    expect_type("experiment_state", payload["experiment_state"], dict)
    return payload


def validate_output(payload: dict[str, Any], patch_path: Path) -> dict[str, Any]:
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
        "change_axis",
        "search_intent",
        "candidate_config",
        "step_size",
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
    expect_type("change_axis", payload["change_axis"], str)
    expect_type("search_intent", payload["search_intent"], str)
    expect_type("candidate_config", payload["candidate_config"], dict)
    expect_type("step_size", payload["step_size"], str)
    if artifacts != [str(patch_path)]:
        raise ValueError("artifacts must contain only the patch path")
    return payload


def build_patch(old_text: str, new_text: str, file_label: str) -> str:
    return "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=file_label,
            tofile=file_label,
        )
    )


def replace_one(text: str, pattern: str, repl: str) -> str:
    updated, count = re.subn(pattern, repl, text, count=1, flags=re.MULTILINE | re.DOTALL)
    if count != 1:
        raise ValueError(f"pattern not found: {pattern}")
    return updated


def infer_prompt_factors(system_prompt: str, user_prompt: str) -> list[str]:
    combined = f"{system_prompt}\n{user_prompt}"
    factors = []
    if (
        "Treat extreme 0.0 or 1.0 prices" in combined
        or "Treat extreme prices" in combined
        or "Compare the market-implied probability" in combined
    ):
        factors.append("extreme_price_skepticism")
    if (
        "disconfirming evidence" in combined
        or "evidence could make the market wrong" in combined
        or "optimizing for calibration, not certainty." in combined
    ):
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


def parse_current_config(text: str) -> dict[str, Any]:
    threshold_match = re.search(r"^CONFIDENCE_THRESHOLD = ([0-9.]+)$", text, flags=re.MULTILINE)
    sizing_match = re.search(r'^BET_SIZING = "([^"]+)"$', text, flags=re.MULTILINE)
    max_bet_match = re.search(r"^MAX_BET_FRACTION = ([0-9.]+)$", text, flags=re.MULTILINE)
    prompt_factors_match = re.search(r"^PROMPT_FACTORS = (\[[^\n]*\])$", text, flags=re.MULTILINE)
    system_match = re.search(r'SYSTEM_PROMPT = """(.*?)"""', text, flags=re.MULTILINE | re.DOTALL)
    user_match = re.search(r'USER_PROMPT_TEMPLATE = """(.*?)"""', text, flags=re.MULTILINE | re.DOTALL)
    if not threshold_match or not sizing_match or not max_bet_match:
        raise ValueError("failed to parse current config")
    if prompt_factors_match:
        try:
            prompt_factors = normalize_prompt_factors(ast.literal_eval(prompt_factors_match.group(1)))
        except (ValueError, SyntaxError):
            prompt_factors = []
        system_prompt, user_prompt = build_prompt_text(prompt_factors)
    else:
        if not system_match or not user_match:
            raise ValueError("failed to parse current config")
        system_prompt = system_match.group(1)
        user_prompt = user_match.group(1)
        prompt_factors = infer_prompt_factors(system_prompt, user_prompt)
    return {
        "CONFIDENCE_THRESHOLD": float(threshold_match.group(1)),
        "BET_SIZING": sizing_match.group(1),
        "MAX_BET_FRACTION": float(max_bet_match.group(1)),
        "PROMPT_FACTORS": prompt_factors,
        "PROMPT_PROFILE": prompt_profile_label(prompt_factors),
        "SYSTEM_PROMPT": system_prompt,
        "USER_PROMPT_TEMPLATE": user_prompt,
    }


def normalize_threshold(value: float) -> float:
    return round(max(THRESHOLD_MIN, min(THRESHOLD_MAX, value)), 2)


def normalize_max_bet(value: float) -> float:
    return round(max(MAX_BET_MIN, min(MAX_BET_MAX, value)), 3)


def config_signature(config: dict[str, Any]) -> tuple[Any, ...]:
    return (
        round(float(config.get("CONFIDENCE_THRESHOLD", 0.0)), 2),
        config.get("BET_SIZING", ""),
        round(float(config.get("MAX_BET_FRACTION", 0.0)), 3),
        tuple(sorted(config.get("PROMPT_FACTORS", []))),
    )


def make_candidate(
    current: dict[str, Any],
    *,
    change_axis: str,
    search_intent: str,
    step_size: str,
    confidence_threshold: float | None = None,
    bet_sizing: str | None = None,
    max_bet_fraction: float | None = None,
    prompt_factors: list[str] | None = None,
    factor_change: str = "",
) -> dict[str, Any]:
    candidate = {
        "CONFIDENCE_THRESHOLD": round(
            float(current["CONFIDENCE_THRESHOLD"] if confidence_threshold is None else confidence_threshold),
            2,
        ),
        "BET_SIZING": current["BET_SIZING"] if bet_sizing is None else bet_sizing,
        "MAX_BET_FRACTION": round(
            float(current["MAX_BET_FRACTION"] if max_bet_fraction is None else max_bet_fraction),
            3,
        ),
        "PROMPT_FACTORS": normalize_prompt_factors(
            list(current["PROMPT_FACTORS"] if prompt_factors is None else prompt_factors)
        ),
        "change_axis": change_axis,
        "search_intent": search_intent,
        "step_size": step_size,
        "factor_change": factor_change,
    }
    candidate["PROMPT_PROFILE"] = prompt_profile_label(candidate["PROMPT_FACTORS"])
    return candidate


def prompt_factor_moves(current_factors: list[str], *, prefer_remove: bool) -> list[tuple[list[str], str]]:
    current_set = set(current_factors)
    moves: list[tuple[list[str], str]] = []
    ordered = list(PROMPT_FACTOR_ORDER)
    if prefer_remove:
        for factor in ordered:
            if factor in current_set:
                next_factors = sorted(current_set - {factor})
                moves.append((next_factors, f"remove:{factor}"))
        for factor in ordered:
            if factor not in current_set:
                next_factors = sorted(current_set | {factor})
                moves.append((next_factors, f"add:{factor}"))
    else:
        for factor in ordered:
            if factor not in current_set:
                next_factors = sorted(current_set | {factor})
                moves.append((next_factors, f"add:{factor}"))
        for factor in ordered:
            if factor in current_set:
                next_factors = sorted(current_set - {factor})
                moves.append((next_factors, f"remove:{factor}"))
    return moves


def ordered_numeric_values(
    current: float,
    step: float,
    *,
    lower: float,
    upper: float,
    mode: str,
    favor_conservative: bool,
    precision: int,
) -> list[float]:
    offsets = [step, step * 2, -step, -step * 2] if favor_conservative else [-step, step, -step * 2, step * 2]
    if mode == "recovery":
        offsets = [step, -step, step * 2]
    values = []
    for offset in offsets:
        candidate = round(current + offset, precision)
        candidate = max(lower, min(upper, candidate))
        if candidate != round(current, precision) and candidate not in values:
            values.append(candidate)
    return values


def build_mode_candidates(current: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    if mode == "recovery":
        for value in ordered_numeric_values(
            current["MAX_BET_FRACTION"],
            MAX_BET_RECOVERY_STEP,
            lower=MAX_BET_MIN,
            upper=MAX_BET_MAX,
            mode=mode,
            favor_conservative=True,
            precision=3,
        ):
            candidates.append(
                make_candidate(
                    current,
                    change_axis="MAX_BET_FRACTION",
                    search_intent="recovery",
                    step_size=f"{MAX_BET_RECOVERY_STEP:.3f}",
                    max_bet_fraction=normalize_max_bet(value),
                )
            )
        for value in ordered_numeric_values(
            current["CONFIDENCE_THRESHOLD"],
            THRESHOLD_RECOVERY_STEP,
            lower=THRESHOLD_MIN,
            upper=THRESHOLD_MAX,
            mode=mode,
            favor_conservative=True,
            precision=2,
        ):
            candidates.append(
                make_candidate(
                    current,
                    change_axis="CONFIDENCE_THRESHOLD",
                    search_intent="recovery",
                    step_size=f"{THRESHOLD_RECOVERY_STEP:.2f}",
                    confidence_threshold=normalize_threshold(value),
                )
            )
        for next_factors, factor_change in prompt_factor_moves(current["PROMPT_FACTORS"], prefer_remove=True):
            candidates.append(
                make_candidate(
                    current,
                    change_axis="PROMPT_FACTORS",
                    search_intent="recovery",
                    step_size="single_factor",
                    prompt_factors=next_factors,
                    factor_change=factor_change,
                )
            )
        return candidates

    if mode == "structured_exploration":
        for next_factors, factor_change in prompt_factor_moves(current["PROMPT_FACTORS"], prefer_remove=False):
            candidates.append(
                make_candidate(
                    current,
                    change_axis="PROMPT_FACTORS",
                    search_intent="structured_exploration",
                    step_size="single_factor",
                    prompt_factors=next_factors,
                    factor_change=factor_change,
                )
            )
        for sizing in BET_SIZING_OPTIONS:
            if sizing != current["BET_SIZING"]:
                candidates.append(
                    make_candidate(
                        current,
                        change_axis="BET_SIZING",
                        search_intent="structured_exploration",
                        step_size="categorical",
                        bet_sizing=sizing,
                    )
                )
        for value in ordered_numeric_values(
            current["CONFIDENCE_THRESHOLD"],
            THRESHOLD_STEP,
            lower=THRESHOLD_MIN,
            upper=THRESHOLD_MAX,
            mode=mode,
            favor_conservative=False,
            precision=2,
        ):
            candidates.append(
                make_candidate(
                    current,
                    change_axis="CONFIDENCE_THRESHOLD",
                    search_intent="structured_exploration",
                    step_size=f"{THRESHOLD_STEP:.2f}",
                    confidence_threshold=normalize_threshold(value),
                )
            )
        for value in ordered_numeric_values(
            current["MAX_BET_FRACTION"],
            MAX_BET_STEP,
            lower=MAX_BET_MIN,
            upper=MAX_BET_MAX,
            mode=mode,
            favor_conservative=False,
            precision=3,
        ):
            candidates.append(
                make_candidate(
                    current,
                    change_axis="MAX_BET_FRACTION",
                    search_intent="structured_exploration",
                    step_size=f"{MAX_BET_STEP:.2f}",
                    max_bet_fraction=normalize_max_bet(value),
                )
            )
        return candidates

    for value in ordered_numeric_values(
        current["CONFIDENCE_THRESHOLD"],
        THRESHOLD_STEP,
        lower=THRESHOLD_MIN,
        upper=THRESHOLD_MAX,
        mode=mode,
        favor_conservative=False,
        precision=2,
    ):
        candidates.append(
            make_candidate(
                current,
                change_axis="CONFIDENCE_THRESHOLD",
                search_intent="exploit_local",
                step_size=f"{THRESHOLD_STEP:.2f}",
                confidence_threshold=normalize_threshold(value),
            )
        )
    for value in ordered_numeric_values(
        current["MAX_BET_FRACTION"],
        MAX_BET_STEP,
        lower=MAX_BET_MIN,
        upper=MAX_BET_MAX,
        mode=mode,
        favor_conservative=False,
        precision=3,
    ):
        candidates.append(
            make_candidate(
                current,
                change_axis="MAX_BET_FRACTION",
                search_intent="exploit_local",
                step_size=f"{MAX_BET_STEP:.2f}",
                max_bet_fraction=normalize_max_bet(value),
            )
        )
    for sizing in BET_SIZING_OPTIONS:
        if sizing != current["BET_SIZING"]:
            candidates.append(
                make_candidate(
                    current,
                    change_axis="BET_SIZING",
                    search_intent="exploit_local",
                    step_size="categorical",
                    bet_sizing=sizing,
                )
            )
    for next_factors, factor_change in prompt_factor_moves(current["PROMPT_FACTORS"], prefer_remove=False):
        candidates.append(
            make_candidate(
                current,
                change_axis="PROMPT_FACTORS",
                search_intent="exploit_local",
                step_size="single_factor",
                prompt_factors=next_factors,
                factor_change=factor_change,
            )
        )
    return candidates


def fallback_candidates(current: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    exhaustive: list[dict[str, Any]] = []
    threshold_values = []
    value = THRESHOLD_MIN
    while value <= THRESHOLD_MAX + 1e-9:
        rounded = round(value, 2)
        if rounded != round(current["CONFIDENCE_THRESHOLD"], 2):
            threshold_values.append(rounded)
        value = round(value + THRESHOLD_STEP, 2)
    for threshold in threshold_values:
        exhaustive.append(
            make_candidate(
                current,
                change_axis="CONFIDENCE_THRESHOLD",
                search_intent=mode,
                step_size="grid_fallback",
                confidence_threshold=threshold,
            )
        )

    max_bet_values = []
    value = MAX_BET_MIN
    while value <= MAX_BET_MAX + 1e-9:
        rounded = round(value, 3)
        if rounded != round(current["MAX_BET_FRACTION"], 3):
            max_bet_values.append(rounded)
        value = round(value + MAX_BET_RECOVERY_STEP, 3)
    for max_bet in max_bet_values:
        exhaustive.append(
            make_candidate(
                current,
                change_axis="MAX_BET_FRACTION",
                search_intent=mode,
                step_size="grid_fallback",
                max_bet_fraction=max_bet,
            )
        )

    for sizing in BET_SIZING_OPTIONS:
        if sizing != current["BET_SIZING"]:
            exhaustive.append(
                make_candidate(
                    current,
                    change_axis="BET_SIZING",
                    search_intent=mode,
                    step_size="categorical",
                    bet_sizing=sizing,
                )
            )

    for next_factors, factor_change in prompt_factor_moves(current["PROMPT_FACTORS"], prefer_remove=False):
        exhaustive.append(
            make_candidate(
                current,
                change_axis="PROMPT_FACTORS",
                search_intent=mode,
                step_size="single_factor",
                prompt_factors=next_factors,
                factor_change=factor_change,
            )
        )
    return exhaustive


def choose_next_config(experiment_state: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    current_mode = str(
        experiment_state.get("requested_mode")
        or experiment_state.get("current_mode")
        or "exploit_local"
    )
    tried_signatures = {
        config_signature(item)
        for item in experiment_state.get("tried_configs", [])
        if isinstance(item, dict)
    }
    tried_signatures.update(
        config_signature(item)
        for item in experiment_state.get("excluded_configs", [])
        if isinstance(item, dict)
    )
    tried_signatures.add(config_signature(current))
    allowed_axes = experiment_state.get("allowed_axes", list(SUPPORTED_ALLOWED_AXES))
    if not isinstance(allowed_axes, list):
        allowed_axes = list(SUPPORTED_ALLOWED_AXES)
    allowed_axes_set = {axis for axis in allowed_axes if axis in SUPPORTED_ALLOWED_AXES} or set(SUPPORTED_ALLOWED_AXES)

    for candidate in build_mode_candidates(current, current_mode):
        if candidate["change_axis"] not in allowed_axes_set:
            continue
        if config_signature(candidate) not in tried_signatures:
            return candidate
    for candidate in fallback_candidates(current, current_mode):
        if candidate["change_axis"] not in allowed_axes_set:
            continue
        if config_signature(candidate) not in tried_signatures:
            return candidate

    # If every signature has been tried, reuse the best local exploit move rather than failing.
    fallback = [item for item in build_mode_candidates(current, "exploit_local") if item["change_axis"] in allowed_axes_set]
    if fallback:
        return fallback[0]
    raise ValueError("no candidate config available under allowed_axes constraints")


def format_max_bet_fraction(value: float) -> str:
    formatted = f"{value:.3f}"
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def format_prompt_factor_literal(factors: list[str]) -> str:
    return "[" + ", ".join(json.dumps(factor) for factor in normalize_prompt_factors(factors)) + "]"


def apply_candidate_config(text: str, current: dict[str, Any], candidate: dict[str, Any]) -> str:
    updated = replace_one(
        text,
        r"^CONFIDENCE_THRESHOLD = [0-9.]+$",
        f"CONFIDENCE_THRESHOLD = {candidate['CONFIDENCE_THRESHOLD']:.2f}",
    )
    updated = replace_one(
        updated,
        r'^BET_SIZING = "[^"]+"$',
        f'BET_SIZING = "{candidate["BET_SIZING"]}"',
    )
    updated = replace_one(
        updated,
        r"^MAX_BET_FRACTION = [0-9.]+$",
        f"MAX_BET_FRACTION = {format_max_bet_fraction(candidate['MAX_BET_FRACTION'])}",
    )
    if sorted(candidate["PROMPT_FACTORS"]) != sorted(current["PROMPT_FACTORS"]):
        if re.search(r"^PROMPT_FACTORS = \[[^\n]*\]$", updated, flags=re.MULTILINE):
            updated = replace_one(
                updated,
                r"^PROMPT_FACTORS = \[[^\n]*\]$",
                f"PROMPT_FACTORS = {format_prompt_factor_literal(candidate['PROMPT_FACTORS'])}",
            )
        else:
            system_prompt, user_prompt = build_prompt_text(candidate["PROMPT_FACTORS"])
            updated = replace_one(
                updated,
                r'SYSTEM_PROMPT = """(.*?)"""',
                'SYSTEM_PROMPT = """' + system_prompt + '"""',
            )
            updated = replace_one(
                updated,
                r'USER_PROMPT_TEMPLATE = """(.*?)"""',
                'USER_PROMPT_TEMPLATE = """' + user_prompt + '"""',
            )
    return updated


def estimate_tokens(*texts: str) -> int:
    chars = sum(len(text) for text in texts)
    return max(1, chars // 4)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: python3 demo/codex_strategy_worker.py <input_json> <output_json>", file=sys.stderr)
        return 2

    input_path = Path(sys.argv[1]).expanduser().resolve()
    output_path = Path(sys.argv[2]).expanduser().resolve()
    payload = validate_input(load_json(input_path))

    target_path = Path(payload["managed_target_file"]).expanduser().resolve()
    patch_path = Path(payload["managed_patch_file"]).expanduser().resolve()
    experiment_state = payload["experiment_state"]

    current_text = target_path.read_text(encoding="utf-8")
    current_config = parse_current_config(current_text)
    candidate = choose_next_config(experiment_state, current_config)
    updated_text = apply_candidate_config(current_text, current_config, candidate)
    patch_text = build_patch(current_text, updated_text, target_path.name)
    write_text(patch_path, patch_text)

    factor_detail = candidate.get("factor_change", "")
    summary = (
        f"Proposed single-axis patch: axis={candidate['change_axis']} "
        f"mode={candidate['search_intent']} "
        f"threshold={candidate['CONFIDENCE_THRESHOLD']:.2f} "
        f"bet_sizing={candidate['BET_SIZING']} "
        f"max_bet={format_max_bet_fraction(candidate['MAX_BET_FRACTION'])} "
        f"prompt_profile={candidate['PROMPT_PROFILE']}"
    )
    if factor_detail:
        summary += f" ({factor_detail})"

    output = {
        "summary": summary,
        "deliverables": [patch_path.name],
        "tool_summary": [f"write: {patch_path.name}"],
        "feedback": [
            "Single-axis neighborhood patch generated for controlled pm_train.py target.",
            f"Mode={candidate['search_intent']} axis={candidate['change_axis']} step={candidate['step_size']}",
        ],
        "next_action": "Run search gate first, then validation and holdout only if the candidate clears earlier gates.",
        "details": [
            f"current_config={current_config}",
            f"candidate_config={candidate}",
            f"current_mode={experiment_state.get('current_mode')}",
            f"requested_mode={experiment_state.get('requested_mode')}",
            f"excluded_configs={len(experiment_state.get('excluded_configs', []))}",
            f"validated_no_edge_streak={experiment_state.get('validated_no_edge_streak')}",
            f"search_reject_streak={experiment_state.get('search_reject_streak')}",
            f"target_path={target_path}",
            f"patch_path={patch_path}",
        ],
        "artifacts": [str(patch_path)],
        "prompt_tokens": estimate_tokens(current_text, json.dumps(experiment_state, ensure_ascii=False)),
        "completion_tokens": estimate_tokens(summary, json.dumps(candidate, ensure_ascii=False)),
        "change_axis": candidate["change_axis"],
        "search_intent": candidate["search_intent"],
        "candidate_config": {
            "CONFIDENCE_THRESHOLD": candidate["CONFIDENCE_THRESHOLD"],
            "BET_SIZING": candidate["BET_SIZING"],
            "MAX_BET_FRACTION": candidate["MAX_BET_FRACTION"],
            "PROMPT_FACTORS": candidate["PROMPT_FACTORS"],
            "PROMPT_PROFILE": candidate["PROMPT_PROFILE"],
        },
        "step_size": candidate["step_size"],
    }
    atomic_write_json(output_path, validate_output(output, patch_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
