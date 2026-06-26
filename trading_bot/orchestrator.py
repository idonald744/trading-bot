import json
import os
from typing import TypedDict, Optional
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END

from agents.sentiment_agent import get_sentiment
from agents.rag_agent import query_textbooks
from agents.risk_agent import evaluate_risk
from trigger_log import log_trigger
from execution import execute_paper_trade

load_dotenv()

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

# ==========================================
# AGENT NODES
# ==========================================
def sentiment_node(state: TradingState) -> TradingState:
    print("\n[1/4] 🔍 Running Sentiment Agent...")
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
    matrix = state['state_matrix']
    
    risk = evaluate_risk(matrix)
    matrix['risk_evaluation'] = risk
    state['risk_result'] = risk
    
    print(f"     Approved: {risk['approved']} | "
          f"Reason: {risk['reason']}")
    
    if risk['approved'] and risk['position']:
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

    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    brief = f"""You are a senior risk officer for a crypto trading firm.
Review this trade setup and make a final decision.

TRADE SETUP:
- Ticker: {matrix['ticker']}
- Direction: {matrix['quant_trigger']['direction']}
- Price: ${matrix['quant_trigger']['price_at_trigger']}
- RSI: {matrix['market_metrics']['rsi_14']}
- MACD: {matrix['market_metrics']['macd_line']}
- Volume Spike: {matrix['market_metrics']['volume_spike']}

SENTIMENT ANALYSIS:
- Score: {sentiment['sentiment_score']}/10
- Smart Money: {sentiment['smart_money_signal']}
- Retail Signal: {sentiment['retail_signal']}
- Trap Warning: {sentiment['trap_warning']}
- Key Narratives: {sentiment['key_narratives'][:2]}

TEXTBOOK VALIDATION:
- Validated: {rag['validated']}
- Sources: {rag['sources']}
- Recommendation: {rag['recommendation']}
- Methodology: {rag['methodology'][:200]}

RISK ASSESSMENT:
- Approved: {risk['approved']}
- Reason: {risk['reason']}
- Position Size: ${risk['position']['position_usd'] if risk['position'] else 'N/A'}
- Stop Loss: ${risk['position']['stop_loss_price'] if risk['position'] else 'N/A'}
- Take Profit: ${risk['position']['take_profit_price'] if risk['position'] else 'N/A'}

Respond with ONLY one of these two formats:
EXECUTE: TRUE
or
EXECUTE: FALSE
REASON: [one sentence explanation]"""

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
        # Fallback if API fails
        decision = "EXECUTE: TRUE" if (
            rag['validated'] and
            risk['approved'] and
            4 <= sentiment['sentiment_score'] <= 8
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
        state['final_decision'] = f"EXECUTE: FALSE\nREASON: {state['risk_result']['reason']}"
        return "abort"
    if not state['rag_result']['validated']:
        state['abort_reason'] = "Textbook validation failed"
        state['final_decision'] = "EXECUTE: FALSE\nREASON: Setup not confirmed by trading literature"
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
def route_to_orchestrator(state_matrix: dict) -> dict:
    print("\n" + "="*50)
    print(f"🚨 ORCHESTRATOR ACTIVATED: {state_matrix['ticker']}")
    print(f"   Signal: {state_matrix['quant_trigger']['direction']}")
    print(f"   Price: ${state_matrix['quant_trigger']['price_at_trigger']}")
    print("="*50)

    graph = build_graph()

    initial_state = TradingState(
        state_matrix=state_matrix,
        sentiment_result={},
        rag_result={},
        risk_result={},
        final_decision="",
        abort_reason=""
    )

    result = graph.invoke(initial_state)

    # Log the final enriched state matrix
    log_trigger(result['state_matrix'])

    # Execute paper trade
    execute_paper_trade(
        result['final_decision'],
        result['state_matrix']
    )

    print("\n" + "="*50)
    print(f"FINAL DECISION: {result['final_decision']}")
    print("="*50 + "\n")

    return result

if __name__ == "__main__":
    # Test with a sample trigger
    test_matrix = {
        "session_id": "test_001",
        "timestamp": "2026-06-24 01:00:00",
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

    result = route_to_orchestrator(test_matrix)