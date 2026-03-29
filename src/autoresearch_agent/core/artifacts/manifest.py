from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ArtifactRecord:
    name: str
    path: str
    kind: str
    size_bytes: int


def _classify(path: Path) -> str:
    if path.suffix.lower() in {".json", ".jsonl"}:
        return "json"
    if path.suffix.lower() in {".txt", ".log", ".md"}:
        return "text"
    return "file"


def build_artifact_index(run_dir: Path) -> list[dict[str, Any]]:
    artifacts_dir = run_dir / "artifacts"
    items: list[ArtifactRecord] = []
    if artifacts_dir.exists():
        for path in sorted(p for p in artifacts_dir.rglob("*") if p.is_file()):
            items.append(
                ArtifactRecord(
                    name=path.name,
                    path=str(path.relative_to(run_dir)),
                    kind=_classify(path),
                    size_bytes=path.stat().st_size,
                )
            )

    for name in ("run_manifest.json", "run_spec.json", "result.json", "summary.json", "dataset_profile.json"):
        path = run_dir / name
        if path.exists():
            items.append(
                ArtifactRecord(
                    name=name,
                    path=str(path.relative_to(run_dir)),
                    kind=_classify(path),
                    size_bytes=path.stat().st_size,
                )
            )

    return [asdict(item) for item in items]
