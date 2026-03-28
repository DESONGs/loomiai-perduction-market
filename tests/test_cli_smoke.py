from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autoresearch_agent.mcp.server import StdioMcpServer


class CliSmokeTests(unittest.TestCase):
    def _run_cli(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC)
        return subprocess.run(
            [sys.executable, "-m", "autoresearch_agent", *args],
            cwd=str(cwd or ROOT),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def _write_dataset(self, path: Path) -> Path:
        payload = [
            {
                "market_id": "m1",
                "question": "Will event A happen?",
                "outcomes": ["Yes", "No"],
                "last_trade_price": 0.7,
                "final_resolution_index": 0,
                "volume": 1000,
                "context": {"category": "news", "liquidity": 500.0},
            },
            {
                "market_id": "m2",
                "question": "Will event B happen?",
                "outcomes": ["Yes", "No"],
                "last_trade_price": 0.3,
                "final_resolution_index": 1,
                "volume": 800,
                "context": {"category": "sports", "liquidity": 300.0},
            },
        ]
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_cli_init_validate_run_status_artifacts_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            dataset_path = self._write_dataset(root / "dataset.json")
            project_root = root / "demo-project"

            init_proc = self._run_cli(
                "init",
                str(project_root),
                "--pack",
                "prediction_market",
                "--data-source",
                str(dataset_path),
            )
            self.assertEqual(init_proc.returncode, 0, init_proc.stderr)
            init_payload = json.loads(init_proc.stdout)
            self.assertTrue((project_root / "research.yaml").exists())
            self.assertEqual(init_payload["project_root"], str(project_root.resolve()))

            validate_proc = self._run_cli("validate", str(project_root))
            self.assertEqual(validate_proc.returncode, 0, validate_proc.stderr)
            validate_payload = json.loads(validate_proc.stdout)
            self.assertTrue(validate_payload["ok"])
            self.assertEqual(validate_payload["pack"]["id"], "prediction_market")

            run_proc = self._run_cli("run", str(project_root))
            self.assertEqual(run_proc.returncode, 0, run_proc.stderr)
            run_payload = json.loads(run_proc.stdout)
            run_id = run_payload["run_id"]
            self.assertEqual(run_payload["status"], "finished")

            status_proc = self._run_cli("status", run_id, "--project-root", str(project_root))
            self.assertEqual(status_proc.returncode, 0, status_proc.stderr)
            status_payload = json.loads(status_proc.stdout)
            self.assertEqual(status_payload["status"], "finished")

            artifacts_proc = self._run_cli("artifacts", run_id, "--project-root", str(project_root))
            self.assertEqual(artifacts_proc.returncode, 0, artifacts_proc.stderr)
            artifacts_payload = json.loads(artifacts_proc.stdout)
            self.assertTrue(any(item["path"] == "result.json" for item in artifacts_payload))

            continue_proc = self._run_cli("continue", run_id, "--project-root", str(project_root))
            self.assertEqual(continue_proc.returncode, 0, continue_proc.stderr)
            continue_payload = json.loads(continue_proc.stdout)
            self.assertEqual(continue_payload["parent_run_id"], run_id)

    def test_pack_list_and_install(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            project_root = Path(tempdir) / "project"
            project_root.mkdir(parents=True, exist_ok=True)

            list_proc = self._run_cli("pack", "list")
            self.assertEqual(list_proc.returncode, 0, list_proc.stderr)
            list_payload = json.loads(list_proc.stdout)
            self.assertTrue(any(item["pack_id"] == "prediction_market" for item in list_payload["packs"]))

            install_proc = self._run_cli("pack", "install", "prediction_market", "--project-root", str(project_root))
            self.assertEqual(install_proc.returncode, 0, install_proc.stderr)
            install_payload = json.loads(install_proc.stdout)
            self.assertTrue(Path(install_payload["snapshot"]).exists())

    def test_mcp_server_handle_request(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            dataset_path = self._write_dataset(root / "dataset.json")
            project_root = root / "demo-project"
            init_proc = self._run_cli(
                "init",
                str(project_root),
                "--pack",
                "prediction_market",
                "--data-source",
                str(dataset_path),
            )
            self.assertEqual(init_proc.returncode, 0, init_proc.stderr)

            server = StdioMcpServer(project_root)
            packs_response = server.handle_request({"id": "1", "method": "list_packs", "params": {}})
            self.assertTrue(packs_response["ok"])
            self.assertTrue(any(item["pack_id"] == "prediction_market" for item in packs_response["result"]["packs"]))

            validate_response = server.handle_request({"id": "2", "method": "validate_project", "params": {}})
            self.assertTrue(validate_response["ok"])
            self.assertTrue(validate_response["result"]["ok"])


if __name__ == "__main__":
    unittest.main()
