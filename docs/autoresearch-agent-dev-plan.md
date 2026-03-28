# `autoresearch-agent` 开发方案

## 1. 产品定位

目标不是继续做一个以 `Dashboard` 为中心的演示仓库，而是把现有能力收敛成一个“本地可安装、可本地运行、可被 `MCP` 调用”的研究型 `agent` 包。

首版形态：

- 本地安装方式：优先 `Python CLI`，推荐 `uv tool install` / `pipx install`
- 核心入口：`ar` 命令
- 配置入口：`research.yaml`
- 扩展方式：通过 `pack` 安装领域插件
- 外部接入：可选开启本地 `MCP server`

首版不做的事：

- 不做真正的基础模型 `fine-tuning`
- 不做多租户云平台
- 不开放任意多文件自动修改
- 不把 `Dashboard` 当主入口

首版“训练”定义：

- 策略文件、提示词、参数、特征和评估目标的自我迭代优化

## 2. 推荐包目录结构

```text
autoresearch-agent/
  pyproject.toml
  README.md
  docs/
    architecture.md
    pack-spec.md
    research-schema.md
  src/
    autoresearch_agent/
      cli/
        main.py
        commands/
          init.py
          run.py
          continue_run.py
          status.py
          compare.py
          pack.py
          mcp.py
      core/
        config/
          schema.py
          loader.py
          validator.py
        runtime/
          run_manager.py
          workspace_manager.py
          artifact_manager.py
          checkpoint_manager.py
        engine/
          iteration_engine.py
          mutation_engine.py
          evaluation_engine.py
          gate_engine.py
        adapters/
          dataset_adapter.py
          model_provider.py
          secret_provider.py
        contracts/
          research_spec.py
          pack_manifest.py
          run_record.py
          artifact_record.py
      packs/
        registry/
          local_registry.py
          installer.py
          resolver.py
        prediction_market/
          pack.yaml
          README.md
          adapter.py
          evaluator.py
          strategy_template.py
          prompt_templates/
            baseline.md
          defaults/
            research.defaults.yaml
            axes.yaml
            gates.yaml
      mcp/
        server.py
        tools/
          init_project.py
          list_packs.py
          validate_dataset.py
          create_run.py
          continue_run.py
          get_run.py
          compare_runs.py
      ui/
        inspector/
          app.py
  templates/
    project/
      research.yaml
      strategy.py
      .secrets.example
      datasets/
      artifacts/
      runs/
  tests/
    unit/
    integration/
    fixtures/
```

目录边界：

- `cli/`：面向最终用户的命令入口
- `core/`：完全通用，不关心预测市场
- `packs/`：领域插件，预测市场只是第一个内置 `pack`
- `mcp/`：把本地能力暴露给外部 `agent`
- `ui/`：仅做本地运行结果查看器，不承载核心逻辑
- `templates/`：初始化项目脚手架

## 3. 本地项目目录结构

用户运行 `ar init` 后，本地生成：

```text
my-research-project/
  research.yaml
  strategy.py
  datasets/
  artifacts/
  runs/
  .secrets.local
```

职责边界：

- `research.yaml`：唯一主配置
- `strategy.py`：唯一默认可编辑目标文件
- `datasets/`：用户自己的数据
- `artifacts/`：导出报告、patch、对比结果
- `runs/`：每次运行的快照和中间状态
- `.secrets.local`：仅本地使用，不进入版本库

## 4. `research.yaml` 完整字段设计

