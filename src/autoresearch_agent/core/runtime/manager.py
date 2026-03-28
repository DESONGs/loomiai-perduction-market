from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
import copy
import json
import uuid

from ..artifacts import build_artifact_index
from ..datasets import load_dataset_records, profile_dataset
from ..search import IterationEngine
from .lifecycle import RuntimeStatus
from .spec import RuntimeSpecError, load_runtime_spec, normalize_runtime_spec, validate_runtime_spec
from .state_store import atomic_write_json, ensure_dir, read_json


StrategyFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    return f"run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"


def _resolve_path(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


@dataclass(frozen=True)
class RuntimeRun:
    run_id: str
    status: str
    run_dir: Path
    spec: dict[str, Any]
    manifest: dict[str, Any]
    result: dict[str, Any]
    summary: dict[str, Any]
    artifacts: list[dict[str, Any]]


class RuntimeManager:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()

    def _runtime_root(self, spec: dict[str, Any]) -> Path:
        project = spec.get("project", {})
        runs_dir = project.get("runs_dir", "./.autoresearch/runs")
        return _resolve_path(self.project_root, runs_dir)

    def _run_dir(self, spec: dict[str, Any], run_id: str) -> Path:
        return self._runtime_root(spec) / run_id

    def _write_manifest(self, run_dir: Path, manifest: dict[str, Any]) -> None:
        atomic_write_json(run_dir / "run_manifest.json", manifest)

    def _write_spec(self, run_dir: Path, spec: dict[str, Any]) -> None:
        atomic_write_json(run_dir / "run_spec.json", spec)

    def _write_result(self, run_dir: Path, result: dict[str, Any]) -> None:
        atomic_write_json(run_dir / "result.json", result)

    def _write_summary(self, run_dir: Path, summary: dict[str, Any]) -> None:
        atomic_write_json(run_dir / "summary.json", summary)

    def _write_dataset_snapshot(self, run_dir: Path, records: list[dict[str, Any]], summary: dict[str, Any]) -> None:
        data_dir = ensure_dir(run_dir / "artifacts")
        atomic_write_json(data_dir / "dataset_snapshot.json", records)
        atomic_write_json(data_dir / "dataset_profile.json", summary)

    def _build_summary(
        self,
        *,
        spec: dict[str, Any],
        profile: dict[str, Any],
        result: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": "runtime_summary.v1",
            "project": spec.get("project", {}),
            "pack": spec.get("pack", {}),
            "data_profile": profile,
            "best_result": result,
            "iteration_count": len(history),
            "history_tail": history[-3:],
            "updated_at": _now_iso(),
        }

    def _read_manifest(self, run_dir: Path) -> dict[str, Any]:
        return read_json(run_dir / "run_manifest.json", {}) or {}

    def _read_spec(self, run_dir: Path) -> dict[str, Any]:
        return read_json(run_dir / "run_spec.json", {}) or {}

    def _read_result(self, run_dir: Path) -> dict[str, Any]:
        return read_json(run_dir / "result.json", {}) or {}

    def _read_summary(self, run_dir: Path) -> dict[str, Any]:
        return read_json(run_dir / "summary.json", {}) or {}

    def create_run(
        self,
        spec_input: dict[str, Any] | str | Path,
        *,
        run_id: str | None = None,
        strategy_fn: StrategyFn | None = None,
        parent_run_id: str = "",
    ) -> RuntimeRun:
        spec = load_runtime_spec(spec_input) if isinstance(spec_input, (str, Path)) else validate_runtime_spec(spec_input)
        runtime_root = self._runtime_root(spec)
        ensure_dir(runtime_root)

        resolved_run_id = run_id or _run_id()
        run_dir = ensure_dir(self._run_dir(spec, resolved_run_id))
        dataset = load_dataset_records(_resolve_path(self.project_root, spec["data"]["source"]))
        profile = profile_dataset(dataset)
        engine = IterationEngine()
        search = spec.get("search", {})
        initial_config = {
            "confidence_threshold": 0.75,
            "max_bet_fraction": 0.15,
            "bet_sizing": "confidence_scaled",
            "prompt_factors": [],
        }
        initial_config.update({k: v for k, v in search.items() if k in {"confidence_threshold", "max_bet_fraction", "bet_sizing", "prompt_factors"}})
        iteration_bundle = engine.run(
            dataset,
            initial_config,
            max_iterations=int(search.get("max_iterations", 1) or 1),
            strategy_fn=strategy_fn,
        )
        best_result = iteration_bundle["best_result"]
        history = iteration_bundle["history"]
        summary = self._build_summary(spec=spec, profile=profile, result=best_result, history=history)

        self._write_spec(run_dir, spec)
        self._write_result(run_dir, best_result)
        self._write_summary(run_dir, summary)
        self._write_dataset_snapshot(run_dir, dataset, profile)
        atomic_write_json(run_dir / "artifacts" / "iteration_history.json", history)

        manifest = {
            "run_id": resolved_run_id,
            "status": RuntimeStatus.FINISHED,
            "created_at": _now_iso(),
            "started_at": _now_iso(),
            "finished_at": _now_iso(),
            "updated_at": _now_iso(),
            "parent_run_id": parent_run_id,
            "project_name": spec.get("project", {}).get("name", ""),
            "pack_id": spec.get("pack", {}).get("id", ""),
            "dataset_source": spec.get("data", {}).get("source", ""),
            "best_result_fitness": best_result.get("fitness", 0.0),
            "best_result_accuracy": best_result.get("accuracy", 0.0),
        }
        self._write_manifest(run_dir, manifest)
        artifacts = build_artifact_index(run_dir)
        atomic_write_json(run_dir / "artifacts" / "artifact_index.json", artifacts)
        artifacts = build_artifact_index(run_dir)

        return RuntimeRun(
            run_id=resolved_run_id,
            status=RuntimeStatus.FINISHED,
            run_dir=run_dir,
            spec=spec,
            manifest=manifest,
            result=best_result,
            summary=summary,
            artifacts=artifacts,
        )

    def continue_run(
        self,
        run_id: str,
        *,
        spec_override: dict[str, Any] | None = None,
        strategy_fn: StrategyFn | None = None,
    ) -> RuntimeRun:
        previous = self.get_run(run_id)
        spec = copy.deepcopy(previous.spec)
        if spec_override:
            spec = normalize_runtime_spec({**spec, **spec_override})
        return self.create_run(spec, strategy_fn=strategy_fn, parent_run_id=previous.run_id)

    def get_run(self, run_id: str) -> RuntimeRun:
        run_root = self._find_run_root(run_id)
        if run_root is None:
            raise FileNotFoundError(f"run not found: {run_id}")
        spec = self._read_spec(run_root)
        manifest = self._read_manifest(run_root)
        result = self._read_result(run_root)
        summary = self._read_summary(run_root)
        artifacts = build_artifact_index(run_root)
        return RuntimeRun(
            run_id=run_id,
            status=str(manifest.get("status", RuntimeStatus.CREATED)),
            run_dir=run_root,
            spec=spec,
            manifest=manifest,
            result=result,
            summary=summary,
            artifacts=artifacts,
        )

    def _find_run_root(self, run_id: str) -> Path | None:
        for root in self._candidate_roots():
            run_root = root / run_id
            if run_root.exists():
                return run_root
        return None

    def _candidate_roots(self) -> list[Path]:
        roots: list[Path] = []
        default_root = (self.project_root / ".autoresearch" / "runs").resolve()
        roots.append(default_root)
        for child in self.project_root.rglob("run_manifest.json"):
            roots.append(child.parent)
        return roots

    def status(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        return {
            "run_id": run.run_id,
            "status": run.status,
            "fitness": run.result.get("fitness", 0.0),
            "accuracy": run.result.get("accuracy", 0.0),
            "updated_at": run.summary.get("updated_at", run.manifest.get("updated_at", "")),
        }

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        return self.get_run(run_id).artifacts

    def run(self, spec_input: dict[str, Any] | str | Path, *, run_id: str | None = None) -> RuntimeRun:
        return self.create_run(spec_input, run_id=run_id)
