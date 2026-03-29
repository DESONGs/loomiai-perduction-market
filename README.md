# autoresearch-agent

一个本地可安装的 `research agent runtime`。

当前仓库只保留一条链路：
- 唯一运行时：`src/autoresearch_agent`
- 唯一示例：`examples/prediction-market`
- 唯一对外能力面：标准 `MCP` over `stdio` + 本地 `CLI`

## 它能做什么

- 初始化一个本地研究项目
- 在 `research.yaml` 的 `search.editable_targets[0]` 上执行受控迭代
- 按 `project.artifacts_dir` 和 `outputs.*` 控制运行产物
- 通过标准 `MCP` 暴露给外部 `agent`

## 安装

从源码安装：

```bash
pip install -e .
```

如果你要运行测试：

```bash
pip install -e ".[dev]"
```

## 3 分钟上手

仓库已经自带一个可直接运行的预测市场示例数据：

```text
examples/prediction-market/datasets/eval_markets.json
```

### 1. 初始化项目

```bash
python -m autoresearch_agent init ./demo-project --pack prediction_market --data-source ./examples/prediction-market/datasets/eval_markets.json
```

初始化后会生成：

```text
demo-project/
  research.yaml
  datasets/
  workspace/
    strategy.py
  artifacts/
  .autoresearch/
```

### 2. 校验项目

```bash
python -m autoresearch_agent validate ./demo-project
```

### 3. 运行一轮研究

```bash
python -m autoresearch_agent run ./demo-project
```

### 4. 查看状态和产物

```bash
python -m autoresearch_agent status <run_id> --project-root ./demo-project
python -m autoresearch_agent artifacts <run_id> --project-root ./demo-project
```

### 5. 继续迭代

```bash
python -m autoresearch_agent continue <run_id> --project-root ./demo-project
```

## 产物说明

每次运行都会在 `.autoresearch/runs/<run_id>/` 下写出运行根目录文件：

- `result.json`
- `summary.json`

产物子目录由 `project.artifacts_dir` 控制，默认是 `./artifacts`，所以默认情况下还会看到：

- `artifacts/iteration_history.json`
- `artifacts/artifact_index.json`

当 `outputs.write_dataset_profile=true` 时，还会写出：

- `artifacts/dataset_profile.json`
- `artifacts/dataset_snapshot.json`

当 `outputs.write_best_strategy=true` 时，还会写出：

- `artifacts/best_strategy.py`

当 `outputs.write_patch=true` 时，还会写出：

- `artifacts/strategy.patch`

当 `outputs.write_report=true` 时，还会写出：

- `artifacts/report.md`

如果你把 `project.artifacts_dir` 改成 `./runtime-artifacts`，上面这些路径会整体变成 `runtime-artifacts/...`。

## 让自己的 agent 调用

这个服务现在使用标准 `MCP` over `stdio`。

启动方式：

```bash
python -m autoresearch_agent mcp serve --project-root ./demo-project
```

暴露的工具：

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

推荐调用顺序：

- `initialize`
- `notifications/initialized`
- `tools/list`
- `tools/call(name=run_project)`
- 轮询 `tools/call(name=get_run_status)`
- 完成后调用 `tools/call(name=list_artifacts)`
- 需要读取内容时调用 `tools/call(name=read_artifact)`
- 如果需要中止运行，调用 `tools/call(name=cancel_run)` 或 `tools/call(name=stop_run)`

`run_project` 和 `continue_run` 会返回一个可轮询的 `run_id`。服务端会把任务状态持久化到：

```text
<project>/.autoresearch/state/mcp_jobs/
```

这样即使 `MCP` 客户端重连，也可以继续查询状态或发送取消请求。

`cancel_run` / `stop_run` 采用分阶段终止：

- 先发送一次优雅终止信号
- 超过宽限期仍未退出时，自动升级为强制终止
- 状态会在 `get_run_status` 里持续收敛，直到 `cancelled`

`read_artifact` 默认最多返回前 `12000` 个字符，并在响应里带上 `truncated` 字段。  
如果需要完整读取更大的文本文件，请显式传入更大的 `max_chars`。

所有工具错误都会以结构化结果返回：

```json
{
  "ok": false,
  "error": {
    "code": "run_not_found",
    "message": "run not found: <run_id>",
    "details": {}
  }
}
```

一个最小 `MCP` 客户端配置示意：

```json
{
  "command": "python",
  "args": [
    "-m",
    "autoresearch_agent",
    "mcp",
    "serve",
    "--project-root",
    "/absolute/path/to/demo-project"
  ]
}
```

## Skill

仓库内提供了一份正式 `skill` 使用契约：

```text
skills/autoresearch-agent/SKILL.md
```

它定义了适用场景、前置条件、标准调用顺序、取消方式、产物读取方式和禁止事项。

## 验证脚本

仓库自带一个真实 `MCP` smoke：

```bash
./.venv/bin/python tools/mcp_real_smoke.py
```

它会：

- 用示例数据初始化临时项目
- 通过标准 `MCP` 调用 `initialize`、`notifications/initialized`、`tools/list`、`tools/call`
- 真实运行一轮研究
- 把结果保留到 `tmp/<run_id>/`

## 文档

- 架构与 `MCP/skill` 说明：[docs/runtime-architecture.md](docs/runtime-architecture.md)
- 发布流程：[docs/pypi-release.md](docs/pypi-release.md)
- 变更记录：[CHANGELOG.md](CHANGELOG.md)
- 贡献指南：[CONTRIBUTING.md](CONTRIBUTING.md)
- 安全策略：[SECURITY.md](SECURITY.md)

## 许可证

本项目使用 `MIT` 许可证。
