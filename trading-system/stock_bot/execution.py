import os
import sys
import json
from datetime import datetime
from dotenv import load_dotenv

from stock_bot import moomoo_client

load_dotenv()

# Windows consoles can default to a non-UTF-8 codepage (e.g. cp1252), which
# raises UnicodeEncodeError on the emoji/box-drawing characters used in the
# print statements below. Make stdout/stderr tolerant so a console encoding
# mismatch can't crash trade execution or logging.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(errors='replace')

# ==========================================
# PAPER TRADING CONFIGURATION
# ==========================================
PAPER_TRADING = True
PORTFOLIO_BALANCE = 1000.0
POSITION_SIZE_PCT = 0.03  # 3% for stocks (slightly higher than crypto)

paper_trades = []
paper_balance = PORTFOLIO_BALANCE

def calculate_position(price: float, stop_loss: float, balance: float) -> dict:
    """Calculate position size based on structural stop distance"""
    max_risk_usd = balance * POSITION_SIZE_PCT * 0.015  # Risk 1.5% of position
    stop_distance_pct = abs(price - stop_loss) / price
    
    if stop_distance_pct == 0:
        stop_distance_pct = 0.03  # Default 3% stop
    
    position_usd = min(
        max_risk_usd / stop_distance_pct,
        balance * POSITION_SIZE_PCT
    )
    
    quantity = position_usd / price
    target_1 = price * (1 + (stop_distance_pct * 3))  # 3:1 RR minimum
    target_2 = price * (1 + (stop_distance_pct * 5))  # 5:1 stretch target

    return {
        'position_usd': round(position_usd, 2),
        'quantity': round(quantity, 4),
        'stop_loss': round(stop_loss, 4),
        'target_1': round(target_1, 4),
        'target_2': round(target_2, 4),
        'risk_usd': round(position_usd * stop_distance_pct, 2),
        'reward_usd_t1': round(position_usd * stop_distance_pct * 3, 2),
        'reward_usd_t2': round(position_usd * stop_distance_pct * 5, 2),
        'risk_reward_ratio': '3:1 min / 5:1 stretch'
    }

def execute_paper_trade(decision: str, state_matrix: dict) -> dict:
    """Execute a paper stock trade via Moomoo (TrdEnv.SIMULATE)"""
    global paper_balance

    if 'EXECUTE: TRUE' not in decision:
        print(f"[*] Trade skipped — {decision}")
        return {'executed': False, 'reason': decision}

    ticker = state_matrix['ticker']
    direction = state_matrix['quant_trigger']['direction']
    price = state_matrix['quant_trigger']['price_at_trigger']
    risk = state_matrix.get('risk_evaluation', {})
    position = risk.get('position', {})

    if not position:
        position = calculate_position(price, price * 0.97, paper_balance)

    trade_record = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'ticker': ticker,
        'direction': direction,
        'price': price,
        'position_usd': position.get('position_usd', 0),
        'quantity': position.get('quantity', 0),
        'stop_loss': position.get('stop_loss', 0),
        'target_1': position.get('target_1', 0),
        'target_2': position.get('target_2', 0),
        'risk_usd': position.get('risk_usd', 0),
        'catalyst': state_matrix.get('catalyst', {}),
        'status': 'OPEN',
        'paper_balance_before': round(paper_balance, 2)
    }

    # Moomoo SIMULATE execution — gated by PAPER_TRADING here and by
    # TrdEnv.SIMULATE hardcoded in moomoo_client.py; both must hold.
    if PAPER_TRADING:
        quantity = position.get('quantity', 0)

        if quantity <= 0:
            result = {'ret_ok': False, 'reason': 'Degenerate position size (quantity <= 0)'}
        else:
            try:
                ctx = moomoo_client.get_moomoo_context()
                try:
                    result = moomoo_client.place_order(
                        ctx, ticker=ticker, direction=direction,
                        quantity=quantity, price=price,
                    )
                finally:
                    ctx.close()
            except Exception as e:
                result = {'ret_ok': False, 'reason': str(e)}

        if result.get('ret_ok'):
            trade_record['order_id'] = result['order_id']
            trade_record['order_status'] = result.get('order_status')
            trade_record['validated'] = True
            trade_record['execution_type'] = 'MOOMOO_SIMULATE'
        else:
            trade_record['status'] = 'REJECTED'
            trade_record['validated'] = False
            trade_record['execution_type'] = 'MOOMOO_SIMULATE'
            trade_record['reason'] = result.get('reason', result.get('raw'))

    # Persist before any display/print code runs — a print failure must never
    # prevent, or appear to roll back, logging of a trade that already happened.
    paper_trades.append(trade_record)
    save_paper_trades()

    try:
        if trade_record.get('validated'):
            print(f"[OK] Moomoo SIMULATE order placed: {trade_record['order_id']}")
        elif trade_record.get('status') == 'REJECTED':
            print(f"[!] Moomoo order rejected for {ticker}: {trade_record.get('reason')}")

        print(f"""
    📋 STOCK PAPER TRADE LOGGED:
    ├── Ticker:    {ticker}
    ├── Direction: {direction}
    ├── Price:     ${price:,.4f}
    ├── Size:      ${position.get('position_usd', 0)} ({position.get('quantity', 0)} shares)
    ├── Stop Loss: ${position.get('stop_loss', 0)}
    ├── Target 1:  ${position.get('target_1', 0)}
    ├── Target 2:  ${position.get('target_2', 0)}
    ├── Risk:      ${position.get('risk_usd', 0)}
    └── Balance:   ${paper_balance:.2f}
    """)
    except Exception:
        pass  # display-only; the trade is already durably logged above

    return trade_record

def save_paper_trades():
    """Save paper trades to log"""
    os.makedirs('logs', exist_ok=True)
    with open('logs/stock_paper_trades.json', 'w') as f:
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

def view_performance():
    """Print paper trading performance"""
    print(f"""
    📊 STOCK PAPER TRADING PERFORMANCE
    ═══════════════════════════════════
    Total trades:  {len(paper_trades)}
    Balance:       ${paper_balance:.2f}
    Starting:      ${PORTFOLIO_BALANCE:.2f}
    Return:        {round((paper_balance - PORTFOLIO_BALANCE) / PORTFOLIO_BALANCE * 100, 2)}%
    ═══════════════════════════════════
    """)

if __name__ == "__main__":
    view_performance()