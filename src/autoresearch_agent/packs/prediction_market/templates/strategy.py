"""Default prediction-market strategy template.

This file is intentionally small and easy to mutate. Users are expected to keep
the editable surface constrained to this file in V1.
"""

from __future__ import annotations

CONFIDENCE_THRESHOLD = 0.75
BET_SIZING = "confidence_scaled"
MAX_BET_FRACTION = 0.15
PROMPT_FACTORS = []


def build_system_prompt(prompt_factors: list[str]) -> str:
    factors = sorted(prompt_factors)
    if not factors:
        return """You are a prediction market analyst. For each market, you must:
1. Briefly explain your thinking process (2-3 sentences)
2. Make a prediction

Respond ONLY with valid JSON, no other text."""

    lines = [
        "You are a prediction market analyst focused on calibrated, risk-adjusted edge.",
        "For each market:",
        "1. Briefly explain your thinking process (2-3 sentences).",
        "2. Compare the market-implied probability with plausible real-world uncertainty.",
        "3. Explicitly consider why the market might be wrong before making a prediction.",
    ]
    if "extreme_price_skepticism" in factors:
        lines.append("Treat extreme 0.0 or 1.0 prices with skepticism unless supporting evidence is strong.")
    if "evidence_balance" in factors:
        lines.append("Balance supporting evidence with disconfirming evidence before locking the final view.")
    if "volume_awareness" in factors:
        lines.append("Treat low-volume markets as noisier and use volume as a reliability signal.")
    if "event_type_branching" in factors:
        lines.append("Adjust emphasis based on the market category and event type before assigning confidence.")
    lines.append("")
    lines.append("Respond ONLY with valid JSON, no other text.")
    return "\n".join(lines)


def build_user_prompt_template(prompt_factors: list[str]) -> str:
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

    focus = []
    if "extreme_price_skepticism" in factors:
        focus.append("Treat extreme prices as potentially stale or overconfident if evidence is weak.")
    if "evidence_balance" in factors:
        focus.append("Balance supporting and disconfirming evidence before finalizing the prediction.")
    if "volume_awareness" in factors:
        focus.append("Treat low-volume markets as noisier and use volume as a reliability signal.")
    if "event_type_branching" in factors:
        focus.append("Use the market category and event type as a branching cue for what evidence matters most.")

    lines = [
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
        "Focus checklist:",
    ]
    for item in focus:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "Reply with JSON only:",
            '{{"prediction": 0 or 1, "confidence": 0.0 to 1.0, "thinking": "2-3 sentences on your analysis logic", "reasoning": "one-line conclusion"}}',
        ]
    )
    return "\n".join(lines)


SYSTEM_PROMPT = build_system_prompt(PROMPT_FACTORS)
USER_PROMPT_TEMPLATE = build_user_prompt_template(PROMPT_FACTORS)
