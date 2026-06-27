import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# STOCK DAY TRADING RISK RULES
# ==========================================
MAX_POSITION_PCT = 0.03       # 3% of portfolio per trade
DAILY_LOSS_LIMIT_PCT = 0.05   # 5% daily loss shuts bot down
MAX_TRADES_PER_DAY = 5        # Maximum 5 trades per day
MIN_VOLUME_RATIO = 3.0        # Minimum 3x average volume
MIN_CATALYST_STRENGTH = 'medium'  # Reject weak catalyst trades
MIN_REWARD_RISK = 3.0         # Minimum 3:1 reward/risk ratio

PORTFOLIO_BALANCE = 1000.0
trades_today = 0
daily_pnl = 0.0

CATALYST_STRENGTH_RANK = {
    'strong': 3,
    'medium': 2,
    'weak': 1,
    'unknown': 0,
    'error': 0
}

def calculate_structural_stop(
    price: float,
    vwap: float,
    orb_low: float = None
) -> float:
    """
    Calculate stop loss below key structural level
    Uses VWAP or ORB low — whichever is closer to price
    """
    vwap_stop = vwap * 0.995  # 0.5% below VWAP

    if orb_low:
        orb_stop = orb_low * 0.995
        # Use the higher stop (closer to price = tighter risk)
        structural_stop = max(vwap_stop, orb_stop)
    else:
        structural_stop = vwap_stop

    return round(structural_stop, 4)

def calculate_position_size(
    price: float,
    stop_loss: float,
    balance: float = PORTFOLIO_BALANCE
) -> dict:
    """
    Calculate position size based on structural stop distance
    Risk never exceeds MAX_POSITION_PCT of portfolio
    """
    stop_distance = abs(price - stop_loss)
    stop_distance_pct = stop_distance / price

    if stop_distance_pct == 0:
        stop_distance_pct = 0.03

    # Max dollar risk per trade
    max_risk_usd = balance * MAX_POSITION_PCT * 0.015

    # Position size based on stop distance
    position_usd = min(
        max_risk_usd / stop_distance_pct,
        balance * MAX_POSITION_PCT
    )

    quantity = position_usd / price

    # Targets based on reward/risk
    target_1 = price + (stop_distance * MIN_REWARD_RISK)
    target_2 = price + (stop_distance * 5.0)

    return {
        'position_usd': round(position_usd, 2),
        'quantity': round(quantity, 4),
        'entry_price': round(price, 4),
        'stop_loss_price': round(stop_loss, 4),
        'take_profit_price': round(target_1, 4),
        'target_2_price': round(target_2, 4),
        'stop_distance_pct': round(stop_distance_pct * 100, 2),
        'risk_usd': round(position_usd * stop_distance_pct, 2),
        'reward_usd_t1': round(position_usd * stop_distance_pct * MIN_REWARD_RISK, 2),
        'reward_usd_t2': round(position_usd * stop_distance_pct * 5.0, 2),
        'risk_reward': f'{MIN_REWARD_RISK}:1 min / 5:1 stretch'
    }

def evaluate_stock_risk(state_matrix: dict) -> dict:
    """
    Stock-specific risk evaluation
    Has ABSOLUTE VETO POWER over all other agents
    """
    global trades_today, daily_pnl

    direction = state_matrix['quant_trigger']['direction']
    price = state_matrix['quant_trigger']['price_at_trigger']
    sentiment = state_matrix.get('sentiment', {})
    catalyst = state_matrix.get('catalyst', {})
    momentum = state_matrix.get('momentum_metrics', {})
    market_metrics = state_matrix.get('market_metrics', {})

    trap_warning = sentiment.get('trap_warning', False)
    sentiment_score = sentiment.get('sentiment_score', 5)
    catalyst_strength = catalyst.get('strength', 'unknown')
    volume_ratio = momentum.get('volume_ratio', 0)
    above_vwap = momentum.get('above_vwap', False)
    orb_confirmed = momentum.get('orb_confirmed', False)
    vwap = momentum.get('vwap', price * 0.98)

    # Rule 1: Daily trade limit
    if trades_today >= MAX_TRADES_PER_DAY:
        return {
            'approved': False,
            'reason': f'Daily trade limit reached ({MAX_TRADES_PER_DAY} trades)',
            'position': None
        }

    # Rule 2: Daily loss limit
    if daily_pnl <= -(PORTFOLIO_BALANCE * DAILY_LOSS_LIMIT_PCT):
        return {
            'approved': False,
            'reason': 'Daily loss limit reached — bot shutting down',
            'position': None
        }

    # Rule 3: Trap warning
    if trap_warning:
        return {
            'approved': False,
            'reason': f'Trap warning detected — {sentiment.get("trap_reason", "potential trap")}',
            'position': None
        }

    # Rule 4: Catalyst strength check
    catalyst_rank = CATALYST_STRENGTH_RANK.get(catalyst_strength, 0)
    min_rank = CATALYST_STRENGTH_RANK.get(MIN_CATALYST_STRENGTH, 2)
    if catalyst_rank < min_rank:
        return {
            'approved': False,
            'reason': f'Catalyst too weak ({catalyst_strength}) — need medium or strong',
            'position': None
        }

    # Rule 5: Volume confirmation
    if volume_ratio < MIN_VOLUME_RATIO:
        return {
            'approved': False,
            'reason': f'Volume insufficient ({volume_ratio:.1f}x) — need {MIN_VOLUME_RATIO}x+',
            'position': None
        }

    # Rule 6: Must be above VWAP for buys
    if direction == 'BUY_SIGNAL' and not above_vwap:
        return {
            'approved': False,
            'reason': 'Price below VWAP — no long trades below VWAP',
            'position': None
        }

    # Rule 7: ORB must be confirmed
    if not orb_confirmed:
        return {
            'approved': False,
            'reason': 'Opening range breakout not confirmed',
            'position': None
        }

    # Rule 8: Sentiment boundaries
    if sentiment_score > 9:
        return {
            'approved': False,
            'reason': f'Extreme greed ({sentiment_score}/10) — likely exhaustion',
            'position': None
        }

    # All rules passed — calculate structural position
    stop_loss = calculate_structural_stop(price, vwap)
    position = calculate_position_size(price, stop_loss)

    return {
        'approved': True,
        'reason': 'All stock risk rules passed',
        'position': position,
        'structural_stop': stop_loss,
        'vwap_reference': vwap,
        'catalyst_strength': catalyst_strength,
        'volume_ratio': volume_ratio
    }

if __name__ == "__main__":
    import json

    test_state = {
        'quant_trigger': {
            'direction': 'BUY_SIGNAL',
            'price_at_trigger': 158.32
        },
        'market_metrics': {
            'rsi_14': 62.5,
            'volume_spike': True
        },
        'momentum_metrics': {
            'volume_ratio': 5.4,
            'above_vwap': True,
            'orb_confirmed': True,
            'vwap': 156.62
        },
        'catalyst': {
            'type': 'Strong Catalyst',
            'strength': 'strong',
            'headline': 'CRM beats earnings expectations'
        },
        'sentiment': {
            'sentiment_score': 7,
            'trap_warning': False,
            'trap_reason': None,
            'confidence': 'high'
        }
    }

    print("[*] Testing stock risk agent...")
    result = evaluate_stock_risk(test_state)
    print(json.dumps(result, indent=2))