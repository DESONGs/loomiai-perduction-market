# Autoresearch Module

`autoresearch` 是本仓库中的策略实验子目录，负责承载最小化的训练/推理脚本与评测样例，供 `prediction_market_data` 调用或二次修改。

## 目录说明

- `pm_prepare.py`：预测市场评测数据准备脚本
- `pm_train.py`：策略实验主脚本
- `pm_config.py`：模型接口配置，公开仓库中通过环境变量读取
- `eval_markets.json`：示例评测数据
- `train.py` / `prepare.py`：保留的基础实验脚本

## 环境变量

常用配置：

```bash
export API_BASE_URL=https://api.moonshot.cn/v1
export MOONSHOT_API_KEY=your_key
export MODEL_NAME=moonshot-v1-auto
```

## 说明

- 本目录已移除开发期日志、实验记录和内部 agent 指令文件
- 公开版本只保留运行所需的最小代码与样例数据
