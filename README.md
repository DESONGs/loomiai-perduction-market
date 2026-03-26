# OpenClaw Prediction Market Lab

一个面向黑客松展示的预测市场研究项目：把历史市场数据整理成标准化评测集，用 LLM 生成交易判断，并通过可视化面板管理实验运行、结果和产物。

## 项目亮点

- 将 Polymarket 历史市场数据清洗为统一评测数据集
- 用可配置的 LLM 推理流程对市场进行自动分析
- 提供 Flask Dashboard 查看运行状态、日志、结果和产物
- 支持任务提交、预检、运行记录与工人进程管理
- 内置 `autoresearch` 子目录，用于策略脚本和实验逻辑演化

## 仓库结构

```text
prediction_market_data/   数据准备、任务编排、Dashboard 与运行目录
autoresearch/             策略实验脚本与最小训练/推理工程
```

## 快速开始

### 1. 配置环境变量

复制环境模板并填入自己的值：

```bash
cp prediction_market_data/.env.example prediction_market_data/.env
```

至少需要：

- `KAGGLE_API_TOKEN`：下载原始数据时使用
- `MOONSHOT_API_KEY` 或 `OPENAI_API_KEY`：模型推理时使用

仓库默认不提交原始大体积 CSV；如需完整原始数据，请运行下载脚本重新生成。

### 2. 启动 Dashboard

```bash
cd prediction_market_data
docker compose up --build
```

默认访问地址：

```text
http://localhost:5050
```

### 3. 本地数据准备

如果你只想先生成评测数据集：

```bash
cd prediction_market_data
python run_data_pipeline.py
```

## 典型流程

1. 下载并清洗 Polymarket 数据
2. 生成 `prediction/eval_markets.json`
3. 在 Dashboard 中提交任务或查看历史运行
4. 调用策略实验脚本执行评测
5. 下载结果、日志和产物进行展示或复盘

## 技术栈

- Python
- Flask
- Docker Compose
- KaggleHub
- OpenAI-compatible LLM API

## 安全说明

- 本仓库已移除开发期文档、运行缓存和敏感配置
- 所有 API key 通过环境变量注入，不再硬编码在代码中
- 发布时请不要提交 `.env`、运行日志、缓存目录和个人工具配置
- 原始下载数据未随仓库发布，避免仓库体积过大

## 演示建议

- 先展示 Dashboard 首页和运行记录
- 再展示数据准备流程和策略实验目录
- 最后补充一段真实运行结果或样例输出
