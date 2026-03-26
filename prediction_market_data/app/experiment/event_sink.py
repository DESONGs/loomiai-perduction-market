from __future__ import annotations

from pathlib import Path
from typing import Any

from app.repositories.run_repository import append_jsonl, build_runtime_event


class RuntimeEventSink:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append_run_started(self, stream_payload: dict[str, Any]) -> None:
        append_jsonl(self.path, build_runtime_event("run_started", stream=stream_payload))

    def append_state_synced(self, orchestrator_payload: dict[str, Any]) -> None:
        append_jsonl(self.path, build_runtime_event("state_synced", orchestrator=orchestrator_payload))

    def append_iteration_recorded(
        self,
        *,
        detail: dict[str, Any],
        result: dict[str, Any],
        token_snapshot: dict[str, Any],
        stream_payload: dict[str, Any],
    ) -> None:
        append_jsonl(
            self.path,
            build_runtime_event(
                "iteration_recorded",
                detail=detail,
                result=result,
                token_snapshot=token_snapshot,
                stream=stream_payload,
            ),
        )

    def append_run_finished(self, *, stream_payload: dict[str, Any], token_snapshot: dict[str, Any]) -> None:
        append_jsonl(
            self.path,
            build_runtime_event(
                "run_finished",
                stream=stream_payload,
                token_snapshot=token_snapshot,
            ),
        )
