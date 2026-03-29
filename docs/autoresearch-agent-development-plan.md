# Autoresearch Agent Development Plan

## 1. Product Positioning

This project should evolve from a prediction-market demo into an installable local `research agent` package.

The first release should feel like:

- a local package users can install with `uv tool` or `pipx`
- a local project they can initialize with one command
- a reusable `pack` system for domain-specific evaluation logic
- an optional `MCP` server wrapper so desktop agents can call the same runtime

The first release should not depend on the dashboard to be usable.

## 2. Package Layout

### 2.1 Package Repository Layout

```text
autoresearch-agent/
  pyproject.toml
  README.md
  docs/
    autoresearch-agent-development-plan.md
  examples/
    prediction-market/
      research.yaml
      datasets/
      workspace/
  src/
    autoresearch_agent/
      __init__.py
      cli/
        main.py
        commands/
          init.py
          validate.py
          run.py
          continue_run.py
          status.py
          artifacts.py
          pack.py
          mcp.py
      core/
        spec/
          research_schema.py
          loader.py
        runtime/
          runner.py
          lifecycle.py
          sandbox.py
          state_store.py
        datasets/
          adapters.py
          profiler.py
          snapshot.py
        search/
          iteration_engine.py
          mutation_policy.py
          gate_policy.py
        evaluation/
          evaluator_base.py
          metrics.py
        artifacts/
          manifest.py
          writers.py
        security/
          secrets.py
          env_refs.py
      packs/
        prediction_market/
          pack.yaml
          docs.md
          adapters/
            polymarket_csv.py
            canonical_json.py
          evaluators/
            prediction_market.py
          templates/
            research.yaml
            strategy.py
          prompts/
            system.txt
            user.txt
          axes.yaml
      mcp/
        server.py
        tools/
          init_project.py
          validate_dataset.py
          run_task.py
          continue_run.py
          get_status.py
          get_results.py
          list_packs.py
```

### 2.2 User Project Layout After `init`

```text
my-research-project/
  research.yaml
  datasets/
  workspace/
    strategy.py
  artifacts/
  .autoresearch/
    runs/
    cache/
    state/
  .secrets.local
```

## 3. `research.yaml` Design

### 3.1 Top-Level Schema

The first release should support the following top-level fields:

- `schema_version`
- `project`
- `pack`
- `data`
- `objective`
- `search`
- `evaluation`
- `constraints`
- `runtime`
- `outputs`
- `pack_config`

### 3.2 Field Definitions

#### `schema_version`

- type: `string`
- required: yes
- default: `research.yaml.v1`

#### `project`

- `name`: `string`, required
- `description`: `string`, optional
- `workspace_dir`: `string`, optional, default `./workspace`
- `artifacts_dir`: `string`, optional, default `./artifacts`
- `runs_dir`: `string`, optional, default `./.autoresearch/runs`

#### `pack`

- `id`: `string`, required
- `version`: `string`, optional, default `latest`
- `entry_profile`: `string`, optional, default `default`

#### `data`

- `source`: `string`, required
- `format`: `string`, optional, default `auto`
- `adapter`: `string`, optional, default `auto`
- `snapshot_on_run`: `bool`, optional, default `true`
- `schema_map`: `object`, optional
- `filters`: `object`, optional
- `sampling.mode`: `string`, optional, default `fixed_count`
- `sampling.max_records`: `int`, optional
- `sampling.seed`: `int`, optional, default `42`
- `split.mode`: `string`, optional, default `auto`
- `split.train_ratio`: `float`, optional
- `split.validation_ratio`: `float`, optional
- `split.holdout_ratio`: `float`, optional

#### `objective`

- `primary`: `string`, required
- `secondary`: `list[string]`, optional, default `[]`
- `direction`: `string`, optional, default `maximize`
- `stop_when.metric`: `string`, optional
- `stop_when.threshold`: `float`, optional
- `notes`: `string`, optional

#### `search`

