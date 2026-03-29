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
    if path.suffix.lower() == ".py":
        return "code"
    if path.suffix.lower() == ".patch":
        return "patch"
    if path.suffix.lower() in {".txt", ".log", ".md"}:
        return "text"
    return "file"


def _artifact_record_path(path: Path, run_dir: Path) -> str:
    try:
        return str(path.relative_to(run_dir))
    except ValueError:
        return str(path)


def build_artifact_index(run_dir: Path, *, artifacts_dir: Path | None = None) -> list[dict[str, Any]]:
    artifacts_dir = artifacts_dir or (run_dir / "artifacts")
    items: list[ArtifactRecord] = []
    if artifacts_dir.exists():
        for path in sorted(p for p in artifacts_dir.rglob("*") if p.is_file()):
            items.append(
                ArtifactRecord(
                    name=path.name,
                    path=_artifact_record_path(path, run_dir),
                    kind=_classify(path),
                    size_bytes=path.stat().st_size,
                )
            )

    for name in ("run_manifest.json", "run_spec.json", "result.json", "summary.json"):
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
