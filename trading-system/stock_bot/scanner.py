import os
import sys
import json
import pandas as pd
import pandas_ta as ta
import requests
import yfinance as yf
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# Windows async fix
if sys.platform == 'win32':
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ==========================================
# SCANNER CONFIGURATION
# ==========================================
VOLUME_RATIO_MIN = 3.0       # 3x average volume minimum
RSI_MIN = 45                 # Momentum zone minimum
RSI_MAX = 75                 # Not exhausted maximum
PRICE_MIN = 5.0              # Minimum stock price
PRICE_MAX = 500.0            # Maximum stock price
PREMARKET_CHANGE_MIN = 2.0   # Minimum move %

# High momentum universe — mix of large cap and momentum stocks
STOCK_UNIVERSE = [
    'AAPL', 'MSFT', 'NVDA', 'AMD', 'META',
    'GOOGL', 'AMZN', 'TSLA', 'NFLX', 'CRM',
    'BABA', 'SHOP', 'SQ', 'COIN', 'HOOD',
    'PLTR', 'RBLX', 'SNAP', 'PINS', 'UBER',
    'LYFT', 'ABNB', 'DASH', 'RIVN', 'LCID',
    'NIO', 'XPEV', 'SOFI', 'AFRM', 'UPST',
    'MARA', 'RIOT', 'CLSK', 'BITF', 'HUT',
    'SMCI', 'CRWD', 'PANW', 'ZS', 'OKTA'
]

def is_market_open() -> bool:
    """Check if US stock market is currently open EST"""
    import pytz
    est = pytz.timezone('US/Eastern')
    now = datetime.now(est)
    if now.weekday() > 4:
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close

def is_orb_ready() -> bool:
    """Check if past 9:45am EST opening range window"""
    import pytz
    est = pytz.timezone('US/Eastern')
    now = datetime.now(est)
    orb_time = now.replace(hour=9, minute=45, second=0, microsecond=0)
    return now >= orb_time

