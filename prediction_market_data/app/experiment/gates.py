from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GatePolicy:
    search_min_delta_fitness: float
    validation_min_delta_fitness: float
    validation_std_multiplier: float
    max_drawdown_deterioration: float
    holdout_max_drawdown_deterioration: float
    max_token_per_market_ratio: float
    high_token_validation_delta: float
    trade_ratio_band: tuple[float, float]


def safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 1.0 if numerator <= 0 else float("inf")
    return numerator / denominator


def round_metric(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


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


def within_trade_band(value: float, trade_ratio_band: tuple[float, float]) -> bool:
    return trade_ratio_band[0] <= value <= trade_ratio_band[1]


def evaluate_search_gate(candidate_summary: dict[str, Any], champion_summary: dict[str, Any], policy: GatePolicy) -> tuple[bool, dict[str, float], str]:
    metrics = summarize_gate_metrics(candidate_summary, champion_summary)
    checks = [
        metrics["delta_fitness"] >= policy.search_min_delta_fitness,
        metrics["delta_drawdown"] <= policy.max_drawdown_deterioration,
        within_trade_band(metrics["trade_ratio"], policy.trade_ratio_band),
        metrics["token_ratio"] <= policy.max_token_per_market_ratio,
    ]
    decision_logic = (
        f"search delta_fitness={metrics['delta_fitness']:.6f}, "
        f"delta_search_rank_score={metrics['delta_search_rank_score']:.6f}, "
        f"delta_drawdown={metrics['delta_drawdown']:.4f}, "
        f"trade_ratio={metrics['trade_ratio']:.4f}, "
        f"token_ratio={metrics['token_ratio']:.4f}"
    )
    return all(checks), metrics, decision_logic


def evaluate_validation_gate(
    candidate_summary: dict[str, Any],
    champion_summary: dict[str, Any],
    policy: GatePolicy,
    baseline_validation_std: float,
) -> tuple[bool, dict[str, float], str]:
    metrics = summarize_gate_metrics(candidate_summary, champion_summary)
    effect_floor = max(policy.validation_min_delta_fitness, policy.validation_std_multiplier * baseline_validation_std)
    passed = (
        metrics["delta_fitness"] >= effect_floor
        and metrics["delta_pnl"] > 0
        and metrics["delta_drawdown"] <= policy.max_drawdown_deterioration
        and within_trade_band(metrics["trade_ratio"], policy.trade_ratio_band)
        and (
            metrics["token_ratio"] <= policy.max_token_per_market_ratio
            or metrics["delta_fitness"] >= policy.high_token_validation_delta
        )
    )
    decision_logic = (
        f"validation delta_fitness={metrics['delta_fitness']:.6f} "
        f"(effect_floor={effect_floor:.6f}), "
        f"delta_pnl={metrics['delta_pnl']:.2f}, "
        f"delta_drawdown={metrics['delta_drawdown']:.4f}, "
        f"trade_ratio={metrics['trade_ratio']:.4f}, "
        f"token_ratio={metrics['token_ratio']:.4f}"
    )
    return passed, metrics, decision_logic


def evaluate_holdout_gate(candidate_summary: dict[str, Any], champion_summary: dict[str, Any], policy: GatePolicy) -> tuple[bool, dict[str, float], str]:
    metrics = summarize_gate_metrics(candidate_summary, champion_summary)
    passed = metrics["delta_fitness"] >= 0 and metrics["delta_drawdown"] <= policy.holdout_max_drawdown_deterioration
    decision_logic = (
        f"holdout delta_fitness={metrics['delta_fitness']:.6f}, "
        f"delta_drawdown={metrics['delta_drawdown']:.4f}"
    )
    return passed, metrics, decision_logic
