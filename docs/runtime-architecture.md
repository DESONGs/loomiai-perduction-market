# Runtime Architecture

## 单一链路

当前仓库只保留一个主运行时：

- `src/autoresearch_agent`

它负责：

- 项目初始化
- `research.yaml` 解析
- `workspace/strategy.py` 加载
- 研究迭代执行
- 产物落盘
- 标准 `MCP` over `stdio`

## 运行模型

一次标准运行包含这些阶段：

1. 读取 `research.yaml`
2. 读取 `data.source`
3. 加载 `search.editable_targets[0]` 指向的策略文件
4. 以策略常量作为初始配置执行迭代
5. 写出 `result.json`、`summary.json`
6. 把运行产物写到 `project.artifacts_dir` 指定的 run 内子目录

## strategy 契约

`search.editable_targets[0]` 当前是运行时实际加载的受控变更面。

默认脚手架会把它写成 `workspace/strategy.py`，但如果你在 `research.yaml` 里改成别的路径，校验和运行都会跟随这个字段。

推荐暴露：

- `CONFIDENCE_THRESHOLD`
- `BET_SIZING`
- `MAX_BET_FRACTION`
- `PROMPT_FACTORS`
- `strategy(record, config=None)`

运行时会：

- 读取这些常量作为初始配置
- 调用 `strategy(record, config)` 做真实评估
- 用最佳配置生成新的 `best_strategy.py`

## 产物契约

`project.artifacts_dir` 定义每次 run 的产物子目录，默认值是 `./artifacts`。

- 这个路径以 `run_dir` 为基准解析
- 当前必须落在 `run_dir` 内部，不能指向 run 外路径
- `iteration_history.json` 和 `artifact_index.json` 始终写出到该目录

`outputs.*` 的执行语义如下：

- `write_dataset_profile=true`：写出 `dataset_profile.json` 和 `dataset_snapshot.json`
- `write_best_strategy=true`：写出 `best_strategy.py`
- `write_patch=true`：写出 `strategy.patch`
- `write_report=true`：写出 `report.md`

## MCP 能力面

`python -m autoresearch_agent mcp serve --project-root <path>`

当前暴露工具：

- `ping`
- `list_packs`
- `validate_project`
- `run_project`
- `continue_run`
- `cancel_run`
- `stop_run`
- `get_run_status`
- `list_artifacts`
- `read_artifact`

其中 `run_project` 和 `continue_run` 采用“提交后轮询”模型：

- 第一次调用只返回 `run_id` 和排队状态
- 真实执行在服务端后台子进程中完成
- 任务状态会持久化到 `.autoresearch/state/mcp_jobs/<run_id>.json`
- 客户端通过 `get_run_status` 轮询直到 `finished`
- 如需中止，调用 `cancel_run` 或 `stop_run`
- 中止会先尝试优雅终止；超过宽限期仍未退出时，服务端会升级为强制终止
- 完成后再调用 `list_artifacts`
- 需要读取 `best_strategy.py`、`strategy.patch`、`report.md` 等文本产物时，使用 `read_artifact`

`read_artifact` 的默认行为：

- 未传 `max_chars` 时，默认只返回前 `12000` 个字符
- 响应会带 `truncated` 字段，标记是否发生截断

工具错误统一包装为结构化响应：

- `ok=false`
- `error.code`
- `error.message`
- `error.details`

## 示例路径

唯一官方示例：

- `examples/prediction-market`

唯一官方示例数据：

- `examples/prediction-market/datasets/eval_markets.json`
