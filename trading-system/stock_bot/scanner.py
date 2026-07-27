import os
import sys
import json
import pandas as pd
import pandas_ta as ta
import requests
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# SCANNER CONFIGURATION
# ==========================================
VOLUME_RATIO_MIN = 0.7       # Lowered for intraday volume calculation
RSI_MIN = 45                 # Momentum zone minimum
RSI_MAX = 75                 # Not exhausted maximum
PRICE_MIN = 5.0              # Minimum stock price
PRICE_MAX = 1000.0
PREMARKET_CHANGE_MIN = 2.0   # Minimum move %
MAX_WORKERS = 8              # Concurrent yfinance/NewsAPI requests — tune if throttled
FUNDAMENTALS_CACHE_TTL_HOURS = 24  # market cap/float don't meaningfully move intraday

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

# {symbol: {"data": {...}, "fetched_at": datetime}} — see fetch_stock_fundamentals
_fundamentals_cache = {}

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
            'catalyst_float_ratio': f'{catalyst_type} detected'
        }

    except Exception as e:
        return {
            'type': 'Error',
            'strength': 'unknown',
            'headline': str(e)[:50],
            'catalyst_float_ratio': 'Cannot assess'
        }

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """Calculate VWAP from OHLCV data"""
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    vwap = (typical_price * df['Volume']).cumsum() / df['Volume'].cumsum()
    return vwap

def _fetch_one_fundamentals(symbol: str) -> dict:
    """Static fundamentals for one symbol via yfinance's full .info call —
    the only source that carries floatShares/shortPercentOfFloat; fast_info
    has marketCap but not float data."""
    try:
        info = yf.Ticker(symbol).info
        short_pct_raw = info.get('shortPercentOfFloat')
        return {
            'market_cap': info.get('marketCap'),
            'float_shares': info.get('floatShares'),
            'shares_outstanding': info.get('sharesOutstanding'),
            'short_interest_pct': round(short_pct_raw * 100, 2) if short_pct_raw is not None else None,
        }
    except Exception:
        return {
            'market_cap': None,
            'float_shares': None,
            'shares_outstanding': None,
            'short_interest_pct': None,
        }

def fetch_stock_fundamentals(symbols: list) -> dict:
    """
    Market cap / float / shares outstanding / short interest, cached with a
    24h TTL since these don't meaningfully change intraday — no reason to
    pay a yfinance .info call per symbol on every 5-minute scan cycle.
    Only cache misses/stale entries hit the network, concurrently.
    """
    now = datetime.now()
    result = {}
    to_fetch = []

    for symbol in symbols:
        cached = _fundamentals_cache.get(symbol)
        if cached and (now - cached['fetched_at']) < timedelta(hours=FUNDAMENTALS_CACHE_TTL_HOURS):
            result[symbol] = cached['data']
        else:
            to_fetch.append(symbol)

    if to_fetch:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_fetch_one_fundamentals, symbol): symbol for symbol in to_fetch}
            for future in as_completed(futures):
                symbol = futures[future]
                data = future.result()
                _fundamentals_cache[symbol] = {'data': data, 'fetched_at': now}
                result[symbol] = data

    return result