```yaml
schema_version: research.v1
project:
  name: prediction-market-local
  description: optional
  pack: prediction-market
  workspace_mode: local

data:
  source:
    type: file
    path: ./datasets/markets.csv
  adapter: polymarket_csv
  format: csv
  split:
    strategy: fixed_ratio
    train_ratio: 0.5
    validation_ratio: 0.3
    holdout_ratio: 0.2
    seed: 42
  sampling:
    enabled: true
    max_records: 200
    stratify_by: volume_bucket
  schema_mapping:
    question: question
    outcomes: outcomes
    probability: last_trade_price
    label: final_resolution_index

objective:
  primary: maximize_pnl
  secondary:
    - maximize_accuracy
    - minimize_drawdown
  stop_when:
    min_primary_improvement: 0.1
    plateau_rounds: 4

search:
  editable_files:
    - ./strategy.py
  allowed_axes:
    - confidence_threshold
    - bet_sizing
    - max_bet_fraction
    - prompt_factors
  frozen_axes: []
  max_iterations: 20
  candidates_per_iteration: 3
  mutation_policy: patch_only

evaluation:
  evaluator: prediction_market_default
  repeats:
    search: 2
    validation: 2
    holdout: 1
  sample_ratio:
    search: 0.8
    validation: 0.8
    holdout: 1.0
  gate_policy:
    search_min_delta_fitness: 0.1
    validation_min_delta_fitness: 0.15
    validation_std_multiplier: 0.5
    max_drawdown_deterioration: 0.02
    holdout_max_drawdown_deterioration: 0.015
    max_token_per_market_ratio: 1.25
    high_token_validation_delta: 0.35
    trade_ratio_band: [0.7, 1.3]

runtime:
  provider: openai_compatible
  model: moonshot-v1-auto
  base_url_env: API_BASE_URL
  api_key_env: MOONSHOT_API_KEY
  temperature: 0.3
  max_completion_tokens: 300
  timeout_seconds: 900
  parallelism: 1

budgets:
  total_token_budget: 300000
  per_eval_token_budget: 150000
  max_runtime_minutes: 120

safety:
  allow_network: true
  allow_shell: false
  patch_validation: strict
  allowed_paths:
    - ./strategy.py
  preserve_failed_artifacts: true

outputs:
  save_run_log: true
  save_patch: true
  save_projection: true
  save_live_events: true
  export_summary_md: true

mcp:
  enabled: false
  server_name: autoresearch-local
  expose_tools:
    - create_run
    - continue_run
    - get_run
    - compare_runs
```

字段设计原则：

- `project`：项目身份与 `pack` 绑定
- `data`：用户真正关心的“拿什么数据做研究”
- `objective`：用户真正关心的“朝什么方向训练”
- `search`：定义允许 agent 改什么
- `evaluation`：定义如何判断改得更好
- `runtime`：模型与推理后端
- `budgets`：成本控制
- `safety`：本地运行边界
- `outputs`：产物保留策略
- `mcp`：是否暴露成可调用服务

必填字段建议：

- `project.name`
- `project.pack`
- `data.source.type`
- `data.source.path`
- `data.adapter`
- `objective.primary`
- `search.allowed_axes`
- `search.max_iterations`
- `runtime.provider`
- `runtime.model`

## 5. `pack` 插件规范

`pack` 是领域能力包，不是完整运行时。它必须只负责领域差异。

### 5.1 `pack` 目录

```text
packs/prediction_market/
  pack.yaml
  README.md
  adapter.py
  evaluator.py
  strategy_template.py
  prompt_templates/
  defaults/
    research.defaults.yaml
    axes.yaml
    gates.yaml
```

### 5.2 `pack.yaml` 字段

```yaml
pack_version: 1
id: prediction-market
name: Prediction Market Pack
description: Optimize local strategies against prediction market datasets
entrypoints:
  adapter: adapter.py
  evaluator: evaluator.py
  strategy_template: strategy_template.py
capabilities:
  supports_local_files: true
  supports_json: true
  supports_csv: true
  supports_mcp: true
dataset_contract:
  required_fields:
    - question
    - outcomes
    - last_trade_price
    - final_resolution_index
  optional_fields:
    - volume
    - category
    - price_signals
adapters:
  - canonical_json
  - canonical_csv
  - polymarket_csv
axes_catalog:
  - id: confidence_threshold
    type: numeric
    default: 0.75
  - id: bet_sizing
    type: enum
    values: [fixed, confidence_scaled, kelly]
  - id: max_bet_fraction
    type: numeric
    default: 0.15
  - id: prompt_factors
    type: multiselect
    values:
      - extreme_price_skepticism
      - evidence_balance
      - volume_awareness
      - event_type_branching
default_objectives:
  primary: maximize_pnl
  secondary:
    - maximize_accuracy
    - minimize_drawdown
managed_targets:
  - path: ./strategy.py
    mutation_policy: patch_only
    required_exports:
      - strategy
default_gate_profile: standard
artifact_contract:
  required:
    - run_log
    - patch
    - iteration_summary
    - projection
```

### 5.3 `pack` 边界

- `pack` 可以定义数据协议、评估逻辑、默认轴、默认模板
- `pack` 不应该自己管理运行生命周期
- `pack` 不应该自己直接管理 secrets
- `pack` 不应该自己实现 `CLI`
- `pack` 不应该绕开 `core` 直接写运行目录

### 5.4 MVP 对 `pack` 的限制

- 首版只允许单一 `managed_target`
- 首版只支持本地文件数据源
- 首版只支持同步运行
- 首版不支持第三方远程下载安装市场，先支持内置和本地路径安装

## 6. 首版命令设计

