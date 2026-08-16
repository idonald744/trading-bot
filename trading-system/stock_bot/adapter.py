"""
Stock market adapter — configuration and market-specific plumbing consumed
by core.runner. Market-hours/ORB gating lives here; risk rules and the
Claude prompt stay in stock_bot/risk_agent.py and stock_bot/prompts.py,
selected by the orchestrator via prompt_type='stock'.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.state_matrix import build_state_matrix
from stock_bot.scanner import run_stock_scanner, is_market_open, is_orb_ready
from stock_bot.execution import execute_paper_trade, check_open_positions


class StockAdapter:
    name = 'stock'
    prompt_type = 'stock'
    mode = 'poll'

    scan_interval_seconds = 300  # Scan every 5 minutes during market hours
    position_check_interval_seconds = 75  # cheap poll for open-position exits, independent of scan cadence
    max_setups_per_scan = 3      # Max 3 trades per scan
    indicator_setup = 'ORB + VWAP + Volume Momentum'
    timeframe = '1m'

    def gate(self):
        """Return (message, sleep_seconds) while inactive, None when tradeable"""
        if not is_market_open():
            return ('Market closed — sleeping 5 minutes...', 300)
        if not is_orb_ready():
            return ('Waiting for 9:45am ORB window...', 60)
        return None

    async def scan(self) -> list:
        # Scanner is synchronous (yfinance/requests) — keep the loop responsive
        return await asyncio.get_event_loop().run_in_executor(
            None, run_stock_scanner
        )

    def build_state_matrix(self, scanner_result: dict) -> dict:
        catalyst = scanner_result.get('catalyst', {})
        return build_state_matrix(
            ticker=scanner_result['symbol'],
            direction='BUY_SIGNAL',
            indicator_setup=self.indicator_setup,
            timeframe=self.timeframe,
            price=scanner_result['current_price'],
            market_metrics={
                'rsi_14': scanner_result['rsi_14'],
                'macd_line': scanner_result['macd_line'],
                'macd_signal': scanner_result['macd_signal'],
                'volume_spike': scanner_result['volume_ratio'] >= 5.0,
                'recent_volume': scanner_result['volume_ratio'],
            },
            session_prefix='stock',
            extras={
                'catalyst': catalyst,
                'fundamentals': scanner_result.get('fundamentals', {}),
                'momentum_metrics': {
                    'premarket_change_pct': scanner_result.get(
                        'premarket_change_pct',
                        scanner_result.get('change_pct', 0)
                    ),
                    'volume_ratio': scanner_result['volume_ratio'],
                    'above_vwap': scanner_result['above_vwap'],
                    'orb_confirmed': scanner_result['orb_confirmed'],
                    'vwap': scanner_result['vwap'],
                },
            },
        )

    def execute(self, decision: str, state_matrix: dict) -> dict:
        return execute_paper_trade(decision, state_matrix)

    def positions_checkable(self) -> bool:
        return is_market_open()

    def check_open_positions(self) -> None:
        check_open_positions()