def fetch_universe_prefilter(symbols: list) -> dict:
    """
    One batched daily-bar call for the whole universe, used to cheaply filter
    out symbols before any per-symbol intraday fetch. Also carries the 20-day
    average volume so analyze_stock() doesn't need its own second history()
    call for survivors.

    Returns {symbol: {"price", "prev_close", "change_pct", "avg_daily_vol_20d"}}
    for symbols with usable data; symbols with missing/insufficient data are
    simply omitted (same net effect as the old per-symbol try/except skip).
    """
    prefilter = {}
    try:
        data = yf.download(
            symbols, period='2mo', interval='1d',
            group_by='ticker', threads=True, progress=False
        )
    except Exception as e:
        print(f"[!] Universe prefilter batch download failed: {e}")
        return prefilter

    for symbol in symbols:
        try:
            df = data[symbol].dropna(subset=['Close'])
            if len(df) < 21:
                continue

            price = float(df['Close'].iloc[-1])
            prev_close = float(df['Close'].iloc[-2])
            if prev_close == 0:
                continue
            change_pct = ((price - prev_close) / prev_close) * 100
            avg_daily_vol_20d = float(df['Volume'].iloc[-21:-1].mean())

            prefilter[symbol] = {
                "price": price,
                "prev_close": prev_close,
                "change_pct": change_pct,
                "avg_daily_vol_20d": avg_daily_vol_20d
            }
        except Exception:
            continue

    return prefilter

def analyze_stock(symbol: str, avg_daily_vol_20d: float) -> dict:
    """
    Fetch and analyze a single stock's intraday data using Yahoo Finance.
    avg_daily_vol_20d comes from fetch_universe_prefilter()'s batched call,
    so this only issues the one intraday history() request it actually needs.
    """
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

        # Project today's volume based on time elapsed in trading day
        try:
            import pytz
            from datetime import datetime
            est = pytz.timezone('US/Eastern')
            now_est = datetime.now(est)

            # Minutes elapsed since market open
            market_open = now_est.replace(hour=9, minute=30, second=0)
            minutes_elapsed = max(1, (now_est - market_open).seconds / 60)
            total_minutes = 390  # 6.5 hour trading day

            # Project full day volume based on pace
            pct_day_elapsed = minutes_elapsed / total_minutes
            total_vol_today = df['Volume'].sum()
            projected_vol = total_vol_today / pct_day_elapsed if pct_day_elapsed > 0 else total_vol_today

            if avg_daily_vol_20d > 0:
                day_volume_ratio = projected_vol / avg_daily_vol_20d
            else:
                day_volume_ratio = 1.0
        except Exception:
            day_volume_ratio = 1.0

        volume_ratio = day_volume_ratio

        latest = df.iloc[-1]
        rsi = latest.get('RSI_14', 50)
        macd_line = latest.get('MACD_12_26_9', 0)
        macd_signal_val = latest.get('MACDs_12_26_9', 0)
        price = latest['Close']
        vwap = latest['vwap']

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

    # Stage 1 — one batched daily-bar call for the whole universe, cheap filter
    print(f"[*] Prefiltering {len(STOCK_UNIVERSE)} stocks (batched daily bars)...")
    prefilter = fetch_universe_prefilter(STOCK_UNIVERSE)

    candidates = [
        symbol for symbol, data in prefilter.items()
        if PRICE_MIN <= data['price'] <= PRICE_MAX
        and abs(data['change_pct']) >= PREMARKET_CHANGE_MIN
    ]
    print(f"[✓] {len(candidates)} candidates passed price/change prefilter")

    # Stage 2 — expensive intraday analysis, concurrent, candidates only
    results = []
    if candidates:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(analyze_stock, symbol, prefilter[symbol]['avg_daily_vol_20d']): symbol
                for symbol in candidates
            }
            for future in as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)

    # Stage 3 — catalyst + fundamentals enrichment, concurrent, survivors only
    if results:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            catalyst_futures = {
                pool.submit(get_catalyst, r['symbol']): r for r in results
            }
            for future in as_completed(catalyst_futures):
                r = catalyst_futures[future]
                r['catalyst'] = future.result()

        fundamentals = fetch_stock_fundamentals([r['symbol'] for r in results])
        for r in results:
            r['fundamentals'] = fundamentals.get(r['symbol'], {})

        for r in results:
            print(f"  ✅ {r['symbol']}: ${r['current_price']} | "
                  f"RSI: {r['rsi_14']} | "
                  f"Vol: {r['volume_ratio']}x | "
                  f"Catalyst: {r['catalyst']['strength']}")

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