统一命令前缀：`ar`

### 6.1 用户命令

- `ar init`
  初始化本地项目，生成 `research.yaml`、`strategy.py`、目录骨架

- `ar pack list`
  查看本地可用 `pack`

- `ar pack inspect <pack_id>`
  查看某个 `pack` 的数据要求、可搜索轴、默认目标

- `ar pack install <path-or-pack-id>`
  安装本地 `pack`

- `ar validate`
  校验 `research.yaml`、数据格式和本地环境

- `ar run`
  发起一次完整迭代运行

- `ar continue <run_id>`
  基于已有运行继续迭代

- `ar status <run_id>`
  查看当前运行状态

- `ar result <run_id>`
  查看运行摘要、最佳候选和 artifact

- `ar compare <run_id> <run_id>`
  对比两个运行结果

- `ar export <run_id>`
  导出总结和产物

- `ar mcp serve`
  启动本地 `MCP server`

### 6.2 首版 `MCP` 工具

- `init_project`
- `list_packs`
- `inspect_pack`
- `validate_project`
- `create_run`
- `continue_run`
- `get_run_status`
- `get_run_result`
- `compare_runs`

## 7. 首版执行流程

### 7.1 本地运行主链路

1. 读取 `research.yaml`
2. 加载目标 `pack`
3. 校验数据源与配置
4. 将数据转为规范评估集
5. 初始化 `runs/<run_id>/`
6. 复制 `strategy_template` 到工作区
7. 进入迭代引擎
8. 生成 patch
9. 执行 search / validation / holdout
10. 保存 artifact 和最佳结果
11. 输出摘要

### 7.2 运行目录

```text
runs/<run_id>/
  run_manifest.json
  research.snapshot.yaml
  data/
    prepared_dataset.json
  runtime/
    strategy.py
    patch.diff
    run_log.jsonl
    live_events.jsonl
    projection.json
  artifacts/
    summary.md
    comparison.json
```

## 8. 具体迭代执行开发方案

### 阶段 0：方案冻结

目标：

- 冻结目录结构
- 冻结 `research.yaml` 结构
- 冻结 `pack` 规范
- 冻结首版命令面

完成标准：

- 方案文档入库
- 评审通过后才能开始编码

### 阶段 1：抽 `core runtime`

范围：

- 把现有 `prediction_market_data/app` 中与领域无关的能力抽成 `core`
- 保留 `run_spec`、任务生命周期、artifact、projection 逻辑

完成标准：

- 不依赖预测市场语义也能创建和追踪一次运行
- 原测试思路可迁移

### 阶段 2：做 `prediction-market pack`

范围：

- 把 `adapter`、`evaluator`、默认策略模板、默认 gate 参数移入 `pack`
- 把当前 `allowed_axes` 变成 `pack` 声明

完成标准：

- 用 `pack` 可以完整重现当前预测市场运行链路

### 阶段 3：做本地项目骨架与 `CLI`

范围：

- `ar init`
- `ar validate`
- `ar run`
- `ar status`
- `ar result`

完成标准：

- 用户不需要看仓库内部代码即可跑起来

### 阶段 4：做本地 `MCP adapter`

范围：

- `ar mcp serve`
- 暴露首版 `MCP` 工具

完成标准：

- `Codex` / 其他 `MCP client` 能调用本地 agent

### 阶段 5：补充本地查看器与文档

范围：

- 最小本地结果查看页面或终端报告
- 使用文档
- `pack` 开发指南

完成标准：

- 第三方可以按规范创建第二个 `pack`

## 9. 多 `subagent` 执行拆分方案

主控 agent 负责任务编排、验收和合并。

建议至少拆成 4 个 `subagent`：

- `subagent-1: core-runtime`
  负责 `run manager`、workspace、artifact、projection

- `subagent-2: pack-system`
  负责 `pack manifest`、`prediction-market pack`、适配器和评估器迁移

- `subagent-3: cli-mcp`
  负责 `CLI`、命令面、`MCP server` 封装

- `subagent-4: docs-validation`
  负责 schema 文档、示例项目、测试和验收清单

协作规则：

- `subagent` 不跨边界改文件
- 所有集成点以冻结协议为准
- 每轮合并前由主控 agent 做接口核对和回归验证

## 10. 云端保存建议

建议保存方式：

- 在目标仓库新建分支
- 提交本文件到 `docs/autoresearch-agent-dev-plan.md`
- 分支名建议：`codex/autoresearch-agent-plan`

原因：

- 可版本化
- 可继续增量修订
- 可直接作为后续开发唯一依据
