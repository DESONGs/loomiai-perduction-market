from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autoresearch_agent.core.runtime import RuntimeManager, load_runtime_spec


class RuntimeFlowTests(unittest.TestCase):
    def _write_sample_project(
        self,
        base_dir: Path,
        *,
        editable_target: str = "workspace/strategy.py",
        artifacts_dir: str = "./artifacts",
        outputs: dict[str, bool] | None = None,
    ) -> Path:
        datasets_dir = base_dir / "datasets"
        datasets_dir.mkdir(parents=True, exist_ok=True)
        workspace_dir = base_dir / "workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        strategy_path = base_dir / editable_target
        strategy_path.parent.mkdir(parents=True, exist_ok=True)
        dataset_path = datasets_dir / "markets.json"
        dataset_path.write_text(
            json.dumps(
                [
                    {
                        "market_id": "m1",
                        "question": "Will it rain tomorrow?",
                        "outcomes": ["Yes", "No"],
                        "last_trade_price": 0.8,
                        "final_resolution_index": 0,
                        "volume": 1200,
                        "context": {"category": "weather", "liquidity": 500.0},
                    },
                    {
                        "market_id": "m2",
                        "question": "Will the team win?",
                        "outcomes": ["Yes", "No"],
                        "last_trade_price": 0.2,
                        "final_resolution_index": 1,
                        "volume": 2400,
                        "context": {"category": "sports", "liquidity": 900.0},
                    },
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        strategy_path.write_text(
            "\n".join(
                [
                    "CONFIDENCE_THRESHOLD = 0.75",
                    'BET_SIZING = "confidence_scaled"',
                    "MAX_BET_FRACTION = 0.15",
                    "PROMPT_FACTORS = []",
                    "",
                    "def strategy(record, config=None):",
                    "    active = {",
                    "        'confidence_threshold': CONFIDENCE_THRESHOLD,",
                    "        'bet_sizing': BET_SIZING,",
                    "        'max_bet_fraction': MAX_BET_FRACTION,",
                    "        'prompt_factors': list(PROMPT_FACTORS),",
                    "    }",
                    "    if config:",
                    "        active.update(config)",
                    "    price = float(record.get('last_trade_price', 0.0) or 0.0)",
                    "    threshold = float(active['confidence_threshold'])",
                    "    predicted = 0 if price >= threshold else 1",
                    "    confidence = min(1.0, max(0.0, abs(price - 0.5) * 2))",
                    "    return {",
                    "        'action': 'buy',",
                    "        'outcome_index': predicted,",
                    "        'size': float(active['max_bet_fraction']) * max(0.25, confidence),",
                    "        'prediction': predicted,",
                    "        'confidence': confidence,",
                    "    }",
                ]
            ),
            encoding="utf-8",
        )

        spec_path = base_dir / "research.yaml"
        spec_path.write_text(
            "\n".join(
                [
                    "schema_version: research.yaml.v1",
                    "project:",
                    "  name: demo-research",
                    "  workspace_dir: ./workspace",
                    f"  artifacts_dir: {artifacts_dir}",
                    "  runs_dir: ./.autoresearch/runs",
                    "pack:",
                    "  id: prediction_market",
                    "  version: latest",
                    "data:",
                    f"  source: {dataset_path.as_posix()}",
                    "  format: auto",
                    "  adapter: auto",
                    "  snapshot_on_run: true",
                    "  sampling:",
                    "    mode: fixed_count",
                    "    max_records: 2",
                    "    seed: 42",
                    "objective:",
                    "  primary: maximize_pnl",
                    "  secondary:",
                    "    - maximize_accuracy",
                    "    - minimize_drawdown",
                    "search:",
                    "  editable_targets:",
                    f"    - {editable_target}",
                    "  allowed_axes:",
                    "    - prompt_factors",
                    "    - confidence_threshold",
                    "    - bet_sizing",
                    "    - max_bet_fraction",
                    "  max_iterations: 3",
                    "  candidates_per_iteration: 2",
                    "evaluation:",
                    "  sample_size: 2",
                    "  search_repeats: 2",
                    "  validation_repeats: 2",
                    "  holdout_repeats: 1",
                    "constraints:",
                    "  total_token_budget: 0",
                    "  per_eval_token_budget: 150000",
                    "  max_completion_tokens: 1200",
                    "  eval_timeout_seconds: 900",
                    "  max_runtime_minutes: 240",
                    "  max_memory_mb: 4096",
                    "  max_cpu_seconds: 7200",
                    "  allow_network: false",
                    "  real_execution: false",
                    "  preserve_run: false",
                    "  retention_hours: 168",
                    "runtime:",
                    "  provider: openai",
                    "  model: gpt-5.4",
                    "  env_refs:",
                    "    - OPENAI_API_KEY",
                    "  secret_refs: []",
                    "  concurrency: 1",
                    "outputs:",
                    f"  write_patch: {'true' if (outputs or {}).get('write_patch', True) else 'false'}",
                    f"  write_report: {'true' if (outputs or {}).get('write_report', True) else 'false'}",
                    f"  write_dataset_profile: {'true' if (outputs or {}).get('write_dataset_profile', True) else 'false'}",
                    f"  write_best_strategy: {'true' if (outputs or {}).get('write_best_strategy', True) else 'false'}",
                ]
            ),
            encoding="utf-8",
        )
        return spec_path

    def test_yaml_spec_loads_and_run_flow_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            spec_path = self._write_sample_project(root)
            spec = load_runtime_spec(spec_path)

            manager = RuntimeManager(root)
            run = manager.run(spec)

            self.assertEqual(run.status, "finished")
            self.assertTrue(run.run_dir.exists())
            self.assertGreater(run.result["fitness"], 0.0)
            self.assertEqual(run.summary["data_profile"]["num_records"], 2)
            self.assertEqual(manager.status(run.run_id)["status"], "finished")

            artifacts = manager.list_artifacts(run.run_id)
            artifact_paths = {item["path"] for item in artifacts}
            self.assertIn("artifacts/dataset_snapshot.json", artifact_paths)
            self.assertIn("artifacts/dataset_profile.json", artifact_paths)
            self.assertIn("artifacts/iteration_history.json", artifact_paths)
            self.assertIn("artifacts/best_strategy.py", artifact_paths)
            self.assertIn("artifacts/strategy.patch", artifact_paths)
            self.assertIn("artifacts/report.md", artifact_paths)
            self.assertIn("result.json", artifact_paths)
            self.assertIn("summary.json", artifact_paths)
            self.assertNotIn("dataset_profile.json", artifact_paths)

    def test_continue_creates_child_run_with_parent_link(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            spec_path = self._write_sample_project(root)
            manager = RuntimeManager(root)
            parent = manager.run(spec_path)
            child = manager.continue_run(parent.run_id)

            self.assertNotEqual(child.run_id, parent.run_id)
            self.assertEqual(child.manifest["parent_run_id"], parent.run_id)
            self.assertEqual(child.status, "finished")
            self.assertEqual(manager.status(child.run_id)["status"], "finished")

    def test_run_uses_first_editable_target_path(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            spec_path = self._write_sample_project(root, editable_target="custom/strategy_v2.py")
            manager = RuntimeManager(root)
            run = manager.run(spec_path)

            self.assertEqual(run.status, "finished")
            best_strategy_path = run.run_dir / "artifacts" / "best_strategy.py"
            self.assertTrue(best_strategy_path.exists())
            best_strategy = best_strategy_path.read_text(encoding="utf-8")
            self.assertIn("def strategy(record, config=None):", best_strategy)

    def test_run_respects_artifacts_dir_and_output_switches(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            spec_path = self._write_sample_project(
                root,
                artifacts_dir="./runtime-artifacts",
                outputs={
                    "write_patch": True,
                    "write_report": False,
                    "write_dataset_profile": False,
                    "write_best_strategy": True,
                },
            )
            manager = RuntimeManager(root)
            run = manager.run(spec_path)

            artifact_paths = {item["path"] for item in run.artifacts}
            self.assertIn("runtime-artifacts/best_strategy.py", artifact_paths)
            self.assertIn("runtime-artifacts/strategy.patch", artifact_paths)
            self.assertIn("runtime-artifacts/iteration_history.json", artifact_paths)
            self.assertIn("runtime-artifacts/artifact_index.json", artifact_paths)
            self.assertNotIn("runtime-artifacts/report.md", artifact_paths)
            self.assertNotIn("runtime-artifacts/dataset_profile.json", artifact_paths)
            self.assertNotIn("runtime-artifacts/dataset_snapshot.json", artifact_paths)
            self.assertFalse((run.run_dir / "artifacts").exists())
            self.assertTrue((run.run_dir / "runtime-artifacts").exists())

    def test_json_formatted_research_yaml_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            spec_path = self._write_sample_project(root)
            spec = load_runtime_spec(spec_path)
            spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

            manager = RuntimeManager(root)
            run = manager.run(spec_path)

            self.assertEqual(run.status, "finished")
            self.assertTrue((run.run_dir / "artifacts" / "best_strategy.py").exists())


if __name__ == "__main__":
    unittest.main()
