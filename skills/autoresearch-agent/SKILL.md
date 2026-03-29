---
name: autoresearch-agent
description: Use this skill when the user wants to run a local prediction-market research project, inspect the generated strategy artifacts, or expose the project as a standard MCP server for another agent.
---

# Autoresearch Agent

Use this skill when the user wants to run a local research iteration, inspect the result artifacts, or expose the project as an `MCP` server for another agent.

## Contract

- Treat this file as the formal operating contract for the local `autoresearch-agent` runtime.
- Treat `search.editable_targets[0]` as the only editable surface for the runtime.
- Treat `project.artifacts_dir` as the run-local artifact directory. The default scaffold uses `./artifacts`.
- Treat `outputs.*` as the source of truth for optional artifacts:
  - `write_dataset_profile` controls `dataset_profile.json` and `dataset_snapshot.json`
  - `write_best_strategy` controls `best_strategy.py`
  - `write_patch` controls `strategy.patch`
  - `write_report` controls `report.md`
- Always expect `result.json`, `summary.json`, `iteration_history.json`, and `artifact_index.json` for a finished run.

## Preconditions

- Work from the repository root or from a project root created by `autoresearch_agent init`.
- Prefer the bundled example dataset at `examples/prediction-market/datasets/eval_markets.json` when validating the stack.

## Workflow

1. Validate the project.

```bash
python -m autoresearch_agent validate ./demo-project
```

2. Run a research iteration.

```bash
python -m autoresearch_agent run ./demo-project
```

3. Inspect status and artifacts.

```bash
python -m autoresearch_agent status <run_id> --project-root ./demo-project
python -m autoresearch_agent artifacts <run_id> --project-root ./demo-project
```

4. If an external agent needs access, start the standard `MCP` server.

```bash
python -m autoresearch_agent mcp serve --project-root ./demo-project
```

5. Use the `MCP` lifecycle in this order:

- `initialize`
- `notifications/initialized`
- `tools/list`
- `tools/call(name=run_project)`
- poll `tools/call(name=get_run_status)` until `finished`
- `tools/call(name=list_artifacts)`
- use `tools/call(name=read_artifact)` to read `best_strategy.py`, `report.md`, or `strategy.patch`
- if the run should stop, call `tools/call(name=cancel_run)` or `tools/call(name=stop_run)`

The server persists run control state under:

```text
<project>/.autoresearch/state/mcp_jobs/
```

This allows a restarted `MCP` client to continue polling or cancel a run by `run_id`.
Cancellation is staged: first a graceful stop is requested, then the server escalates to force-kill if the run does not exit within the grace window.

## Expected outputs

- `result.json`
- `summary.json`
- `<project.artifacts_dir>/iteration_history.json`
- `<project.artifacts_dir>/artifact_index.json`
- `<project.artifacts_dir>/best_strategy.py` when `write_best_strategy=true`
- `<project.artifacts_dir>/strategy.patch` when `write_patch=true`
- `<project.artifacts_dir>/report.md` when `write_report=true`
- `<project.artifacts_dir>/dataset_profile.json` and `<project.artifacts_dir>/dataset_snapshot.json` when `write_dataset_profile=true`

`read_artifact` returns at most `12000` characters by default and marks truncation with `artifact.truncated=true`.
If the tool fails, expect a structured envelope under `error.code`, `error.message`, and `error.details`.

## Guardrails

- Treat `search.editable_targets[0]` as the editable surface. The default scaffold uses `workspace/strategy.py`.
- Keep `project.artifacts_dir` inside the run directory. Do not point it outside the run tree.
- Do not use deleted legacy paths such as `prediction_market_data/` or `autoresearch/`.
- Prefer the standard `MCP` tool flow: `initialize` -> `notifications/initialized` -> `tools/list` -> `tools/call`.
- Do not assume long-running tasks are synchronous. Always poll `get_run_status` and inspect artifacts after the run finishes.
