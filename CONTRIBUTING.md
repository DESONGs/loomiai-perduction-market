# Contributing

感谢你为这个项目投入时间。

## 开始之前

- 先阅读 [`README.md`](README.md) 了解项目定位
- 对发布相关改动，先阅读 [`docs/pypi-release.md`](docs/pypi-release.md)
- 提交前请确认你的变更范围清晰，并尽量让一个分支只服务一个 `PR`

## 本地开发

安装开发依赖：

```bash
pip install -e ".[dev]"
```

如需测试可选能力：

```bash
pip install -e ".[yaml,mcp,dev]"
```

## 建议工作流

1. 从最新 `main` 创建新分支
2. 保持改动聚焦，不要把无关文件混入同一个 `PR`
3. 为新行为补测试或更新现有测试
4. 运行本地验证
5. 提交草稿 `PR`

## 本地验证

提交前请至少运行：

```bash
python -m unittest discover tests
python -m build --no-isolation
python -m twine check dist/*
```

## Pull Request 指南

- 标题尽量明确说明范围和目的
- 描述中说明：
  - 改了什么
  - 为什么改
  - 对用户或开发者的影响
  - 如何验证
- 如果是发布、打包或工作流改动，请明确说明是否影响 `PyPI` / `TestPyPI`

## 文档要求

以下场景请同步更新文档：

- CLI 命令变化：更新 [`README.md`](README.md)
- 发布流程变化：更新 [`docs/pypi-release.md`](docs/pypi-release.md)
- 对外行为变化：更新 [`CHANGELOG.md`](CHANGELOG.md)

## 行为准则

参与项目前，请遵循 [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)。
