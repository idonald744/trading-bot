"""
Read-only access to the bots' JSON logs. Deliberately decoupled from
whether the bots are currently running — this just reads whatever's on
disk, resolved relative to this file's location (not the process's cwd),
and never touches source data.
"""
import json
import os
import pandas as pd

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(DASHBOARD_DIR)  # trading-system/
TRIGGERS_LOG = os.path.join(PROJECT_ROOT, 'logs', 'triggers.json')
CRYPTO_TRADES_LOG = os.path.join(PROJECT_ROOT, 'logs', 'paper_trades.json')
STOCK_TRADES_LOG = os.path.join(PROJECT_ROOT, 'logs', 'stock_paper_trades.json')


def _safe_load_json(path: str, default):
    """Missing file, corrupt/torn JSON (a bot mid-write), or any other read
    failure all fall back to `default` — the dashboard must never crash on
    a bad read, just show stale/empty data until the next successful poll."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return default


def load_triggers() -> pd.DataFrame:
    """Flatten triggers.json into a summary DataFrame, plus the full raw
    dict per row (under '_raw') for the decision-trail detail view.

    Defensive .get() at every level: the 2 entries logged before tonight's
    asset-tier/fundamentals work have no 'asset_tier' key at all, and only
    stock triggers carry 'catalyst'/'momentum_metrics' — a KeyError here
    would break the dashboard on real, already-existing data.
    """
    raw = _safe_load_json(TRIGGERS_LOG, [])
    if not isinstance(raw, list):
        raw = []

    rows = []
    for trigger in raw:
        quant = trigger.get('quant_trigger', {})
        sentiment = trigger.get('sentiment', {})
        divergence = sentiment.get('divergence', {})
        rag = trigger.get('rag_validation', {})
        risk = trigger.get('risk_evaluation', {})
        final_decision = trigger.get('final_decision', '')

        rows.append({
            'timestamp': trigger.get('timestamp', 'unknown'),
            'market': trigger.get('prompt_type', 'unknown'),
            'ticker': trigger.get('ticker', 'unknown'),
            'direction': quant.get('direction', 'unknown'),
            'price': quant.get('price_at_trigger'),
            'executed': 'EXECUTE: TRUE' in final_decision,
            'final_decision': final_decision,
            'asset_tier': risk.get('asset_tier', 'n/a (pre-classification)'),
            'risk_approved': risk.get('approved'),
            'risk_reason': risk.get('reason', ''),
            'sentiment_score': sentiment.get('sentiment_score'),
            'sentiment_confidence': sentiment.get('confidence', ''),
            'divergence_detected': divergence.get('detected', False),
            'rag_validated': rag.get('validated'),
            '_raw': trigger,
        })

    return pd.DataFrame(rows)


def load_market_paper_trades(path: str, market_label: str) -> tuple:
    """Returns (trades_df, summary_dict) for one market's log file — a single
    read serves both the trades table and the balance metric, instead of
    discarding 'summary' the way the old per-market loader did."""
    data = _safe_load_json(path, {'summary': {}, 'trades': []})
    trades = data.get('trades', [])
    if not isinstance(trades, list):
        trades = []
    df = pd.DataFrame(trades)
    if not df.empty:
        df['market'] = market_label
    return df, data.get('summary', {}) or {}


def load_all_paper_trades() -> tuple:
    """Returns (combined trades_df, {'crypto': summary, 'stock': summary})."""
    crypto_df, crypto_summary = load_market_paper_trades(CRYPTO_TRADES_LOG, 'crypto')
    stock_df, stock_summary = load_market_paper_trades(STOCK_TRADES_LOG, 'stock')
    trades_df = (
        pd.DataFrame() if crypto_df.empty and stock_df.empty
        else pd.concat([crypto_df, stock_df], ignore_index=True)
    )
    balances = {'crypto': crypto_summary, 'stock': stock_summary}
    return trades_df, balances
