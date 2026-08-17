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

# ==========================================
# ASSET TIER CLASSIFICATION — deterministic from CoinGecko fundamentals.
# Tier is a pure function of the data; the LLM never influences it.
# ==========================================
BLUE_CHIP_MIN_CAP = 10_000_000_000    # >= $10B: deep books, tight stops are meaningful
ESTABLISHED_MIN_CAP = 1_000_000_000   # $1B-$10B: wider stops, smaller size
LIQUIDITY_FLOOR_VOLUME = 10_000_000   # 24h volume under $10M demotes one tier (the CC/USD case)

# A >=2% "signal" on a pegged asset is a depeg event, not a setup — hard veto.
STABLE_PEGGED_BASES = {
    'USDT', 'USDC', 'USDG', 'DAI', 'TUSD', 'USDP', 'PYUSD', 'EURT',
    'PAXG', 'XAUT',  # gold-pegged
}

# Constant dollar risk: position_pct * stop_loss_pct == 0.0003 of portfolio in
# every tier ($0.30 on $1000). Tiers change the geometry, not the risk.
TIER_RISK_PROFILES = {
    'blue_chip':   {'position_pct': MAX_POSITION_PCT, 'stop_loss_pct': STOP_LOSS_PCT, 'reward_risk': 2.0},
    'established': {'position_pct': 0.015,  'stop_loss_pct': 0.02, 'reward_risk': 2.5},
    'speculative': {'position_pct': 0.0075, 'stop_loss_pct': 0.04, 'reward_risk': 3.0},
}

_TIER_DEMOTION = {'blue_chip': 'established', 'established': 'speculative'}


def _base_symbol(ticker: str) -> str:
    """'SOLUSD' / 'SOL/USD' / 'BTCUSDT' -> base symbol ('SOL', 'BTC')"""
    base = ticker.replace('/', '').upper()
    for quote in ('USDT', 'USD'):
        if base.endswith(quote) and len(base) > len(quote):
            return base[:-len(quote)]
    return base


def classify_asset(ticker: str, fundamentals: dict) -> dict:
    """Returns {'tier', 'reason'}. Missing data always lands in the most
    conservative bucket — never assume average when data is absent."""
    base = _base_symbol(ticker)
    if base in STABLE_PEGGED_BASES:
        return {'tier': 'stable_pegged', 'reason': f'{base} is a pegged asset'}

    if not fundamentals:
        return {'tier': 'unresolved',
                'reason': 'no market data match — possibly a non-crypto pair (e.g. fiat forex)'}

    cap = fundamentals.get('market_cap')
    volume = fundamentals.get('total_volume')

    if cap is None:
        tier, reason = 'speculative', 'market cap unavailable — conservative default'
    elif cap >= BLUE_CHIP_MIN_CAP:
        tier, reason = 'blue_chip', f'market cap ${cap:,.0f}'
    elif cap >= ESTABLISHED_MIN_CAP:
        tier, reason = 'established', f'market cap ${cap:,.0f}'
    else:
        tier, reason = 'speculative', f'market cap ${cap:,.0f}'

    if volume is not None and volume < LIQUIDITY_FLOOR_VOLUME and tier in _TIER_DEMOTION:
        reason += (f'; demoted from {tier} — 24h volume ${volume:,.0f} '
                   f'below ${LIQUIDITY_FLOOR_VOLUME:,.0f} liquidity floor')
        tier = _TIER_DEMOTION[tier]

    return {'tier': tier, 'reason': reason}


