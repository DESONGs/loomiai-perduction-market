# Prediction Market Autoresearch

这个仓库当前同时包含两条能力线：

- 旧版预测市场实验与 `Dashboard`
- 新版可安装本地包 `autoresearch-agent`

`autoresearch-agent` 的目标，是把原来的预测市场 `auto research` 抽成一个可本地安装、可复用、可扩展的 `research agent`。用户可以用自己的数据、自己的优化方向，以及自己的约束条件来驱动迭代。

## 核心能力

- 本地初始化研究项目
- 自定义数据源、目标函数和搜索轴
- 基于 `pack` 复用领域能力
- 通过 CLI 或最小 `MCP` 外壳调用同一套运行时
- 为后续 `PyPI` / 开源发布保留标准打包与发布路径

## 仓库结构

```text
src/autoresearch_agent/    可安装本地包
examples/                  示例项目
prediction_market_data/    旧版 Dashboard / worker / runtime
autoresearch/              旧版策略实验目录
docs/                      规划与发布文档
```

## 安装

### 从源码安装

```bash
pip install -e .
```

### 按能力安装可选依赖

```bash
pip install -e ".[yaml]"
pip install -e ".[mcp]"
pip install -e ".[dev]"
```

### 未来从 `PyPI` 安装

正式发布后可直接使用：

```bash
pip install autoresearch-agent
```

## 快速开始

### 1. 初始化研究项目

```bash
python -m autoresearch_agent init ./demo-project --pack prediction_market --data-source ./dataset.json
```

初始化后目录结构：

```text
demo-project/
  research.yaml
  datasets/
  workspace/
    strategy.py
  artifacts/
  .autoresearch/
```

### 2. 校验配置

```bash
python -m autoresearch_agent validate ./demo-project
```

### 3. 启动一次本地迭代

```bash
python -m autoresearch_agent run ./demo-project
```

### 4. 查看运行状态和产物

```bash
python -m autoresearch_agent status <run_id> --project-root ./demo-project
python -m autoresearch_agent artifacts <run_id> --project-root ./demo-project
```

### 5. 基于已有运行继续迭代

```bash
python -m autoresearch_agent continue <run_id> --project-root ./demo-project
```

## CLI 命令

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

首版内置 `prediction_market` 这个 `pack`，负责定义：

- 数据适配方式
- 默认目标函数
- 可搜索轴
- 默认 `strategy.py` 模板
- 默认 prompt 模板

后续可以继续扩展更多 `pack`，例如事件预测、信号研究、报告生成等。

## 最小 `MCP` 外壳

仓库中已经提供一个不依赖第三方 `MCP` 框架的最小 stdio server：

```bash
python -m autoresearch_agent mcp serve --project-root ./demo-project
```

当前暴露的最小方法集包括：

- `ping`
- `list_packs`
- `validate_project`
- `run_project`
- `continue_run`
- `get_run_status`
- `list_artifacts`

## 示例项目

可以直接参考：

```text
examples/prediction-market/
```

## 验证

当前基础验证命令：

```bash
python -m unittest discover tests
python -m build --no-isolation
python -m twine check dist/*
```

## 发布与开源文档

- 发布流程：[`docs/pypi-release.md`](docs/pypi-release.md)
- 变更记录：[`CHANGELOG.md`](CHANGELOG.md)
- 贡献指南：[`CONTRIBUTING.md`](CONTRIBUTING.md)
- 安全策略：[`SECURITY.md`](SECURITY.md)
- 社区行为准则：[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)

正式发包建议先走 `TestPyPI`，再走生产 `PyPI`。

## 当前状态

当前 MVP 已具备：

- `research.yaml` 规格与校验
- 本地项目 scaffold
- `prediction_market` 首个 `pack`
- 本地 runtime：`run/status/artifacts/continue`
- CLI 外壳
- 最小 `MCP` 外壳
- `PyPI` 打包与发布工作流

## 历史能力

如果你仍然想使用原有的预测市场 `Dashboard` 形态，历史代码仍保留在：

```text
prediction_market_data/
autoresearch/
```
