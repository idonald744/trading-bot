"""
Ticker validation/normalization shared by every buzz source.

Two jobs:
  1. extract_cashtags() — pulls $CASHTAG-style symbols out of raw post/
     comment text. Used by reddit_source.py once it's live (Reddit gives
     raw text, not structured tickers); x_source.py doesn't need this since
     Grok already returns structured ticker strings.
  2. validate_candidates() — the gate every source's output passes through
     before it can reach velocity/cooldown/dispatch: filters candidate
     tickers down to ones that are both a real string and an actually
     tradable Kraken USD pair. A source (especially an LLM-mediated one
     like Grok) can report a ticker that doesn't exist or isn't tradable
     here at all.
"""
import re
import time

import ccxt.async_support as ccxt

TRADABLE_BASES_TTL_SECONDS = 6 * 3600  # Kraken's listed USD pairs change rarely intraday

CASHTAG_RE = re.compile(r'\$([A-Za-z]{2,10})\b')

# {data: set of base symbols, fetched_at: epoch seconds}
_tradable_bases_cache = {"data": set(), "fetched_at": 0}


def extract_cashtags(text: str) -> list:
    """'$SOL just broke out, $sol to the moon' -> ['SOL', 'SOL']"""
    return [m.upper() for m in CASHTAG_RE.findall(text or "")]


def normalize_ticker(raw: str) -> str:
    """'$SOL' / 'sol' / 'SOL/USD' -> 'SOL'"""
    return (raw or "").strip().lstrip('$').split('/')[0].upper()


async def _fetch_tradable_bases() -> set:
    # Google DNS resolver — same Windows async-DNS fix as
    # crypto_bot/adapter.py's create_exchange(); plain ccxt.async_support
    # Kraken calls fail here with aiodns.error.DNSError otherwise.
    import aiohttp
    resolver = aiohttp.AsyncResolver(nameservers=['8.8.8.8', '8.8.4.4'])
    connector = aiohttp.TCPConnector(resolver=resolver)
    session = aiohttp.ClientSession(connector=connector)
    exchange = ccxt.kraken({'enableRateLimit': True, 'session': session})
    try:
        markets = await exchange.load_markets()
    finally:
        # ccxt doesn't take ownership of an externally-provided session —
        # exchange.close() alone leaves it (and its connector) open.
        await exchange.close()
        await session.close()
    return {
        m['base'].upper() for m in markets.values()
        if m.get('quote') == 'USD' and m.get('active', True)
    }


async def get_tradable_bases() -> set:
    """Cached set of Kraken USD-pair base symbols (e.g. {'BTC', 'SOL', ...})."""
    now = time.time()
    if _tradable_bases_cache["data"] and (now - _tradable_bases_cache["fetched_at"] < TRADABLE_BASES_TTL_SECONDS):
        return _tradable_bases_cache["data"]

    try:
        bases = await _fetch_tradable_bases()
    except Exception as e:
        print(f"[!] Buzz: failed to refresh tradable base list: {e}")
        return _tradable_bases_cache["data"]  # keep stale data over nothing

    _tradable_bases_cache["data"] = bases
    _tradable_bases_cache["fetched_at"] = now
    return bases


async def validate_candidates(candidates: list) -> list:
    """Filters a merged candidate list down to real, tradable Kraken USD
    bases. Returns the same dicts with 'ticker' normalized to the base
    symbol and a 'symbol' field added (e.g. 'SOL/USD') for downstream
    price/fundamentals lookups."""
    bases = await get_tradable_bases()
    validated = []
    for c in candidates:
        base = normalize_ticker(c.get('ticker', ''))
        if not base or base not in bases:
            continue
        validated.append({**c, 'ticker': base, 'symbol': f'{base}/USD'})
    return validated