def calculate_position_size(price: float, balance: float = PORTFOLIO_BALANCE,
                            position_pct: float = MAX_POSITION_PCT,
                            stop_loss_pct: float = STOP_LOSS_PCT,
                            reward_risk: float = 2.0) -> dict:
    position_usd = balance * position_pct
    quantity = position_usd / price
    stop_loss_price = price * (1 - stop_loss_pct)
    take_profit_price = price * (1 + (stop_loss_pct * reward_risk))

    return {
        "position_usd": round(position_usd, 2),
        "quantity": round(quantity, 6),
        "entry_price": price,
        "stop_loss_price": round(stop_loss_price, 4),
        "take_profit_price": round(take_profit_price, 4),
        "risk_usd": round(position_usd * stop_loss_pct, 2),
        "reward_usd": round(position_usd * stop_loss_pct * reward_risk, 2)
    }

def evaluate_risk(state_matrix: dict) -> dict:
    """
    Hard risk evaluation — returns approved/rejected with reason
    This agent has ABSOLUTE VETO POWER over all other agents
    """
    direction = state_matrix['quant_trigger']['direction']
    price = state_matrix['quant_trigger']['price_at_trigger']
    sentiment = state_matrix.get('sentiment', {})
    fundamentals = state_matrix.get('fundamentals', {})

    classification = classify_asset(state_matrix['ticker'], fundamentals)
    tier = classification['tier']
    tier_reason = classification['reason']

    sentiment_score = sentiment.get('sentiment_score', 5)
    trap_warning = sentiment.get('trap_warning', False)
    confidence = sentiment.get('confidence', 'low')
    volume_spike = state_matrix['market_metrics'].get('volume_spike', False)
    signal_source = state_matrix.get('signal_source', 'scanner')
    rsi = state_matrix['market_metrics'].get('rsi_14')

    # Rule 1: pegged assets are never traded — the only way one clears the
    # scanner's 2% move filter is a depeg in progress
    if tier == 'stable_pegged':
        return {
            "approved": False,
            "reason": f"Stablecoin/pegged asset veto — {tier_reason}",
            "position": None,
            "asset_tier": tier, "tier_reason": tier_reason
        }

    # Rule 2: refuse anything unclassifiable (e.g. EUR/USD, GBP/USD — Kraken
    # lists fiat forex pairs that can clear the volume cutoff)
    if tier == 'unresolved':
        return {
            "approved": False,
            "reason": f"Unclassified asset veto — {tier_reason}",
            "position": None,
            "asset_tier": tier, "tier_reason": tier_reason
        }

    # Rule 3: Trap warning = immediate reject
    if trap_warning:
        return {
            "approved": False,
            "reason": f"TRAP WARNING: {sentiment.get('trap_reason', 'Potential trap detected')}",
            "position": None,
            "asset_tier": tier, "tier_reason": tier_reason
        }

    # Rule 4: Sentiment confidence must be medium or high
    if confidence == "low":
        return {
            "approved": False,
            "reason": "Sentiment confidence too low — insufficient data",
            "position": None,
            "asset_tier": tier, "tier_reason": tier_reason
        }

    # Rule 5: Sentiment score boundaries
    if sentiment_score < MIN_SENTIMENT_SCORE:
        return {
            "approved": False,
            "reason": f"Extreme fear detected (score {sentiment_score}/10) — wait for stabilization",
            "position": None,
            "asset_tier": tier, "tier_reason": tier_reason
        }

    if sentiment_score > MAX_SENTIMENT_SCORE:
        return {
            "approved": False,
            "reason": f"Extreme greed detected (score {sentiment_score}/10) — potential exhaustion",
            "position": None,
            "asset_tier": tier, "tier_reason": tier_reason
        }

    # Rule 6: RSI extreme check — skipped for buzz-sourced triggers, which
    # fire on social mention velocity rather than a technical RSI setup and
    # have no RSI reading to check in the first place.
    if signal_source != 'buzz':
        if direction == "BUY_SIGNAL" and rsi > 50:
            return {
                "approved": False,
                "reason": f"RSI at {rsi:.1f} — not oversold enough for high-probability buy",
                "position": None,
                "asset_tier": tier, "tier_reason": tier_reason
            }

        if direction == "SELL_SIGNAL" and rsi < 50:
            return {
                "approved": False,
                "reason": f"RSI at {rsi:.1f} — not overbought enough for high-probability sell",
                "position": None,
                "asset_tier": tier, "tier_reason": tier_reason
            }

    # All rules passed — size the position by asset tier
    profile = TIER_RISK_PROFILES[tier]
    position = calculate_position_size(
        price,
        position_pct=profile['position_pct'],
        stop_loss_pct=profile['stop_loss_pct'],
        reward_risk=profile['reward_risk'],
    )

    return {
        "approved": True,
        "reason": f"All risk rules passed (tier: {tier})",
        "position": position,
        "risk_reward_ratio": f"{profile['reward_risk']}:1",
        "max_loss_usd": position['risk_usd'],
        "target_gain_usd": position['reward_usd'],
        "asset_tier": tier, "tier_reason": tier_reason
    }

