"""
Read-only dashboard over the bots' JSON logs — no connection to the running
bot processes required. Run with: streamlit run dashboard/app.py
"""
import streamlit as st

from data_loaders import load_triggers, load_all_paper_trades
from views import render_overview, render_decision_trail, render_paper_trades

st.set_page_config(page_title="Trading Bot Dashboard", layout="wide")
st.title("Trading Bot Dashboard")

if st.sidebar.button("Refresh"):
    st.rerun()

triggers_df = load_triggers()
trades_df, balances = load_all_paper_trades()

tab_overview, tab_decisions, tab_trades = st.tabs(
    ["Overview", "Decision Trail", "Paper Trades"]
)

with tab_overview:
    render_overview(triggers_df)

with tab_decisions:
    render_decision_trail(triggers_df)

with tab_trades:
    render_paper_trades(trades_df, balances)
