"""
Shared trading bot entrypoint.

Usage:
    python bot.py crypto    # 24/7 Kraken mean-reversion bot (websocket streaming)
    python bot.py stock     # 9:30am-4:00pm EST momentum day-trading bot (polling)

The market argument selects an adapter (crypto_bot/adapter.py or
stock_bot/adapter.py) which supplies scanner, signal, and execution plumbing
to the shared run-loop in core/runner.py. Prompts and risk rules remain
market-specific modules routed by the orchestrator via prompt_type.
"""
import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

# Windows asyncio fix — required for aiodns/websockets on Windows
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

BANNERS = {
    'crypto': """
    ╔══════════════════════════════════════╗
    ║     AI TRADING BOT — CRYPTO          ║
    ║     Dynamic Market Scanner Active    ║
    ║     Paper Trading Active             ║
    ╚══════════════════════════════════════╝
    """,
    'stock': """
    ╔══════════════════════════════════════════╗
    ║     AI TRADING BOT — STOCKS              ║
    ║     High Risk / High Reward Mode         ║
    ║     Paper Trading Active                 ║
    ╚══════════════════════════════════════════╝
    """,
}


def get_adapter(market: str):
    if market == 'crypto':
        from crypto_bot.adapter import CryptoAdapter
        return CryptoAdapter()
    from stock_bot.adapter import StockAdapter
    return StockAdapter()


def main():
    parser = argparse.ArgumentParser(description='AI trading bot')
    parser.add_argument('market', choices=['crypto', 'stock'],
                        help='Which market to trade')
    args = parser.parse_args()

    print(BANNERS[args.market])

    adapter = get_adapter(args.market)

    if args.market == 'stock':
        print("[*] Market hours: 9:30am - 4:00pm EST, Mon-Fri")
        print("[*] ORB window: trades start at 9:45am EST")
        print(f"[*] Scan interval: every {adapter.scan_interval_seconds // 60} minutes")

    from core.runner import run
    try:
        asyncio.run(run(adapter))
    except KeyboardInterrupt:
        print("\n[!] Exiting gracefully.")


if __name__ == '__main__':
    main()
