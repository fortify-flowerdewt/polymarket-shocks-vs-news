"""Phase 0.6 — Sensitivity tests on the variance-filtered sports strategy.

Tests:
  1. Slippage sweep: 0 / 1 / 2 / 3 ticks
  2. Drop the institutional bot (0x204f…5e14)
  3. Shrink to top-20 survivors by historical PnL
  4. Forward validation on Jan–Mar 2026 (out-of-sample)

Tests 1-3 operate on the existing `output/backtest_sports/trades.parquet`
(2024-2025 in-sample). Test 4 re-scans trade data for the 2026 period only.
"""

from __future__ import annotations

import os
from pathlib import Path

import polars as pl

DATA_DIR    = Path("/Users/tom/projects/polymarketFraud/data")
OUTPUT_DIR  = Path("/Users/tom/projects/polymarketFraud/output")
BT_DIR      = OUTPUT_DIR / "backtest_sports"

BET_SIZE_USD     = 100.0
INSTITUTIONAL_BOT = "0x204f72f35326db932158cba6adff0b9a1da95e14"
MIN_PRICE = 0.04
MAX_PRICE = 0.96


def recompute_pnl(trades: pl.DataFrame, slippage_dollars: float) -> pl.DataFrame:
    """Recompute per-trade PnL for an alternative slippage assumption.

    Uses the already-stored `entry_price_raw` (taker's pre-slippage entry)
    and `we_win` columns; everything else is derived.
    """
    return trades.with_columns(
        (pl.col("entry_price_raw") + slippage_dollars).clip(0.01, 0.99).alias("entry_price"),
    ).filter(
        pl.col("entry_price") <= MAX_PRICE
    ).with_columns(
        pl.when(pl.col("we_win"))
          .then(BET_SIZE_USD * (1 / pl.col("entry_price") - 1))
          .otherwise(-BET_SIZE_USD)
          .alias("pnl"),
    )


def headline(df: pl.DataFrame, label: str) -> dict:
    if not df.shape[0]:
        print(f"  {label:<60s}  (no trades)")
        return {"label": label, "n": 0, "pnl": 0, "roi": 0, "hit_rate": 0}
    n     = df.shape[0]
    wins  = df["we_win"].sum()
    pnl   = df["pnl"].sum()
    hit   = wins / n
    roi   = pnl / (n * BET_SIZE_USD)
    pnl_per = pnl / n
    print(f"  {label:<60s}  n={n:>8,}  hit={hit:.3f}  pnl=${pnl:>14,.0f}  $/trade={pnl_per:>7.2f}  ROI={roi:>7.2%}")
    return {"label": label, "n": int(n), "wins": int(wins), "pnl": float(pnl),
            "hit_rate": float(hit), "pnl_per_trade": float(pnl_per), "roi": float(roi)}


