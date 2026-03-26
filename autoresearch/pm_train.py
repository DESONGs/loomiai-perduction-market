"""
预测市场策略 — autoresearch 自动迭代文件

此文件是 agent 修改迭代的唯一文件。
Agent 可以修改：策略逻辑、提示词、特征工程、下注逻辑、风控参数等。

用法: python pm_train.py
输出: fitness 和各项评估指标
"""

import json
import time
import os
import threading
from openai import OpenAI

from pm_prepare import evaluate_strategy, sample_eval_markets, BET_UNIT, INITIAL_BANKROLL
from pm_config import API_BASE_URL, API_KEY, MODEL_NAME, TEMPERATURE, MAX_TOKENS

# ---------------------------------------------------------------------------
# Kimi K2.5 客户端
# ---------------------------------------------------------------------------

client = OpenAI(
    api_key=API_KEY,
    base_url=API_BASE_URL,
)

# ---------------------------------------------------------------------------
# Token 用量监控
# ---------------------------------------------------------------------------

class TokenMonitor:
    """线程安全的 token 用量追踪器"""

    def __init__(self, budget_limit=0):
        self._lock = threading.Lock()
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.api_calls = 0
        self.api_errors = 0
        self.budget_limit = budget_limit
        self.start_time = time.time()

    def record(self, usage):
        if usage is None:
            return
        with self._lock:
            self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            self.total_tokens += getattr(usage, "total_tokens", 0) or 0
            self.api_calls += 1

    def record_error(self):
        with self._lock:
            self.api_errors += 1

    def is_over_budget(self):
        if self.budget_limit <= 0:
            return False
        with self._lock:
            return self.total_tokens >= self.budget_limit

    def summary(self):
        elapsed = time.time() - self.start_time
        with self._lock:
            avg = self.total_tokens / max(1, self.api_calls)
            return {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "api_calls": self.api_calls,
                "api_errors": self.api_errors,
                "avg_tokens_per_call": round(avg, 1),
                "elapsed_seconds": round(elapsed, 1),
                "tokens_per_second": round(self.total_tokens / max(1, elapsed), 1),
            }


token_monitor = TokenMonitor(budget_limit=500_000)

# ---------------------------------------------------------------------------
# 运行时 P&L 追踪
# ---------------------------------------------------------------------------

class PnLTracker:
    """追踪运行时盈亏变化，供 Dashboard 绘制曲线"""

    def __init__(self, initial=INITIAL_BANKROLL):
        self.initial = initial
        self.bankroll = initial
        self.history = []  # [(index, bankroll, pnl, is_win)]
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0

    def record(self, index, pnl, action):
        if action == "skip":
            return
        self.bankroll += pnl
        self.total_pnl += pnl
        is_win = pnl > 0
        if is_win:
            self.wins += 1
        else:
            self.losses += 1
        self.history.append({
            "i": index,
            "bankroll": round(self.bankroll, 2),
            "pnl": round(pnl, 2),
            "win": is_win,
        })

    def snapshot(self):
        return {
            "bankroll": round(self.bankroll, 2),
            "total_pnl": round(self.total_pnl, 2),
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.wins / max(1, self.wins + self.losses), 4),
        }


pnl_tracker = PnLTracker()

# ---------------------------------------------------------------------------
# 实时日志（供 Dashboard 读取）
# ---------------------------------------------------------------------------

LIVE_LOG_PATH = os.path.join(os.path.dirname(__file__), "pm_live.jsonl")
_call_counter = 0
_total_markets = 0


def init_live_log(total_markets):
    """实验开始时清空日志，写入参数快照"""
    global _call_counter, _total_markets
    _call_counter = 0
    _total_markets = total_markets

    # 策略参数快照 — Dashboard 用于展示当前配置
    params_snapshot = {
        "model": MODEL_NAME,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "bet_sizing": BET_SIZING,
        "max_bet_fraction": MAX_BET_FRACTION,
        "system_prompt_preview": SYSTEM_PROMPT[:200],
        "user_prompt_preview": USER_PROMPT_TEMPLATE[:200],
    }

    with open(LIVE_LOG_PATH, "w") as f:
        f.write(json.dumps({
            "type": "start",
            "ts": time.time(),
            "total_markets": total_markets,
            "budget_limit": token_monitor.budget_limit,
            "params": params_snapshot,
        }, ensure_ascii=False) + "\n")