- `mode`: `string`, optional, default `self_iterate`
- `editable_targets`: `list[string]`, optional, default `["workspace/strategy.py"]`
- `allowed_axes`: `list[string]`, optional
- `frozen_axes`: `list[string]`, optional, default `[]`
- `max_iterations`: `int`, optional, default `10`
- `candidates_per_iteration`: `int`, optional, default `3`
- `mutation_policy`: `string`, optional, default `single_target_patch`
- `allow_prompt_edits`: `bool`, optional, default `true`
- `allow_feature_edits`: `bool`, optional, default `true`
- `allow_risk_edits`: `bool`, optional, default `true`

#### `evaluation`

- `sample_size`: `int`, optional
- `search_repeats`: `int`, optional, default `2`
- `validation_repeats`: `int`, optional, default `2`
- `holdout_repeats`: `int`, optional, default `1`
- `gate_profile`: `string`, optional, default `balanced`
- `gate_overrides`: `object`, optional
- `emit_breakdowns`: `bool`, optional, default `true`

#### `constraints`

- `total_token_budget`: `int`, optional, default `0`
- `per_eval_token_budget`: `int`, optional
- `max_completion_tokens`: `int`, optional
- `eval_timeout_seconds`: `int`, optional
- `max_runtime_minutes`: `int`, optional
- `max_memory_mb`: `int`, optional
- `max_cpu_seconds`: `int`, optional
- `allow_network`: `bool`, optional, default `false`
- `real_execution`: `bool`, optional, default `false`
- `preserve_run`: `bool`, optional, default `false`
- `retention_hours`: `int`, optional, default `168`

#### `runtime`

- `provider`: `string`, optional
- `model`: `string`, optional
- `api_base_url`: `string`, optional
- `env_refs`: `list[string]`, optional, default `[]`
- `secret_refs`: `list[string]`, optional, default `[]`
- `concurrency`: `int`, optional, default `1`

#### `outputs`

- `write_patch`: `bool`, optional, default `true`
- `write_report`: `bool`, optional, default `true`
- `write_dataset_profile`: `bool`, optional, default `true`
- `write_best_strategy`: `bool`, optional, default `true`
- `export_format`: `string`, optional, default `json`

#### `pack_config`

- type: `object`
- required: no
- purpose: pack-specific extra knobs without polluting the top-level contract

### 3.3 Example MVP File

```yaml
schema_version: research.yaml.v1

project:
  name: my-prediction-market-research

pack:
  id: prediction_market

data:
  source: ./datasets/eval_markets.json
  adapter: canonical_json
  snapshot_on_run: true
  sampling:
    max_records: 200
    seed: 42

objective:
  primary: maximize_pnl
  secondary:
    - maximize_accuracy
    - minimize_drawdown

search:
  editable_targets:
    - workspace/strategy.py
  allowed_axes:
    - prompt_factors
    - confidence_threshold
    - bet_sizing
    - max_bet_fraction
  max_iterations: 10
  candidates_per_iteration: 3

evaluation:
  sample_size: 200
  gate_profile: balanced

constraints:
  total_token_budget: 300000
  per_eval_token_budget: 150000
  eval_timeout_seconds: 900
  retention_hours: 168

runtime:
  provider: openai
  model: gpt-5.4
  env_refs:
    - OPENAI_API_KEY
    - OPENAI_BASE_URL

outputs:
  write_patch: true
  write_report: true
  write_best_strategy: true
```

## 4. `pack` Plugin Specification

### 4.1 `pack.yaml`

Each pack should contain a `pack.yaml` manifest with:

- `schema_version`
- `pack_id`
- `name`
- `version`
- `description`
- `domain`
- `entry_profile`
- `supported_formats`
- `default_adapter`
- `default_objective`
- `axes_catalog`
- `editable_targets`
- `entrypoints.adapter_module`
- `entrypoints.evaluator_module`
- `entrypoints.strategy_template`
- `entrypoints.prompt_templates`
- `defaults.research_template`
- `defaults.constraints`
- `defaults.evaluation`
- `security.allowed_env_refs`
- `security.allowed_secret_refs`
- `compatibility.min_agent_version`

### 4.2 Pack Folder Contract

Every pack should provide:

- one manifest file: `pack.yaml`
- at least one dataset adapter
- one evaluator
- one default strategy template
- one default `research.yaml`
- one axes catalog
- one short pack documentation file

