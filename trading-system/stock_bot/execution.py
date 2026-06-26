import os
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# PAPER TRADING CONFIGURATION
# ==========================================
PAPER_TRADING = True
PORTFOLIO_BALANCE = 1000.0
POSITION_SIZE_PCT = 0.03  # 3% for stocks (slightly higher than crypto)

paper_trades = []
paper_balance = PORTFOLIO_BALANCE

def get_alpaca_client():
    """Get Alpaca paper trading client"""
    from alpaca.trading.client import TradingClient
    return TradingClient(
        api_key=os.getenv('ALPACA_API_KEY'),
        secret_key=os.getenv('ALPACA_SECRET'),
        paper=True
    )

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
    """Execute a paper stock trade via Alpaca"""
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

    # Simulated paper trade execution
    if PAPER_TRADING:
        import uuid
        trade_record['order_id'] = str(uuid.uuid4())[:8]
        trade_record['validated'] = True
        trade_record['execution_type'] = 'SIMULATED'
        print(f"✅ Paper trade simulated: {trade_record['order_id']}")
        # TODO: Replace with IBKR execution when account approved

    paper_trades.append(trade_record)
    save_paper_trades()

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