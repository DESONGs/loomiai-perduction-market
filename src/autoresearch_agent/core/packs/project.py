from __future__ import annotations

from pathlib import Path
from typing import Any

from .loader import dump_document, find_pack_manifest, load_document
from .schema import ResearchSpec, build_default_research_spec
from autoresearch_agent.core.spec.research_config import dump_research_yaml


DEFAULT_PROJECT_DIRS = [
    "datasets",
    "workspace",
    "artifacts",
    ".autoresearch/runs",
    ".autoresearch/cache",
    ".autoresearch/state",
]


def default_research_spec(
    *,
    project_name: str,
    pack_id: str,
    data_source: str,
    allowed_axes: list[str] | None = None,
    pack_config: dict[str, Any] | None = None,
) -> ResearchSpec:
    return build_default_research_spec(
        project_name=project_name,
        pack_id=pack_id,
        data_source=data_source,
        allowed_axes=allowed_axes,
        pack_config=pack_config,
    )


def render_research_spec(spec: ResearchSpec) -> str:
    return dump_research_yaml(spec.to_dict())


def render_pack_template(path: Path, placeholders: dict[str, str] | None = None) -> str:
    template = path.read_text(encoding="utf-8")
    if not placeholders:
        return template
    for key, value in placeholders.items():
        template = template.replace("${" + key + "}", value)
    return template


def create_project_scaffold(
    project_root: Path,
    *,
    project_name: str,
    pack_id: str,
    data_source: str = "./datasets/eval_markets.json",
    pack_config: dict[str, Any] | None = None,
) -> dict[str, Path]:
    pack_manifest = find_pack_manifest(pack_id)
    project_root = project_root.resolve()
    created_paths: dict[str, Path] = {}

    for relative_dir in DEFAULT_PROJECT_DIRS:
        path = project_root / relative_dir
        path.mkdir(parents=True, exist_ok=True)
        created_paths[relative_dir] = path

    spec = default_research_spec(
        project_name=project_name,
        pack_id=pack_manifest.pack_id,
        data_source=data_source,
        allowed_axes=pack_manifest.allowed_axes,
        pack_config=pack_config,
    )
    research_path = project_root / "research.yaml"
    research_path.write_text(render_research_spec(spec), encoding="utf-8")
    created_paths["research.yaml"] = research_path

    template_path = project_root / "workspace" / "strategy.py"
    strategy_template = pack_manifest.entrypoints.get("strategy_template", "")
    if strategy_template:
        source_path = Path(pack_manifest.manifest_path).parent / strategy_template
        rendered = render_pack_template(
            source_path,
            {
                "PROJECT_NAME": project_name,
                "PACK_ID": pack_manifest.pack_id,
                "DATA_SOURCE": data_source,
            },
        )
        template_path.write_text(rendered, encoding="utf-8")
    else:
        template_path.write_text("# strategy template\n", encoding="utf-8")
    created_paths["workspace/strategy.py"] = template_path

    manifest_copy_path = project_root / ".autoresearch" / "state" / "pack_manifest.json"
    manifest_copy_path.write_text(dump_document(pack_manifest.to_dict()), encoding="utf-8")
    created_paths[".autoresearch/state/pack_manifest.json"] = manifest_copy_path

    dataset_readme = project_root / "datasets" / "README.md"
    if not dataset_readme.exists():
        dataset_readme.write_text(
            "Put your dataset files here and point `data.source` at the chosen file.\n",
            encoding="utf-8",
        )
    created_paths["datasets/README.md"] = dataset_readme
    return created_paths
