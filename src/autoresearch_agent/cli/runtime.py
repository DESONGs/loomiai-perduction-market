from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autoresearch_agent.core.datasets import load_dataset_records, profile_dataset
from autoresearch_agent.core.packs import PackLoader
from autoresearch_agent.core.runtime import RuntimeManager
from autoresearch_agent.core.strategy import load_strategy
from autoresearch_agent.core.spec.research_config import load_research_spec
from autoresearch_agent.core.paths import project_file_path, resolve_project_root
from autoresearch_agent.project.scaffold import scaffold_project


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)


def project_root_from_input(path: str | Path | None) -> Path:
    return resolve_project_root(path or Path.cwd())


def spec_path_from_project_root(project_root: str | Path) -> Path:
    return project_file_path(project_root)


def pack_loader() -> PackLoader:
    return PackLoader()


def install_pack_snapshot(project_root: str | Path, pack_id: str) -> Path:
    root = project_root_from_input(project_root)
    manifest = pack_loader().load(pack_id)
    state_dir = root / ".autoresearch" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / f"{manifest.pack_id}.pack.json"
    target.write_text(json_dumps(manifest.to_dict()) + "\n", encoding="utf-8")
    return target


def init_project(
    project_root: str | Path,
    *,
    project_name: str | None,
    pack_id: str,
    data_source: str,
    overwrite: bool,
) -> dict[str, Any]:
    root = project_root_from_input(project_root)
    result = scaffold_project(
        root,
        project_name=project_name,
        pack_id=pack_id,
        data_source=data_source,
        overwrite=overwrite,
    )
    snapshot = install_pack_snapshot(root, pack_id)
    return {
        "project_root": str(root),
        "config_path": str(result["config_path"]),
        "pack_snapshot": str(snapshot),
        "created_paths": [str(path) for path in result["created_paths"]],
    }


def validate_project(project_root: str | Path) -> dict[str, Any]:
    root = project_root_from_input(project_root)
    spec_path = spec_path_from_project_root(root)
    spec = load_research_spec(spec_path)
    manifest = pack_loader().load(str(spec["pack"]["id"]))
    dataset_path = Path(spec["data"]["source"])
    if not dataset_path.is_absolute():
        dataset_path = (root / dataset_path).resolve()
    records = load_dataset_records(dataset_path)
    profile = profile_dataset(records)
    editable_targets = spec.get("search", {}).get("editable_targets", []) or ["workspace/strategy.py"]
    strategy_path = Path(str(editable_targets[0]))
    if not strategy_path.is_absolute():
        strategy_path = (root / strategy_path).resolve()
    loaded_strategy = load_strategy(strategy_path)
    return {
        "ok": True,
        "project_root": str(root),
        "spec_path": str(spec_path),
        "pack": {
            "id": manifest.pack_id,
            "name": manifest.name,
            "version": manifest.version,
            "allowed_axes": manifest.allowed_axes,
        },
        "dataset": {
            "source": str(dataset_path),
            "num_records": profile.get("num_records", 0),
            "profile": profile,
        },
        "strategy": {
            "path": str(strategy_path),
            "base_config": loaded_strategy.config,
        },
    }


def run_project(project_root: str | Path, *, run_id: str | None = None) -> dict[str, Any]:
    root = project_root_from_input(project_root)
    spec_path = spec_path_from_project_root(root)
    run = RuntimeManager(root).run(spec_path, run_id=run_id)
    return {
        "run_id": run.run_id,
        "status": run.status,
        "run_dir": str(run.run_dir),
        "result": run.result,
        "summary": run.summary,
        "artifacts": run.artifacts,
    }


def continue_project_run(project_root: str | Path, run_id: str, *, next_run_id: str | None = None) -> dict[str, Any]:
    root = project_root_from_input(project_root)
    run = RuntimeManager(root).continue_run(run_id, next_run_id=next_run_id)
    return {
        "run_id": run.run_id,
        "parent_run_id": run.manifest.get("parent_run_id", ""),
        "status": run.status,
        "run_dir": str(run.run_dir),
        "result": run.result,
    }


def get_run_status(project_root: str | Path, run_id: str) -> dict[str, Any]:
    root = project_root_from_input(project_root)
    return RuntimeManager(root).status(run_id)


def get_run_artifacts(project_root: str | Path, run_id: str) -> list[dict[str, Any]]:
    root = project_root_from_input(project_root)
    return RuntimeManager(root).list_artifacts(run_id)


def list_packs() -> list[dict[str, Any]]:
    return [
        {
            "pack_id": manifest.pack_id,
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "supported_formats": manifest.supported_formats,
            "allowed_axes": manifest.allowed_axes,
        }
        for manifest in pack_loader().list_packs()
    ]
