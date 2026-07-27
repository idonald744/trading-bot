"""
CoinGecko market-data lookups — market cap / volume / supply, the crypto-side
equivalent of the yfinance fundamentals added for stocks. This is data
plumbing only: it exposes the numbers, it doesn't classify anything.

Kraken trades base/quote symbols (e.g. "PEPE/USD"); CoinGecko keys everything
by an internal id (e.g. "pepe"), and ticker symbols are NOT unique across
CoinGecko's ~17k listed coins — obscure/meme tickers are exactly where
collisions are most likely. Resolution strategy:
  1. A hardcoded override for well-known majors (explicit, not guessed).
  2. Single-candidate symbols resolve trivially.
  3. Multi-candidate symbols are disambiguated by picking the highest
     market-cap match, scoped only to the symbols actually being requested
     this scan cycle (never the full 17k-coin universe at once).
"""
import os
import time
import aiohttp
from dotenv import load_dotenv

load_dotenv()

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
SYMBOL_LIST_TTL_SECONDS = 7 * 24 * 3600  # /coins/list barely changes day to day

# Known majors likely to appear in Kraken's top-volume USD pairs — explicit,
# not left to the market-cap heuristic. Extend as ambiguous symbols get
# reviewed (see the "[!] CoinGecko symbol ... ambiguous" log line).
SYMBOL_ID_OVERRIDES = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
    "ADA": "cardano", "DOGE": "dogecoin", "DOT": "polkadot",
    "MATIC": "matic-network", "LTC": "litecoin", "LINK": "chainlink",
    "AVAX": "avalanche-2", "ATOM": "cosmos", "UNI": "uniswap",
    "XLM": "stellar", "ALGO": "algorand", "FIL": "filecoin",
    "ETC": "ethereum-classic", "BCH": "bitcoin-cash", "SHIB": "shiba-inu",
    "PEPE": "pepe", "NEAR": "near", "ICP": "internet-computer",
    "APT": "aptos", "ARB": "arbitrum", "OP": "optimism", "SUI": "sui",
    "INJ": "injective-protocol", "TIA": "celestia", "SEI": "sei-network",
    "WIF": "dogwifcoin", "BONK": "bonk",
    "JUP": "jupiter-exchange-solana", "GRT": "the-graph", "AAVE": "aave",
    "MKR": "maker", "CRV": "curve-dao-token", "LDO": "lido-dao",
    "RUNE": "thorchain", "SAND": "the-sandbox", "MANA": "decentraland",
    "AXS": "axie-infinity", "COMP": "compound-governance-token",
    "SNX": "havven", "YFI": "yearn-finance", "ENS": "ethereum-name-service",
    "DYDX": "dydx",
}

# {symbol: [candidate coingecko ids]}, from /coins/list — raw, unresolved
_symbol_candidates_cache = {"data": {}, "fetched_at": 0}

# {symbol (e.g. "BTC/USD"): {market_cap, total_volume, circulating_supply,
# market_cap_rank}} — last resolved market data, read synchronously by
# crypto_bot/adapter.py's build_stream_state_matrix (which isn't async), so
# the scanner's periodic get_market_caps() call is the only writer.
_fundamentals_cache = {}


def _api_key_headers() -> dict:
    api_key = os.getenv("COINGECKO_API_KEY")
    return {"x-cg-demo-api-key": api_key} if api_key else {}


