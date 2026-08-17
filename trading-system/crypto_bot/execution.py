import os
import json
import ccxt
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# PAPER TRADING CONFIGURATION
# ==========================================
PAPER_TRADING = True  # Set to False for live trading
PORTFOLIO_BALANCE = 1000.0  # Starting paper balance
POSITION_SIZE_PCT = 0.02    # 2% per trade
LOG_FILE = 'logs/paper_trades.json'

def _load_paper_trades() -> tuple:
    """Restore trades/balance from disk so a bot restart doesn't reset
    history to empty and overwrite it on the next save."""
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, 'r') as f:
                data = json.load(f)
            trades = data.get('trades', [])
            if not isinstance(trades, list):
                trades = []
            balance = data.get('summary', {}).get('current_balance', PORTFOLIO_BALANCE)
            return trades, balance
        except Exception:
            pass
    return [], PORTFOLIO_BALANCE

# Track paper trading performance
paper_trades, paper_balance = _load_paper_trades()

def get_exchange():
    return ccxt.kraken({
        'apiKey': os.getenv('KRAKEN_API_KEY'),
        'secret': os.getenv('KRAKEN_SECRET'),
        'enableRateLimit': True
    })

def calculate_position(price: float, balance: float, direction: str) -> dict:
    position_usd = balance * POSITION_SIZE_PCT
    quantity = position_usd / price
    if direction == 'SELL_SIGNAL':
        stop_loss = price * 1.015    # 1.5% stop above entry (short)
        take_profit = price * 0.970  # 3.0% target below entry (short)
    else:
        stop_loss = price * 0.985    # 1.5% stop below entry (long)
        take_profit = price * 1.030  # 3.0% target above entry (long)
    return {
        'position_usd': round(position_usd, 2),
        'quantity': round(quantity, 6),
        'stop_loss': round(stop_loss, 4),
        'take_profit': round(take_profit, 4)
    }


def _to_ccxt_symbol(ticker: str) -> str:
    """Kraken pair ticker (e.g. 'SOLUSD', 'MATICUSDT') -> ccxt symbol ('SOL/USD')."""
    if ticker.endswith('USDT'):
        return ticker[:-4] + '/USDT'
    if ticker.endswith('USD'):
        return ticker[:-3] + '/USD'
    return ticker[:3] + '/' + ticker[3:]


def get_current_price(ticker: str) -> float:
    """REST ticker fetch for position monitoring — separate from the
    websocket stream, since a symbol only streams while it's on the
    scanner's watchlist and an open position can outlive that."""
    exchange = get_exchange()
    return float(exchange.fetch_ticker(_to_ccxt_symbol(ticker))['last'])


def check_exit(trade: dict, current_price: float) -> str:
    """Direction-aware stop/target check against a polled price. Stop wins
    if both are crossed in the same check window (conservative tie-break).

    NOTE: this is estimated-fill detection from our own polled price feed,
    not a broker-confirmed fill — that distinction (reconciliation against
    exchange/broker truth) is a deliberate later phase, not implemented
    here. See docs/blueprint-reference.md #2.
    """
    stop_loss = trade['stop_loss']
    take_profit = trade['take_profit']

    if trade['direction'] == 'SELL_SIGNAL':
        if current_price >= stop_loss:
            return 'stop_loss'
        if current_price <= take_profit:
            return 'take_profit'
    else:
        if current_price <= stop_loss:
            return 'stop_loss'
        if current_price >= take_profit:
            return 'take_profit'
    return None


