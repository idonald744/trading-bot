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
MIN_CONFIDENCE = 'medium'     # Minimum sentiment confidence required (parity with core/agents/risk_agent.py)

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

# ==========================================
# ASSET TIER CLASSIFICATION — deterministic from yfinance fundamentals.
# Escalation model: current rules are the large_stable baseline; elevated-risk
# signals (high SI, low float, small cap, missing data) size DOWN from it.
# ==========================================
SMALL_CAP_MAX = 2_000_000_000        # under $2B — tightest sizing
LOW_FLOAT_MAX = 50_000_000           # under 50M shares — classic low-float line
HIGH_SI_MIN_PCT = 10.0               # short interest >= 10% of float — squeeze mechanics live
HIGH_BETA_CAP_MAX = 10_000_000_000   # under $10B — at least high_beta regardless of SI
SMALL_CAP_MIN_STOP_PCT = 0.02        # stop floor: structural stops run deceptively tight on gappy small caps

TIER_POSITION_PCT = {
    'large_stable': MAX_POSITION_PCT,  # 3% — unchanged baseline
    'high_beta': 0.02,
    'small_cap_spec': 0.01,
}


def classify_stock(fundamentals: dict) -> dict:
    """Returns {'tier', 'reason'}. Missing fundamentals (yfinance .info is a
    scrape and will sometimes fail) land in the most conservative tier."""
    if not fundamentals or fundamentals.get('market_cap') is None:
        return {'tier': 'small_cap_spec',
                'reason': 'fundamentals unavailable — conservative default'}

    cap = fundamentals['market_cap']
    float_shares = fundamentals.get('float_shares')
    si = fundamentals.get('short_interest_pct')

    if cap < SMALL_CAP_MAX:
        return {'tier': 'small_cap_spec', 'reason': f'market cap ${cap:,.0f} under ${SMALL_CAP_MAX:,.0f}'}
    if float_shares is not None and float_shares < LOW_FLOAT_MAX:
        return {'tier': 'small_cap_spec', 'reason': f'float {float_shares:,.0f} shares under {LOW_FLOAT_MAX:,.0f}'}
    if si is not None and si >= HIGH_SI_MIN_PCT:
        return {'tier': 'high_beta', 'reason': f'short interest {si}% of float (>= {HIGH_SI_MIN_PCT}%)'}
    if cap < HIGH_BETA_CAP_MAX:
        return {'tier': 'high_beta', 'reason': f'market cap ${cap:,.0f} under ${HIGH_BETA_CAP_MAX:,.0f}'}
    return {'tier': 'large_stable',
            'reason': f'market cap ${cap:,.0f}, short interest {si if si is not None else "n/a"}%'}


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
    balance: float = PORTFOLIO_BALANCE,
    position_pct: float = MAX_POSITION_PCT
) -> dict:
    """
    Calculate position size based on structural stop distance
    Risk never exceeds position_pct of portfolio
    """
    stop_distance = abs(price - stop_loss)
    stop_distance_pct = stop_distance / price

    if stop_distance_pct == 0:
        stop_distance_pct = 0.03

    # Max dollar risk per trade
    max_risk_usd = balance * position_pct * 0.015

    # Position size based on stop distance
    position_usd = min(
        max_risk_usd / stop_distance_pct,
        balance * position_pct
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
    fundamentals = state_matrix.get('fundamentals', {})

    classification = classify_stock(fundamentals)
    tier = classification['tier']
    tier_reason = classification['reason']

    trap_warning = sentiment.get('trap_warning', False)
    sentiment_score = sentiment.get('sentiment_score', 5)
    confidence = sentiment.get('confidence', 'low')
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
            'position': None,
            'asset_tier': tier, 'tier_reason': tier_reason
        }

    # Rule 2: Daily loss limit
    if daily_pnl <= -(PORTFOLIO_BALANCE * DAILY_LOSS_LIMIT_PCT):
        return {
            'approved': False,
            'reason': 'Daily loss limit reached — bot shutting down',
            'position': None,
            'asset_tier': tier, 'tier_reason': tier_reason
        }

    # Rule 3: Trap warning
    if trap_warning:
        return {
            'approved': False,
            'reason': f'Trap warning detected — {sentiment.get("trap_reason", "potential trap")}',
            'position': None,
            'asset_tier': tier, 'tier_reason': tier_reason
        }

    # Rule 4: Sentiment confidence must be medium or high
    if confidence == 'low':
        return {
            'approved': False,
            'reason': 'Sentiment confidence too low — insufficient data',
            'position': None,
            'asset_tier': tier, 'tier_reason': tier_reason
        }

    # Rule 5: Catalyst strength check — small_cap_spec requires 'strong';
    # a medium catalyst can't be trusted to move a thin name safely
    min_strength = 'strong' if tier == 'small_cap_spec' else MIN_CATALYST_STRENGTH
    catalyst_rank = CATALYST_STRENGTH_RANK.get(catalyst_strength, 0)
    min_rank = CATALYST_STRENGTH_RANK.get(min_strength, 2)
    if catalyst_rank < min_rank:
        return {
            'approved': False,
            'reason': f'Catalyst too weak ({catalyst_strength}) — need {min_strength} for {tier}',
            'position': None,
            'asset_tier': tier, 'tier_reason': tier_reason
        }

    # Rule 6: Volume confirmation
    if volume_ratio < MIN_VOLUME_RATIO:
        return {
            'approved': False,
            'reason': f'Volume insufficient ({volume_ratio:.1f}x) — need {MIN_VOLUME_RATIO}x+',
            'position': None,
            'asset_tier': tier, 'tier_reason': tier_reason
        }

    # Rule 7: Must be above VWAP for buys
    if direction == 'BUY_SIGNAL' and not above_vwap:
        return {
            'approved': False,
            'reason': 'Price below VWAP — no long trades below VWAP',
            'position': None,
            'asset_tier': tier, 'tier_reason': tier_reason
        }

    # Rule 8: ORB must be confirmed
    if not orb_confirmed:
        return {
            'approved': False,
            'reason': 'Opening range breakout not confirmed',
            'position': None,
            'asset_tier': tier, 'tier_reason': tier_reason
        }

    # Rule 9: Sentiment boundaries
    if sentiment_score > 9:
        return {
            'approved': False,
            'reason': f'Extreme greed ({sentiment_score}/10) — likely exhaustion',
            'position': None,
            'asset_tier': tier, 'tier_reason': tier_reason
        }

    # All rules passed — structural stop, with a floor for gappy small caps,
    # sized by asset tier
    stop_loss = calculate_structural_stop(price, vwap)
    if tier == 'small_cap_spec':
        floor_stop = price * (1 - SMALL_CAP_MIN_STOP_PCT)
        stop_loss = min(stop_loss, round(floor_stop, 4))
    position = calculate_position_size(price, stop_loss,
                                       position_pct=TIER_POSITION_PCT[tier])

    return {
        'approved': True,
        'reason': f'All stock risk rules passed (tier: {tier})',
        'position': position,
        'structural_stop': stop_loss,
        'vwap_reference': vwap,
        'catalyst_strength': catalyst_strength,
        'volume_ratio': volume_ratio,
        'asset_tier': tier, 'tier_reason': tier_reason
    }

