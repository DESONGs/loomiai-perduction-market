from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
DATASET_PATH = REPO_ROOT / "examples" / "prediction-market" / "datasets" / "eval_markets.json"


def _write_message(stdin, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
    stdin.write(header)
    stdin.write(body)
    stdin.flush()


def _read_message(stdout) -> dict[str, Any]:
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
    if content_length <= 0:
        raise RuntimeError("mcp response missing content-length")
    body = stdout.read(content_length)
    if not body:
        raise RuntimeError("mcp server returned empty body")
    return json.loads(body.decode("utf-8"))


def send_request(proc: subprocess.Popen[str], payload: dict[str, Any]) -> dict[str, Any]:
    if proc.stdin is None or proc.stdout is None:
        raise RuntimeError("mcp server pipes are not available")
    _write_message(proc.stdin, payload)
    return _read_message(proc.stdout)


def send_notification(proc: subprocess.Popen[str], payload: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise RuntimeError("mcp server stdin is not available")
    _write_message(proc.stdin, payload)


def call_tool(proc: subprocess.Popen[str], request_id: str, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments or {},
        },
    }
    return send_request(proc, payload)


def main() -> int:
    if not DATASET_PATH.exists():
        raise FileNotFoundError(f"dataset not found: {DATASET_PATH}")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_ROOT)

    with tempfile.TemporaryDirectory(prefix="autoresearch-real-smoke-") as tempdir:
        project_root = Path(tempdir) / "demo-project"
        init = subprocess.run(
            [
                sys.executable,
                "-m",
                "autoresearch_agent",
                "init",
                str(project_root),
                "--pack",
                "prediction_market",
                "--data-source",
                str(DATASET_PATH),
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if init.returncode != 0:
            raise RuntimeError(init.stderr.strip() or "init failed")

        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "autoresearch_agent",
                "mcp",
                "serve",
                "--project-root",
                str(project_root),
            ],
            cwd=str(REPO_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            initialize = send_request(
                server,
                {
                    "jsonrpc": "2.0",
                    "id": "0",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "mcp-real-smoke", "version": "0.1.0"},
                    },
                },
            )
            send_notification(server, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            tools = send_request(server, {"jsonrpc": "2.0", "id": "1", "method": "tools/list", "params": {}})
            ping = call_tool(server, "2", "ping")
            validate = call_tool(server, "3", "validate_project")
            run = call_tool(server, "4", "run_project")
            run_payload = run["result"]["structuredContent"]
            run_id = run_payload["run_id"]
            status = call_tool(server, "5", "get_run_status", {"run_id": run_id})
            status_payload = status["result"]["structuredContent"]
            poll_id = 6
            while status_payload["status"] in {"queued", "running"}:
                status = call_tool(server, str(poll_id), "get_run_status", {"run_id": run_id})
                status_payload = status["result"]["structuredContent"]
                poll_id += 1
            artifacts = call_tool(server, "6", "list_artifacts", {"run_id": run_id})
            best_strategy = call_tool(
                server,
                "7",
                "read_artifact",
                {"run_id": run_id, "artifact_path": "artifacts/best_strategy.py", "max_chars": 1200},
            )
        finally:
            server.terminate()
            try:
                server.wait(timeout=2)
            except subprocess.TimeoutExpired:
                server.kill()
            for stream in (server.stdin, server.stdout, server.stderr):
                if stream is not None:
                    stream.close()

        run_dir = project_root / ".autoresearch" / "runs" / run_id
        retained_root = REPO_ROOT / "tmp" / run_id
        if retained_root.exists():
            shutil.rmtree(retained_root)
        retained_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(run_dir, retained_root)

    summary = {
        "ok": True,
        "dataset": str(DATASET_PATH),
        "project_root": str(project_root),
        "retained_run_dir": str(retained_root),
        "initialize": initialize,
        "tools": tools,
        "ping": ping,
        "validate": validate,
        "run": run,
        "status": status,
        "artifacts": artifacts,
        "best_strategy": best_strategy,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
