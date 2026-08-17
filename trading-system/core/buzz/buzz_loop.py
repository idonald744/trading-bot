"""
Buzz-detection poll loop — social-velocity discovery, independent of the
scanner/watchlist and of market hours (see core/runner.py's stream loop for
the technical-confirmation discovery path this complements, not replaces).

Wired in from core/runner.run() only for adapters with buzz_enabled=True
(currently just CryptoAdapter — see docs/roadmap.md for scope).
"""
import asyncio

import aiohttp
import ccxt.async_support as ccxt

from core.buzz import x_source, reddit_source, velocity, cooldown
from core.buzz.ticker_extraction import validate_candidates
from core.state_matrix import build_state_matrix
from crypto_bot.market_data import get_market_caps

POLL_INTERVAL_SECONDS = 15 * 60  # matches the crypto stream loop's scan cadence
LOOKBACK_HOURS = 1

# reddit_source.get_candidates() no-ops (returns []) until BUZZ_REDDIT_ENABLED
# is set — see reddit_source.py. Adding it here now means it activates with
# zero changes to this loop once/if Reddit access comes through.
SOURCES = [x_source, reddit_source]


async def _merge_candidates() -> list:
    """Pulls every source and sums mention counts per ticker. Sources aren't
    required to corroborate each other (either alone can trigger downstream
    in velocity.record_and_check) — summing here just avoids discarding a
    source's signal when both happen to mention the same ticker."""
    merged = {}
    for source in SOURCES:
        try:
            candidates = source.get_candidates(lookback_hours=LOOKBACK_HOURS)
        except Exception as e:
            print(f"[!] Buzz: {source.__name__} raised: {e}")
            continue
        for c in candidates:
            ticker = c.get('ticker')
            if not ticker:
                continue
            entry = merged.setdefault(
                ticker, {'ticker': ticker, 'estimated_mentions': 0, 'notes': [], 'sources': []}
            )
            entry['estimated_mentions'] += c.get('estimated_mentions', 0)
            if c.get('note'):
                entry['notes'].append(c['note'])
            entry['sources'].append(c.get('source', source.__name__))
    return list(merged.values())


async def _fetch_price(symbol: str) -> float:
    # Google DNS resolver — same Windows async-DNS fix as
    # crypto_bot/adapter.py's create_exchange() and ticker_extraction.py.
    resolver = aiohttp.AsyncResolver(nameservers=['8.8.8.8', '8.8.4.4'])
    connector = aiohttp.TCPConnector(resolver=resolver)
    session = aiohttp.ClientSession(connector=connector)
    exchange = ccxt.kraken({'enableRateLimit': True, 'session': session})
    try:
        ticker = await exchange.fetch_ticker(symbol)
        return ticker['last']
    finally:
        # ccxt doesn't take ownership of an externally-provided session —
        # exchange.close() alone leaves it (and its connector) open.
        await exchange.close()
        await session.close()


async def _fetch_fundamentals(symbol: str) -> dict:
    # Same Google DNS resolver fix as _fetch_price — crypto_bot/scanner.py
    # reuses one DNS-fixed session for both Kraken and CoinGecko calls;
    # a plain aiohttp.ClientSession() hits the same Windows aiodns failure.
    resolver = aiohttp.AsyncResolver(nameservers=['8.8.8.8', '8.8.4.4'])
    connector = aiohttp.TCPConnector(resolver=resolver)
    async with aiohttp.ClientSession(connector=connector) as session:
        result = await get_market_caps(session, [symbol])
    return result.get(symbol, {})


def _build_buzz_state_matrix(ticker: str, price: float, fundamentals: dict,
                              velocity_result: dict, candidate: dict) -> dict:
    return build_state_matrix(
        ticker=ticker,
        direction='BUY_SIGNAL',
        indicator_setup='Social Momentum Spike (buzz detection)',
        timeframe='n/a',
        price=price,
        market_metrics={},  # deliberately no rsi_14/macd — see risk_agent.py Rule 6 skip
        session_prefix='buzz',
        extras={
            'fundamentals': fundamentals,
            'signal_source': 'buzz',
            'buzz_metrics': {
                'mention_count': velocity_result['mention_count'],
                'baseline': velocity_result['baseline'],
                'trigger_type': velocity_result['trigger_type'],
                'reason': velocity_result['reason'],
                'sources': candidate['sources'],
                'notes': candidate['notes'],
            },
        },
    )


async def run_buzz_loop(adapter):
    print(f"[*] Buzz loop starting — polling every {POLL_INTERVAL_SECONDS // 60} minutes")
    while True:
        try:
            candidates = await _merge_candidates()
            validated = await validate_candidates(candidates)
            print(f"[*] Buzz: {len(candidates)} raw candidates, {len(validated)} tradable on Kraken")

            for candidate in validated:
                ticker = candidate['ticker']
                symbol = candidate['symbol']

                if cooldown.is_on_cooldown(ticker):
                    continue

                result = velocity.record_and_check(ticker, candidate['estimated_mentions'])
                if not result['triggered']:
                    continue

                print(f"[!] Buzz spike: {ticker} — {result['reason']}")
                try:
                    price = await _fetch_price(symbol)
                    fundamentals = await _fetch_fundamentals(symbol)
                except Exception as e:
                    print(f"[!] Buzz: price/fundamentals fetch failed for {symbol}: {e}")
                    continue

                state_matrix = _build_buzz_state_matrix(ticker, price, fundamentals, result, candidate)
                cooldown.mark_triggered(ticker)

                from core.runner import dispatch_trigger_async
                await dispatch_trigger_async(adapter, state_matrix)

        except Exception as e:
            print(f"[!] Buzz loop error: {e}")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