if __name__ == "__main__":
    import json

    def make_state(fundamentals):
        return {
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
                'headline': 'Beats earnings expectations'
            },
            'sentiment': {
                'sentiment_score': 7,
                'trap_warning': False,
                'trap_reason': None,
                'confidence': 'high'
            },
            'fundamentals': fundamentals
        }

    # Fundamentals below are the real yfinance values fetched 2026-07-27.
    # Same price/vwap across cases so tier sizing compares cleanly.
    cases = [
        ("AAPL-like -> large_stable ($4.95T cap, 1.0% SI)",
         make_state({'market_cap': 4_948_317_175_808, 'float_shares': 14_662_387_495,
                     'shares_outstanding': 14_687_356_000, 'short_interest_pct': 1.0})),
        ("GME-like -> high_beta via short interest (13.54%)",
         make_state({'market_cap': 9_655_836_672, 'float_shares': 408_807_091,
                     'shares_outstanding': 448_691_257, 'short_interest_pct': 13.54})),
        ("missing fundamentals -> small_cap_spec fallback (stop floor applies)",
         make_state({})),
    ]

    print("[*] Testing stock risk agent with asset-tier classification...")
    for label, state in cases:
        result = evaluate_stock_risk(state)
        print(f"\n=== {label} ===")
        print(json.dumps(result, indent=2))