async def _get_json(session: aiohttp.ClientSession, path: str, params: dict = None) -> object:
    async with session.get(
        f"{COINGECKO_BASE_URL}{path}", params=params, headers=_api_key_headers()
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


async def _fetch_markets_by_ids(session: aiohttp.ClientSession, ids: list) -> list:
    if not ids:
        return []
    return await _get_json(session, "/coins/markets", {
        "vs_currency": "usd", "ids": ",".join(ids), "per_page": 250, "page": 1,
    })


async def _get_symbol_candidates(session: aiohttp.ClientSession) -> dict:
    """Cached {symbol: [coingecko ids]} from /coins/list. Refreshed weekly."""
    now = time.time()
    if _symbol_candidates_cache["data"] and (now - _symbol_candidates_cache["fetched_at"] < SYMBOL_LIST_TTL_SECONDS):
        return _symbol_candidates_cache["data"]

    try:
        coins = await _get_json(session, "/coins/list")
    except Exception as e:
        print(f"[!] CoinGecko /coins/list fetch failed: {e}")
        return _symbol_candidates_cache["data"]  # keep stale data over nothing

    candidates = {}
    for coin in coins:
        sym = coin["symbol"].upper()
        candidates.setdefault(sym, []).append(coin["id"])

    _symbol_candidates_cache["data"] = candidates
    _symbol_candidates_cache["fetched_at"] = now
    return candidates


async def _resolve_ids(session: aiohttp.ClientSession, base_symbols: list) -> dict:
    """Resolve base symbols (e.g. 'PEPE') to a single coingecko id each."""
    candidates = await _get_symbol_candidates(session)
    resolved = {}
    ambiguous = {}

    for sym in base_symbols:
        if sym in SYMBOL_ID_OVERRIDES:
            resolved[sym] = SYMBOL_ID_OVERRIDES[sym]
            continue
        ids = candidates.get(sym, [])
        if len(ids) == 1:
            resolved[sym] = ids[0]
        elif len(ids) > 1:
            ambiguous[sym] = ids
        # len == 0: unresolved, omitted — logged by the caller via the
        # eventual gap between requested and returned symbols.

    if ambiguous:
        # Bounded to this scan cycle's symbols only — never the full
        # CoinGecko universe's worth of collisions at once.
        all_candidate_ids = sorted({cid for ids in ambiguous.values() for cid in ids})
        try:
            markets = await _fetch_markets_by_ids(session, all_candidate_ids)
            cap_by_id = {m["id"]: (m.get("market_cap") or 0) for m in markets}
            for sym, ids in ambiguous.items():
                best = max(ids, key=lambda cid: cap_by_id.get(cid, 0))
                if cap_by_id.get(best, 0) > 0:
                    resolved[sym] = best
                else:
                    print(f"[!] CoinGecko symbol '{sym}' ambiguous ({len(ids)} candidates), "
                          f"no market-cap data to disambiguate — leaving unresolved")
        except Exception as e:
            print(f"[!] CoinGecko ambiguity resolution failed: {e}")

    return resolved


async def get_market_caps(session: aiohttp.ClientSession, symbols: list) -> dict:
    """
    One batched /coins/markets call (plus, only when needed, one bounded
    disambiguation call) for the given Kraken pair symbols (e.g. "BTC/USD").

    Returns {symbol: {market_cap, total_volume, circulating_supply,
    market_cap_rank}} for whatever resolves; unresolved symbols are omitted.
    Also updates the module-level fundamentals cache so
    get_cached_fundamentals() can be read synchronously elsewhere.
    """
    base_to_full = {}
    for symbol in symbols:
        base = symbol.split('/')[0].upper() if '/' in symbol else symbol.upper()
        base_to_full[base] = symbol

    try:
        id_map = await _resolve_ids(session, list(base_to_full.keys()))
    except Exception as e:
        print(f"[!] CoinGecko symbol resolution failed: {e}")
        return {}

    id_to_full_symbol = {cg_id: base_to_full[sym] for sym, cg_id in id_map.items()}

    try:
        markets = await _fetch_markets_by_ids(session, list(id_to_full_symbol.keys()))
    except Exception as e:
        print(f"[!] CoinGecko /coins/markets fetch failed: {e}")
        return {}

    result = {}
    for m in markets:
        full_symbol = id_to_full_symbol.get(m["id"])
        if not full_symbol:
            continue
        result[full_symbol] = {
            "market_cap": m.get("market_cap"),
            "total_volume": m.get("total_volume"),
            "circulating_supply": m.get("circulating_supply"),
            "market_cap_rank": m.get("market_cap_rank"),
        }

    _fundamentals_cache.update(result)
    return result


def get_cached_fundamentals(symbol: str) -> dict:
    """
    Synchronous lookup for the streaming trigger path (build_stream_state_matrix
    isn't async), populated by the scanner's periodic get_market_caps() call.
    Returns {} if this symbol hasn't been resolved yet.
    """
    return _fundamentals_cache.get(symbol, {})
