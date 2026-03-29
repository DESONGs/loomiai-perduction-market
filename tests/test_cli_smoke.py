from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import autoresearch_agent.mcp.server as mcp_server_module
from autoresearch_agent.mcp.server import StdioMcpServer


class CliSmokeTests(unittest.TestCase):
    def _write_message(self, stdin, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
        stdin.write(header)
        stdin.write(body)
        stdin.flush()

    def _read_message(self, stdout) -> dict[str, Any]:
        headers: dict[str, str] = {}
        while True:
            line = stdout.readline()
            if not line:
                raise RuntimeError("mcp server returned no response")
            if line in {b"\r\n", b"\n"}:
                break
            decoded = line.decode("utf-8").strip()
            if ":" not in decoded:
                raise RuntimeError(f"invalid response header: {decoded}")
            name, value = decoded.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        content_length = int(headers.get("content-length", "0") or "0")
        self.assertGreater(content_length, 0)
        body = stdout.read(content_length)
        return json.loads(body.decode("utf-8"))

    def _call_tool(self, server: StdioMcpServer, request_id: str, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments or {}},
            }
        )

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

    def _write_slow_strategy(self, project_root: Path) -> None:
        (project_root / "workspace" / "strategy.py").write_text(
            "\n".join(
                [
                    "from __future__ import annotations",
                    "import time",
                    "",
                    "CONFIDENCE_THRESHOLD = 0.75",
                    'BET_SIZING = "confidence_scaled"',
                    "MAX_BET_FRACTION = 0.15",
                    "PROMPT_FACTORS = []",
                    "",
                    "def strategy(record, config=None):",
                    "    time.sleep(0.5)",
                    "    active = {",
                    "        'confidence_threshold': CONFIDENCE_THRESHOLD,",
                    "        'bet_sizing': BET_SIZING,",
                    "        'max_bet_fraction': MAX_BET_FRACTION,",
                    "        'prompt_factors': list(PROMPT_FACTORS),",
                    "    }",
                    "    if config:",
                    "        active.update(config)",
                    "    price = float(record.get('last_trade_price', 0.0) or 0.0)",
                    "    predicted = 0 if price >= float(active['confidence_threshold']) else 1",
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

    def _write_sigterm_ignoring_strategy(self, project_root: Path) -> Path:
        marker_path = project_root / "workspace" / ".sigterm-ignore-ready"
        (project_root / "workspace" / "strategy.py").write_text(
            "\n".join(
                [
                    "from __future__ import annotations",
                    "import signal",
                    "import time",
                    "from pathlib import Path",
                    "",
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                    f'Path("{marker_path.as_posix()}").write_text("ready", encoding="utf-8")',
                    "",
                    "CONFIDENCE_THRESHOLD = 0.75",
                    'BET_SIZING = "confidence_scaled"',
                    "MAX_BET_FRACTION = 0.15",
                    "PROMPT_FACTORS = []",
                    "",
                    "def strategy(record, config=None):",
                    "    time.sleep(5.0)",
                    "    active = {",
                    "        'confidence_threshold': CONFIDENCE_THRESHOLD,",
                    "        'bet_sizing': BET_SIZING,",
                    "        'max_bet_fraction': MAX_BET_FRACTION,",
                    "        'prompt_factors': list(PROMPT_FACTORS),",
                    "    }",
                    "    if config:",
                    "        active.update(config)",
                    "    return {",
                    "        'action': 'buy',",
                    "        'outcome_index': 0,",
                    "        'size': float(active['max_bet_fraction']) * 0.25,",
                    "        'prediction': 0,",
                    "        'confidence': 0.5,",
                    "    }",
                ]
            ),
            encoding="utf-8",
        )
        return marker_path

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
            initialize = server.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": "0",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0.1.0"},
                    },
                }
            )
            self.assertEqual(initialize["result"]["protocolVersion"], "2024-11-05")

            premature_server = StdioMcpServer(project_root)
            premature_tools = premature_server.handle_request({"jsonrpc": "2.0", "id": "premature", "method": "tools/list", "params": {}})
            self.assertEqual(premature_tools["error"]["code"], -32002)

            server.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            tools_response = server.handle_request({"jsonrpc": "2.0", "id": "1", "method": "tools/list", "params": {}})
            tool_names = {item["name"] for item in tools_response["result"]["tools"]}
            self.assertIn("list_packs", tool_names)
            self.assertIn("run_project", tool_names)
            self.assertIn("read_artifact", tool_names)
            self.assertIn("cancel_run", tool_names)
            self.assertIn("stop_run", tool_names)

            packs_response = self._call_tool(server, "2", "list_packs")
            self.assertFalse(packs_response["result"]["isError"])
            packs = packs_response["result"]["structuredContent"]["packs"]
            self.assertTrue(any(item["pack_id"] == "prediction_market" for item in packs))

            validate_response = self._call_tool(server, "3", "validate_project")
            self.assertFalse(validate_response["result"]["isError"])
            self.assertTrue(validate_response["result"]["structuredContent"]["ok"])

            invalid_response = server.handle_request({"id": "legacy", "method": "ping", "params": {}})
            self.assertEqual(invalid_response["error"]["code"], -32600)

            run_response = self._call_tool(server, "4", "run_project")
            self.assertFalse(run_response["result"]["isError"])
            run_payload = run_response["result"]["structuredContent"]
            self.assertIn(run_payload["status"], {"queued", "running"})
            run_id = run_payload["run_id"]

            status_response = self._call_tool(server, "5", "get_run_status", {"run_id": run_id})
            status_payload = status_response["result"]["structuredContent"]
            while status_payload["status"] in {"queued", "running"}:
                status_response = self._call_tool(server, "6", "get_run_status", {"run_id": run_id})
                status_payload = status_response["result"]["structuredContent"]
            self.assertEqual(status_payload["status"], "finished")

            artifacts_response = self._call_tool(server, "7", "list_artifacts", {"run_id": run_id})
            self.assertFalse(artifacts_response["result"]["isError"])
            artifacts_payload = artifacts_response["result"]["structuredContent"]
            self.assertEqual(artifacts_payload["status"], "finished")
            self.assertTrue(any(item["path"] == "artifacts/best_strategy.py" for item in artifacts_payload["artifacts"]))

            read_response = self._call_tool(
                server,
                "8",
                "read_artifact",
                {"run_id": run_id, "artifact_path": "artifacts/best_strategy.py", "max_chars": 6000},
            )
            self.assertFalse(read_response["result"]["isError"])
            artifact_payload = read_response["result"]["structuredContent"]["artifact"]
            self.assertEqual(artifact_payload["path"], "artifacts/best_strategy.py")
            self.assertIn("def strategy(", artifact_payload["content"])

    def test_mcp_cancel_run_uses_persisted_job_state(self) -> None:
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
            self._write_slow_strategy(project_root)

            server = StdioMcpServer(project_root)
            server.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": "0",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0.1.0"},
                    },
                }
            )
            server.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

            run_response = self._call_tool(server, "1", "run_project")
            self.assertFalse(run_response["result"]["isError"])
            run_id = run_response["result"]["structuredContent"]["run_id"]

            restarted = StdioMcpServer(project_root)
            restarted.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": "2",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "restart", "version": "0.1.0"},
                    },
                }
            )
            restarted.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

            initial_status = self._call_tool(restarted, "3", "get_run_status", {"run_id": run_id})
            self.assertFalse(initial_status["result"]["isError"])
            status_payload = initial_status["result"]["structuredContent"]
            deadline = time.time() + 3.0
            while status_payload["status"] == "queued" and time.time() < deadline:
                time.sleep(0.05)
                status_payload = self._call_tool(restarted, "3a", "get_run_status", {"run_id": run_id})["result"]["structuredContent"]
            self.assertEqual(status_payload["status"], "running")

            cancel_response = self._call_tool(restarted, "4", "cancel_run", {"run_id": run_id})
            self.assertFalse(cancel_response["result"]["isError"])
            self.assertEqual(cancel_response["result"]["structuredContent"]["status"], "cancelling")
            self.assertTrue(cancel_response["result"]["structuredContent"]["cancel_requested_at"])
            self.assertTrue(cancel_response["result"]["structuredContent"]["terminate_sent_at"])

            final_status = self._call_tool(restarted, "5", "get_run_status", {"run_id": run_id})
            status_payload = final_status["result"]["structuredContent"]
            while status_payload["status"] in {"queued", "running", "cancelling"}:
                final_status = self._call_tool(restarted, "6", "get_run_status", {"run_id": run_id})
                status_payload = final_status["result"]["structuredContent"]
            self.assertEqual(status_payload["status"], "cancelled")

            artifacts_response = self._call_tool(restarted, "7", "list_artifacts", {"run_id": run_id})
            self.assertFalse(artifacts_response["result"]["isError"])
            self.assertEqual(artifacts_response["result"]["structuredContent"]["status"], "cancelled")

            job_state_path = project_root / ".autoresearch" / "state" / "mcp_jobs" / f"{run_id}.json"
            job_state = json.loads(job_state_path.read_text(encoding="utf-8"))
            self.assertGreater(int(job_state.get("pgid", 0) or 0), 0)
            self.assertTrue(str(job_state.get("cancel_requested_at", "")).strip())

    def test_mcp_stop_run_escalates_to_force_kill_when_sigterm_is_ignored(self) -> None:
        original_grace = mcp_server_module.CANCEL_GRACE_SECONDS
        mcp_server_module.CANCEL_GRACE_SECONDS = 0.2
        try:
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
                marker_path = self._write_sigterm_ignoring_strategy(project_root)

                server = StdioMcpServer(project_root)
                server.handle_request(
                    {
                        "jsonrpc": "2.0",
                        "id": "0",
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "0.1.0"},
                        },
                    }
                )
                server.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

                run_response = self._call_tool(server, "1", "run_project")
                self.assertFalse(run_response["result"]["isError"])
                run_id = run_response["result"]["structuredContent"]["run_id"]

                restarted = StdioMcpServer(project_root)
                restarted.handle_request(
                    {
                        "jsonrpc": "2.0",
                        "id": "2",
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "restart", "version": "0.1.0"},
                        },
                    }
                )
                restarted.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

                status_payload = self._call_tool(restarted, "2a", "get_run_status", {"run_id": run_id})["result"]["structuredContent"]
                deadline = time.time() + 3.0
                while status_payload["status"] == "queued" and time.time() < deadline:
                    time.sleep(0.05)
                    status_payload = self._call_tool(restarted, "2b", "get_run_status", {"run_id": run_id})["result"]["structuredContent"]
                self.assertEqual(status_payload["status"], "running")
                marker_deadline = time.time() + 3.0
                while not marker_path.exists() and time.time() < marker_deadline:
                    time.sleep(0.05)
                self.assertTrue(marker_path.exists())

                stop_response = self._call_tool(restarted, "3", "stop_run", {"run_id": run_id})
                self.assertFalse(stop_response["result"]["isError"])
                self.assertEqual(stop_response["result"]["structuredContent"]["status"], "cancelling")

                status_payload = self._call_tool(restarted, "4", "get_run_status", {"run_id": run_id})["result"]["structuredContent"]
                deadline = time.time() + 5.0
                while status_payload["status"] in {"queued", "running", "cancelling"} and time.time() < deadline:
                    time.sleep(0.1)
                    status_payload = self._call_tool(restarted, "5", "get_run_status", {"run_id": run_id})["result"]["structuredContent"]
                self.assertEqual(status_payload["status"], "cancelled")
                self.assertTrue(str(status_payload.get("kill_sent_at", "")).strip())

                job_state_path = project_root / ".autoresearch" / "state" / "mcp_jobs" / f"{run_id}.json"
                job_state = json.loads(job_state_path.read_text(encoding="utf-8"))
                self.assertTrue(str(job_state.get("kill_sent_at", "")).strip())
        finally:
            mcp_server_module.CANCEL_GRACE_SECONDS = original_grace

    def test_stdio_transport_uses_content_length_framing(self) -> None:
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

            env = dict(os.environ)
            env["PYTHONPATH"] = str(SRC)
            server = subprocess.Popen(
                [sys.executable, "-m", "autoresearch_agent", "mcp", "serve", "--project-root", str(project_root)],
                cwd=str(ROOT),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            try:
                self._write_message(
                    server.stdin,
                    {
                        "jsonrpc": "2.0",
                        "id": "0",
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "0.1.0"},
                        },
                    },
                )
                initialize = self._read_message(server.stdout)
                self.assertEqual(initialize["result"]["protocolVersion"], "2024-11-05")

                self._write_message(
                    server.stdin,
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {},
                    },
                )

                self._write_message(
                    server.stdin,
                    {
                        "jsonrpc": "2.0",
                        "id": "1",
                        "method": "tools/list",
                        "params": {},
                    },
                )
                tools = self._read_message(server.stdout)
                tool_names = {item["name"] for item in tools["result"]["tools"]}
                self.assertIn("validate_project", tool_names)
                self.assertIn("run_project", tool_names)
            finally:
                server.terminate()
                try:
                    server.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    server.kill()
                for stream in (server.stdin, server.stdout, server.stderr):
                    if stream is not None:
                        stream.close()


if __name__ == "__main__":
    unittest.main()
