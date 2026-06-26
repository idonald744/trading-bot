import ccxt
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import json
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# BACKTEST CONFIGURATION
# ==========================================
SYMBOLS = ['BTC/USD', 'ETH/USD', 'SOL/USD']
TIMEFRAME = '1d'
LOOKBACK_DAYS = 730  # 2 years
RSI_OVERSOLD = 35
RSI_OVERBOUGHT = 65
STOP_LOSS_PCT = 0.015
TAKE_PROFIT_PCT = 0.030
INITIAL_CAPITAL = 1000.0
POSITION_SIZE_PCT = 0.02
REQUIRE_VOLUME_SPIKE = False
REQUIRE_TREND_FILTER = False

def fetch_historical_data(symbol: str, days: int = 180) -> pd.DataFrame:
    """Fetch historical OHLCV data from Yahoo Finance"""
    print(f"[*] Fetching {days} days of {symbol} data from Yahoo Finance...")

    # Convert Kraken symbol to Yahoo Finance format
    yahoo_symbols = {
        'BTC/USD': 'BTC-USD',
        'ETH/USD': 'ETH-USD',
        'SOL/USD': 'SOL-USD'
    }

    yahoo_symbol = yahoo_symbols.get(symbol)
    if not yahoo_symbol:
        print(f"[!] No Yahoo Finance mapping for {symbol}")
        return pd.DataFrame()

    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        ticker = yf.Ticker(yahoo_symbol)
        df = ticker.history(
            start=start_date.strftime('%Y-%m-%d'),
            end=end_date.strftime('%Y-%m-%d'),
            interval='1d'
        )

        if df.empty:
            print(f"[!] No data returned for {yahoo_symbol}")
            return pd.DataFrame()

        # Standardize column names
        df = df.rename(columns={
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume'
        })
        df = df[['open', 'high', 'low', 'close', 'volume']]
        df.index = df.index.tz_localize(None)

        print(f"[✓] Fetched {len(df)} candles for {symbol} "
              f"({df.index[0].date()} to {df.index[-1].date()})")
        return df

    except Exception as e:
        print(f"[!] Yahoo Finance error for {symbol}: {e}")
        return pd.DataFrame()

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate all technical indicators"""
    df.ta.rsi(length=14, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.bbands(length=20, std=2, append=True)
    df['volume_ma'] = df['volume'].rolling(20).mean()
    df['volume_spike'] = df['volume'] > (df['volume_ma'] * 1.5)
    # ATR for volatility regime filter
    df.ta.atr(length=14, append=True)
    df['atr_ma'] = df['ATRr_14'].rolling(20).mean()
    # High volatility = ATR above its own average
    df['high_volatility'] = df['ATRr_14'] > df['atr_ma']
    return df.dropna()

def run_backtest(symbol: str, df: pd.DataFrame) -> dict:
    """Run backtest simulation on historical data"""
    print(f"\n[*] Backtesting {symbol}...")

    trades = []
    capital = INITIAL_CAPITAL
    peak_capital = INITIAL_CAPITAL
    max_drawdown = 0
    in_trade = False
    entry_price = 0
    entry_direction = None
    entry_idx = None

    for i in range(1, len(df)):
        row = df.iloc[i]

        rsi = row['RSI_14']
        macd_line = row['MACD_12_26_9']
        macd_signal_val = row['MACDs_12_26_9']
        price = row['close']
        high_volatility = row.get('high_volatility', True)
        volume_spike = row.get('volume_spike', False)

        if pd.isna(rsi) or pd.isna(macd_line):
            continue

        is_bullish = (
            (rsi <= RSI_OVERSOLD) and
            (macd_line > macd_signal_val) and
            high_volatility
        )
        is_bearish = (
            (rsi >= RSI_OVERBOUGHT) and
            (macd_line < macd_signal_val) and
            high_volatility
        )

        if not in_trade:
            if is_bullish or is_bearish:
                entry_price = price
                entry_direction = 'BUY' if is_bullish else 'SELL'
                entry_idx = df.index[i]
                in_trade = True

        else:
            if entry_direction == 'BUY':
                stop_loss = entry_price * (1 - STOP_LOSS_PCT)
                take_profit = entry_price * (1 + TAKE_PROFIT_PCT)
                if price <= stop_loss:
                    pnl_pct = -STOP_LOSS_PCT
                    exit_reason = 'STOP_LOSS'
                elif price >= take_profit:
                    pnl_pct = TAKE_PROFIT_PCT
                    exit_reason = 'TAKE_PROFIT'
                else:
                    continue

            else:  # SELL
                stop_loss = entry_price * (1 + STOP_LOSS_PCT)
                take_profit = entry_price * (1 - TAKE_PROFIT_PCT)
                if price >= stop_loss:
                    pnl_pct = -STOP_LOSS_PCT
                    exit_reason = 'STOP_LOSS'
                elif price <= take_profit:
                    pnl_pct = TAKE_PROFIT_PCT
                    exit_reason = 'TAKE_PROFIT'
                else:
                    continue

            position_size = capital * POSITION_SIZE_PCT
            pnl_usd = position_size * pnl_pct
            capital += pnl_usd

            if capital > peak_capital:
                peak_capital = capital
            drawdown = (peak_capital - capital) / peak_capital
            max_drawdown = max(max_drawdown, drawdown)

            trades.append({
                'symbol': symbol,
                'direction': entry_direction,
                'entry_price': entry_price,
                'exit_price': price,
                'entry_time': str(entry_idx),
                'exit_time': str(df.index[i]),
                'pnl_pct': round(pnl_pct * 100, 2),
                'pnl_usd': round(pnl_usd, 2),
                'exit_reason': exit_reason,
                'capital_after': round(capital, 2)
            })

            in_trade = False

    if not trades:
        return {
            'symbol': symbol,
            'total_trades': 0,
            'message': 'No trades generated'
        }

    wins = [t for t in trades if t['pnl_usd'] > 0]
    losses = [t for t in trades if t['pnl_usd'] <= 0]
    win_rate = len(wins) / len(trades) * 100
    total_pnl = sum(t['pnl_usd'] for t in trades)
    avg_win = sum(t['pnl_usd'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['pnl_usd'] for t in losses) / len(losses) if losses else 0
    rr_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    weeks = LOOKBACK_DAYS / 7
    months = LOOKBACK_DAYS / 30
    signals_per_week = len(trades) / weeks
    signals_per_month = len(trades) / months
    return {
        'symbol': symbol,
        'total_trades': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate_pct': round(win_rate, 1),
        'total_pnl_usd': round(total_pnl, 2),
        'final_capital': round(capital, 2),
        'return_pct': round((capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100, 2),
        'max_drawdown_pct': round(max_drawdown * 100, 2),
        'avg_win_usd': round(avg_win, 2),
        'avg_loss_usd': round(avg_loss, 2),
        'risk_reward_ratio': round(rr_ratio, 2),
        'signals_per_week': round(signals_per_week, 2),
        'signals_per_month': round(signals_per_month, 1),
        'trades': trades
    }

def print_results(results: list):
    """Print backtest summary"""
    print("\n" + "="*60)
    print("📊 BACKTEST RESULTS — 6 MONTHS")
    print("="*60)

    # Targets
    print("\n🎯 TARGETS TO PASS:")
    print("   Win rate:        >55%")
    print("   Risk/reward:     >1.5:1")
    print("   Max drawdown:    <15%")
    print("   Signals/week:    3-8")

    print("\n📈 RESULTS BY TICKER:")
    
    all_trades = []
    for r in results:
        if r['total_trades'] == 0:
            print(f"\n  {r['symbol']}: No trades generated")
            continue

        win_icon = "✅" if r['win_rate_pct'] >= 55 else "❌"
        rr_icon = "✅" if r['risk_reward_ratio'] >= 1.5 else "❌"
        dd_icon = "✅" if r['max_drawdown_pct'] <= 15 else "❌"
        sig_icon = "✅" if r.get('signals_per_month', 0) >= 0.5 else "⚠️"

        print(f"\n  {r['symbol']}:")
        print(f"    Trades: {r['total_trades']} | "
              f"Wins: {r['wins']} | Losses: {r['losses']}")
        print(f"    {win_icon} Win rate: {r['win_rate_pct']}%")
        print(f"    {rr_icon} Risk/reward: {r['risk_reward_ratio']}:1")
        print(f"    {dd_icon} Max drawdown: {r['max_drawdown_pct']}%")
        print(f"    {sig_icon} Signals/month: {r.get('signals_per_month', 0)} "
              f"({r['signals_per_week']} /week)")
        print(f"    💰 Total P&L: ${r['total_pnl_usd']} "
              f"({r['return_pct']}% return)")

        all_trades.extend(r.get('trades', []))

    # Combined stats
    if all_trades:
        all_wins = [t for t in all_trades if t['pnl_usd'] > 0]
        combined_win_rate = len(all_wins) / len(all_trades) * 100
        combined_pnl = sum(t['pnl_usd'] for t in all_trades)

        print(f"\n{'='*60}")
        print(f"📊 COMBINED STATS:")
        print(f"   Total trades: {len(all_trades)}")
        print(f"   Combined win rate: {combined_win_rate:.1f}%")
        print(f"   Combined P&L: ${combined_pnl:.2f}")
        print(f"{'='*60}\n")

    # Save results
    with open('logs/backtest_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    print("[✓] Full results saved to logs/backtest_results.json")

if __name__ == "__main__":
    print("🔍 Starting 6-month backtest...")
    print(f"   Symbols: {SYMBOLS}")
    print(f"   Timeframe: {TIMEFRAME}")
    print(f"   Capital: ${INITIAL_CAPITAL}")
    print(f"   Position size: {POSITION_SIZE_PCT*100}%")
    print(f"   Stop loss: {STOP_LOSS_PCT*100}%")
    print(f"   Take profit: {TAKE_PROFIT_PCT*100}%\n")

    all_results = []

    for symbol in SYMBOLS:
        df = fetch_historical_data(symbol, LOOKBACK_DAYS)
        if df.empty:
            continue
        df = calculate_indicators(df)
        result = run_backtest(symbol, df)
        all_results.append(result)

    print_results(all_results)