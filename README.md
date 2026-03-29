# autoresearch-agent

一个可以本地安装的 `research agent`。

你可以给它自己的数据，让它自动迭代策略，并通过 CLI 或 `MCP` 让自己的 `agent` 调用它。

## 它能做什么

- 用本地数据初始化一个研究项目
- 自动运行一轮研究迭代
- 查看结果和产物
- 继续在上一次结果上迭代
- 通过 `MCP` 暴露给自己的 `agent`

当前内置的第一个方向是 `prediction_market`。

## 安装

### 方式 1：从源码安装

```bash
pip install -e .
```

如果你想安装可选依赖：

```bash
pip install -e ".[yaml,mcp,dev]"
```

### 方式 2：从 `PyPI` 安装

正式发布后可以直接：

```bash
pip install autoresearch-agent
```

## 3 分钟上手

### 1. 准备一个数据文件

先准备一个 `json` 文件，例如 `dataset.json`：

```json
[
  {
    "market_id": "m1",
    "question": "Will event A happen?",
    "outcomes": ["Yes", "No"],
    "last_trade_price": 0.7,
    "final_resolution_index": 0
  }
]
```

### 2. 初始化项目

```bash
python -m autoresearch_agent init ./demo-project --pack prediction_market --data-source ./dataset.json
```

初始化后会生成：

```text
demo-project/
  research.yaml
  workspace/
    strategy.py
  artifacts/
  .autoresearch/
```

### 3. 校验项目

```bash
python -m autoresearch_agent validate ./demo-project
```

### 4. 启动一次迭代

```bash
python -m autoresearch_agent run ./demo-project
```

### 5. 查看结果

```bash
python -m autoresearch_agent status <run_id> --project-root ./demo-project
python -m autoresearch_agent artifacts <run_id> --project-root ./demo-project
```

### 6. 继续迭代

```bash
python -m autoresearch_agent continue <run_id> --project-root ./demo-project
```

## 让自己的 agent 调用

如果你的 `agent` 支持 `MCP stdio`，直接启动这个服务：

```bash
python -m autoresearch_agent mcp serve --project-root ./demo-project
```

它会暴露这些基础方法：

- `ping`
- `list_packs`
- `validate_project`
- `run_project`
- `continue_run`
- `get_run_status`
- `list_artifacts`

一个最小的 `MCP` 配置示意：

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

## 最常用命令

```bash
python -m autoresearch_agent init ./demo-project --pack prediction_market --data-source ./dataset.json
python -m autoresearch_agent validate ./demo-project
python -m autoresearch_agent run ./demo-project
python -m autoresearch_agent status <run_id> --project-root ./demo-project
python -m autoresearch_agent continue <run_id> --project-root ./demo-project
python -m autoresearch_agent mcp serve --project-root ./demo-project
```

## 例子

可以直接参考：

```text
examples/prediction-market/
```

## 许可证

本项目使用 [`MIT`](LICENSE) 许可证。

## 其他文档

- 发布流程：[`docs/pypi-release.md`](docs/pypi-release.md)
- 变更记录：[`CHANGELOG.md`](CHANGELOG.md)
- 贡献指南：[`CONTRIBUTING.md`](CONTRIBUTING.md)
- 安全策略：[`SECURITY.md`](SECURITY.md)
