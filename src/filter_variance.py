"""Phase 0.5 — Post-hoc per-wallet variance filter.

Reads the per-trade backtest output and computes wallet-level statistics:
hit rate, mean / std of per-trade PnL, Sharpe-analog. Drops wallets whose
edge is statistically indistinguishable from a lucky long-shot streak,
then re-aggregates the headline PnL on the surviving subset.

Usage:
    CATEGORY=sports uv run python src/filter_variance.py
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import polars as pl

CATEGORY = os.environ.get("CATEGORY", "").lower() or None
TAG      = f"_{CATEGORY}" if CATEGORY else ""

OUTPUT_DIR = Path("/Users/tom/projects/polymarketFraud/output")
BT_DIR     = OUTPUT_DIR / f"backtest{TAG}"

# Filter thresholds.
MIN_HIT_RATE        = 0.50    # win at least half the time → no lottery streaks
MIN_PNL_PER_TRADE   = 0.50    # mean per-trade PnL must clear $0.50
MIN_SHARPE_ANALOG   = 0.04    # mean/std of per-trade PnL (no √n scaling)
MIN_TRADES_PER_WALLET = 100   # need a meaningful sample to score the wallet


def main() -> None:
    trades = pl.read_parquet(BT_DIR / "trades.parquet")
    print(f"Loaded {trades.shape[0]:,} trades from {BT_DIR.name}")

    wstats = (
        trades.group_by("taker_address")
        .agg(
            pl.len().alias("n"),
            pl.col("we_win").sum().alias("wins"),
            pl.col("pnl").sum().alias("pnl_total"),
            pl.col("pnl").mean().alias("pnl_mean"),
            pl.col("pnl").std().alias("pnl_std"),
        )
        .with_columns(
            (pl.col("wins") / pl.col("n")).alias("hit_rate"),
            (pl.col("pnl_mean") / pl.col("pnl_std")).alias("sharpe_analog"),
        )
        .sort("pnl_total", descending=True)
    )

    print(f"\n{wstats.shape[0]:,} wallets in watchlist")
    print(f"  median trades / wallet:     {wstats['n'].median():>12,.0f}")
    print(f"  median hit rate:            {wstats['hit_rate'].median():>12.3f}")
    print(f"  median pnl_mean:            {wstats['pnl_mean'].median():>12,.3f}")
    print(f"  median pnl_std:             {wstats['pnl_std'].median():>12,.2f}")
    print(f"  median sharpe_analog:       {wstats['sharpe_analog'].median():>12.4f}")

    # Apply variance / quality filters
    stages = [
        ("n >= 100",                       pl.col("n") >= MIN_TRADES_PER_WALLET),
        ("hit_rate >= 0.50",               pl.col("hit_rate") >= MIN_HIT_RATE),
        ("pnl_mean >= $0.50",              pl.col("pnl_mean") >= MIN_PNL_PER_TRADE),
        ("sharpe_analog >= 0.04",          pl.col("sharpe_analog") >= MIN_SHARPE_ANALOG),
    ]
    cur = wstats
    print("\nVariance filter funnel:")
    print(f"  start                         {cur.shape[0]:>8,}")
    for label, expr in stages:
        cur = cur.filter(expr)
        print(f"  {label:<32s} {cur.shape[0]:>8,}")

    survivor_set = set(cur["taker_address"].to_list())
    print(f"\nSurvivors: {len(survivor_set):,} wallets")

    # Re-aggregate the headline using only survivors
    sub = trades.filter(pl.col("taker_address").is_in(survivor_set))
    n = sub.shape[0]
    wins = sub["we_win"].sum()
    pnl  = sub["pnl"].sum()

    summary = {
        "category": CATEGORY or "ALL",
        "watchlist_after_filter": len(survivor_set),
        "n_copy_trades": int(n),
        "hit_rate": float(wins / n),
        "total_pnl": float(pnl),
        "avg_pnl_per_trade": float(pnl / n),
        "roi_if_serial": float(pnl / (n * 100.0)),
    }
    print("\nHeadline on survivors:")
    for k, v in summary.items():
        if isinstance(v, float):
            if "pnl" in k or "capital" in k:
                print(f"  {k:>28s}: ${v:>14,.2f}")
            else:
                print(f"  {k:>28s}: {v:>14.4f}")
        else:
            print(f"  {k:>28s}: {v}")

    # Monthly stability
    monthly = (
        sub.with_columns(pl.col("timestamp").dt.strftime("%Y-%m").alias("month"))
        .group_by("month")
        .agg(
            pl.len().alias("n_trades"),
            pl.col("pnl").sum().alias("pnl"),
            (pl.col("we_win").sum() / pl.len()).alias("hit_rate"),
        )
        .sort("month")
    )
    print("\nMonthly:")
    print(monthly.tail(12).to_pandas().to_string(index=False))

    print("\nTop 10 surviving wallets:")
    print(cur.head(10).select([
        "taker_address", "n", "hit_rate", "pnl_total", "pnl_mean", "pnl_std", "sharpe_analog"
    ]).to_pandas().to_string(index=False))

    # Write outputs
    (BT_DIR / "filtered_summary.json").write_text(json.dumps(summary, indent=2))
    cur.write_csv(BT_DIR / "per_wallet_filtered.csv")
    monthly.write_csv(BT_DIR / "monthly_filtered.csv")
    print(f"\nWrote {BT_DIR / 'filtered_summary.json'}, per_wallet_filtered.csv, monthly_filtered.csv")


if __name__ == "__main__":
    main()
