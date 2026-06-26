import asyncio
import sys
import os
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Windows async fix
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Add parent to path for shared core imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_bot.scanner import run_stock_scanner, is_market_open, is_orb_ready
from stock_bot.execution import execute_paper_trade
from stock_bot.prompts import get_stock_prompt

SCAN_INTERVAL_SECONDS = 300  # Scan every 5 minutes during market hours

def build_state_matrix(scanner_result: dict) -> dict:
    """Convert scanner result to state matrix for orchestrator"""
    catalyst = scanner_result.get('catalyst', {})
    return {
        'session_id': f"stock_{int(datetime.now().timestamp())}",
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'ticker': scanner_result['symbol'],
        'catalyst': catalyst,
        'quant_trigger': {
            'direction': 'BUY_SIGNAL',
            'indicator_setup': 'ORB + VWAP + Volume Momentum',
            'timeframe': '1m',
            'price_at_trigger': scanner_result['current_price']
        },
        'market_metrics': {
            'rsi_14': scanner_result['rsi_14'],
            'macd_line': scanner_result['macd_line'],
            'macd_signal': scanner_result['macd_signal'],
            'volume_spike': scanner_result['volume_ratio'] >= 5.0,
            'recent_volume': scanner_result['volume_ratio']
        },
        'momentum_metrics': {
            'premarket_change_pct': scanner_result['premarket_change_pct'],
            'volume_ratio': scanner_result['volume_ratio'],
            'above_vwap': scanner_result['above_vwap'],
            'orb_confirmed': scanner_result['orb_confirmed'],
            'vwap': scanner_result['vwap']
        },
        'consensus': {'status': 'AWAITING_AGENT_EVALUATION'}
    }

def run_orchestrator(state_matrix: dict) -> dict:
    """Run the shared LangGraph orchestrator with stock prompt"""
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'core'
    ))

    from core.orchestrator import route_to_orchestrator
    return route_to_orchestrator(state_matrix, prompt_type='stock')

def main():
    print("""
    ╔══════════════════════════════════════════╗
    ║     AI STOCK DAY TRADING BOT             ║
    ║     High Risk / High Reward Mode         ║
    ║     Paper Trading Active                 ║
    ╚══════════════════════════════════════════╝
    """)

    print("[*] Stock bot starting...")
    print("[*] Market hours: 9:30am - 4:00pm EST, Mon-Fri")
    print("[*] ORB window: trades start at 9:45am EST")
    print(f"[*] Scan interval: every {SCAN_INTERVAL_SECONDS//60} minutes")

    while True:
        if not is_market_open():
            now = datetime.now().strftime('%H:%M:%S')
            print(f"[{now}] Market closed — sleeping 5 minutes...")
            time.sleep(300)
            continue

        if not is_orb_ready():
            print("[*] Waiting for 9:45am ORB window...")
            time.sleep(60)
            continue

        # Run scanner
        setups = run_stock_scanner()

        if setups:
            print(f"[*] Found {len(setups)} setups — running orchestrator...")
            for setup in setups[:3]:  # Max 3 trades per scan
                state_matrix = build_state_matrix(setup)
                try:
                    result = run_orchestrator(state_matrix)
                    execute_paper_trade(
                        result.get('final_decision', 'EXECUTE: FALSE'),
                        result.get('state_matrix', state_matrix)
                    )
                except Exception as e:
                    print(f"[!] Orchestrator error: {e}")

        print(f"[*] Next scan in {SCAN_INTERVAL_SECONDS//60} minutes...")
        time.sleep(SCAN_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()