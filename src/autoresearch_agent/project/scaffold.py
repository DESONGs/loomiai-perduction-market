from __future__ import annotations

from pathlib import Path
from typing import Any

from autoresearch_agent.core.spec.research_config import default_research_spec, write_research_spec


DEFAULT_STRATEGY_TEMPLATE = """\
\"\"\"Workspace strategy template for autoresearch iterations.\"\"\"


def strategy(record):
    # Replace this stub with a pack-specific strategy.
    return {
        "action": "skip",
        "confidence": 0.0,
        "note": "scaffold placeholder",
    }
"""


def _mkdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_project_scaffold(
    project_root: str | Path,
    *,
    project_name: str | None = None,
    pack_id: str = "prediction_market",
    data_source: str = "./datasets/input.json",
    overwrite: bool = False,
) -> dict[str, Any]:
    root = Path(project_root)
    if root.exists() and not root.is_dir():
        raise NotADirectoryError(f"project root is not a directory: {root}")
    root.mkdir(parents=True, exist_ok=True)

    config_path = root / "research.yaml"
    if config_path.exists() and not overwrite:
        raise FileExistsError(f"research.yaml already exists: {config_path}")

    datasets_dir = _mkdir(root / "datasets")
    workspace_dir = _mkdir(root / "workspace")
    artifacts_dir = _mkdir(root / "artifacts")
    runtime_runs_dir = _mkdir(root / ".autoresearch" / "runs")
    runtime_cache_dir = _mkdir(root / ".autoresearch" / "cache")
    runtime_state_dir = _mkdir(root / ".autoresearch" / "state")

    spec = default_research_spec(
        project_name=project_name or root.name,
        pack_id=pack_id,
        data_source=data_source,
        editable_target="workspace/strategy.py",
    )
    spec["project"]["workspace_dir"] = "workspace"
    spec["project"]["artifacts_dir"] = "artifacts"
    spec["project"]["runs_dir"] = ".autoresearch/runs"
    write_research_spec(config_path, spec)

    strategy_path = workspace_dir / "strategy.py"
    strategy_path.write_text(DEFAULT_STRATEGY_TEMPLATE, encoding="utf-8")

    created_paths = [
        datasets_dir,
        workspace_dir,
        artifacts_dir,
        runtime_runs_dir,
        runtime_cache_dir,
        runtime_state_dir,
        config_path,
        strategy_path,
    ]
    return {
        "project_root": root,
        "config_path": config_path,
        "created_paths": created_paths,
        "spec": spec,
    }


def scaffold_project(
    project_root: str | Path,
    *,
    project_name: str | None = None,
    pack_id: str = "prediction_market",
    data_source: str = "./datasets/input.json",
    overwrite: bool = False,
) -> dict[str, Any]:
    return build_project_scaffold(
        project_root,
        project_name=project_name,
        pack_id=pack_id,
        data_source=data_source,
        overwrite=overwrite,
    )
