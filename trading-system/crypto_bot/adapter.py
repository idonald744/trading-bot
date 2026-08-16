"""
Crypto market adapter — configuration and market-specific plumbing consumed
by core.runner. Signal thresholds live here; risk rules and the Claude
prompt stay in core/agents/risk_agent.py and crypto_bot/prompts.py,
selected by the orchestrator via prompt_type='crypto'.
"""
import os
import sys
from datetime import datetime

import ccxt
import ccxt.pro as ccxtpro
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.state_matrix import build_state_matrix
from crypto_bot.scanner import run_market_scanner
from crypto_bot.execution import execute_paper_trade, check_open_positions
from crypto_bot.market_data import get_cached_fundamentals


class CryptoAdapter:
    name = 'crypto'
    prompt_type = 'crypto'
    mode = 'stream'

    timeframe = '15m'
    max_candles = 100
    rsi_oversold = 30
    rsi_overbought = 70
    scan_interval_seconds = 15 * 60
    position_check_interval_seconds = 45  # cheap poll for open-position exits, independent of scan cadence
    require_volatility = True  # Only trade during high volatility periods
    indicator_setup = 'RSI + MACD + Bollinger Confluence'
    default_watchlist = ['BTC/USD', 'ETH/USD', 'SOL/USD']

    async def create_exchange(self):
        # Google DNS resolver — Windows DNS fix for async Kraken connection
        import aiohttp
        resolver = aiohttp.AsyncResolver(nameservers=['8.8.8.8', '8.8.4.4'])
        connector = aiohttp.TCPConnector(resolver=resolver)
        session = aiohttp.ClientSession(connector=connector)
        return ccxtpro.kraken({
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'},
            'session': session,
        })

    async def fetch_warmup(self, symbol: str) -> list:
        rest = ccxt.kraken({'enableRateLimit': True})
        return rest.fetch_ohlcv(symbol, self.timeframe, limit=self.max_candles)

    async def scan(self) -> list:
        return await run_market_scanner()

    def check_signal(self, row) -> str:
        """Return 'BUY_SIGNAL' / 'SELL_SIGNAL' or None for the latest candle row"""
        rsi = row['RSI_14']
        macd_line = row['MACD_12_26_9']
        macd_signal = row['MACDs_12_26_9']

        high_volatility = row.get('high_volatility')
        if high_volatility is None or pd.isna(high_volatility):
            high_volatility = True
        volatility_ok = (not self.require_volatility) or bool(high_volatility)

        if (rsi <= self.rsi_oversold) and (macd_line > macd_signal) and volatility_ok:
            return 'BUY_SIGNAL'
        if (rsi >= self.rsi_overbought) and (macd_line < macd_signal) and volatility_ok:
            return 'SELL_SIGNAL'
        return None

    def build_stream_state_matrix(self, symbol: str, candle_ts: int,
                                  direction: str, row) -> dict:
        return build_state_matrix(
            ticker=symbol.replace('/', ''),
            direction=direction,
            indicator_setup=self.indicator_setup,
            timeframe=self.timeframe,
            price=row['close'],
            market_metrics={
                'rsi_14': round(row['RSI_14'], 2),
                'macd_line': round(row['MACD_12_26_9'], 4),
                'macd_signal': round(row['MACDs_12_26_9'], 4),
                'volume_spike': bool(row['volume_spike']),
                'recent_volume': round(row['volume'], 2),
            },
            session_id=f"trigger_{int(candle_ts / 1000)}",
            timestamp=datetime.fromtimestamp(
                candle_ts / 1000).strftime('%Y-%m-%d %H:%M:%S'),
            extras={'fundamentals': get_cached_fundamentals(symbol)},
        )

    def execute(self, decision: str, state_matrix: dict) -> dict:
        return execute_paper_trade(decision, state_matrix)

    def positions_checkable(self) -> bool:
        return True  # crypto trades 24/7

    def check_open_positions(self) -> None:
        check_open_positions()
