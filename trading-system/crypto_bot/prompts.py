def get_crypto_prompt(matrix: dict, sentiment: dict, rag: dict, risk: dict) -> str:
    """Mean reversion prompt for crypto trading"""
    return f"""You are a senior risk officer for a crypto trading firm.
Review this trade setup and make a final decision.

TRADE SETUP:
- Ticker: {matrix['ticker']}
- Direction: {matrix['quant_trigger']['direction']}
- Price: ${matrix['quant_trigger']['price_at_trigger']}
- RSI: {matrix['market_metrics']['rsi_14']}
- MACD: {matrix['market_metrics']['macd_line']}
- Volume Spike: {matrix['market_metrics']['volume_spike']}

SENTIMENT ANALYSIS:
- Score: {sentiment.get('sentiment_score', 5)}/10
- Smart Money: {sentiment.get('smart_money_signal', 'neutral')}
- Retail Signal: {sentiment.get('retail_signal', 'neutral')}
- Trap Warning: {sentiment.get('trap_warning', False)}
- Key Narratives: {sentiment.get('key_narratives', [])[:2]}

TEXTBOOK VALIDATION:
- Validated: {rag.get('validated', False)}
- Sources: {rag.get('sources', [])}
- Recommendation: {rag.get('recommendation', 'UNKNOWN')}
- Methodology: {str(rag.get('methodology', ''))[:200]}

RISK ASSESSMENT:
- Approved: {risk.get('approved', False)}
- Reason: {risk.get('reason', 'Unknown')}
- Position Size: ${risk['position']['position_usd'] if risk.get('position') else 'N/A'}
- Stop Loss: ${risk['position']['stop_loss_price'] if risk.get('position') else 'N/A'}
- Take Profit: ${risk['position']['take_profit_price'] if risk.get('position') else 'N/A'}

Respond with ONLY one of these two formats:
EXECUTE: TRUE
or
EXECUTE: FALSE
REASON: [one sentence explanation]"""