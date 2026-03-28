# Autoresearch Agent 开发方案

## 1. 产品定位

目标不是继续扩展当前 `Dashboard` demo，而是把现有能力沉淀成一个“本地可安装、本地可调用、可配置、可迭代”的 `autoresearch agent` 包。

首版产品形态：

- 一个本地安装包：`autoresearch-agent`
- 一个项目配置文件：`research.yaml`
- 一个可插拔领域包机制：`pack`
- 一个可选调用壳：`MCP server`
- 一个可选本地观察界面：`Dashboard`

首版默认不做模型微调平台，先把“训练”定义为：

- 基于用户指定数据
- 围绕用户指定目标函数
- 对策略模板进行自我迭代、评估、筛选、继续运行

## 2. 推荐包目录结构

```text
autoresearch-agent/
├─ pyproject.toml
├─ README.md
├─ src/
│  └─ autoresearch_agent/
│     ├─ cli/
│     │  ├─ main.py
│     │  ├─ commands/
│     │  │  ├─ init.py
│     │  │  ├─ validate.py
│     │  │  ├─ run.py
│     │  │  ├─ continue_run.py
│     │  │  ├─ runs.py
│     │  │  ├─ inspect.py
│     │  │  ├─ packs.py
│     │  │  └─ serve_mcp.py
│     ├─ core/
│     │  ├─ config/
│     │  │  ├─ schema.py
│     │  │  ├─ defaults.py
│     │  │  └─ loader.py
│     │  ├─ runtime/
│     │  │  ├─ run_manager.py
│     │  │  ├─ worker_loop.py
│     │  │  ├─ artifact_store.py
│     │  │  ├─ event_stream.py
│     │  │  └─ sandbox.py
│     │  ├─ engine/
│     │  │  ├─ iteration_engine.py
│     │  │  ├─ gates.py
│     │  │  ├─ evaluator_runner.py
│     │  │  └─ mutation_controller.py
│     │  ├─ datasets/
│     │  │  ├─ adapter_base.py
│     │  │  ├─ validators.py
│     │  │  └─ snapshotter.py
│     │  ├─ packs/
│     │  │  ├─ registry.py
│     │  │  ├─ manifest.py
│     │  │  └─ loader.py
│     │  ├─ security/
│     │  │  ├─ secrets.py
│     │  │  ├─ policy.py
│     │  │  └─ quotas.py
│     │  └─ projections/
│     │     ├─ run_projection.py
│     │     └─ summaries.py
│     ├─ packs/
│     │  └─ prediction_market/
│     │     ├─ pack.yaml
│     │     ├─ adapters/
│     │     │  ├─ polymarket_csv.py
│     │     │  └─ canonical_json.py
│     │     ├─ evaluator/
│     │     │  ├─ prepare.py
│     │     │  └─ metrics.py
│     │     ├─ templates/
│     │     │  ├─ strategy.py
│     │     │  ├─ prompts/
│     │     │  └─ research.yaml
│     │     ├─ axes/
│     │     │  └─ axes.yaml
│     │     └─ docs/
│     │        └─ README.md
│     ├─ mcp/
│     │  ├─ server.py
│     │  ├─ tools/
│     │  │  ├─ init_project.py
│     │  │  ├─ list_packs.py
│     │  │  ├─ validate_dataset.py
│     │  │  ├─ create_run.py
│     │  │  ├─ get_run.py
│     │  │  └─ continue_run.py
│     │  └─ resources/
│     │     ├─ pack_docs.py
│     │     ├─ run_summary.py
│     │     └─ artifact_index.py
│     └─ ui/
│        └─ dashboard/
├─ docs/
│  ├─ config-reference.md
│  ├─ pack-spec.md
│  └─ cli-reference.md
└─ examples/
   ├─ prediction-market-basic/
   └─ prediction-market-custom-data/
```

目录边界：

- `core`：通用运行时，不含任何预测市场语义
- `packs`：领域包，预测市场只是第一个实现
- `cli`：本地调用入口
- `mcp`：给外部 agent 或桌面客户端调用的适配层
- `ui`：可选观察界面，不是核心依赖

## 3. research.yaml 完整字段设计

### 3.1 顶层原则

`research.yaml` 是用户唯一需要长期维护的核心配置。首版只保留六个维度：

- 项目元信息
- pack 与数据
- 目标函数
- 搜索空间
- 运行预算
- 安全与产物

### 3.2 字段清单