### 4.3 Prediction Market Pack Mapping

The current repository should become the first pack with:

- adapters derived from current canonical and Polymarket adapters
- evaluator derived from current `pm_prepare.py`
- default strategy template derived from current `pm_train.py`
- axes catalog derived from current editable search axes

## 5. First Command Set

The first release should ship with these commands:

- `ar init`
  - create a new local research project
- `ar pack list`
  - list installed packs
- `ar pack install <pack>`
  - install a pack into the local environment
- `ar validate`
  - validate `research.yaml`, dataset, secrets, and runtime readiness
- `ar run`
  - start a new run from the local project
- `ar continue <run_id>`
  - continue iterating from the previous best state
- `ar status [run_id]`
  - show run status and summary
- `ar artifacts [run_id]`
  - list artifacts for a run
- `ar mcp serve`
  - expose the same runtime through an `MCP` server

## 6. `MCP` Surface for V1

The `MCP` wrapper should expose:

- `init_project`
- `list_packs`
- `validate_project`
- `run_project`
- `continue_run`
- `get_run_status`
- `get_run_summary`
- `list_artifacts`

## 7. Execution Model

### 7.1 Runtime Behavior

The runtime should:

- load `research.yaml`
- load the selected pack
- adapt and profile the dataset
- snapshot inputs
- copy the strategy template into the local workspace if needed
- run controlled iteration
- evaluate candidate changes
- write artifacts and structured run state

### 7.2 Safety Rules

The first release should keep these constraints:

- only one editable target by default
- patch-based mutation only
- no arbitrary multi-file mutation
- no network by default unless explicitly allowed
- all secrets injected by named references only

## 8. Development Plan

### Phase 0: Planning and Cloud Record

Goal:

- finalize this development plan
- store it in GitHub on a dedicated branch before coding

Output:

- one markdown plan document committed to GitHub

### Phase 1: Extract Core Runtime

Goal:

- move reusable runtime pieces out of the prediction-market app shell

Scope:

- spec loading
- run lifecycle
- artifact writing
- dataset snapshotting
- iteration engine wrapper

Output:

- `core/` package usable without dashboard

### Phase 2: Build the Project Scaffold

Goal:

- let users create a local project with `ar init`

Scope:

- scaffold generation
- `research.yaml` loader and validator
- local run directories

Output:

- local package can initialize and validate a project

### Phase 3: Implement Pack System

Goal:

- make prediction market the first installable pack

Scope:

- manifest loader
- adapter interface
- evaluator interface
- template loader
- axes catalog loader

Output:

- prediction market becomes a proper pack instead of hardcoded repo logic

### Phase 4: First End-to-End CLI

Goal:

- users can run local iteration from the command line

Scope:

- `init`
- `validate`
- `run`
- `status`
- `artifacts`

Output:

- first usable local agent workflow

### Phase 5: Add Continue and Resume

Goal:

- let users continue from a prior best run

Scope:

- persistent state
- best-strategy restore
- `continue` command

Output:

- iterative local agent workflow

### Phase 6: Wrap with `MCP`

Goal:

- make the same runtime callable from desktop agents

Scope:

- `MCP` server
- tool contracts
- local project resolution

Output:

- `MCP` wrapper over the same local runtime

### Phase 7: Optional Dashboard Adaptation

Goal:

- make the dashboard a secondary observer client instead of the execution core

Output:

- optional visual inspection layer

## 9. Suggested Subagent Workstream Split

- Subagent A: project scaffold + `research.yaml` schema
- Subagent B: pack manifest loader + prediction market pack extraction
- Subagent C: CLI workflow + lifecycle + artifact commands
- Subagent D: `MCP` adapter + docs and examples

The main agent should keep ownership of:

- final contract decisions
- merge integration
- behavior validation
- branch and release flow

## 10. Definition of Done

The plan is fully implemented when:

- a user can install the package locally
- a user can run `init`, edit `research.yaml`, add a dataset, and run iteration
- prediction market works as the first built-in pack
- the same local runtime is callable through `MCP`
- the dashboard is no longer required for core execution
