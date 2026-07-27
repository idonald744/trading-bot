"""Render functions for each dashboard tab. Each takes the DataFrames
produced by data_loaders.py and is safe to call with an empty DataFrame
(no triggers/trades logged yet, or the log file was unreadable this poll)."""
import streamlit as st
import pandas as pd

SUMMARY_COLUMNS = [
    'timestamp', 'market', 'ticker', 'direction', 'price',
    'executed', 'asset_tier', 'risk_approved', 'sentiment_score',
]


def render_overview(triggers_df: pd.DataFrame):
    if triggers_df.empty:
        st.info("No triggers logged yet.")
        return

    total = len(triggers_df)
    executed = int(triggers_df['executed'].sum())
    rejected = total - executed

    col1, col2, col3 = st.columns(3)
    col1.metric("Total triggers", total)
    col2.metric("Executed", executed)
    col3.metric("Rejected / aborted", rejected)

    st.subheader("Asset tier breakdown")
    tier_counts = triggers_df['asset_tier'].value_counts()
    st.bar_chart(tier_counts)

    st.subheader("Most recent triggers")
    recent = triggers_df.sort_values('timestamp', ascending=False).head(10)
    st.dataframe(recent[SUMMARY_COLUMNS], use_container_width=True)


def render_decision_trail(triggers_df: pd.DataFrame):
    if triggers_df.empty:
        st.info("No triggers logged yet.")
        return

    markets = sorted(triggers_df['market'].unique())
    selected_markets = st.multiselect("Market", markets, default=markets)
    filtered = triggers_df[triggers_df['market'].isin(selected_markets)]
    filtered = filtered.sort_values('timestamp', ascending=False)

    st.dataframe(filtered[SUMMARY_COLUMNS], use_container_width=True)

    if filtered.empty:
        return

    st.subheader("Trigger detail")
    labels = [f"{row.ticker} @ {row.timestamp}" for row in filtered.itertuples()]
    selected_label = st.selectbox("Select a trigger to inspect", labels)
    selected_idx = labels.index(selected_label)
    st.json(filtered.iloc[selected_idx]['_raw'])


def render_paper_trades(trades_df: pd.DataFrame):
    st.caption(
        "Status will always read 'OPEN' and balance figures reflect the "
        "starting paper balance only — position-closing and P&L tracking "
        "aren't implemented in the bots yet, so this view can't show "
        "realized gains/losses or a live balance."
    )

    if trades_df.empty:
        st.info("No paper trades logged yet.")
        return

    markets = sorted(trades_df['market'].unique())
    selected_markets = st.multiselect(
        "Market", markets, default=markets, key="trades_market_filter"
    )
    filtered = trades_df[trades_df['market'].isin(selected_markets)]
    if 'timestamp' in filtered.columns:
        filtered = filtered.sort_values('timestamp', ascending=False)

    st.dataframe(filtered, use_container_width=True)