```yaml
version: research.v1

project:
  name: my-first-research
  description: ""
  owner: local
  tags: []

pack:
  id: prediction-market
  version: "1"
  strategy_template: default

data:
  source: ./datasets/input.csv
  adapter: polymarket_csv
  format: auto
  snapshot_on_run: true
  schema_map: {}
  split:
    mode: auto
    train_ratio: 0.5
    validation_ratio: 0.3
    holdout_ratio: 0.2
    seed: 42
  filters: []
  sample:
    enabled: true
    max_records: 200
    stratified_by: volume

objective:
  primary: maximize_pnl
  secondary:
    - maximize_accuracy
    - minimize_drawdown
  stop_when:
    min_primary_improvement: 0.15
    max_no_improve_iterations: 4

search:
  max_iterations: 10
  allowed_axes:
    - confidence_threshold
    - bet_sizing
    - max_bet_fraction
    - prompt_factors
  frozen_axes: []
  editable_targets:
    - strategy.py
  candidates_per_iteration: 3
  mutation_policy:
    mode: single_target_patch
    max_patch_hunks: 8
    allow_prompt_edits: true
    allow_logic_edits: true
    allow_dependency_changes: false

evaluation:
  evaluator: default
  repeats:
    search: 2
    validation: 2
    holdout: 1
  gate_policy:
    search_min_delta_fitness: 0.10
    validation_min_delta_fitness: 0.15
    validation_std_multiplier: 0.50
    max_drawdown_deterioration: 0.02
    holdout_max_drawdown_deterioration: 0.015
    max_token_per_market_ratio: 1.25
    high_token_validation_delta: 0.35
    trade_ratio_band:
      - 0.70
      - 1.30

runtime:
  provider: openai_compatible
  model: moonshot-v1-auto
  api_base: env:API_BASE_URL
  env_refs:
    - OPENAI_API_KEY
    - MOONSHOT_API_KEY
  secret_refs: []
  timeout_seconds: 900
  max_completion_tokens: 1200
  per_eval_token_budget: 150000
  total_token_budget: 0
  cpu_limit_seconds: 7200
  memory_limit_mb: 4096
  real_execution: false

safety:
  allow_network: true
  allow_shell: false
  allow_external_writes: false
  preserve_run: false
  retention_hours: 168
  require_dataset_validation: true
  require_model_probe: false

outputs:
  artifact_dir: ./artifacts
  save_patch: true
  save_logs: true
  save_dataset_snapshot: true
  save_run_projection: true
  export_formats:
    - json
    - md
```

### 3.3 字段设计说明

必填字段：

- `version`
- `project.name`
- `pack.id`
- `data.source`
- `objective.primary`

建议默认值：

- `pack.strategy_template = default`
- `data.format = auto`
- `data.snapshot_on_run = true`
- `data.split.mode = auto`
- `search.max_iterations = 10`
- `search.candidates_per_iteration = 3`
- `runtime.real_execution = false`
- `runtime.total_token_budget = 0`
- `safety.require_dataset_validation = true`
- `outputs.save_run_projection = true`

设计理由：

- `project` 只保留标识信息，避免把运行配置与展示元信息混在一起
- `pack` 把领域能力显式化，便于以后切换新领域
- `data` 支持用户自定义数据，但通过 `adapter` 和 `schema_map` 控制进入统一协议
- `objective` 让“训练方向”变成配置，而不是写死在策略脚本里
- `search` 单独声明可搜索轴、冻结轴和可编辑目标，方便做安全控制
- `evaluation` 单独承载 gate 与重复评估策略，避免污染业务配置
- `runtime` 管理模型与预算
- `safety` 明确哪些能力可开，避免首版权限失控
- `outputs` 让 artifact 保持可复盘

## 4. pack 插件规范

### 4.1 pack 的职责

每个 `pack` 负责提供某一类研究领域的完整最小闭环：

- 数据适配
- 数据校验
- 评估逻辑
- 默认策略模板
- 默认 prompt 模板
- 可变更轴目录
- 默认目标函数
- 默认 gate 策略

平台本身不理解预测市场，也不理解具体任务语义；平台只理解 pack 提供的能力清单。

### 4.2 pack manifest

每个 pack 根目录必须包含 `pack.yaml`：

```yaml
id: prediction-market
version: "1"
display_name: Prediction Market
description: Evaluate and self-iterate on prediction-market strategies.

capabilities:
  supports_custom_data: true
  supports_real_execution: true
  supports_mcp: true

data:
  supported_adapters:
    - polymarket_csv
    - canonical_json
  default_adapter: polymarket_csv
  required_fields:
    - question
    - outcomes
    - final_resolution_index
    - last_trade_price
  optional_fields:
    - volume
    - category
    - event_title
    - price_signals

strategy:
  template_file: templates/strategy.py
  editable_targets:
    - strategy.py
  mutation_mode: single_target_patch

evaluation:
  evaluator: evaluator/prepare.py
  metrics:
    - fitness
    - total_pnl
    - accuracy
    - max_drawdown
    - num_trades
  default_objective: maximize_pnl

search:
  axes_catalog: axes/axes.yaml
  default_allowed_axes:
    - confidence_threshold
    - bet_sizing
    - max_bet_fraction
    - prompt_factors

outputs:
  required_artifacts:
    - run_projection.json
    - results.tsv
    - strategy.patch
    - runtime_events.jsonl
```

