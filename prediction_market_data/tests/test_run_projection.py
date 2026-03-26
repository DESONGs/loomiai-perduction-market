from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.runs
from app.projections.run_projection import build_run_projection


class RunProjectionTests(unittest.TestCase):
    def test_build_projection_from_runtime_events(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            runs_dir = Path(tempdir)
            run_id = "projection-test-run"
            root = runs_dir / run_id
            runtime = root / "runtime"
            data_dir = root / "data"
            root.mkdir()
            runtime.mkdir()
            data_dir.mkdir()

            (root / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "run_name": "projection-test-run",
                        "user_id": "tester",
                        "tenant_id": "default",
                        "status": "finished",
                        "created_at": "2026-03-26T00:00:00+00:00",
                        "started_at": "2026-03-26T00:01:00+00:00",
                        "finished_at": "2026-03-26T00:02:00+00:00",
                        "error": "",
                        "worker_id": "worker-1",
                        "pid": 0,
                    }
                ),
                encoding="utf-8",
            )
            (root / "run_spec.json").write_text(
                json.dumps(
                    {
                        "schema_version": "run_spec.v2",
                        "runtime_version": "pm-autoresearch.v1",
                        "run_id": run_id,
                        "run_name": "projection-test-run",
                        "user_id": "tester",
                        "tenant_id": "default",
                        "dataset": {"adapter": "canonical_json", "input_format": "json"},
                        "runtime": {"env_refs": [], "secret_refs": [], "real_execution": False, "retention_hours": 168, "preserve_run": False},
                        "constraints": {"max_iterations": 2, "eval_timeout": 900, "sample_size": 5, "per_eval_token_budget": 1000, "total_token_budget": 0, "max_completion_tokens": 200, "allowed_axes": ["CONFIDENCE_THRESHOLD"]},
                        "harness_policy": {"engine": "strategy_iteration_v2", "projection_contract": "run_projection.v1", "artifact_contract": "runtime_artifacts.v1", "allowed_axes": ["CONFIDENCE_THRESHOLD"]},
                        "retention_policy": {"retention_hours": 168, "preserve_run": False},
                    }
                ),
                encoding="utf-8",
            )
            (data_dir / "summary.json").write_text(json.dumps({"summary": {"num_records": 5}}), encoding="utf-8")
            events = [
                {"event": "run_started", "timestamp": "2026-03-26T00:01:00+00:00", "stream": {"type": "start", "total_budget_limit": 1000, "budget_limit": 1000, "model": "test"}},
                {
                    "event": "state_synced",
                    "timestamp": "2026-03-26T00:01:01+00:00",
                    "orchestrator": {"goal": "test", "status": "running", "updated_at": "2026-03-26T00:01:01+00:00", "main_agent": {"constraints": ["sample size <= 5"]}, "workers": []},
                },
                {
                    "event": "iteration_recorded",
                    "timestamp": "2026-03-26T00:01:30+00:00",
                    "detail": {"iteration": 1, "status": "accepted", "patch_summary": "improve threshold", "phase_results": {}},
                    "result": {"commit": "iteration-1", "description": "improve threshold", "status": "accepted", "search_fitness": "1.1", "validation_fitness": "1.2", "holdout_fitness": "1.0", "validation_pnl": "12.5", "tokens": "500", "decision_logic": "accepted"},
                    "token_snapshot": {"total_tokens": 500, "prompt_tokens": 300, "completion_tokens": 200, "api_calls": 2, "api_errors": 0, "progress": "1/2", "status": "running"},
                    "stream": {"type": "inference", "index": 1, "progress": "1/2"},
                },
                {
                    "event": "run_finished",
                    "timestamp": "2026-03-26T00:02:00+00:00",
                    "stream": {"type": "finish", "results": {"fitness": 1.2, "stop_reason": "done"}, "token_summary": {"total_tokens": 500, "api_calls": 2, "api_errors": 0}},
                    "token_snapshot": {"total_tokens": 500, "prompt_tokens": 300, "completion_tokens": 200, "api_calls": 2, "api_errors": 0, "progress": "1/2", "status": "finished"},
                },
            ]
            with (runtime / "runtime_events.jsonl").open("w", encoding="utf-8") as handle:
                for event in events:
                    handle.write(json.dumps(event) + "\n")

            with patch.object(app.runs, "RUNS_DIR", runs_dir):
                projection = build_run_projection(run_id)

            self.assertEqual(projection["source"], "runtime_events")
            self.assertEqual(len(projection["results"]), 1)
            self.assertEqual(len(projection["iterations"]), 1)
            self.assertEqual(projection["summary"]["best_result"]["commit"], "iteration-1")
            self.assertTrue((runtime / "run_projection.json").exists())


if __name__ == "__main__":
    unittest.main()
