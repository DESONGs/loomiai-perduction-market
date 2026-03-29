# Prediction Market Autoresearch

这个仓库现在同时包含两条能力线：

- 旧版预测市场实验与 `Dashboard`
- 新版可安装本地包 `autoresearch-agent`

新版目标是把原来的预测市场 `auto research` 抽成一个本地可安装的 `research agent`，支持：

- 用户本地初始化研究项目
- 用户自定义数据源和优化方向
- 基于 `pack` 的领域能力复用
- 通过 CLI 或最小 `MCP` 外壳调用同一套运行时

## 新版目录

```text
src/autoresearch_agent/    可安装本地包
examples/                  示例项目
prediction_market_data/    旧版 Dashboard / worker / runtime
autoresearch/              旧版策略实验目录
docs/                      规划与演进文档
```

## 安装与本地使用

### 1. 以开发模式安装

```bash
pip install -e .
```

### 2. 初始化一个研究项目

```bash
python -m autoresearch_agent init ./demo-project --pack prediction_market --data-source ./dataset.json
```

初始化后项目目录会包含：

```text
demo-project/
  research.yaml
  datasets/
  workspace/
    strategy.py
  artifacts/
  .autoresearch/
```

### 3. 校验项目

```bash
python -m autoresearch_agent validate ./demo-project
```

### 4. 运行一轮本地迭代

```bash
python -m autoresearch_agent run ./demo-project
```

### 5. 查看状态与产物

```bash
python -m autoresearch_agent status <run_id> --project-root ./demo-project
python -m autoresearch_agent artifacts <run_id> --project-root ./demo-project
```

### 6. 从已有结果继续迭代

```bash
python -m autoresearch_agent continue <run_id> --project-root ./demo-project
```

## 支持的首版命令

- `init`
- `validate`
- `run`
- `continue`
- `status`
- `artifacts`
- `pack list`
- `pack install`
- `mcp serve`

## `pack` 机制

首版内置 `prediction_market` 这个 `pack`。

它定义了：

- 数据适配方式
- 默认目标函数
- 可搜索轴
- 默认 `strategy.py` 模板
- 默认 prompt 模板

后续可以继续扩展更多 `pack`，例如事件预测、信号研究、报告生成等。

## 最小 `MCP` 外壳

仓库内已经提供一个不依赖第三方 `MCP` 框架的最小 stdio server：

```bash
python -m autoresearch_agent mcp serve --project-root ./demo-project
```

它当前暴露的最小方法集包括：

- `ping`
- `list_packs`
- `validate_project`
- `run_project`
- `continue_run`
- `get_run_status`
- `list_artifacts`

## 示例

可以直接参考：

```text
examples/prediction-market/
```

## 开发状态

当前 MVP 已经具备这些能力：

- `research.yaml` 规格与校验
- 本地项目 scaffold
- `prediction_market` 首个 `pack`
- 本地 runtime：`run/status/artifacts/continue`
- CLI 外壳
- 最小 `MCP` 外壳

当前自动化验证：

```bash
python -m unittest discover tests
```

## PyPI 发布流程

仓库已经补上首版 `PyPI` 发包链路，包括：

- [package-check.yml](/tmp/loomiai-perduction-market-readonly/.github/workflows/package-check.yml)
- [publish-pypi.yml](/tmp/loomiai-perduction-market-readonly/.github/workflows/publish-pypi.yml)
- [pypi-release.md](/tmp/loomiai-perduction-market-readonly/docs/pypi-release.md)

本地发包验证命令：

```bash
python3 -m pip install --upgrade build twine
python3 -m build
python3 -m twine check dist/*
```

正式发布建议先走 `TestPyPI`，再走 `PyPI`。

## 历史能力

如果你仍然想使用原有的预测市场 `Dashboard` 形态，历史代码仍保留在：

```text
prediction_market_data/
autoresearch/
```
