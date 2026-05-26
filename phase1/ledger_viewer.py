"""Live ledger viewer for the Phase 1 paper-trader.

Run alongside the paper-trader daemon to watch it operate in real time:

    uv run streamlit run phase1/ledger_viewer.py

Opens at http://localhost:8501. Auto-refreshes every 10 s.

What it shows:
  - Status: days running, observed trades, paper positions opened/closed
  - Headline: realised paper PnL vs the backtest prediction ($7–9 per $100)
  - Cumulative paper-PnL chart
  - Daily PnL bars
  - PnL contribution by followed wallet
  - Open positions table (current exposure)
  - Recent paper-trade decisions with the reason for each gate
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

PHASE1_DIR  = Path(__file__).parent
LEDGER_PATH = PHASE1_DIR / "ledger.sqlite"

BACKTEST_PER_100_LOWER = 6.50    # latency-decay clean-subset @ 5 s
BACKTEST_PER_100_UPPER = 9.40    # Jan 2026 out-of-sample headline
BET_SIZE_USD = 100.0


st.set_page_config(
    page_title="Phase 1 — Paper Trader Ledger",
    page_icon="📒",
    layout="wide",
)


# ---- Helpers ----------------------------------------------------------- #
@st.cache_data(ttl=5)
def _read_table(sql: str, _bust: float) -> pd.DataFrame:
    if not LEDGER_PATH.exists():
        return pd.DataFrame()
    with sqlite3.connect(str(LEDGER_PATH)) as db:
        return pd.read_sql(sql, db)


def fmt_money(v: float) -> str:
    sign = "−" if v < 0 else ""
    v = abs(v)
    if v >= 1_000_000:
        return f"{sign}${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"{sign}${v/1_000:.1f}k"
    return f"{sign}${v:,.0f}"


# ---- Header ------------------------------------------------------------ #
st.title("📒 Phase 1 — Paper Trader Ledger")

if not LEDGER_PATH.exists():
    st.warning(
        f"No ledger found at `{LEDGER_PATH}`. Start the paper trader with "
        "`uv run python phase1/paper_trader.py` and refresh."
    )
    st.stop()

col_refresh, col_ts, col_path = st.columns([1, 2, 4])
with col_refresh:
    auto_refresh = st.checkbox("Auto-refresh", value=True)
with col_ts:
    st.caption(f"Last reload: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
with col_path:
    st.caption(f"Ledger file: `{LEDGER_PATH}`")

# Bust cache periodically so queries re-execute on rerun
bust = time.time() // 5

obs   = _read_table("SELECT * FROM observed_trade ORDER BY timestamp_unix DESC", bust)
paper = _read_table("SELECT * FROM paper_position ORDER BY opened_at_unix DESC", bust)
mkt   = _read_table("SELECT * FROM market_meta", bust)
state = _read_table("SELECT * FROM poll_state", bust)
daily = _read_table("SELECT * FROM daily_pnl ORDER BY day", bust)


# ---- Headline KPIs ---------------------------------------------------- #
n_obs       = len(obs)
n_open      = (paper["status"] == "open").sum() if not paper.empty else 0
n_closed    = (paper["status"] == "closed").sum() if not paper.empty else 0
realised    = (paper.loc[paper["status"] == "closed", "realised_pnl_usd"]).sum() if not paper.empty else 0.0
n_wins      = ((paper["status"] == "closed") & (paper["realised_pnl_usd"] > 0)).sum() if not paper.empty else 0
hit_rate    = n_wins / n_closed if n_closed else 0.0
pnl_per_100 = realised / n_closed if n_closed else 0.0
open_expo   = paper.loc[paper["status"] == "open", "bet_usd"].sum() if not paper.empty else 0.0

# Daemon's first poll
if not state.empty:
    first_poll = state["last_seen_unix"].min()
    days_running = (time.time() - first_poll) / 86400
else:
    days_running = 0

# Header KPIs in a row
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Days running",         f"{days_running:.1f}" if days_running >= 1 else f"{days_running*24:.1f}h")
k2.metric("Observed trades",      f"{n_obs:,}")
k3.metric("Open paper positions", f"{n_open}")
k4.metric("Closed positions",     f"{n_closed}")
k5.metric("Realised PnL",         fmt_money(realised),
          delta=f"{pnl_per_100:+.2f} / $100 bet" if n_closed else None)
k6.metric("Hit rate (closed)",    f"{hit_rate:.1%}" if n_closed else "—")


# ---- Backtest-vs-live comparison ------------------------------------- #
st.markdown("---")
st.subheader("Live vs backtest")
if n_closed == 0:
    st.info("No closed positions yet — comparison appears once the paper trader has executed its first mirror-exit.")
else:
    c1, c2, c3 = st.columns(3)
    c1.metric("Backtest target (in-sample)",   f"${BACKTEST_PER_100_UPPER:.2f} / $100 bet")
    c1.caption("Per Phase 0 latency-decay analysis at 5 s")
    c2.metric("Backtest target (out-of-sample)", f"${BACKTEST_PER_100_LOWER:.2f} / $100 bet")
    c2.caption("Jan 2026 OOS subset")
    c3.metric("Live paper achieving",          f"${pnl_per_100:+.2f} / $100 bet")
    if pnl_per_100 >= BACKTEST_PER_100_LOWER:
        c3.caption("✅ Within backtest range")
    elif pnl_per_100 > 0:
        c3.caption("⚠️  Positive but below target — small-sample variance?")
    else:
        c3.caption("🚨 Negative — review trades and gates")


# ---- Cumulative PnL chart -------------------------------------------- #
st.markdown("---")
st.subheader("Cumulative paper PnL")
if n_closed >= 2:
    p = paper.loc[paper["status"] == "closed",
                  ["closed_at_unix", "realised_pnl_usd"]].sort_values("closed_at_unix")
    p["t"] = pd.to_datetime(p["closed_at_unix"], unit="s", utc=True)
    p["cum"] = p["realised_pnl_usd"].cumsum()
    chart = (
        alt.Chart(p)
        .mark_area(opacity=0.2, color="#1d4ed8")
        .encode(x=alt.X("t:T", title=None),
                y=alt.Y("cum:Q", title="Cumulative PnL ($)"))
        + alt.Chart(p)
            .mark_line(color="#1d4ed8", strokeWidth=2)
            .encode(x="t:T", y="cum:Q")
    )
    st.altair_chart(chart, use_container_width=True)
else:
    st.caption("Cumulative chart appears after the second closed position.")


# ---- Daily PnL --------------------------------------------------------- #
if not daily.empty and (daily["realised_usd"] != 0).any():
    st.subheader("Daily PnL")
    daily["color"] = daily["realised_usd"].apply(lambda v: "#16a34a" if v >= 0 else "#dc2626")
    chart_d = alt.Chart(daily).mark_bar().encode(
        x=alt.X("day:O", title=None),
        y=alt.Y("realised_usd:Q", title="Realised PnL ($)"),
        color=alt.Color("color:N", scale=None, legend=None),
        tooltip=["day", alt.Tooltip("realised_usd:Q", format="$.2f")],
    )
    st.altair_chart(chart_d, use_container_width=True)


# ---- Per-wallet contribution ---------------------------------------- #
if n_closed > 0:
    st.subheader("PnL contribution by followed wallet")
    by_wallet = (
        paper.loc[paper["status"] == "closed"]
        .merge(obs[["trade_id", "proxy_wallet"]], on="trade_id", how="left")
        .groupby("proxy_wallet", as_index=False)
        .agg(n=("paper_id", "count"),
             pnl=("realised_pnl_usd", "sum"),
             mean_pnl=("realised_pnl_usd", "mean"))
        .sort_values("pnl", ascending=False)
    )
    by_wallet["wallet"] = by_wallet["proxy_wallet"].str[:12] + "…"
    chart_w = alt.Chart(by_wallet).mark_bar().encode(
        x=alt.X("pnl:Q", title="Realised PnL ($)"),
        y=alt.Y("wallet:N", title=None, sort="-x"),
        color=alt.Color(
            "pnl:Q",
            scale=alt.Scale(domain=[-100, 0, 100], range=["#dc2626", "#cbd5e1", "#16a34a"]),
            legend=None,
        ),
        tooltip=["proxy_wallet", "n", alt.Tooltip("pnl:Q", format="$.2f"),
                 alt.Tooltip("mean_pnl:Q", format="$.2f", title="Mean / trade")],
    )
    st.altair_chart(chart_w, use_container_width=True)


# ---- Open positions -------------------------------------------------- #
st.markdown("---")
col_open, col_obs = st.columns(2)
with col_open:
    st.subheader(f"Open positions ({n_open})")
    open_df = paper.loc[paper["status"] == "open"].copy()
    if not open_df.empty:
        open_df["opened"] = pd.to_datetime(open_df["opened_at_unix"], unit="s", utc=True)
        st.dataframe(
            open_df[["opened", "title", "side", "entry_price", "shares", "bet_usd"]]
              .rename(columns={"opened": "Opened (UTC)", "title": "Market", "side": "Side",
                               "entry_price": "Entry", "shares": "Shares", "bet_usd": "Bet $"}),
            hide_index=True, use_container_width=True,
            column_config={
                "Entry":  st.column_config.NumberColumn(format="$%.3f"),
                "Shares": st.column_config.NumberColumn(format="%.2f"),
                "Bet $":  st.column_config.NumberColumn(format="$%.0f"),
            },
        )
        st.caption(f"Total exposure: {fmt_money(open_expo)}")
    else:
        st.caption("None.")

with col_obs:
    st.subheader("Recent observations (last 25)")
    if not obs.empty:
        recent = obs.head(25).copy()
        recent["t"] = pd.to_datetime(recent["timestamp_unix"], unit="s", utc=True)
        recent["short_addr"] = recent["proxy_wallet"].str[:10] + "…"
        st.dataframe(
            recent[["t", "short_addr", "side", "price", "size", "title"]]
              .rename(columns={"t": "Time (UTC)", "short_addr": "Wallet",
                               "side": "Side", "price": "Price", "size": "Size",
                               "title": "Market"}),
            hide_index=True, use_container_width=True,
            column_config={
                "Price": st.column_config.NumberColumn(format="$%.3f"),
                "Size":  st.column_config.NumberColumn(format="%.2f"),
            },
        )
    else:
        st.caption("No trades observed yet. Waiting for watchlist activity…")


# ---- Closed positions ------------------------------------------------ #
if n_closed > 0:
    st.markdown("---")
    st.subheader(f"Closed positions ({n_closed})")
    closed_df = paper.loc[paper["status"] == "closed"].copy()
    closed_df["opened"] = pd.to_datetime(closed_df["opened_at_unix"], unit="s", utc=True)
    closed_df["closed"] = pd.to_datetime(closed_df["closed_at_unix"], unit="s", utc=True)
    closed_df["hold_minutes"] = (closed_df["closed_at_unix"] - closed_df["opened_at_unix"]) / 60
    st.dataframe(
        closed_df.sort_values("closed_at_unix", ascending=False)[
            ["opened", "closed", "hold_minutes", "title", "side", "entry_price",
             "realised_pnl_usd", "close_reason"]
        ].rename(columns={
            "opened": "Opened", "closed": "Closed", "hold_minutes": "Hold (min)",
            "title": "Market", "side": "Side", "entry_price": "Entry",
            "realised_pnl_usd": "PnL ($)", "close_reason": "Reason",
        }),
        hide_index=True, use_container_width=True,
        column_config={
            "Hold (min)": st.column_config.NumberColumn(format="%.1f"),
            "Entry":      st.column_config.NumberColumn(format="$%.3f"),
            "PnL ($)":    st.column_config.NumberColumn(format="$%+,.2f"),
        },
    )


# ---- Auto-refresh ---------------------------------------------------- #
if auto_refresh:
    time.sleep(10)
    st.rerun()
