"""
预测市场 autoresearch 固定评估框架（不可修改）

功能：
  - 加载 eval_markets.json 固定评估集
  - 提供 evaluate_strategy() 函数计算 fitness
  - 提供采样功能控制每轮 API 调用量

用法：
  由 pm_train.py 导入使用，不直接运行。

Fitness 公式：
  fitness = total_pnl + 10 * accuracy - 5 * max_drawdown
"""

import json
import os
import random

# ---------------------------------------------------------------------------
# 常量（固定，不可修改）
# ---------------------------------------------------------------------------

EVAL_DATA_PATH = os.path.join(os.path.dirname(__file__), "eval_markets.json")
SAMPLE_SIZE = 200          # 每轮评估采样数量（控制 API 成本）
INITIAL_BANKROLL = 10000   # 初始资金
BET_UNIT = 100             # 默认下注单位
RANDOM_SEED = 42           # 采样随机种子（保证可复现）

# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def load_eval_markets():
    """加载完整评估集"""
    with open(EVAL_DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def sample_eval_markets(n=SAMPLE_SIZE, seed=RANDOM_SEED):
    """
    从评估集中采样 n 条市场，保证每次采样结果一致。
    覆盖不同交易量级别以保持多样性。
    """
    markets = load_eval_markets()
    rng = random.Random(seed)

    # 按交易量分3层采样，保证覆盖高/中/低流动性
    markets_sorted = sorted(markets, key=lambda x: x["volume"], reverse=True)
    third = len(markets_sorted) // 3
    high = markets_sorted[:third]
    mid = markets_sorted[third:2*third]
    low = markets_sorted[2*third:]

    per_tier = n // 3
    remainder = n - per_tier * 3

    sampled = []
    sampled.extend(rng.sample(high, min(per_tier + remainder, len(high))))
    sampled.extend(rng.sample(mid, min(per_tier, len(mid))))
    sampled.extend(rng.sample(low, min(per_tier, len(low))))

    rng.shuffle(sampled)
    return sampled[:n]


# ---------------------------------------------------------------------------
# 评估函数（固定，不可修改）
# ---------------------------------------------------------------------------

def calculate_pnl(bet, market):
    """
    计算单笔下注的 PnL。

    参数:
      bet: dict，策略的下注决策
        {
          "action": "buy" | "sell" | "skip",
          "outcome_index": 0 或 1,        # 买入哪个 outcome
          "size": float,                   # 下注金额
          "confidence": float,             # 0-1 置信度
        }
      market: dict，eval_markets.json 中的一条记录

    返回: float，盈亏金额
    """
    if bet["action"] == "skip":
        return 0.0

    outcome_idx = bet["outcome_index"]
    size = bet["size"]
    winning_idx = market["final_resolution_index"]

    # 使用 last_trade_price 作为买入价格
    # 对于 outcome_index=0，买入价 = last_trade_price
    # 对于 outcome_index=1，买入价 = 1 - last_trade_price
    last_price = market["last_trade_price"]
    if outcome_idx == 0:
        entry_price = last_price
    else:
        entry_price = 1.0 - last_price

    # 防止除零和极端价格
    entry_price = max(0.01, min(0.99, entry_price))

    if bet["action"] == "buy":
        # 买入：如果该 outcome 赢了，赚 (1 - entry_price) / entry_price * size
        #       如果输了，亏 size
        if outcome_idx == winning_idx:
            pnl = size * (1.0 - entry_price) / entry_price
        else:
            pnl = -size
    elif bet["action"] == "sell":
        # 卖出（做空）：如果该 outcome 输了，赚 entry_price / (1 - entry_price) * size
        #              如果赢了，亏 size
        if outcome_idx != winning_idx:
            pnl = size * entry_price / (1.0 - entry_price)
        else:
            pnl = -size
    else:
        pnl = 0.0

    return pnl


def evaluate_strategy(strategy_func, markets=None, verbose=False):
    """
    评估策略在固定 eval 集上的表现。

    参数:
      strategy_func: callable，接收 market dict，返回 bet dict
      markets: list[dict]，市场数据（默认使用采样集）
      verbose: bool，是否打印详细信息

    返回: dict，包含所有评估指标
      {
        "fitness": float,          # 综合得分（优化目标）
        "total_pnl": float,        # 总盈亏
        "accuracy": float,         # 预测准确率（0-1）
        "max_drawdown": float,     # 最大回撤（0-1）
        "num_trades": int,         # 实际交易次数
        "num_skipped": int,        # 跳过次数
        "win_rate": float,         # 胜率
        "avg_pnl_per_trade": float, # 平均每笔盈亏
        "sharpe_ratio": float,     # 夏普比率（简化版）
      }
    """
    if markets is None:
        markets = sample_eval_markets()

    bankroll = INITIAL_BANKROLL
    peak_bankroll = INITIAL_BANKROLL
    max_drawdown = 0.0

    pnl_list = []
    correct = 0
    total_trades = 0
    skipped = 0

    for i, market in enumerate(markets):
        try:
            bet = strategy_func(market)
        except Exception as e:
            if verbose:
                print(f"  策略异常 (market {market['market_id']}): {e}")
            bet = {"action": "skip", "outcome_index": 0, "size": 0, "confidence": 0}

        if bet["action"] == "skip":
            skipped += 1
            continue

        # 限制下注金额不超过当前资金的 20%
        max_bet = bankroll * 0.2
        bet["size"] = min(bet["size"], max_bet)

        if bet["size"] <= 0:
            skipped += 1
            continue

        pnl = calculate_pnl(bet, market)
        pnl_list.append(pnl)
        bankroll += pnl
        total_trades += 1

        if pnl > 0:
            correct += 1

        # 更新最大回撤
        peak_bankroll = max(peak_bankroll, bankroll)
        drawdown = (peak_bankroll - bankroll) / peak_bankroll if peak_bankroll > 0 else 0
        max_drawdown = max(max_drawdown, drawdown)

        if verbose and (i + 1) % 50 == 0:
            print(f"  进度: {i+1}/{len(markets)} | 资金: ${bankroll:.0f} | 交易: {total_trades} | 胜率: {correct/max(1,total_trades):.1%}")

    # 计算指标
    total_pnl = bankroll - INITIAL_BANKROLL
    accuracy = correct / max(1, total_trades)
    win_rate = correct / max(1, total_trades)
    avg_pnl = total_pnl / max(1, total_trades)

    # 夏普比率（简化版）
    if len(pnl_list) > 1:
        import statistics
        mean_pnl = statistics.mean(pnl_list)
        std_pnl = statistics.stdev(pnl_list)
        sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0
    else:
        sharpe = 0

    # Fitness 公式（核心优化目标）
    # 归一化 PnL 到合理范围
    normalized_pnl = total_pnl / INITIAL_BANKROLL
    fitness = normalized_pnl + 10 * accuracy - 5 * max_drawdown

    results = {
        "fitness": round(fitness, 6),
        "total_pnl": round(total_pnl, 2),
        "accuracy": round(accuracy, 4),
        "max_drawdown": round(max_drawdown, 4),
        "num_trades": total_trades,
        "num_skipped": skipped,
        "win_rate": round(win_rate, 4),
        "avg_pnl_per_trade": round(avg_pnl, 2),
        "sharpe_ratio": round(sharpe, 4),
        "final_bankroll": round(bankroll, 2),
    }

    return results


# ---------------------------------------------------------------------------
# 命令行运行：打印数据集摘要
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    markets = load_eval_markets()
    print(f"评估集总量: {len(markets)}")
    print(f"采样数量: {SAMPLE_SIZE}")

    sampled = sample_eval_markets()
    print(f"采样结果: {len(sampled)} 条")

    # 摘要统计
    vols = [m["volume"] for m in sampled]
    res_dist = {}
    for m in sampled:
        r = m["final_resolution"]
        res_dist[r] = res_dist.get(r, 0) + 1

    print(f"交易量范围: ${min(vols):,.0f} - ${max(vols):,.0f}")
    print(f"Resolution 分布: {res_dist}")

    # 测试随机策略
    print("\n--- 随机策略基线 ---")
    rng = random.Random(123)
    def random_strategy(market):
        action = rng.choice(["buy", "skip"])
        return {
            "action": action,
            "outcome_index": rng.randint(0, 1),
            "size": BET_UNIT,
            "confidence": 0.5,
        }

    results = evaluate_strategy(random_strategy, sampled, verbose=True)
    for k, v in results.items():
        print(f"  {k}: {v}")
