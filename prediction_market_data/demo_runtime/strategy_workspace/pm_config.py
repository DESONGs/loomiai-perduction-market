"""
Kimi 2.5 API 配置

使用 OpenAI 兼容格式。公开仓库中请通过环境变量提供 API key。
"""

import os

# Kimi 2.5 API 配置（OpenAI 兼容格式）
API_BASE_URL = os.environ.get("API_BASE_URL", "https://api.moonshot.cn/v1")
API_KEY = os.environ.get("MOONSHOT_API_KEY", "")
MODEL_NAME = os.environ.get("MODEL_NAME", "moonshot-v1-auto")

# 推理参数
TEMPERATURE = 0.3
MAX_TOKENS = 300
