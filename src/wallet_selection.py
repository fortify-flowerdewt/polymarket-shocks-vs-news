"""Phase 0 — Wallet selection for the copy-trading backtest.

Reads `data/user_pnl_summary.parquet` (PnL per wallet, 2.48 M wallets) and
`data/user_features.parquet` (87 behavioural features per wallet), then ranks
candidates by *taker-driven* PnL after a series of quality filters.

Why taker-driven? Akey, Grégoire, Harvie & Martineau (2026) show that the
top 1% of profitable wallets are dominated by **liquidity providers** who
earn the spread on patient limit orders. A copy-trading bot cannot
practically follow a maker — by the time we see their fill, the spread is
already gone. The strategy only works for takers: wallets that *cross*
the spread on directional bets that pay off.

Output: `output/watchlist.parquet` — one row per candidate wallet,
sorted by an `edge_score` that combines absolute PnL, taker share,
diversity, and recency.
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta

import polars as pl

DATA_DIR = Path("/Users/tom/projects/polymarketFraud/data")
OUTPUT_DIR = Path("/Users/tom/projects/polymarketFraud/output")


# Category to focus on. None = all categories. Set to "sports" / "politics"
# / etc. to rank wallets by category-specific PnL and require they have
# traded that category.
import os
CATEGORY = os.environ.get("CATEGORY", "").lower() or None
SUFFIX   = f"_{CATEGORY}" if CATEGORY else ""

# Filter thresholds — tuned to retain wallets that have:
#   * a non-trivial bankroll on the line
#   * enough activity to be statistically distinguishable from luck
#   * exposure to multiple markets and counterparties (not single-event)
#   * predominantly TAKER (not maker) volume
#   * activity recent enough that they may still be active when we deploy
MIN_PNL_TOTAL          = 10_000     # $ realised + unrealised (category-specific if CATEGORY set)
MIN_PNL_RESOLVED       = 5_000      # $ resolved (settled markets only)
MIN_N_TRADES           = 50
MIN_N_MARKETS          = 10
MIN_N_COUNTERPARTIES   = 10
MAX_FRAC_MAKER_VOLUME  = 0.50       # ≤ 50 % maker — i.e. mostly a taker
MIN_TRADING_SPAN_DAYS  = 30         # avoid one-off lucky streaks
MIN_ACTIVE_DAYS        = 10
MAX_DAYS_SINCE_LAST    = 365        # active in the last year
# Variance filters — drop wallets whose PnL is dominated by tail bets.
# `frac_extreme_price` = share of trades at <10¢ or >90¢. The Akey paper
# uses the same threshold to identify lottery-style bettors.
MAX_FRAC_EXTREME_PRICE = 0.30       # ≤ 30 % of trades in the extreme tails
MAX_FRAC_LONGSHOT      = 0.15       # ≤ 15 % of volume on long-shot picks
MAX_VOLUME_GINI        = 0.85       # bet sizes not too concentrated in one trade
TOP_N_OUTPUT           = 500


def main() -> None:
    print(f"Loading user_pnl_summary…   (CATEGORY={CATEGORY or 'ALL'})")
    pnl_focus_col          = f"pnl{SUFFIX}"            if CATEGORY else "pnl_total"
    pnl_focus_resolved_col = f"pnl_resolved{SUFFIX}"   if CATEGORY else "pnl_resolved_total"
    pnl = pl.read_parquet(DATA_DIR / "user_pnl_summary.parquet").select([
        "user_address",
        "pnl_total",
        "pnl_resolved_total",
        pl.col(pnl_focus_col).alias("pnl_focus"),
        pl.col(pnl_focus_resolved_col).alias("pnl_focus_resolved"),
    ])

    print("Loading user_features…")
    feats = pl.read_parquet(DATA_DIR / "user_features.parquet").select([
        "user_address",
        "n_trades", "n_maker_trades", "n_markets", "n_events",
        "n_categories", "n_counterparties", "total_volume",
        "frac_maker", "frac_maker_volume",
        "first_trade_date", "last_trade_date", "active_days",
        "trading_span_days", "avg_trade_volume", "median_trade_volume",
        "category_hhi", "counterparty_hhi",
        "frac_longshot", "frac_sureshot", "frac_extreme_price",
        "volume_gini",
        "frac_held_to_resolution", "avg_holding_duration",
        "round_trip_rate", "trades_per_week",
        "traded_sports", "traded_crypto", "traded_finance",
        "traded_politics", "traded_tech", "traded_culture", "traded_weather",
    ])

    joined = pnl.join(feats, on="user_address", how="inner")
    print(f"Joined: {joined.shape[0]:,} wallets")

    # Apply filters in stages so we can see the funnel
    stages = [
        ("pnl_focus > $10k",          pl.col("pnl_focus") > MIN_PNL_TOTAL),
        ("pnl_focus_resolved > $5k",  pl.col("pnl_focus_resolved") > MIN_PNL_RESOLVED),
        ("n_trades >= 50",            pl.col("n_trades") >= MIN_N_TRADES),
        ("n_markets >= 10",           pl.col("n_markets") >= MIN_N_MARKETS),
        ("n_counterparties >= 10",    pl.col("n_counterparties") >= MIN_N_COUNTERPARTIES),
        ("frac_maker_volume <= 0.5",  pl.col("frac_maker_volume") <= MAX_FRAC_MAKER_VOLUME),
        ("trading_span_days >= 30",   pl.col("trading_span_days") >= MIN_TRADING_SPAN_DAYS),
        ("active_days >= 10",         pl.col("active_days") >= MIN_ACTIVE_DAYS),
        # Variance / tail-exposure filters
        ("frac_extreme_price <= 0.30", pl.col("frac_extreme_price") <= MAX_FRAC_EXTREME_PRICE),
        ("frac_longshot <= 0.15",      pl.col("frac_longshot") <= MAX_FRAC_LONGSHOT),
        ("volume_gini <= 0.85",        pl.col("volume_gini") <= MAX_VOLUME_GINI),
    ]
    if CATEGORY:
        # Require the wallet has actually traded the focus category.
        trad_col = f"traded_{CATEGORY}"
        if trad_col in joined.columns:
            stages.append((f"traded_{CATEGORY} == 1", pl.col(trad_col) == 1))
    cur = joined
    print("\nFilter funnel:")
    print(f"  start                                 {cur.shape[0]:>10,}")
    for label, expr in stages:
        cur = cur.filter(expr)
        print(f"  {label:<38s} {cur.shape[0]:>10,}")

    # Recency filter: require last trade within MAX_DAYS_SINCE_LAST of
    # the dataset's last observed activity (so we don't bias against
    # wallets that retired before March 2026).
    last_observed = cur.select(pl.col("last_trade_date").max()).item()
    cutoff = last_observed - timedelta(days=MAX_DAYS_SINCE_LAST)
    cur = cur.filter(pl.col("last_trade_date") >= cutoff)
    print(f"  active within {MAX_DAYS_SINCE_LAST}d of {last_observed}      {cur.shape[0]:>10,}")

    # ---- Edge score --------------------------------------------------------
    # Heuristic that combines:
    #  - taker-share-weighted realised PnL (the part we could actually copy)
    #  - per-trade efficiency (PnL / n_trades) — rewards selectivity
    #  - diversity bonus (low HHI on markets and counterparties)
    cur = cur.with_columns(
        (1 - pl.col("frac_maker_volume")).alias("frac_taker_volume"),
    )
    cur = cur.with_columns(
        (pl.col("pnl_focus_resolved") * pl.col("frac_taker_volume")).alias("taker_pnl_est"),
        (pl.col("pnl_focus_resolved") / pl.col("n_trades")).alias("pnl_per_trade"),
        # market/counterparty diversity — invert HHI (0 = max diversity)
        (1 - pl.col("market_hhi" if "market_hhi" in cur.columns else "category_hhi")).alias("market_diversity"),
        (1 - pl.col("counterparty_hhi")).alias("cp_diversity"),
    )
    # Standardize each component, then sum with weights
    def z(col):
        return (pl.col(col) - pl.col(col).mean()) / pl.col(col).std()

    cur = cur.with_columns(
        (
            0.50 * z("taker_pnl_est")
          + 0.25 * z("pnl_per_trade").fill_nan(0)
          + 0.15 * z("market_diversity").fill_nan(0)
          + 0.10 * z("cp_diversity").fill_nan(0)
        ).alias("edge_score")
    )

    cur = cur.sort("edge_score", descending=True)
    head = cur.head(TOP_N_OUTPUT)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"_{CATEGORY}" if CATEGORY else ""
    out = OUTPUT_DIR / f"watchlist{tag}.parquet"
    head.write_parquet(out)
    print(f"\nWrote {out} — top {head.shape[0]:,} of {cur.shape[0]:,} eligible wallets")

    print("\nTop 10 candidates:")
    cols = ["user_address", "pnl_focus", "pnl_focus_resolved",
            "n_trades", "n_markets", "frac_maker_volume",
            "taker_pnl_est", "pnl_per_trade", "edge_score"]
    print(head.head(10).select(cols).to_pandas().to_string())

    # ---- Headline distribution stats --------------------------------------
    print(f"\nWatchlist headline stats ({CATEGORY or 'ALL'}):")
    print(f"  Total focus PnL (resolved):  ${head['pnl_focus_resolved'].sum():>15,.0f}")
    print(f"  Sum of taker_pnl_est:        ${head['taker_pnl_est'].sum():>15,.0f}")
    print(f"  Median wallet focus PnL:     ${head['pnl_focus_resolved'].median():>15,.0f}")
    print(f"  Median n_trades:             {head['n_trades'].median():>15,.0f}")
    print(f"  Median n_markets:            {head['n_markets'].median():>15,.0f}")
    print(f"  Mean frac_maker_volume:      {head['frac_maker_volume'].mean():>15.2%}")


if __name__ == "__main__":
    main()
