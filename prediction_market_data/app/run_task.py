#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.common import now_iso
from app.runs import create_run, run_paths, update_manifest
from app.schemas import ALLOWED_RUNTIME_ENV_VARS, build_run_spec
from app.secrets import resolve_secret_refs
from app.services.run_lifecycle_service import mark_run_failed, mark_run_finished, mark_run_running


def load_run_spec(path: Path | None) -> dict[str, Any]:
    if path is None:
        return build_run_spec()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("run spec file must contain a JSON object")
    return build_run_spec(payload)


def append_launcher_log(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text.rstrip() + "\n")


def build_launch_env(spec: dict[str, Any], source_dir: str = "") -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUNBUFFERED": "1",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
    }
    if os.environ.get("HOME"):
        env["HOME"] = os.environ["HOME"]
    if os.environ.get("PYTHONPATH"):
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    env["SOURCE_AUTORESEARCH_DIR"] = str(Path(source_dir).resolve()) if source_dir else os.environ.get("SOURCE_AUTORESEARCH_DIR", "")

    runtime = spec.get("runtime", {}) or {}
    for name in runtime.get("env_refs", []):
        if name in ALLOWED_RUNTIME_ENV_VARS and os.environ.get(name):
            env[name] = os.environ[name]
    secret_report = resolve_secret_refs(list(runtime.get("secret_refs", [])))
    missing = secret_report["missing"] + secret_report["unsupported"]
    if missing:
        raise ValueError(f"secret refs could not be resolved: {sorted(missing)}")
    env.update(secret_report["injected_env"])
    return env


def build_strategy_command(paths: dict[str, Path], spec: dict[str, Any], source_dir: str = "") -> list[str]:
    constraints = spec["constraints"]
    cmd = [
        sys.executable,
        "demo/strategy_experiment.py",
        "--max-iterations",
        str(constraints["max_iterations"]),
        "--eval-timeout",
        str(constraints["eval_timeout"]),
        "--sample-size",
        str(constraints["sample_size"]),
        "--per-eval-token-budget",
        str(constraints["per_eval_token_budget"]),
        "--total-token-budget",
        str(constraints["total_token_budget"]),
        "--max-completion-tokens",
        str(constraints["max_completion_tokens"]),
        "--runtime-dir",
        str(paths["runtime"]),
        "--eval-data-path",
        str(paths["canonical_eval"]),
        "--run-id",
        spec["run_id"],
        "--run-spec-path",
        str(paths["spec"]),
        "--allowed-axes",
        ",".join(constraints["allowed_axes"]),
    ]
    if source_dir:
        cmd.extend(["--source-dir", str(Path(source_dir).resolve())])
    return cmd


def execute_existing_run(existing_run_id: str, source_dir: str = "") -> dict[str, Any]:
    paths = run_paths(existing_run_id)
    if not paths["spec"].exists():
        raise FileNotFoundError(f"run spec not found for run_id={existing_run_id}")
    spec = json.loads(paths["spec"].read_text(encoding="utf-8"))
    manifest_before = json.loads(paths["manifest"].read_text(encoding="utf-8")) if paths["manifest"].exists() else {}
    if str(manifest_before.get("status", "")) != "running":
        mark_run_running(
            existing_run_id,
            pid=int(manifest_before.get("pid") or 0),
            worker_id=str(manifest_before.get("worker_id", "") or ""),
            started_at=now_iso(),
        )
    cmd = build_strategy_command(paths, spec, source_dir=source_dir)
    launch_env = build_launch_env(spec, source_dir=source_dir)

    proc = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, text=True, env=launch_env)
    if proc.stdout:
        append_launcher_log(paths["launcher_log"], proc.stdout)
    if proc.stderr:
        append_launcher_log(paths["launcher_log"], proc.stderr)

    if proc.returncode == 0:
        return mark_run_finished(existing_run_id, worker_id=str(manifest_before.get("worker_id", "") or ""))
    else:
        mark_run_failed(
            existing_run_id,
            worker_id=str(manifest_before.get("worker_id", "") or ""),
            error=proc.stderr.strip()[:1000] or f"exit code {proc.returncode}",
        )
        raise SystemExit(proc.returncode)

def main() -> None:
    parser = argparse.ArgumentParser(description="Create and execute a task-oriented prediction-market run.")
    parser.add_argument("--input", default="", help="Path to user dataset (.json or .csv)")
    parser.add_argument("--run-spec", default="", help="Optional run_spec.json override")
    parser.add_argument("--run-id", default="", help="Optional explicit run_id")
    parser.add_argument("--existing-run-id", default="", help="Execute an existing run without recreating files")
    parser.add_argument("--run-name", default="", help="Optional run_name override")
    parser.add_argument("--user-id", default="local", help="User or caller identifier")
    parser.add_argument("--source-dir", default="", help="Optional source autoresearch directory")
    parser.add_argument("--dry-run", action="store_true", help="Validate and create run files without launching training")
    args = parser.parse_args()

    if args.existing_run_id:
        manifest = execute_existing_run(args.existing_run_id, source_dir=args.source_dir)
        print(
            json.dumps(
                {"run_id": manifest["run_id"], "run_dir": manifest["paths"]["root"], "status": manifest["status"]},
                ensure_ascii=False,
            )
        )
        return

    if not args.input:
        raise ValueError("--input is required unless --existing-run-id is provided")

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"input file not found: {input_path}")

    spec_override = load_run_spec(Path(args.run_spec).resolve()) if args.run_spec else build_run_spec()
    if args.run_name:
        spec_override["run_name"] = args.run_name
    if args.user_id:
        spec_override["user_id"] = args.user_id
    if not spec_override.get("tenant_id"):
        spec_override["tenant_id"] = "default"

    manifest, paths = create_run(
        input_path=input_path,
        run_spec_payload=spec_override,
        run_id=args.run_id or None,
    )

    if args.dry_run:
        print(json.dumps({"manifest": manifest, "paths": {key: str(val) for key, val in paths.items()}}, ensure_ascii=False, indent=2))
        return

    manifest = execute_existing_run(manifest["run_id"], source_dir=args.source_dir)
    print(json.dumps({"run_id": manifest["run_id"], "run_dir": manifest["paths"]["root"], "status": manifest["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