def main() -> None:
    # ---------------------------------------------------------------------
    # Survivors from the variance filter — 62 wallets
    # ---------------------------------------------------------------------
    per_wallet = pl.read_csv(BT_DIR / "per_wallet_filtered.csv")
    survivors = per_wallet["taker_address"].to_list()
    print(f"Survivors set: {len(survivors)} wallets")

    # All trades from the in-sample backtest
    all_trades = pl.read_parquet(BT_DIR / "trades.parquet")
    print(f"In-sample trades.parquet: {all_trades.shape[0]:,} rows")

    survivor_trades = all_trades.filter(pl.col("taker_address").is_in(survivors))
    print(f"Survivor trades (in-sample): {survivor_trades.shape[0]:,}")

    # =====================================================================
    # TEST 1 — Slippage sweep
    # =====================================================================
    print("\n=== Test 1: slippage sensitivity (in-sample, 62 survivors) ===")
    test1_results = []
    for ticks in [0, 1, 2, 3]:
        rec = recompute_pnl(survivor_trades, ticks * 0.01)
        test1_results.append(headline(rec, f"slippage = {ticks} tick(s) (${ticks*0.01:.2f})"))

    # =====================================================================
    # TEST 2 — Drop the institutional bot
    # =====================================================================
    print("\n=== Test 2: drop institutional bot 0x204f…5e14 ===")
    no_bot_trades = survivor_trades.filter(pl.col("taker_address") != INSTITUTIONAL_BOT)
    print(f"  trades removed: {survivor_trades.shape[0] - no_bot_trades.shape[0]:,} "
          f"({(survivor_trades.shape[0] - no_bot_trades.shape[0]) / survivor_trades.shape[0]:.1%} of survivors)")
    headline(no_bot_trades, "ALL 62 survivors (baseline, 1 tick)")
    headline(
        recompute_pnl(no_bot_trades.with_columns(pl.col("entry_price").alias("entry_price_raw")), 0.0)
            if "entry_price_raw" not in no_bot_trades.columns else no_bot_trades,
        "61 survivors (institutional bot removed)",
    )

    # Note: the existing `pnl` column already uses 1-tick slippage, no need
    # to recompute when we're only changing the wallet set.
    headline(no_bot_trades, "61 survivors (institutional bot removed)")

    # =====================================================================
    # TEST 3 — Shrink to top 20 survivors by historical PnL
    # =====================================================================
    print("\n=== Test 3: shrink watchlist to top 20 by historical PnL ===")
    top20 = per_wallet.sort("pnl_total", descending=True).head(20)
    top20_set = set(top20["taker_address"].to_list())
    top20_trades = survivor_trades.filter(pl.col("taker_address").is_in(top20_set))
    print(f"  trades by top 20: {top20_trades.shape[0]:,}")
    headline(top20_trades, "top 20 survivors")

    top10_set = set(top20.head(10)["taker_address"].to_list())
    top10_trades = survivor_trades.filter(pl.col("taker_address").is_in(top10_set))
    headline(top10_trades, "top 10 survivors")

    top5_set = set(top20.head(5)["taker_address"].to_list())
    top5_trades = survivor_trades.filter(pl.col("taker_address").is_in(top5_set))
    headline(top5_trades, "top 5 survivors")

    # =====================================================================
    # TEST 4 — Forward validation on Jan–Mar 2026
    # =====================================================================
    print("\n=== Test 4: forward validation on 2026 (out-of-sample) ===")
    trades_2026_dir = DATA_DIR / "trades" / "year=2026"
    if not trades_2026_dir.exists() or not list(trades_2026_dir.glob("**/*.parquet")):
        print("  Not run: 2026 trade data not downloaded yet.")
    else:
        # Re-scan the raw trade stream for 2026, filtered to the 62-wallet survivors
        lazy = (
            pl.scan_parquet(str(trades_2026_dir / "**" / "*.parquet"))
            .filter(pl.col("taker_address").is_in(survivors))
            .filter(pl.col("category").str.to_lowercase() == "sports")
            .filter(pl.col("price") >= MIN_PRICE)
            .filter(pl.col("price") <= MAX_PRICE)
            .filter(pl.col("winner").is_not_null())
            .select(["timestamp", "market_id", "category", "outcome", "winner",
                     "price", "quantity", "taker_address", "maker_address", "taker_bought"])
        )
        fw = lazy.collect()
        print(f"  2026 trades by survivors in sports: {fw.shape[0]:,}")
        if fw.shape[0] == 0:
            print("  no in-window data yet")
        else:
            fw = fw.with_columns(
                pl.when(pl.col("taker_bought")).then(pl.col("price")).otherwise(1 - pl.col("price")).alias("entry_price_raw"),
                (pl.col("taker_bought") == pl.col("winner")).alias("we_win"),
            )
            fw = recompute_pnl(fw, 0.01)  # 1-tick slippage
            print(f"  date range: {fw['timestamp'].min()}  →  {fw['timestamp'].max()}")
            headline(fw, "2026 out-of-sample (1 tick slip)")

            # Compare to in-sample over the same calendar months only
            in_sample_jan_mar = survivor_trades.with_columns(
                pl.col("timestamp").dt.month().alias("month")
            ).filter(pl.col("month").is_in([1, 2, 3]))
            headline(in_sample_jan_mar, "in-sample Jan–Mar (any year) — for comparison")


if __name__ == "__main__":
    main()