def write_live_log(market, llm_result, call_tokens=0, raw_response="", bet_action="skip", bet_pnl=0):
    """每次推理完成后追加一条日志"""
    global _call_counter
    _call_counter += 1
    stats = token_monitor.summary()
    pnl_snap = pnl_tracker.snapshot()

    entry = {
        "type": "inference",
        "ts": time.time(),
        "index": _call_counter,
        "total": _total_markets,
        "progress": f"{_call_counter}/{_total_markets}",
        # 市场信息
        "market_id": market.get("market_id", ""),
        "question": market.get("question", "")[:150],
        "outcomes": market.get("outcomes", []),
        "last_trade_price": market.get("last_trade_price", 0),
        "final_resolution": market.get("final_resolution", ""),
        "final_resolution_index": market.get("final_resolution_index", -1),
        "volume": market.get("volume", 0),
        # LLM 输出
        "prediction": llm_result.get("prediction", -1),
        "confidence": llm_result.get("confidence", 0),
        "thinking": llm_result.get("thinking", "")[:400],
        "reasoning": llm_result.get("reasoning", "")[:300],
        "raw_response": raw_response[:500],
        # 下注决策与结果
        "bet_action": bet_action,
        "bet_pnl": round(bet_pnl, 2),
        "is_correct": llm_result.get("prediction", -1) == market.get("final_resolution_index", -2),
        # Token
        "call_tokens": call_tokens,
        "cumulative_tokens": stats["total_tokens"],
        "prompt_tokens": stats["prompt_tokens"],
        "completion_tokens": stats["completion_tokens"],
        "api_calls": stats["api_calls"],
        "api_errors": stats["api_errors"],
        # P&L 状态
        "bankroll": pnl_snap["bankroll"],
        "running_pnl": pnl_snap["total_pnl"],
        "wins": pnl_snap["wins"],
        "losses": pnl_snap["losses"],
        "win_rate": pnl_snap["win_rate"],
    }
    with open(LIVE_LOG_PATH, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def finish_live_log(results):
    """实验结束时写入汇总"""
    stats = token_monitor.summary()
    pnl_snap = pnl_tracker.snapshot()
    entry = {
        "type": "finish",
        "ts": time.time(),
        "results": results,
        "token_summary": stats,
        "pnl_summary": pnl_snap,
        "pnl_history": pnl_tracker.history,
    }
    with open(LIVE_LOG_PATH, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 策略超参数（agent 可调整）
# ---------------------------------------------------------------------------

CONFIDENCE_THRESHOLD = 0.75
BET_SIZING = "confidence_scaled"
MAX_BET_FRACTION = 0.15

# ---------------------------------------------------------------------------
# 特征提取（agent 可优化）
# ---------------------------------------------------------------------------

def extract_features(market):
    features = {
        "question": market["question"],
        "outcomes": market["outcomes"],
        "last_trade_price": market["last_trade_price"],
        "volume_usd": f"${market['volume']:,.0f}",
        "category": market["context"]["category"],
        "event_title": market["context"]["event_title"],
    }
    signals = market.get("price_signals", {})
    if signals:
        features["price_signals"] = {k: v for k, v in signals.items() if v != 0.0}
    return features

# ---------------------------------------------------------------------------
# 提示词模板（agent 核心优化目标之一）
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a prediction market analyst. For each market, you must:
1. Briefly explain your thinking process (2-3 sentences)
2. Make a prediction

Respond ONLY with valid JSON, no other text."""

USER_PROMPT_TEMPLATE = """Analyze this prediction market:

Question: {question}
Outcomes: {outcomes}
Last trade price (probability of outcome[0]): {last_trade_price}
Volume: {volume_usd}
Category: {category}
Event: {event_title}
{price_signals_text}

Reply with JSON only:
{{"prediction": 0 or 1, "confidence": 0.0 to 1.0, "thinking": "2-3 sentences on your analysis logic", "reasoning": "one-line conclusion"}}"""

# ---------------------------------------------------------------------------
# LLM 推理
# ---------------------------------------------------------------------------

def call_llm(market):
    """调用 Kimi K2.5 进行预测，记录原始输出"""
    if token_monitor.is_over_budget():
        last_price = market["last_trade_price"]
        pred = 0 if last_price >= 0.5 else 1
        conf = last_price if last_price >= 0.5 else 1 - last_price
        return {"prediction": pred, "confidence": conf, "reasoning": "budget_exceeded"}, ""

    features = extract_features(market)
    price_signals_text = ""
    if features.get("price_signals"):
        signals_str = ", ".join(f"{k}: {v:+.3f}" for k, v in features["price_signals"].items())
        price_signals_text = f"Price changes: {signals_str}"

    user_prompt = USER_PROMPT_TEMPLATE.format(
        question=features["question"],
        outcomes=features["outcomes"],
        last_trade_price=features["last_trade_price"],
        volume_usd=features["volume_usd"],
        category=features["category"],
        event_title=features["event_title"],
        price_signals_text=price_signals_text,
    )

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )

        usage = response.usage
        call_tokens = (getattr(usage, "prompt_tokens", 0) or 0) + (getattr(usage, "completion_tokens", 0) or 0)
        token_monitor.record(usage)

        raw_content = response.choices[0].message.content or ""
        content = raw_content.strip()

        # 空响应回退
        if not content:
            last_price = market["last_trade_price"]
            pred = 0 if last_price >= 0.5 else 1
            conf = last_price if last_price >= 0.5 else 1 - last_price
            return {"prediction": pred, "confidence": conf, "reasoning": "empty_response"}, raw_content

        # 解析 JSON
        if content.startswith("```"):
            parts = content.split("```")
            if len(parts) >= 2:
                inner = parts[1]
                if inner.startswith("json"):
                    inner = inner[4:]
                content = inner.strip()

        result = json.loads(content)
        llm_result = {
            "prediction": int(result.get("prediction", 0)),
            "confidence": float(result.get("confidence", 0.5)),
            "thinking": result.get("thinking", ""),
            "reasoning": result.get("reasoning", ""),
        }
        return llm_result, raw_content

    except Exception as e:
        token_monitor.record_error()
        last_price = market["last_trade_price"]
        pred = 0 if last_price >= 0.5 else 1
        conf = last_price if last_price >= 0.5 else 1 - last_price
        return {"prediction": pred, "confidence": conf, "reasoning": f"error: {str(e)[:100]}"}, ""

# ---------------------------------------------------------------------------
# 策略决策
# ---------------------------------------------------------------------------

def strategy(market):
    """完整策略：LLM 推理 + 下注逻辑 + P&L 追踪"""
    llm_result, raw_response = call_llm(market)

    prediction = llm_result["prediction"]
    confidence = llm_result["confidence"]

    # 置信度过滤
    if confidence < CONFIDENCE_THRESHOLD:
        bet = {"action": "skip", "outcome_index": 0, "size": 0, "confidence": confidence}
        write_live_log(market, llm_result, 0, raw_response, "skip", 0)
        return bet

    last_price = market["last_trade_price"]
    market_prob = last_price if prediction == 0 else 1 - last_price

    # 市场已充分反映，跳过
    if market_prob > 0.9 and confidence < 0.95:
        bet = {"action": "skip", "outcome_index": 0, "size": 0, "confidence": confidence}
        write_live_log(market, llm_result, 0, raw_response, "skip", 0)
        return bet

    # 下注大小
    if BET_SIZING == "fixed":
        size = BET_UNIT
    elif BET_SIZING == "confidence_scaled":
        size = BET_UNIT * confidence
    elif BET_SIZING == "kelly":
        edge = confidence - market_prob
        if edge <= 0:
            bet = {"action": "skip", "outcome_index": 0, "size": 0, "confidence": confidence}
            write_live_log(market, llm_result, 0, raw_response, "skip", 0)
            return bet
        odds = (1 - market_prob) / market_prob if market_prob > 0 else 1
        kelly_fraction = edge / (1 / odds) if odds > 0 else 0
        size = BET_UNIT * min(kelly_fraction * 2, 3)
    else:
        size = BET_UNIT

    bet = {"action": "buy", "outcome_index": prediction, "size": max(1, size), "confidence": confidence}

    # 计算 P&L（预计算用于日志）
    from pm_prepare import calculate_pnl
    pnl = calculate_pnl(bet, market)
    pnl_tracker.record(_call_counter + 1, pnl, "buy")

    call_tokens = token_monitor.summary()["total_tokens"]
    write_live_log(market, llm_result, 0, raw_response, "buy", pnl)

    return bet

# ---------------------------------------------------------------------------
# 任务定时控制
# ---------------------------------------------------------------------------

RUN_TIMEOUT = 600

def run_with_timeout(func, args=(), kwargs=None, timeout=RUN_TIMEOUT):
    kwargs = kwargs or {}
    result = [None]
    error = [None]

    def target():
        try:
            result[0] = func(*args, **kwargs)
        except Exception as e:
            error[0] = e

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        print(f"\n[TIMEOUT] 实验超时（{timeout}s），强制终止")
        return None
    if error[0]:
        raise error[0]
    return result[0]


if __name__ == "__main__":
    t_start = time.time()

    print("预测市场策略评估")
    print("=" * 50)
    print(f"模型: {MODEL_NAME}")
    print(f"置信度阈值: {CONFIDENCE_THRESHOLD}")
    print(f"下注策略: {BET_SIZING}")
    print(f"Token 预算上限: {token_monitor.budget_limit:,}")
    print(f"运行超时: {RUN_TIMEOUT}s")
    print()

    markets = sample_eval_markets()
    print(f"评估市场数: {len(markets)}")
    print()

    init_live_log(len(markets))

    results = run_with_timeout(
        evaluate_strategy,
        args=(strategy, markets),
        kwargs={"verbose": True},
        timeout=RUN_TIMEOUT,
    )

    t_end = time.time()

    if results is None:
        print("\n---")
        print("fitness:           0.000000")
        print("status:            timeout")
        print(f"total_seconds:     {t_end - t_start:.1f}")
    else:
        print("\n---")
        for k in ["fitness", "total_pnl", "accuracy", "max_drawdown", "num_trades",
                   "num_skipped", "win_rate", "avg_pnl_per_trade", "sharpe_ratio", "final_bankroll"]:
            v = results[k]
            fmt = ".6f" if k == "fitness" else ".4f" if isinstance(v, float) else "d"
            print(f"{k+':':<20}{v:{fmt}}")
        print(f"{'total_seconds:':<20}{t_end - t_start:.1f}")

    finish_live_log(results if results else {"fitness": 0, "status": "timeout"})

    token_stats = token_monitor.summary()
    print("\n=== Token 用量报告 ===")
    for k, v in token_stats.items():
        print(f"{k+':':<22}{v:,}" if isinstance(v, int) else f"{k+':':<22}{v}")