if __name__ == "__main__":
    import json

    def make_state(ticker, fundamentals):
        return {
            "ticker": ticker,
            "quant_trigger": {
                "direction": "BUY_SIGNAL",
                "price_at_trigger": 69.58
            },
            "market_metrics": {
                "rsi_14": 28.5,
                "volume_spike": True
            },
            "sentiment": {
                "sentiment_score": 5,
                "trap_warning": False,
                "trap_reason": None,
                "confidence": "high"
            },
            "fundamentals": fundamentals
        }

    # Fundamentals below use real values pulled from the live Kraken/CoinGecko
    # run on 2026-07-27, same price ($69.58) across cases so sizing compares
    # cleanly. Expected (on $1000 balance): blue_chip $20/1.5%/2:1,
    # established $15/2%/2.5:1, speculative $7.50/4%/3:1 — $0.30 risk in all.
    cases = [
        ("SOLUSD -> blue_chip",
         make_state("SOLUSD", {"market_cap": 43_833_360_655,
                               "total_volume": 1_669_714_910, "market_cap_rank": 7})),
        ("ADAUSD -> established",
         make_state("ADAUSD", {"market_cap": 5_894_928_519,
                               "total_volume": 238_199_082, "market_cap_rank": 20})),
        ("PUMPUSD -> speculative (sub-$1B)",
         make_state("PUMPUSD", {"market_cap": 412_000_000,
                                "total_volume": 6_200_000, "market_cap_rank": 180})),
        ("CCUSD -> liquidity demotion ($4.7B cap but $8.1M volume)",
         make_state("CCUSD", {"market_cap": 4_715_483_843,
                              "total_volume": 8_121_837, "market_cap_rank": 21})),
        ("USDTUSD -> stablecoin veto",
         make_state("USDTUSD", {"market_cap": 183_951_100_257,
                                "total_volume": 44_982_549_150, "market_cap_rank": 3})),
        ("EURUSD -> unresolved veto (fiat pair, no CoinGecko match)",
         make_state("EURUSD", {})),
    ]

    # Buzz-sourced trigger — no rsi_14 at all (Rule 6 must be skipped, not
    # KeyError), same PUMPUSD fundamentals as the speculative case above so
    # only the signal_source difference is under test.
    buzz_state = {
        "ticker": "PUMPUSD",
        "quant_trigger": {"direction": "BUY_SIGNAL", "price_at_trigger": 69.58},
        "market_metrics": {},
        "sentiment": {"sentiment_score": 5, "trap_warning": False,
                      "trap_reason": None, "confidence": "high"},
        "fundamentals": {"market_cap": 412_000_000, "total_volume": 6_200_000,
                         "market_cap_rank": 180},
        "signal_source": "buzz",
    }
    cases.append(("PUMPUSD buzz-sourced -> Rule 6 skipped, no rsi_14 present", buzz_state))

    print("[*] Testing risk agent with asset-tier classification...")
    for label, state in cases:
        result = evaluate_risk(state)
        print(f"\n=== {label} ===")
        print(json.dumps(result, indent=2))
