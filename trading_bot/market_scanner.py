import asyncio
import json
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt
from datetime import datetime

# ==========================================
# SCANNER CONFIGURATION
# ==========================================
TOP_VOLUME_LIMIT = 50
PRICE_CHANGE_MIN = 2.0      # Lowered from 3% to 2%
RSI_OVERSOLD_MAX = 45       # Widened for early detection
RSI_OVERBOUGHT_MIN = 55     # Widened for early detection
MAX_CANDLES = 100

async def fetch_and_analyze_ticker(exchange, symbol, change_24h):
    try:
        ohlcv = await exchange.fetch_ohlcv(
            symbol, timeframe='15m', limit=MAX_CANDLES
        )
        if len(ohlcv) < 30:
            return None

        df = pd.DataFrame(
            ohlcv,
            columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
        )
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)

        df.ta.rsi(length=14, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)

        latest = df.iloc[-1]
        rsi = latest['RSI_14']
        macd_line = latest['MACD_12_26_9']
        macd_signal = latest['MACDs_12_26_9']
        current_price = latest['close']

        if pd.isna(rsi) or pd.isna(macd_line):
            return None

        is_bullish = (rsi <= RSI_OVERSOLD_MAX) and (macd_line > macd_signal)
        is_bearish = (rsi >= RSI_OVERBOUGHT_MIN) and (macd_line < macd_signal)

       

        if is_bullish or is_bearish:
            return {
                "symbol": symbol,
                "current_price": current_price,
                "change_24h": round(change_24h, 2),
                "rsi_14": round(rsi, 2),
                "macd_setup": "BULLISH_CROSS" if is_bullish else "BEARISH_CROSS",
                "recommended_action": "BUY" if is_bullish else "SELL"
            }
    except Exception:
        pass
    return None

async def run_market_scanner():
    exchange = ccxt.kraken({'enableRateLimit': True})
    print(f"[*] Starting Market Scan: "
          f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        # Step 1 — Fetch all tickers and filter USDT pairs
        tickers = await exchange.fetch_tickers()
        usdt_pairs = []

        for symbol, data in tickers.items():
            # USD only for Canadian compliance, skip USDT duplicates
            if (symbol.endswith('/USD') and
                    not symbol.endswith('/USDT') and
                    data.get('quoteVolume') is not None):
                usdt_pairs.append(data)

        print(f"[✓] Found {len(usdt_pairs)} active USDT pairs on Kraken")

        # Step 2 — Sort by volume, take top 50
        usdt_pairs.sort(
            key=lambda x: float(x['quoteVolume']), reverse=True
        )
        top_pairs = usdt_pairs[:TOP_VOLUME_LIMIT]
        print(f"[✓] Isolated top {TOP_VOLUME_LIMIT} by volume")

        # Step 3 — Filter for >3% price movement
        volatile_tasks = []
        for ticker in top_pairs:
            symbol = ticker['symbol']
            change = ticker.get('percentage')
            if change and abs(change) >= PRICE_CHANGE_MIN:
                volatile_tasks.append(
                    fetch_and_analyze_ticker(exchange, symbol, change)
                )

        print(f"[*] Analyzing {len(volatile_tasks)} volatile pairs...")

        # Step 4 — Run all indicator calculations in parallel
        results = await asyncio.gather(*volatile_tasks)
        watchlist = [r for r in results if r is not None]

        print("\n🎯 =============================================")
        print(f"   WATCHLIST: {len(watchlist)} high-conviction targets")
        print("=============================================")

        if watchlist:
            print(json.dumps(watchlist, indent=2))
        else:
            print("   No setups found matching all criteria right now.")
            print("   Try again in 15 minutes or loosen RSI thresholds.")

        print("=============================================\n")
        return watchlist

    except Exception as e:
        print(f"[!] Scanner error: {e}")
        return []
    finally:
        await exchange.close()

if __name__ == '__main__':
    asyncio.run(run_market_scanner())