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


def _paper_trade_row_style(row: pd.Series) -> list:
    """CLOSED rows shaded green (win) or red (loss); OPEN rows unshaded."""
    if row.get('status') == 'CLOSED':
        pnl = row.get('pnl_usd')
        if pnl is not None and pd.notna(pnl):
            if pnl > 0:
                return ['background-color: #c6efce; color: #006100'] * len(row)
            return ['background-color: #ffc7ce; color: #9c0006'] * len(row)
    return [''] * len(row)


def render_paper_trades(trades_df: pd.DataFrame, balances: dict):
    st.caption(
        "Status reflects real trade state. core/position_monitor.py polls open "
        "positions on their own cadence (independent of scan activity) and "
        "closes them with real P&L when a stop/target is crossed — CLOSED rows "
        "below are shaded green (win) or red (loss); OPEN rows are unshaded."
    )

    col1, col2 = st.columns(2)
    for col, market_label, display_name in [
        (col1, 'crypto', 'Crypto balance'),
        (col2, 'stock', 'Stock balance'),
    ]:
        summary = balances.get(market_label) or {}
        current_balance = summary.get('current_balance')
        if current_balance is None:
            col.metric(display_name, "—")
        else:
            return_pct = summary.get('return_pct')
            delta = f"{return_pct:+.2f}%" if return_pct is not None else None
            col.metric(display_name, f"${current_balance:,.2f}", delta)

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

    if 'status' in filtered.columns:
        st.dataframe(filtered.style.apply(_paper_trade_row_style, axis=1), use_container_width=True)
    else:
        st.dataframe(filtered, use_container_width=True)
