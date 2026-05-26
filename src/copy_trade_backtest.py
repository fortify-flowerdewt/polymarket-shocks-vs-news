"""Phase 0 — Copy-trade backtest.

For each watchlist wallet (from `wallet_selection.py`), find every trade where
that wallet was the TAKER (we can't copy a maker — by the time we see their
fill, the spread is already gone). For each such trade, simulate placing a
small follow-bet of the same direction at the same effective price plus a
slippage haircut, and compute the realised PnL when the market resolved.

Output: `output/backtest/` —
    summary.json              top-line numbers
    per_wallet.csv            PnL by wallet
    monthly.csv               PnL by calendar month
    by_category.csv           PnL by market category
    cum_pnl.csv               cumulative PnL by trade
    trades.parquet            every simulated copy-trade
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

import os
CATEGORY = os.environ.get("CATEGORY", "").lower() or None
TAG      = f"_{CATEGORY}" if CATEGORY else ""

DATA_DIR = Path("/Users/tom/projects/polymarketFraud/data")
OUTPUT_DIR = Path("/Users/tom/projects/polymarketFraud/output")
BT_DIR = OUTPUT_DIR / f"backtest{TAG}"
BT_DIR.mkdir(parents=True, exist_ok=True)


# ---- Backtest configuration -------------------------------------------- #

BET_SIZE_USD          = 100.0     # fixed $ per copy trade
SLIPPAGE_TICKS        = 1         # additional ticks worse than the taker's
                                  # fill (1 tick = $0.01)
SLIPPAGE_DOLLARS      = SLIPPAGE_TICKS * 0.01

# Skip extreme-price contracts where the slippage haircut dominates and
# liquidity is thin (favorite-longshot bias amplified here).
MIN_PRICE             = 0.04
MAX_PRICE             = 0.96


def main() -> None:
    watchlist_path = OUTPUT_DIR / f"watchlist{TAG}.parquet"
    if not watchlist_path.exists():
        # Fall back to the universal watchlist if the category-specific one
        # hasn't been generated yet.
        watchlist_path = OUTPUT_DIR / "watchlist.parquet"
    watchlist = pl.read_parquet(watchlist_path)
    addresses = watchlist["user_address"].to_list()
    print(f"Watchlist:  {watchlist_path.name}  ({len(addresses):,} wallets)")
    print(f"Category:   {CATEGORY or 'ALL'}")

    # Lazy scan all trade partitions — polars will skip ones that don't
    # contain any watchlist taker_address via the filter.
    print("Scanning trades…")
    lazy = (
        pl.scan_parquet(DATA_DIR / "trades" / "**" / "*.parquet")
        .filter(pl.col("taker_address").is_in(addresses))
        .filter(pl.col("price") >= MIN_PRICE)
        .filter(pl.col("price") <= MAX_PRICE)
        .filter(pl.col("winner").is_not_null())   # only resolved markets
        .select([
            "timestamp", "market_id", "category", "outcome", "winner",
            "price", "quantity", "taker_address", "maker_address", "taker_bought",
        ])
    )
    if CATEGORY:
        # Trades carry their parent market's category. Match case-insensitively.
        lazy = lazy.filter(pl.col("category").str.to_lowercase() == CATEGORY)
    trades = lazy.collect()
    print(f"  {trades.shape[0]:,} taker trades by watchlist wallets in resolved markets")

    if not trades.shape[0]:
        print("No trades to simulate. Aborting.")
        return

    # ---- Direction & effective price ----------------------------------- #
    # If taker_bought = True, the taker bought `outcome` at `price`.
    # If taker_bought = False, the taker sold `outcome` at `price` —
    #   which is equivalent to buying the complementary token at `1 - price`.
    # We model "we copy" as taking the same effective position with the
    # same exposure to the same direction (`taker_won = taker_bought == winner`).
    trades = trades.with_columns(
        # The price we'd effectively pay per share of the position we want
        pl.when(pl.col("taker_bought"))
          .then(pl.col("price"))
          .otherwise(1 - pl.col("price"))
          .alias("entry_price_raw"),
        (pl.col("taker_bought") == pl.col("winner")).alias("we_win"),
    )

    # Add slippage: we lift the entry by 1 tick (we are *another* taker
    # crossing the spread the same way after our followed wallet did).
    trades = trades.with_columns(
        (pl.col("entry_price_raw") + SLIPPAGE_DOLLARS).clip(0.01, 0.99).alias("entry_price"),
    )
    # Drop trades where the slippage-adjusted entry is too close to 1.0
    # (no upside).
    trades = trades.filter(pl.col("entry_price") <= MAX_PRICE)

    # ---- Per-trade PnL -------------------------------------------------- #
    # We bet a fixed $BET_SIZE_USD per trade. Number of shares = bet / entry.
    # If we_win: each share pays $1; we paid entry. PnL = shares * (1 - entry).
    # If we lose: each share pays $0; we paid entry. PnL = shares * (-entry) = -bet.
    trades = trades.with_columns(
        (BET_SIZE_USD / pl.col("entry_price")).alias("shares"),
        pl.when(pl.col("we_win"))
          .then(BET_SIZE_USD * (1 / pl.col("entry_price") - 1))
          .otherwise(-BET_SIZE_USD)
          .alias("pnl"),
    )

    n = trades.shape[0]
    wins = trades["we_win"].sum()
    total_pnl = trades["pnl"].sum()
    avg_pnl_per_trade = total_pnl / n
    total_capital_at_risk = n * BET_SIZE_USD
    roi = total_pnl / total_capital_at_risk

    # ---- Aggregations -------------------------------------------------- #
    by_wallet = (
        trades.group_by("taker_address")
        .agg(
            pl.len().alias("n_copy_trades"),
            pl.col("we_win").sum().alias("n_wins"),
            (pl.col("we_win").sum() / pl.len()).alias("hit_rate"),
            pl.col("pnl").sum().alias("pnl_total"),
            pl.col("pnl").mean().alias("pnl_mean"),
        )
        .sort("pnl_total", descending=True)
    )

    by_month = (
        trades.with_columns(pl.col("timestamp").dt.strftime("%Y-%m").alias("month"))
        .group_by("month")
        .agg(
            pl.len().alias("n_trades"),
            pl.col("pnl").sum().alias("pnl"),
            pl.col("we_win").sum().alias("n_wins"),
            (pl.col("we_win").sum() / pl.len()).alias("hit_rate"),
        )
        .sort("month")
    )

    by_category = (
        trades.group_by("category")
        .agg(
            pl.len().alias("n_trades"),
            pl.col("pnl").sum().alias("pnl"),
            (pl.col("we_win").sum() / pl.len()).alias("hit_rate"),
        )
        .sort("pnl", descending=True)
    )

    cum = (
        trades.sort("timestamp")
        .with_columns(pl.col("pnl").cum_sum().alias("cum_pnl"))
        .select(["timestamp", "pnl", "cum_pnl"])
    )

    # ---- Outputs -------------------------------------------------------- #
    summary = {
        "watchlist_size": len(addresses),
        "n_copy_trades": int(n),
        "n_wins": int(wins),
        "hit_rate": float(wins / n),
        "total_pnl": float(total_pnl),
        "avg_pnl_per_trade": float(avg_pnl_per_trade),
        "bet_size_usd": BET_SIZE_USD,
        "slippage_ticks": SLIPPAGE_TICKS,
        "total_capital_at_risk_if_serial": float(total_capital_at_risk),
        "roi_if_serial": float(roi),
        "first_trade": str(trades["timestamp"].min()),
        "last_trade": str(trades["timestamp"].max()),
    }
    (BT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))

    by_wallet.write_csv(BT_DIR / "per_wallet.csv")
    by_month.write_csv(BT_DIR / "monthly.csv")
    by_category.write_csv(BT_DIR / "by_category.csv")
    cum.write_parquet(BT_DIR / "cum_pnl.parquet")
    trades.write_parquet(BT_DIR / "trades.parquet")

    print("\nSummary:")
    for k, v in summary.items():
        if isinstance(v, float):
            if "pnl" in k or "capital" in k:
                print(f"  {k:>35s}: ${v:>15,.2f}")
            else:
                print(f"  {k:>35s}: {v:>15,.4f}")
        else:
            print(f"  {k:>35s}: {v}")

    print("\nPnL by month (last 12):")
    print(by_month.tail(12).to_pandas().to_string(index=False))

    print("\nTop 10 wallets by PnL:")
    print(by_wallet.head(10).to_pandas().to_string(index=False))

    print("\nPnL by category:")
    print(by_category.to_pandas().to_string(index=False))


if __name__ == "__main__":
    main()
