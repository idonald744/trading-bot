"""
Shared position-monitor loop — periodically checks open paper trades
against their stop-loss/take-profit levels and closes any that have
crossed, independent of scanner/watchlist cadence.

Phase 1 boundary: closes are detected from our own polled price feed
(get_current_price), not from a broker/exchange fill event. Real
reconciliation-against-broker-truth (exchange/broker state as the
authoritative source) is a deliberate later phase — see
docs/blueprint-reference.md #2.
"""
import asyncio


def monitor_open_positions(trades: list, get_current_price, check_exit, close_trade) -> None:
    """Market-agnostic close-detection scaffold.

    trades: the market's in-memory paper_trades list (mutated in place).
    get_current_price(ticker) -> float
    check_exit(trade, current_price) -> str | None   (close reason, or None)
    close_trade(trade, exit_price, reason) -> None    (mutates trade, persists)
    """
    open_trades = [t for t in trades if t.get('status') == 'OPEN']
    if not open_trades:
        return

    tickers = {t['ticker'] for t in open_trades}
    prices = {}
    for ticker in tickers:
        try:
            prices[ticker] = get_current_price(ticker)
        except Exception as e:
            print(f"[!] Position monitor: price fetch failed for {ticker}: {e}")

    for trade in open_trades:
        price = prices.get(trade['ticker'])
        if price is None:
            continue
        reason = check_exit(trade, price)
        if reason:
            close_trade(trade, price, reason)


async def run_position_monitor_loop(adapter):
    """Runs alongside the adapter's main scan/stream loop (see core/runner.py)."""
    while True:
        if adapter.positions_checkable():
            try:
                adapter.check_open_positions()
            except Exception as e:
                print(f"[!] Position monitor error: {e}")
        await asyncio.sleep(adapter.position_check_interval_seconds)
