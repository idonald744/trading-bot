import asyncio
import json
import ccxt
import ccxt.pro as ccxtpro
import pandas as pd
import pandas_ta as ta
from datetime import datetime
from dotenv import load_dotenv
from market_scanner import run_market_scanner
from orchestrator import route_to_orchestrator
from trigger_log import log_trigger

load_dotenv()

# ==========================================
# CONFIGURATION
# ==========================================
TIMEFRAME = '15m'
MAX_CANDLES = 100
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
SCAN_INTERVAL_MINUTES = 15  # Rescan market every 15 minutes

# Shared state
current_watchlist = []
candle_buffers = {}

async def process_symbol(exchange, symbol):
    """Stream live data for a single symbol and trigger orchestrator on signals"""
    global candle_buffers

    if symbol not in candle_buffers:
        try:
            rest = ccxt.kraken({'enableRateLimit': True})
            historical = rest.fetch_ohlcv(symbol, TIMEFRAME, limit=MAX_CANDLES)
            candle_buffers[symbol] = historical
            print(f"[✓] Warmed up {symbol} with {len(historical)} candles")
        except Exception as e:
            print(f"[!] Failed to warm up {symbol}: {e}")
            return

    while True:
        try:
            # Check if symbol is still in watchlist
            if symbol not in [t['symbol'] for t in current_watchlist]:
                print(f"[*] {symbol} removed from watchlist, stopping stream")
                break

            ohlcv = await exchange.watch_ohlcv(symbol, TIMEFRAME)
            latest = ohlcv[-1]
            timestamp = latest[0]

            if candle_buffers[symbol] and candle_buffers[symbol][-1][0] == timestamp:
                candle_buffers[symbol][-1] = latest
            else:
                candle_buffers[symbol].append(latest)

            if len(candle_buffers[symbol]) > MAX_CANDLES:
                candle_buffers[symbol].pop(0)

            df = pd.DataFrame(
                candle_buffers[symbol],
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)

            df.ta.rsi(length=14, append=True)
            df.ta.macd(fast=12, slow=26, signal=9, append=True)
            df.ta.bbands(length=20, append=True)
            df['volume_ma'] = df['volume'].rolling(20).mean()
            df['volume_spike'] = df['volume'] > (df['volume_ma'] * 1.5)

            latest_row = df.iloc[-1]
            current_price = latest_row['close']
            current_rsi = latest_row['RSI_14']
            macd_line = latest_row['MACD_12_26_9']
            macd_signal_val = latest_row['MACDs_12_26_9']

            if pd.isna(current_rsi) or pd.isna(macd_line):
                continue

            is_bullish = (current_rsi <= RSI_OVERSOLD) and (macd_line > macd_signal_val)
            is_bearish = (current_rsi >= RSI_OVERBOUGHT) and (macd_line < macd_signal_val)

            print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                  f"{symbol}: ${current_price:,.4f} | RSI: {current_rsi:.2f}")

            if is_bullish or is_bearish:
                state_matrix = {
                    "session_id": f"trigger_{int(timestamp/1000)}",
                    "timestamp": datetime.fromtimestamp(
                        timestamp/1000).strftime('%Y-%m-%d %H:%M:%S'),
                    "ticker": symbol.replace('/', ''),
                    "quant_trigger": {
                        "direction": "BUY_SIGNAL" if is_bullish else "SELL_SIGNAL",
                        "indicator_setup": "RSI + MACD + Bollinger Confluence",
                        "timeframe": TIMEFRAME,
                        "price_at_trigger": current_price
                    },
                    "market_metrics": {
                        "rsi_14": round(current_rsi, 2),
                        "macd_line": round(macd_line, 4),
                        "macd_signal": round(macd_signal_val, 4),
                        "volume_spike": bool(latest_row['volume_spike']),
                        "recent_volume": round(latest_row['volume'], 2)
                    },
                    "consensus": {"status": "AWAITING_AGENT_EVALUATION"}
                }

                # Run full pipeline in background
                await asyncio.get_event_loop().run_in_executor(
                    None, route_to_orchestrator, state_matrix
                )

        except Exception as e:
            print(f"[!] Stream error on {symbol}: {e}")
            await asyncio.sleep(5)

async def scanner_loop():
    """Runs market scanner every 15 minutes and updates watchlist"""
    global current_watchlist

    while True:
        print(f"\n{'='*50}")
        print(f"[*] Running market scan...")
        print(f"{'='*50}")

        try:
            watchlist = await run_market_scanner()
            if watchlist:
                current_watchlist = watchlist
                symbols = [t['symbol'] for t in watchlist]
                print(f"[✓] Watchlist updated: {symbols}")
            else:
                print("[*] No new setups found — keeping existing watchlist")
        except Exception as e:
            print(f"[!] Scanner error: {e}")

        # Wait 15 minutes before next scan
        print(f"[*] Next scan in {SCAN_INTERVAL_MINUTES} minutes...")
        await asyncio.sleep(SCAN_INTERVAL_MINUTES * 60)

async def main():
    print("""
    ╔══════════════════════════════════════╗
    ║     AI TRADING BOT — LIVE MODE       ║
    ║     Dynamic Market Scanner Active    ║
    ╚══════════════════════════════════════╝
    """)

    exchange = ccxtpro.kraken({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    })

    try:
        # Run initial scan
        print("[*] Running initial market scan...")
        initial_watchlist = await run_market_scanner()

        if not initial_watchlist:
            print("[*] No setups found on initial scan.")
            print("[*] Using default watchlist: BTC, ETH, SOL")
            current_watchlist.extend([
                {'symbol': 'BTC/USD'},
                {'symbol': 'ETH/USD'},
                {'symbol': 'SOL/USD'}
            ])
        else:
            current_watchlist.extend(initial_watchlist)

        symbols = [t['symbol'] for t in current_watchlist]
        print(f"[✓] Monitoring: {symbols}")

        # Start scanner loop and WebSocket streams concurrently
        tasks = [scanner_loop()]
        for ticker in current_watchlist:
            tasks.append(process_symbol(exchange, ticker['symbol']))

        await asyncio.gather(*tasks)

    except KeyboardInterrupt:
        print("\n[!] Bot stopped manually.")
    finally:
        await exchange.close()
        print("[*] WebSocket closed cleanly.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Exiting gracefully.")