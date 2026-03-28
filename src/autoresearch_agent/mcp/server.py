from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from autoresearch_agent.cli.runtime import (
    continue_project_run,
    get_run_artifacts,
    get_run_status,
    list_packs,
    project_root_from_input,
    run_project,
    validate_project,
)


class StdioMcpServer:
    def __init__(self, project_root: str | Path = ".") -> None:
        self.project_root = project_root_from_input(project_root)

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        method = str(request.get("method", "")).strip()
        params = request.get("params", {})
        if not isinstance(params, dict):
            params = {}

        try:
            if method == "ping":
                result = {"ok": True, "project_root": str(self.project_root)}
            elif method == "list_packs":
                result = {"packs": list_packs()}
            elif method == "validate_project":
                result = validate_project(params.get("project_root", self.project_root))
            elif method == "run_project":
                result = run_project(params.get("project_root", self.project_root), run_id=params.get("run_id") or None)
            elif method == "continue_run":
                run_id = str(params.get("run_id", "")).strip()
                if not run_id:
                    raise ValueError("run_id is required")
                result = continue_project_run(params.get("project_root", self.project_root), run_id)
            elif method == "get_run_status":
                run_id = str(params.get("run_id", "")).strip()
                if not run_id:
                    raise ValueError("run_id is required")
                result = get_run_status(params.get("project_root", self.project_root), run_id)
            elif method == "list_artifacts":
                run_id = str(params.get("run_id", "")).strip()
                if not run_id:
                    raise ValueError("run_id is required")
                result = {"artifacts": get_run_artifacts(params.get("project_root", self.project_root), run_id)}
            else:
                raise ValueError(f"unsupported method: {method}")
            return {"id": request_id, "ok": True, "result": result}
        except Exception as exc:
            return {"id": request_id, "ok": False, "error": str(exc)}


def serve_stdio(project_root: str | Path = ".") -> None:
    server = StdioMcpServer(project_root)
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = {"id": None, "ok": False, "error": f"invalid json: {exc}"}
        else:
            response = server.handle_request(request if isinstance(request, dict) else {"method": "", "params": {}})
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
