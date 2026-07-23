from datetime import datetime


def build_state_matrix(
    ticker: str,
    direction: str,
    indicator_setup: str,
    timeframe: str,
    price: float,
    market_metrics: dict,
    session_prefix: str = 'trigger',
    session_id: str = None,
    timestamp: str = None,
    extras: dict = None,
) -> dict:
    """
    Build the state matrix consumed by the orchestrator.

    Common skeleton shared by all markets; market-specific sections
    (e.g. stock catalyst / momentum_metrics) are passed via `extras`
    and merged in as top-level keys.
    """
    now = datetime.now()
    matrix = {
        'session_id': session_id or f"{session_prefix}_{int(now.timestamp())}",
        'timestamp': timestamp or now.strftime('%Y-%m-%d %H:%M:%S'),
        'ticker': ticker,
        'quant_trigger': {
            'direction': direction,
            'indicator_setup': indicator_setup,
            'timeframe': timeframe,
            'price_at_trigger': price,
        },
        'market_metrics': market_metrics,
        'consensus': {'status': 'AWAITING_AGENT_EVALUATION'},
    }
    if extras:
        matrix.update(extras)
    return matrix
