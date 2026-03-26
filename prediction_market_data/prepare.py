"""
prepare.py - 将 Polymarket 原始数据转换为 autoresearch 固定 eval 数据集

输出: prediction/eval_markets.json（生成后永不修改）

筛选规则:
  1. 已 resolved（closed=True 且有 resolvedBy）
  2. 高流动性（volumeNum >= 5000 USD）
  3. 二元市场（outcomes 只有2个选项）
  4. outcomePrices 可解析且有效

输出格式（每条记录）:
  {
    "market_id": "...",
    "question": "...",
    "outcomes": ["Yes", "No"],
    "outcome_prices": [0.99, 0.01],       # 最终结算价格
    "final_resolution": "Yes",             # 赢的 outcome
    "final_resolution_index": 0,           # 赢的 outcome 索引
    "last_trade_price": 0.85,              # 最后成交价（第一个 outcome 的）
    "price_signals": {                     # 可用的价格变化信号
      "1h_change": 0.02,
      "1d_change": -0.05,
      "1w_change": 0.10,
      "1m_change": 0.25,
      "1y_change": 0.40
    },
    "volume": 12345.67,
    "context": {
      "category": "politics",
      "subcategory": "...",
      "event_title": "...",
      "liquidity": 5000.0,
      "neg_risk": false
    }
  }
"""

import csv
import json
import ast
import os

PREDICTION_DIR = os.environ.get("PREDICTION_DIR", os.path.join(os.path.dirname(__file__), "prediction"))
INPUT_CSV = os.path.join(PREDICTION_DIR, "polymarket_markets.csv")
OUTPUT_JSON = os.path.join(PREDICTION_DIR, "eval_markets.json")

# 筛选参数
MIN_VOLUME = 5000       # 最低交易量（USD）
TARGET_COUNT = 2000     # 目标数量上限
RESOLUTION_THRESHOLD = 0.9  # outcomePrices >= 此值判定为 winning outcome


def parse_float(val, default=0.0):
    """安全解析浮点数"""
    try:
        return float(val) if val else default
    except (ValueError, TypeError):
        return default


def parse_json_list(val):
    """解析 JSON 列表字符串，如 '["Yes", "No"]'"""
    if not val:
        return None
    try:
        return json.loads(val)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(val)
        except (ValueError, SyntaxError):
            return None


def determine_resolution(outcome_prices, outcomes):
    """
    根据 outcomePrices 确定最终 resolution。
    返回 (winning_outcome, winning_index) 或 None。
    """
    if not outcome_prices or not outcomes:
        return None
    if len(outcome_prices) != len(outcomes):
        return None

    max_price = max(outcome_prices)
    if max_price < RESOLUTION_THRESHOLD:
        return None  # 没有明确的 winner

    winner_idx = outcome_prices.index(max_price)
    return outcomes[winner_idx], winner_idx


def process_row(row):
    """处理单行 CSV 数据，返回 eval 记录或 None"""
    # 筛选: 已 resolved
    if row.get("closed") != "True" or not row.get("resolvedBy"):
        return None

    # 筛选: 有足够交易量
    volume = parse_float(row.get("volumeNum", ""))
    if volume < MIN_VOLUME:
        return None

    # 解析 outcomes 和 outcomePrices
    outcomes = parse_json_list(row.get("outcomes", ""))
    outcome_prices_raw = parse_json_list(row.get("outcomePrices", ""))

    if not outcomes or not outcome_prices_raw:
        return None

    # 筛选: 只保留二元市场
    if len(outcomes) != 2:
        return None

    # 转换价格为浮点数
    outcome_prices = [parse_float(p) for p in outcome_prices_raw]

    # 确定 resolution
    resolution = determine_resolution(outcome_prices, outcomes)
    if resolution is None:
        return None

    winning_outcome, winning_index = resolution

    # 构建价格信号
    price_signals = {
        "1h_change": parse_float(row.get("oneHourPriceChange", "")),
        "1d_change": parse_float(row.get("oneDayPriceChange", "")),
        "1w_change": parse_float(row.get("oneWeekPriceChange", "")),
        "1m_change": parse_float(row.get("oneMonthPriceChange", "")),
        "1y_change": parse_float(row.get("oneYearPriceChange", "")),
    }

    # 构建 context
    category = row.get("category", "") or "unknown"
    context = {
        "category": category,
        "subcategory": row.get("subcategory", "") or "",
        "event_title": row.get("event_title", "") or "",
        "liquidity": parse_float(row.get("liquidityNum", "")),
        "neg_risk": row.get("negRisk", "") == "True",
    }

    return {
        "market_id": row.get("id", ""),
        "question": row.get("question", ""),
        "outcomes": outcomes,
        "outcome_prices": outcome_prices,
        "final_resolution": winning_outcome,
        "final_resolution_index": winning_index,
        "last_trade_price": parse_float(row.get("lastTradePrice", "")),
        "price_signals": price_signals,
        "volume": volume,
        "context": context,
    }


def main():
    print(f"读取数据: {INPUT_CSV}")
    records = []
    skipped = 0
    total = 0

    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            record = process_row(row)
            if record:
                records.append(record)
            else:
                skipped += 1

    print(f"总市场数: {total}")
    print(f"跳过: {skipped}")
    print(f"符合条件: {len(records)}")

    # 按交易量降序排序，取 top N
    records.sort(key=lambda x: x["volume"], reverse=True)
    records = records[:TARGET_COUNT]

    # 统计类别分布
    categories = {}
    for r in records:
        cat = r["context"]["category"]
        categories[cat] = categories.get(cat, 0) + 1

    print(f"\n最终 eval 集大小: {len(records)}")
    print(f"类别分布:")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")

    # resolution 分布（第一个 outcome 赢 vs 第二个）
    idx_dist = {0: 0, 1: 0}
    for r in records:
        idx_dist[r["final_resolution_index"]] += 1
    print(f"\nResolution 分布:")
    print(f"  outcome[0] 赢: {idx_dist[0]}")
    print(f"  outcome[1] 赢: {idx_dist[1]}")

    # 交易量范围
    if records:
        vols = [r["volume"] for r in records]
        print(f"\n交易量范围: ${min(vols):,.0f} - ${max(vols):,.0f}")
        print(f"中位数交易量: ${sorted(vols)[len(vols)//2]:,.0f}")

    # 写入 JSON
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"\n已保存到: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