### 4.3 axes 规范

`axes.yaml` 负责声明当前 pack 支持的搜索轴：

- `id`
- `display_name`
- `type`
- `editable_target`
- `allowed_values` 或 `range`
- `default`
- `safety_level`

首版 `prediction-market` pack 建议保留这四个轴：

- `confidence_threshold`
- `bet_sizing`
- `max_bet_fraction`
- `prompt_factors`

原因：

- 与现有仓库兼容
- 搜索空间足够明确
- 便于先保留单文件 patch 模式
- 风险比开放任意逻辑改写更低

## 5. 首版命令设计

### 5.1 本地 CLI

建议首版提供这些命令：

- `ar init`
  生成项目目录、`research.yaml`、默认模板、数据目录
- `ar packs list`
  查看本地可用 pack
- `ar packs inspect <pack_id>`
  查看某个 pack 的字段、轴、默认目标
- `ar validate`
  校验 `research.yaml` 和数据
- `ar run`
  发起一次新运行
- `ar continue <run_id>`
  基于上一次已接受策略继续迭代
- `ar runs`
  列出本地运行
- `ar inspect <run_id>`
  查看某次运行摘要
- `ar artifacts <run_id>`
  列出产物
- `ar serve-mcp`
  启动本地 `MCP server`

### 5.2 命令行为约束

- `ar run` 默认先做 `validate`
- 默认是 `dry-run` 风格，除非 `runtime.real_execution = true`
- `ar continue` 只能基于已完成 run
- `ar serve-mcp` 只暴露本地项目，不直接操控全局文件系统

## 6. 具体迭代执行开发方案

### 阶段 0：规格冻结

目标：

- 冻结目录结构
- 冻结 `research.yaml`
- 冻结 `pack.yaml`
- 冻结 CLI 命令面

交付物：

- 本方案文档
- `config-reference.md`
- `pack-spec.md`
- `cli-reference.md`

### 阶段 1：抽取通用 runtime

目标：

- 从当前 `prediction_market_data/app` 中抽出通用运行时
- 保留现有 `run_spec`、预检、worker、projection、artifact 机制
- 去掉预测市场硬编码

具体工作：

- 把当前 schema 能力转成 `research.v1` loader
- 把任务生命周期迁移到 `core/runtime`
- 把 `run_projection`、artifact、event stream 迁移到 `core/projections`

完成标准：

- 不依赖预测市场目录，也能初始化并加载一个空项目

### 阶段 2：做第一个 pack

目标：

- 把现有 `autoresearch` 逻辑沉淀成 `prediction-market` pack

具体工作：

- `pm_prepare.py` 变 evaluator
- `pm_train.py` 变默认策略模板
- 当前 adapter 迁移到 pack
- 当前搜索轴迁移到 `axes.yaml`

完成标准：

- 用户通过 `pack.id = prediction-market` 即可跑通完整闭环

### 阶段 3：做 CLI

目标：

- 用户无需读源码，只通过本地命令操作

具体工作：

- `init / validate / run / continue / runs / inspect / packs`

完成标准：

- 一个新用户能从空目录到第一次 run，无需手动碰内部脚本

### 阶段 4：做 MCP 壳

目标：

- 把本地 agent 暴露给 `Codex` / `Claude` / 桌面客户端

具体工作：

- `init_project`
- `list_packs`
- `validate_dataset`
- `create_run`
- `get_run`
- `continue_run`

完成标准：

- 外部 agent 能调用本地研究流程，但核心运行逻辑仍然在本地包内

### 阶段 5：做可选 Dashboard

目标：

- 将当前 Dashboard 改造成观察层，而不是运行核心

具体工作：

- 显示 run 列表
- 显示 run projection
- 查看 patch、日志、artifact
- 查看 gate 决策

完成标准：

- UI 不直接承载业务核心，关闭 UI 不影响本地 agent 执行

## 7. subagent 拆分方式

后续正式实施时，建议按这 4 条线并行：

- `subagent-1`
  负责 `research.yaml`、loader、默认值、校验器
- `subagent-2`
  负责 `core runtime`、run lifecycle、artifacts、projection
- `subagent-3`
  负责 `prediction-market pack` 迁移
- `subagent-4`
  负责 CLI 和 MCP 壳

主 agent 负责：

- 决策冻结
- 接口对齐
- 变更合并
- 端到端验证

## 8. 云端保存建议

建议云端先保存到 GitHub：

- 新建分支：`codex/autoresearch-agent-plan`
- 新增文档：`docs/autoresearch-agent-development-plan.md`

原因：

- 可审计
- 可版本化
- 后面实现时可以直接把每个阶段对照这份方案推进
- 比单独聊天记录或本地文件更适合作为团队执行基线
