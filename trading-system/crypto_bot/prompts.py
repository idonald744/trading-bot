def _setup_section(matrix: dict, fundamentals: dict) -> str:
    """Branches on signal_source: buzz-sourced triggers have no RSI/MACD
    reading (they weren't found by the technical scanner) and get a
    social-momentum framing instead of the technical one."""
    if matrix.get('signal_source') == 'buzz':
        buzz = matrix.get('buzz_metrics', {})
        return f"""TRADE SETUP — SOCIAL-MOMENTUM-DRIVEN (no technical confirmation):
- Ticker: {matrix['ticker']}
- Direction: {matrix['quant_trigger']['direction']}
- Price: ${matrix['quant_trigger']['price_at_trigger']}
- This setup was surfaced by a social mention-velocity spike, NOT by RSI/MACD/
  Bollinger confluence — there is no technical indicator confirmation behind it.
- Mention count: {buzz.get('mention_count', 'unknown')} (baseline: {buzz.get('baseline', 'unknown')})
- Trigger type: {buzz.get('trigger_type', 'unknown')}
- Reason: {buzz.get('reason', 'unknown')}
- Source(s): {buzz.get('sources', [])}
- Notes: {buzz.get('notes', [])}
- Market Cap: ${fundamentals.get('market_cap', 'Unknown')}
- Market Cap Rank: #{fundamentals.get('market_cap_rank', 'Unknown')}"""

    return f"""TRADE SETUP — TECHNICALLY-CONFIRMED:
- Ticker: {matrix['ticker']}
- Direction: {matrix['quant_trigger']['direction']}
- Price: ${matrix['quant_trigger']['price_at_trigger']}
- RSI: {matrix['market_metrics']['rsi_14']}
- MACD: {matrix['market_metrics']['macd_line']}
- Volume Spike: {matrix['market_metrics']['volume_spike']}
- Market Cap: ${fundamentals.get('market_cap', 'Unknown')}
- Market Cap Rank: #{fundamentals.get('market_cap_rank', 'Unknown')}"""


def get_crypto_prompt(matrix: dict, sentiment: dict, rag: dict, risk: dict) -> str:
    """Mean reversion prompt for crypto trading"""
    fundamentals = matrix.get('fundamentals', {})
    divergence = sentiment.get('divergence', {})
    if divergence.get('detected'):
        divergence_line = f"CONFLICT — {divergence.get('reason', 'sources disagree')}"
    elif sentiment.get('sources_analyzed', 1) > 1:
        divergence_line = "None — sources aligned"
    else:
        divergence_line = "N/A — single source"

    return f"""You are a senior risk officer for a crypto trading firm.
Review this trade setup and make a final decision.

{_setup_section(matrix, fundamentals)}

SENTIMENT ANALYSIS:
- Score: {sentiment.get('sentiment_score', 5)}/10
- Smart Money: {sentiment.get('smart_money_signal', 'neutral')}
- Retail Signal: {sentiment.get('retail_signal', 'neutral')}
- Trap Warning: {sentiment.get('trap_warning', False)}
- Source Divergence: {divergence_line}
- Key Narratives: {sentiment.get('key_narratives', [])[:2]}

TEXTBOOK VALIDATION:
- Validated: {rag.get('validated', False)}
- Sources: {rag.get('sources', [])}
- Recommendation: {rag.get('recommendation', 'UNKNOWN')}
- Methodology: {str(rag.get('methodology', ''))[:200]}

RISK ASSESSMENT:
- Approved: {risk.get('approved', False)}
- Reason: {risk.get('reason', 'Unknown')}
- Asset Tier: {risk.get('asset_tier', 'unknown').upper()} ({risk.get('tier_reason', 'no classification data')})
- Position Size: ${risk['position']['position_usd'] if risk.get('position') else 'N/A'}
- Stop Loss: ${risk['position']['stop_loss_price'] if risk.get('position') else 'N/A'}
- Take Profit: ${risk['position']['take_profit_price'] if risk.get('position') else 'N/A'}

Respond with ONLY one of these two formats:
EXECUTE: TRUE
or
EXECUTE: FALSE
REASON: [one sentence explanation]"""