def get_catalyst(symbol: str) -> dict:
    """Get catalyst/news for a stock using NewsAPI"""
    try:
        api_key = os.getenv('NEWS_API_KEY')
        url = "https://newsapi.org/v2/everything"
        params = {
            'q': symbol,
            'from': (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d'),
            'sortBy': 'publishedAt',
            'pageSize': 5,
            'apiKey': api_key,
            'language': 'en'
        }
        response = requests.get(url, params=params, timeout=10)
        articles = response.json().get('articles', [])

        if not articles:
            return {
                'type': 'Unknown',
                'strength': 'weak',
                'headline': 'No recent news found',
                'float_shares': 'Unknown',
                'short_interest_pct': 'Unknown',
                'catalyst_float_ratio': 'Cannot assess'
            }

        headline = articles[0].get('title', 'No headline')
        strong_keywords = [
            'fda', 'approval', 'merger', 'acquisition', 'earnings beat',
            'buyout', 'partnership', 'contract', 'breakthrough', 'upgrade',
            'beat', 'record', 'launch', 'deal'
        ]
        weak_keywords = [
            'analyst', 'price target', 'downgrade', 'note', 'coverage'
        ]

        headline_lower = headline.lower()
        if any(k in headline_lower for k in strong_keywords):
            catalyst_type = 'Strong Catalyst'
            strength = 'strong'
        elif any(k in headline_lower for k in weak_keywords):
            catalyst_type = 'Analyst Note'
            strength = 'weak'
        else:
            catalyst_type = 'News'
            strength = 'medium'

        return {
            'type': catalyst_type,
            'strength': strength,
            'headline': headline[:100],
            'float_shares': 'Unknown',
            'short_interest_pct': 'Unknown',
            'catalyst_float_ratio': f'{catalyst_type} detected'
        }

    except Exception as e:
        return {
            'type': 'Error',
            'strength': 'unknown',
            'headline': str(e)[:50],
            'float_shares': 'Unknown',
            'short_interest_pct': 'Unknown',
            'catalyst_float_ratio': 'Cannot assess'
        }

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """Calculate VWAP from OHLCV data"""
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    vwap = (typical_price * df['Volume']).cumsum() / df['Volume'].cumsum()
    return vwap

def analyze_stock(symbol: str) -> dict:
    """Fetch and analyze a single stock using Yahoo Finance"""
    try:
        ticker = yf.Ticker(symbol)

        # Get today's intraday data (1 minute bars)
        df = ticker.history(period='1d', interval='1m')

        if df.empty or len(df) < 20:
            return None

        # Calculate indicators
        df.ta.rsi(length=14, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)
        df['vwap'] = calculate_vwap(df)
        df['volume_ma'] = df['Volume'].rolling(20).mean()
        df['volume_ratio'] = df['Volume'] / df['volume_ma']

        latest = df.iloc[-1]
        rsi = latest.get('RSI_14', 50)
        macd_line = latest.get('MACD_12_26_9', 0)
        macd_signal_val = latest.get('MACDs_12_26_9', 0)
        price = latest['Close']
        vwap = latest['vwap']
        volume_ratio = latest['volume_ratio']

        if pd.isna(rsi) or pd.isna(macd_line) or pd.isna(price):
            return None

        if not (PRICE_MIN <= price <= PRICE_MAX):
            return None

        # Get previous close for change calculation
        prev_close = df.iloc[0]['Open']
        change_pct = ((price - prev_close) / prev_close) * 100

        if abs(change_pct) < PREMARKET_CHANGE_MIN:
            return None

        # Opening range (first 15 mins = first 15 bars on 1m)
        orb_bars = df.head(15)
        orb_high = orb_bars['High'].max()
        orb_low = orb_bars['Low'].min()
        orb_confirmed = price > orb_high

        # Signal conditions
        above_vwap = price > vwap
        is_bullish = (
            RSI_MIN <= rsi <= RSI_MAX and
            macd_line > macd_signal_val and
            above_vwap and
            orb_confirmed and
            volume_ratio >= VOLUME_RATIO_MIN
        )

        if not is_bullish:
            return None

        return {
            'symbol': symbol,
            'current_price': round(float(price), 4),
            'change_pct': round(float(change_pct), 2),
            'rsi_14': round(float(rsi), 2),
            'macd_line': round(float(macd_line), 4),
            'macd_signal': round(float(macd_signal_val), 4),
            'vwap': round(float(vwap), 4),
            'above_vwap': bool(above_vwap),
            'orb_confirmed': bool(orb_confirmed),
            'orb_high': round(float(orb_high), 4),
            'volume_ratio': round(float(volume_ratio), 2),
            'recommended_action': 'BUY'
        }

    except Exception as e:
        return None

def run_stock_scanner() -> list:
    """Main stock scanner — runs during market hours"""
    print(f"\n[*] Stock Scanner: "
          f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if not is_market_open():
        print("[*] Market is closed. Scanner sleeping.")
        return []

    if not is_orb_ready():
        print("[*] Waiting for 9:45am EST opening range...")
        return []

    print(f"[*] Scanning {len(STOCK_UNIVERSE)} stocks...")
    results = []

    for symbol in STOCK_UNIVERSE:
        result = analyze_stock(symbol)
        if result:
            catalyst = get_catalyst(symbol)
            result['catalyst'] = catalyst
            results.append(result)
            print(f"  ✅ {symbol}: ${result['current_price']} | "
                  f"RSI: {result['rsi_14']} | "
                  f"Vol: {result['volume_ratio']}x | "
                  f"Catalyst: {catalyst['strength']}")

    print(f"\n🎯 =============================================")
    print(f"   STOCK WATCHLIST: {len(results)} targets")
    print(f"=============================================")

    if results:
        for r in results:
            print(f"  {r['symbol']}: ${r['current_price']} | "
                  f"RSI {r['rsi_14']} | "
                  f"{r['volume_ratio']}x vol | "
                  f"{r['catalyst']['type']}")
    else:
        print("   No setups found matching all criteria.")

    print(f"=============================================\n")
    return results

if __name__ == "__main__":
    results = run_stock_scanner()