def close_trade(trade: dict, exit_price: float, reason: str) -> None:
    """Mutate an open trade record into a closed one and persist. Estimated
    fill only — see check_exit's docstring."""
    global paper_balance

    direction_sign = -1 if trade['direction'] == 'SELL_SIGNAL' else 1
    pnl_usd = (exit_price - trade['price']) * trade['quantity'] * direction_sign
    pnl_pct = round((pnl_usd / trade['position_usd']) * 100, 2) if trade.get('position_usd') else 0.0

    trade['status'] = 'CLOSED'
    trade['close_reason'] = reason
    trade['exit_price'] = round(exit_price, 4)
    trade['close_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    trade['pnl_usd'] = round(pnl_usd, 2)
    trade['pnl_pct'] = pnl_pct
    trade['fill_type'] = 'estimated'  # our own price feed, not broker-confirmed

    paper_balance += pnl_usd
    save_paper_trades()

    print(f"""
    🔒 PAPER TRADE CLOSED ({reason}):
    ├── Ticker:    {trade['ticker']}
    ├── Exit:      ${exit_price:,.4f}
    ├── P&L:       ${pnl_usd:,.2f} ({pnl_pct}%)
    └── Balance:   ${paper_balance:.2f}
    """)


def check_open_positions() -> None:
    """Called on its own interval by core/position_monitor.py, independent
    of the scanner/stream cadence — see position_check_interval_seconds in
    crypto_bot/adapter.py."""
    from core.position_monitor import monitor_open_positions
    monitor_open_positions(paper_trades, get_current_price, check_exit, close_trade)

def execute_paper_trade(decision: str, state_matrix: dict) -> dict:
    """Execute a paper trade — validates with Kraken but doesn't submit"""
    global paper_balance

    if 'EXECUTE: TRUE' not in decision:
        print(f"[*] Trade skipped — Claude said: {decision}")
        return {'executed': False, 'reason': decision}

    ticker = state_matrix['ticker']
    direction = state_matrix['quant_trigger']['direction']
    price = state_matrix['quant_trigger']['price_at_trigger']

    # Tier-aware sizing from the risk agent (core/agents/risk_agent.py) is
    # authoritative when present — this is what makes blue_chip/established/
    # speculative sizing actually reach a trade instead of staying
    # display-only in the Claude brief. Falls back to the flat-rate position
    # only if evaluate_risk() wasn't run ahead of this call (e.g. a direct/
    # test invocation bypassing the orchestrator).
    risk_position = state_matrix.get('risk_evaluation', {}).get('position')
    if risk_position:
        position = {
            'position_usd': risk_position['position_usd'],
            'quantity': risk_position['quantity'],
            'stop_loss': risk_position['stop_loss_price'],
            'take_profit': risk_position['take_profit_price'],
        }
    else:
        position = calculate_position(price, paper_balance, direction)

    trade_record = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'ticker': ticker,
        'direction': direction,
        'price': price,
        'position_usd': position['position_usd'],
        'quantity': position['quantity'],
        'stop_loss': position['stop_loss'],
        'take_profit': position['take_profit'],
        'paper_balance_before': round(paper_balance, 2),
        'status': 'OPEN',
        'validated': False
    }

    if PAPER_TRADING:
        try:
            exchange = get_exchange()
            symbol = _to_ccxt_symbol(ticker)

            # Use validate=True — Kraken checks the order but doesn't execute
            order = exchange.create_order(
                symbol=symbol,
                type='market',
                side='buy' if 'BUY' in direction else 'sell',
                amount=position['quantity'],
                params={'validate': True}
            )
            trade_record['validated'] = True
            trade_record['order_id'] = order.get('id', 'validated')
            print(f"✅ Paper trade VALIDATED by Kraken:")

        except Exception as e:
            error_msg = str(e)
            # Kraken validate=True returns an error-like response but it's expected
            if 'validate' in error_msg.lower() or 'EOrder' in error_msg:
                trade_record['validated'] = True
                print(f"✅ Paper trade VALIDATED (expected validate response)")
            else:
                trade_record['validated'] = False
                trade_record['error'] = error_msg
                print(f"⚠️ Validation note: {error_msg}")

    # Log the paper trade
    paper_trades.append(trade_record)
    save_paper_trades()

    print(f"""
    📋 PAPER TRADE LOGGED:
    ├── Ticker:    {ticker}
    ├── Direction: {direction}
    ├── Price:     ${price:,.4f}
    ├── Size:      ${position['position_usd']} ({position['quantity']} units)
    ├── Stop Loss: ${position['stop_loss']}
    ├── Take Profit: ${position['take_profit']}
    └── Balance:   ${paper_balance:.2f}
    """)

    return trade_record

def save_paper_trades():
    """Save paper trades to log file"""
    os.makedirs('logs', exist_ok=True)
    with open(LOG_FILE, 'w') as f:
        json.dump({
            'summary': {
                'total_trades': len(paper_trades),
                'starting_balance': PORTFOLIO_BALANCE,
                'current_balance': round(paper_balance, 2),
                'return_pct': round(
                    (paper_balance - PORTFOLIO_BALANCE) / PORTFOLIO_BALANCE * 100, 2
                )
            },
            'trades': paper_trades
        }, f, indent=2)

def view_paper_performance():
    """Print current paper trading performance"""
    if not paper_trades:
        print("[*] No paper trades recorded yet")
        return

    wins = [t for t in paper_trades if t.get('pnl_usd', 0) > 0]
    total = len(paper_trades)

    print(f"""
    📊 PAPER TRADING PERFORMANCE
    ═══════════════════════════════
    Total trades:    {total}
    Open trades:     {len([t for t in paper_trades if t['status'] == 'OPEN'])}
    Balance:         ${paper_balance:.2f}
    Starting:        ${PORTFOLIO_BALANCE:.2f}
    Return:          {round((paper_balance - PORTFOLIO_BALANCE) / PORTFOLIO_BALANCE * 100, 2)}%
    ═══════════════════════════════
    """)

if __name__ == "__main__":
    view_paper_performance()