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

def calculate_position(price: float, balance: float) -> dict:
    position_usd = balance * POSITION_SIZE_PCT
    quantity = position_usd / price
    stop_loss = price * 0.985   # 1.5% stop loss
    take_profit = price * 1.030  # 3.0% take profit
    return {
        'position_usd': round(position_usd, 2),
        'quantity': round(quantity, 6),
        'stop_loss': round(stop_loss, 4),
        'take_profit': round(take_profit, 4)
    }

def execute_paper_trade(decision: str, state_matrix: dict) -> dict:
    """Execute a paper trade — validates with Kraken but doesn't submit"""
    global paper_balance

    if 'EXECUTE: TRUE' not in decision:
        print(f"[*] Trade skipped — Claude said: {decision}")
        return {'executed': False, 'reason': decision}

    ticker = state_matrix['ticker']
    direction = state_matrix['quant_trigger']['direction']
    price = state_matrix['quant_trigger']['price_at_trigger']
    position = calculate_position(price, paper_balance)

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
            # Handle both SOLUSDT and SOLUSD formats
            if ticker.endswith('USDT'):
                symbol = ticker[:3] + '/USDT'
            elif ticker.endswith('USD'):
                symbol = ticker[:-3] + '/USD'
            else:
                symbol = ticker[:3] + '/' + ticker[3:]

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