def get_stock_prompt(matrix: dict, sentiment: dict, rag: dict, risk: dict) -> str:
    """High risk/high reward momentum prompt for stock day trading"""
    
    catalyst = matrix.get('catalyst', {})
    momentum = matrix.get('momentum_metrics', {})
    fundamentals = matrix.get('fundamentals', {})

    divergence = sentiment.get('divergence', {})
    if divergence.get('detected'):
        divergence_line = f"CONFLICT — {divergence.get('reason', 'sources disagree')}"
    elif sentiment.get('sources_analyzed', 1) > 1:
        divergence_line = "None — sources aligned"
    else:
        divergence_line = "N/A — single source"

    return f"""You are a senior risk officer for a momentum day trading desk.
Review this stock setup and make a final decision.

This is a HIGH RISK / HIGH REWARD momentum trade. You are evaluating whether
the catalyst is strong enough to move this float and whether the technical
setup confirms institutional participation.

CATALYST ANALYSIS (most important factor):
- Ticker: {matrix['ticker']}
- Catalyst Type: {catalyst.get('type', 'Unknown')}
- Catalyst Strength: {catalyst.get('strength', 'Unknown')}
- News Headline: {catalyst.get('headline', 'None')}
- Market Cap: ${fundamentals.get('market_cap', 'Unknown')}
- Float Size: {fundamentals.get('float_shares', 'Unknown')} shares
- Short Interest: {fundamentals.get('short_interest_pct', 'Unknown')}%
- Catalyst-to-Float Assessment: {catalyst.get('catalyst_float_ratio', 'Unknown')}

MOMENTUM METRICS:
- Current Price: ${matrix['quant_trigger']['price_at_trigger']}
- Direction: {matrix['quant_trigger']['direction']}
- Pre-market Change: {momentum.get('premarket_change_pct', 'N/A')}%
- Volume vs Average: {momentum.get('volume_ratio', 'N/A')}x
- RSI: {matrix['market_metrics']['rsi_14']}
- MACD: {matrix['market_metrics'].get('macd_line', 'N/A')}
- Above VWAP: {momentum.get('above_vwap', 'Unknown')}
- Opening Range Breakout Confirmed: {momentum.get('orb_confirmed', False)}
- Time of Signal: {matrix.get('timestamp', 'Unknown')}

SENTIMENT ANALYSIS:
- Score: {sentiment.get('sentiment_score', 5)}/10
- Smart Money Signal: {sentiment.get('smart_money_signal', 'neutral')}
- Catalyst Quality: {sentiment.get('catalyst_quality', 'unknown')}
- Source Divergence: {divergence_line}
- Key Narratives: {sentiment.get('key_narratives', [])[:2]}

TEXTBOOK VALIDATION:
- Validated: {rag.get('validated', False)}
- Sources: {rag.get('sources', [])}
- Recommendation: {rag.get('recommendation', 'UNKNOWN')}
- Methodology: {str(rag.get('methodology', ''))[:200]}

RISK ASSESSMENT:
- Approved: {risk.get('approved', False)}
- Asset Tier: {risk.get('asset_tier', 'unknown').upper()} ({risk.get('tier_reason', 'no classification data')})
- Entry: ${matrix['quant_trigger']['price_at_trigger']}
- Structural Stop: ${risk['position']['stop_loss_price'] if risk.get('position') else 'N/A'}
- Target 1: ${risk['position']['take_profit_price'] if risk.get('position') else 'N/A'}
- Position Size: ${risk['position']['position_usd'] if risk.get('position') else 'N/A'}
- Max Loss: ${risk['position']['risk_usd'] if risk.get('position') else 'N/A'}

KEY EVALUATION CRITERIA:
1. Is the catalyst strong enough to move this specific float size?
2. Is price holding above VWAP after the opening range?
3. Is volume confirming institutional participation (5x+ average)?
4. Has the opening range breakout been confirmed (after 9:45am EST)?
5. Does the reward/risk justify the momentum trade?

Respond with ONLY one of these two formats:
EXECUTE: TRUE
or
EXECUTE: FALSE
REASON: [one sentence explanation focusing on catalyst-to-float assessment]"""