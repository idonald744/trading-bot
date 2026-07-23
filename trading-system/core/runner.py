"""
Shared run-loop for all market bots.

Two loop shapes, selected by the adapter's `mode`:
  - 'poll'   — gate on market hours, call adapter.scan() on an interval,
               dispatch each returned setup (stock bot today)
  - 'stream' — periodic scanner refresh of a watchlist plus per-symbol
               websocket candle streaming with buffer management and
               indicator recompute (crypto bot today)

Both shapes converge on dispatch_trigger(): run the LangGraph orchestrator
with the adapter's prompt_type, then hand the decision to the adapter's
market-specific execution module.
"""
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pandas_ta as ta

from core.orchestrator import route_to_orchestrator


# ==========================================
# SHARED DISPATCH
# ==========================================
def dispatch_trigger(adapter, state_matrix: dict) -> dict:
    """Run the full agent pipeline for one trigger, then execute the decision"""
    result = route_to_orchestrator(state_matrix, prompt_type=adapter.prompt_type)
    adapter.execute(
        result.get('final_decision', 'EXECUTE: FALSE'),
        result.get('state_matrix', state_matrix),
    )
    return result


async def dispatch_trigger_async(adapter, state_matrix: dict):
    """Run the pipeline in an executor so the event loop stays responsive"""
    await asyncio.get_event_loop().run_in_executor(
        None, dispatch_trigger, adapter, state_matrix
    )


# ==========================================
# INDICATORS (shared by streaming loops)
# ==========================================
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df.ta.rsi(length=14, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.bbands(length=20, append=True)
    df['volume_ma'] = df['volume'].rolling(20).mean()
    df['volume_spike'] = df['volume'] > (df['volume_ma'] * 1.5)

    # ATR volatility regime: high volatility = ATR above its own average
    try:
        df.ta.atr(length=14, append=True)
        atr_cols = [c for c in df.columns if 'ATR' in c.upper()]
        if atr_cols:
            atr_col = atr_cols[0]
            df['atr_ma'] = df[atr_col].rolling(20).mean()
            df['high_volatility'] = df[atr_col] > df['atr_ma']
    except Exception:
        pass

    return df


# ==========================================
# POLL LOOP
# ==========================================
async def run_poll_loop(adapter):
    while True:
        gate = adapter.gate()
        if gate:
            message, sleep_seconds = gate
            now = datetime.now().strftime('%H:%M:%S')
            print(f"[{now}] {message}")
            await asyncio.sleep(sleep_seconds)
            continue

        setups = await adapter.scan()

        if setups:
            print(f"[*] Found {len(setups)} setups — running orchestrator...")
            for setup in setups[:adapter.max_setups_per_scan]:
                state_matrix = adapter.build_state_matrix(setup)
                try:
                    await dispatch_trigger_async(adapter, state_matrix)
                except Exception as e:
                    print(f"[!] Orchestrator error: {e}")

        print(f"[*] Next scan in {adapter.scan_interval_seconds // 60} minutes...")
        await asyncio.sleep(adapter.scan_interval_seconds)


# ==========================================
# STREAM LOOP
# ==========================================
async def stream_symbol(adapter, exchange, symbol, watchlist_symbols, candle_buffers):
    """Stream live candles for one symbol and dispatch the pipeline on signals"""
    if symbol not in candle_buffers:
        try:
            historical = await adapter.fetch_warmup(symbol)
            candle_buffers[symbol] = historical
            print(f"[✓] Warmed up {symbol} with {len(historical)} candles")
        except Exception as e:
            print(f"[!] Failed to warm up {symbol}: {e}")
            return

    while True:
        try:
            if symbol not in watchlist_symbols:
                print(f"[*] {symbol} removed from watchlist, stopping stream")
                break

            ohlcv = await exchange.watch_ohlcv(symbol, adapter.timeframe)
            latest = ohlcv[-1]
            timestamp = latest[0]
            buffer = candle_buffers[symbol]

            if buffer and buffer[-1][0] == timestamp:
                buffer[-1] = latest
            else:
                buffer.append(latest)
            if len(buffer) > adapter.max_candles:
                buffer.pop(0)

            df = pd.DataFrame(
                buffer,
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)
            df = compute_indicators(df)

            row = df.iloc[-1]
            if pd.isna(row.get('RSI_14')) or pd.isna(row.get('MACD_12_26_9')):
                continue

            print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                  f"{symbol}: ${row['close']:,.4f} | RSI: {row['RSI_14']:.2f}")

            direction = adapter.check_signal(row)
            if direction:
                state_matrix = adapter.build_stream_state_matrix(
                    symbol, timestamp, direction, row
                )
                await dispatch_trigger_async(adapter, state_matrix)

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[!] Stream error on {symbol}: {e}")
            await asyncio.sleep(5)


async def run_stream_loop(adapter):
    exchange = await adapter.create_exchange()
    watchlist_symbols = set()
    candle_buffers = {}
    stream_tasks = {}

    def sync_streams():
        """Ensure every watchlisted symbol has a live stream task"""
        for symbol in sorted(watchlist_symbols):
            task = stream_tasks.get(symbol)
            if task is None or task.done():
                stream_tasks[symbol] = asyncio.ensure_future(
                    stream_symbol(adapter, exchange, symbol,
                                  watchlist_symbols, candle_buffers)
                )

    try:
        print("[*] Running initial market scan...")
        initial_watchlist = await adapter.scan()

        if not initial_watchlist:
            print("[*] No setups found on initial scan.")
            print(f"[*] Using default watchlist: {adapter.default_watchlist}")
            watchlist_symbols.update(adapter.default_watchlist)
        else:
            watchlist_symbols.update(t['symbol'] for t in initial_watchlist)

        print(f"[✓] Monitoring: {sorted(watchlist_symbols)}")
        sync_streams()

        while True:
            print(f"[*] Next scan in {adapter.scan_interval_seconds // 60} minutes...")
            await asyncio.sleep(adapter.scan_interval_seconds)

            print(f"\n{'='*50}")
            print(f"[*] Running market scan...")
            print(f"{'='*50}")
            try:
                watchlist = await adapter.scan()
                if watchlist:
                    watchlist_symbols.clear()
                    watchlist_symbols.update(t['symbol'] for t in watchlist)
                    print(f"[✓] Watchlist updated: {sorted(watchlist_symbols)}")
                    sync_streams()
                else:
                    print("[*] No new setups found — keeping existing watchlist")
            except Exception as e:
                print(f"[!] Scanner error: {e}")

    except KeyboardInterrupt:
        print("\n[!] Bot stopped manually.")
    finally:
        for task in stream_tasks.values():
            task.cancel()
        await exchange.close()
        print("[*] WebSocket closed cleanly.")


# ==========================================
# ENTRY
# ==========================================
async def run(adapter):
    if adapter.mode == 'stream':
        await run_stream_loop(adapter)
    else:
        await run_poll_loop(adapter)
