import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# HARDCODED RISK RULES — NEVER BYPASSED
# ==========================================
MAX_POSITION_PCT = 0.02      # 2% of portfolio per trade
STOP_LOSS_PCT = 0.015        # 1.5% stop loss
DAILY_LOSS_LIMIT_PCT = 0.05  # 5% daily loss limit shuts bot down
MIN_SENTIMENT_SCORE = 3      # Below this = too much fear
MAX_SENTIMENT_SCORE = 8      # Above this = too much greed
MIN_CONFIDENCE = "medium"    # Minimum sentiment confidence required

PORTFOLIO_BALANCE = 1000.0   # Starting paper trading balance in USD

def calculate_position_size(price: float, balance: float = PORTFOLIO_BALANCE) -> dict:
    position_usd = balance * MAX_POSITION_PCT
    quantity = position_usd / price
    stop_loss_price = price * (1 - STOP_LOSS_PCT)
    take_profit_price = price * (1 + (STOP_LOSS_PCT * 2))  # 2:1 reward/risk

    return {
        "position_usd": round(position_usd, 2),
        "quantity": round(quantity, 6),
        "entry_price": price,
        "stop_loss_price": round(stop_loss_price, 4),
        "take_profit_price": round(take_profit_price, 4),
        "risk_usd": round(position_usd * STOP_LOSS_PCT, 2),
        "reward_usd": round(position_usd * STOP_LOSS_PCT * 2, 2)
    }

def evaluate_risk(state_matrix: dict) -> dict:
    """
    Hard risk evaluation — returns approved/rejected with reason
    This agent has ABSOLUTE VETO POWER over all other agents
    """
    direction = state_matrix['quant_trigger']['direction']
    price = state_matrix['quant_trigger']['price_at_trigger']
    sentiment = state_matrix.get('sentiment', {})
    
    sentiment_score = sentiment.get('sentiment_score', 5)
    trap_warning = sentiment.get('trap_warning', False)
    confidence = sentiment.get('confidence', 'low')
    volume_spike = state_matrix['market_metrics'].get('volume_spike', False)
    rsi = state_matrix['market_metrics']['rsi_14']

    # Rule 1: Trap warning = immediate reject
    if trap_warning:
        return {
            "approved": False,
            "reason": f"TRAP WARNING: {sentiment.get('trap_reason', 'Potential trap detected')}",
            "position": None
        }

    # Rule 2: Sentiment confidence must be medium or high
    if confidence == "low":
        return {
            "approved": False,
            "reason": "Sentiment confidence too low — insufficient data",
            "position": None
        }

    # Rule 3: Sentiment score boundaries
    if sentiment_score < MIN_SENTIMENT_SCORE:
        return {
            "approved": False,
            "reason": f"Extreme fear detected (score {sentiment_score}/10) — wait for stabilization",
            "position": None
        }

    if sentiment_score > MAX_SENTIMENT_SCORE:
        return {
            "approved": False,
            "reason": f"Extreme greed detected (score {sentiment_score}/10) — potential exhaustion",
            "position": None
        }

    # Rule 4: RSI extreme check
    if direction == "BUY_SIGNAL" and rsi > 50:
        return {
            "approved": False,
            "reason": f"RSI at {rsi:.1f} — not oversold enough for high-probability buy",
            "position": None
        }

    if direction == "SELL_SIGNAL" and rsi < 50:
        return {
            "approved": False,
            "reason": f"RSI at {rsi:.1f} — not overbought enough for high-probability sell",
            "position": None
        }

    # All rules passed — calculate position
    position = calculate_position_size(price)

    return {
        "approved": True,
        "reason": "All risk rules passed",
        "position": position,
        "risk_reward_ratio": "2:1",
        "max_loss_usd": position['risk_usd'],
        "target_gain_usd": position['reward_usd']
    }

if __name__ == "__main__":
    import json

    # Test with a sample state matrix
    test_state = {
        "quant_trigger": {
            "direction": "BUY_SIGNAL",
            "price_at_trigger": 69.58
        },
        "market_metrics": {
            "rsi_14": 28.5,
            "volume_spike": True
        },
        "sentiment": {
            "sentiment_score": 4,
            "trap_warning": False,
            "trap_reason": None,
            "confidence": "high"
        }
    }

    print("[*] Testing risk agent...")
    result = evaluate_risk(test_state)
    print(json.dumps(result, indent=2))