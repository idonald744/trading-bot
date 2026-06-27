import json
import os
import sys
from typing import TypedDict
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END

load_dotenv()

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ==========================================
# STATE DEFINITION
# ==========================================
class TradingState(TypedDict):
    state_matrix: dict
    sentiment_result: dict
    rag_result: dict
    risk_result: dict
    final_decision: str
    abort_reason: str
    prompt_type: str

# ==========================================
# AGENT NODES
# ==========================================
def sentiment_node(state: TradingState) -> TradingState:
    print("\n[1/4] 🔍 Running Sentiment Agent...")
    from core.agents.sentiment_agent import get_sentiment

    matrix = state['state_matrix']
    sentiment = get_sentiment(
        matrix['ticker'],
        matrix['quant_trigger']['direction']
    )
    matrix['sentiment'] = sentiment
    state['sentiment_result'] = sentiment

    print(f"     Score: {sentiment['sentiment_score']}/10 | "
          f"Confidence: {sentiment['confidence']} | "
          f"Trap: {sentiment['trap_warning']}")
    return state

def rag_node(state: TradingState) -> TradingState:
    print("\n[2/4] 📚 Running RAG Textbook Agent...")
    from core.agents.rag_agent import query_textbooks

    matrix = state['state_matrix']
    rag = query_textbooks(
        setup=matrix['quant_trigger']['indicator_setup'],
        ticker=matrix['ticker'],
        rsi=matrix['market_metrics']['rsi_14'],
        direction=matrix['quant_trigger']['direction']
    )
    matrix['rag_validation'] = rag
    state['rag_result'] = rag

    print(f"     Validated: {rag['validated']} | "
          f"Sources: {rag['sources']} | "
          f"Recommendation: {rag['recommendation']}")
    return state

def risk_node(state: TradingState) -> TradingState:
    print("\n[3/4] 🛡️ Running Risk Agent...")
    prompt_type = state.get('prompt_type', 'crypto')
    if prompt_type == 'stock':
        from stock_bot.risk_agent import evaluate_stock_risk as evaluate_risk
    else:
        from core.agents.risk_agent import evaluate_risk

    matrix = state['state_matrix']
    risk = evaluate_risk(matrix)
    matrix['risk_evaluation'] = risk
    state['risk_result'] = risk

    print(f"     Approved: {risk['approved']} | "
          f"Reason: {risk['reason']}")

    if risk['approved'] and risk.get('position'):
        pos = risk['position']
        print(f"     Position: ${pos['position_usd']} | "
              f"SL: ${pos['stop_loss_price']} | "
              f"TP: ${pos['take_profit_price']}")
    return state

def claude_gatekeeper_node(state: TradingState) -> TradingState:
    print("\n[4/4] 🤖 Running Claude Sonnet Gatekeeper...")

    matrix = state['state_matrix']
    sentiment = state['sentiment_result']
    rag = state['rag_result']
    risk = state['risk_result']
    prompt_type = state.get('prompt_type', 'crypto')

    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    # Select prompt based on bot type
    if prompt_type == 'stock':
        from stock_bot.prompts import get_stock_prompt
        brief = get_stock_prompt(matrix, sentiment, rag, risk)
    else:
        from crypto_bot.prompts import get_crypto_prompt
        brief = get_crypto_prompt(matrix, sentiment, rag, risk)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content": brief}]
        )
        decision = response.content[0].text.strip()
    except Exception as e:
        print(f"[!] Claude API error: {e}")
        print("[*] Falling back to rule-based decision...")
        decision = "EXECUTE: TRUE" if (
            rag.get('validated') and
            risk.get('approved') and
            4 <= sentiment.get('sentiment_score', 5) <= 8
        ) else "EXECUTE: FALSE\nREASON: Fallback rules failed"

    state['final_decision'] = decision
    matrix['final_decision'] = decision
    print(f"\n     Decision: {decision}")
    return state

# ==========================================
# ROUTING LOGIC
# ==========================================
def should_continue_after_risk(state: TradingState) -> str:
    if not state['risk_result']['approved']:
        state['abort_reason'] = state['risk_result']['reason']
        state['final_decision'] = (
            f"EXECUTE: FALSE\nREASON: {state['risk_result']['reason']}"
        )
        return "abort"
    if not state['rag_result']['validated']:
        state['abort_reason'] = "Textbook validation failed"
        state['final_decision'] = (
            "EXECUTE: FALSE\nREASON: Setup not confirmed by trading literature"
        )
        return "abort"
    return "claude_gatekeeper"

def abort_node(state: TradingState) -> TradingState:
    print(f"\n❌ TRADE ABORTED: {state.get('abort_reason', 'Unknown reason')}")
    return state

# ==========================================
# BUILD THE GRAPH
# ==========================================
def build_graph():
    workflow = StateGraph(TradingState)

    workflow.add_node("sentiment_agent", sentiment_node)
    workflow.add_node("rag_agent", rag_node)
    workflow.add_node("risk_agent", risk_node)
    workflow.add_node("claude_gatekeeper", claude_gatekeeper_node)
    workflow.add_node("abort", abort_node)

    workflow.set_entry_point("sentiment_agent")
    workflow.add_edge("sentiment_agent", "rag_agent")
    workflow.add_edge("rag_agent", "risk_agent")
    workflow.add_conditional_edges(
        "risk_agent",
        should_continue_after_risk,
        {
            "claude_gatekeeper": "claude_gatekeeper",
            "abort": "abort"
        }
    )
    workflow.add_edge("claude_gatekeeper", END)
    workflow.add_edge("abort", END)

    return workflow.compile()

# ==========================================
# MAIN ENTRY POINT
# ==========================================
def route_to_orchestrator(
    state_matrix: dict,
    prompt_type: str = 'crypto'
) -> dict:
    print("\n" + "="*50)
    print(f"🚨 ORCHESTRATOR ACTIVATED: {state_matrix['ticker']}")
    print(f"   Signal: {state_matrix['quant_trigger']['direction']}")
    print(f"   Price: ${state_matrix['quant_trigger']['price_at_trigger']}")
    print(f"   Type: {prompt_type.upper()}")
    print("="*50)

    # Inject prompt type into state matrix
    state_matrix['prompt_type'] = prompt_type

    graph = build_graph()

    initial_state = TradingState(
        state_matrix=state_matrix,
        sentiment_result={},
        rag_result={},
        risk_result={},
        final_decision="",
        abort_reason="",
        prompt_type=prompt_type
    )

    result = graph.invoke(initial_state)

    # Log trigger
    from core.trigger_log import log_trigger
    log_trigger(result['state_matrix'])

    print("\n" + "="*50)
    print(f"FINAL DECISION: {result['final_decision']}")
    print("="*50 + "\n")

    return result

# ==========================================
# TEST
# ==========================================
if __name__ == "__main__":
    test_matrix = {
        "session_id": "test_001",
        "timestamp": "2026-06-26 10:00:00",
        "ticker": "SOLUSD",
        "quant_trigger": {
            "direction": "BUY_SIGNAL",
            "indicator_setup": "RSI + MACD + Bollinger Confluence",
            "timeframe": "15m",
            "price_at_trigger": 69.58
        },
        "market_metrics": {
            "rsi_14": 28.5,
            "macd_line": 0.2196,
            "macd_signal": 0.2104,
            "volume_spike": True,
            "recent_volume": 74.71
        },
        "consensus": {
            "status": "AWAITING_AGENT_EVALUATION"
        }
    }

    result = route_to_orchestrator(test_matrix, prompt_type='crypto')