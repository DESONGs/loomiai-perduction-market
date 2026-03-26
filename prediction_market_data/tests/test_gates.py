from __future__ import annotations

import unittest

from app.experiment.gates import GatePolicy, evaluate_holdout_gate, evaluate_search_gate, evaluate_validation_gate


POLICY = GatePolicy(
    search_min_delta_fitness=0.1,
    validation_min_delta_fitness=0.15,
    validation_std_multiplier=0.5,
    max_drawdown_deterioration=0.02,
    holdout_max_drawdown_deterioration=0.015,
    max_token_per_market_ratio=1.25,
    high_token_validation_delta=0.35,
    trade_ratio_band=(0.7, 1.3),
)


class GatePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.champion = {
            "fitness_median": 1.0,
            "search_rank_score": 0.9,
            "total_pnl_mean": 10.0,
            "max_drawdown_max": 0.05,
            "num_trades_mean": 20.0,
            "token_per_market_mean": 100.0,
        }

    def test_search_gate_accepts_improved_candidate(self) -> None:
        candidate = dict(self.champion, fitness_median=1.2, search_rank_score=1.1, max_drawdown_max=0.055, token_per_market_mean=110.0)
        passed, metrics, _logic = evaluate_search_gate(candidate, self.champion, POLICY)
        self.assertTrue(passed)
        self.assertGreater(metrics["delta_fitness"], 0.1)

    def test_validation_gate_rejects_no_edge_candidate(self) -> None:
        candidate = dict(self.champion, fitness_median=1.04, total_pnl_mean=9.0, token_per_market_mean=140.0)
        passed, _metrics, logic = evaluate_validation_gate(candidate, self.champion, POLICY, baseline_validation_std=0.1)
        self.assertFalse(passed)
        self.assertIn("effect_floor", logic)

    def test_holdout_gate_respects_drawdown_guard(self) -> None:
        candidate = dict(self.champion, fitness_median=1.0, max_drawdown_max=0.08)
        passed, _metrics, _logic = evaluate_holdout_gate(candidate, self.champion, POLICY)
        self.assertFalse(passed)
