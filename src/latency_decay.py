"""Phase 0.7 — Latency decay (v4, clean-subset only).

For each survivor trade, find the next trade in the SAME (market_id, outcome)
at or after (t + delay). Drop rows where either side has a null outcome — too
much risk of cross-token contamination.

This restricts the analysis to ~26 k of 84 k survivor trades, but those 26 k
have an unambiguous interpretation: future_price refers to *exactly the same
token* the followed wallet hit, so the price comparison is apples-to-apples.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

DATA_DIR    = Path("/Users/tom/projects/polymarketFraud/data")
OUTPUT_DIR  = Path("/Users/tom/projects/polymarketFraud/output")
BT_DIR      = OUTPUT_DIR / "backtest_sports"

BET_SIZE_USD     = 100.0
SLIPPAGE_DOLLARS = 0.01
DELAYS_S         = [0, 5, 15, 30, 60, 300, 900, 3600, 21600]
MIN_PRICE        = 0.04
MAX_PRICE        = 0.96


def main() -> None:
    per_wallet = pl.read_csv(BT_DIR / "per_wallet_filtered.csv")
    survivors = per_wallet["taker_address"].to_list()

    sv = pl.read_parquet(BT_DIR / "trades.parquet").filter(
        pl.col("taker_address").is_in(survivors)
        & pl.col("outcome").is_not_null()
    ).with_columns(
        pl.col("timestamp").cast(pl.Datetime("ns", "UTC")),
    ).select([
        "timestamp", "market_id", "outcome",
        "taker_bought", "winner", "we_win",
        "entry_price_raw",
    ])
    print(f"Survivor trades with non-null outcome: {sv.shape[0]:,}")

    # Baseline (no delay model): compute what the headline PnL is on THIS subset.
    baseline = sv.with_columns(
        (pl.col("entry_price_raw") + SLIPPAGE_DOLLARS).clip(0.01, 0.99).alias("entry_price")
    ).filter(pl.col("entry_price") <= MAX_PRICE)
    baseline = baseline.with_columns(
        pl.when(pl.col("we_win"))
          .then(BET_SIZE_USD * (1 / pl.col("entry_price") - 1))
          .otherwise(-BET_SIZE_USD)
          .alias("pnl")
    )
    print(f"Baseline subset PnL (delay=0, 1 tick slip): "
          f"${baseline['pnl'].sum():,.0f}  "
          f"mean=${baseline['pnl'].mean():.2f}  "
          f"hit={baseline['we_win'].sum()/baseline.shape[0]:.3f}")

    # ---- Background trade stream, restricted to non-null outcome -------
    print("Loading background sports trade stream (non-null outcome only)…")
    bg_parts = []
    for year in (2024, 2025):
        part = (
            pl.scan_parquet(DATA_DIR / "trades" / f"year={year}" / "**" / "*.parquet")
            .filter(pl.col("category") == "Sports")
            .filter(pl.col("outcome").is_not_null())
            .filter(pl.col("price") >= MIN_PRICE)
            .filter(pl.col("price") <= MAX_PRICE)
            .select(["timestamp", "market_id", "outcome", "price", "taker_bought"])
            .collect()
        )
        bg_parts.append(part)
    bg = pl.concat(bg_parts).sort(["market_id", "outcome", "timestamp"])
    print(f"Background sports trade stream: {bg.shape[0]:,} rows")

    # ---- For each delay, asof join on (market_id, outcome) -------------
    rows = []
    for delay_s in DELAYS_S:
        target = sv.with_columns(
            (pl.col("timestamp") + pl.duration(seconds=delay_s))
              .cast(pl.Datetime("ns", "UTC"))
              .alias("target_ts")
        ).sort(["market_id", "outcome", "target_ts"])

        joined = target.join_asof(
            bg.rename({"price": "future_price"}),
            left_on="target_ts", right_on="timestamp",
            by=["market_id", "outcome"], strategy="forward",
        )

        # delayed_entry: if we bought the token, we pay its prevailing price
        # at T+delay; if we sold the token, our entry is 1 − that price.
        joined = joined.with_columns(
            pl.when(pl.col("future_price").is_null())
              .then(None)
              .when(pl.col("taker_bought"))
              .then(pl.col("future_price"))
              .otherwise(1 - pl.col("future_price"))
              .alias("delayed_entry_raw"),
        )
        # Fallback to original when no future trade exists in the window.
        joined = joined.with_columns(
            pl.coalesce("delayed_entry_raw", "entry_price_raw").alias("entry_no_slip"),
        )
        joined = joined.with_columns(
            (pl.col("entry_no_slip") + SLIPPAGE_DOLLARS).clip(0.01, 0.99).alias("entry_price"),
        )
        joined = joined.filter(pl.col("entry_price") <= MAX_PRICE)

        joined = joined.with_columns(
            pl.when(pl.col("we_win"))
              .then(BET_SIZE_USD * (1 / pl.col("entry_price") - 1))
              .otherwise(-BET_SIZE_USD)
              .alias("pnl")
        )

        n         = joined.shape[0]
        n_matched = joined.filter(pl.col("delayed_entry_raw").is_not_null()).shape[0]
        wins      = joined["we_win"].sum()
        pnl       = joined["pnl"].sum()
        avg_entry = joined["entry_price"].mean()

        rows.append({
            "delay_s": delay_s,
            "n_trades": n,
            "match_rate": n_matched / max(n, 1),
            "hit_rate": wins / max(n, 1),
            "avg_entry_price": float(avg_entry or 0),
            "total_pnl": float(pnl),
            "pnl_per_trade": float(pnl / max(n, 1)),
            "roi": float(pnl / max(n * BET_SIZE_USD, 1)),
        })
        print(f"  delay={delay_s:>6}s  n={n:>6,}  match={n_matched/n:.1%}  "
              f"hit={wins/n:.3f}  avg_entry={avg_entry:.3f}  "
              f"pnl=${pnl:>12,.0f}  $/trade={pnl/n:>7.2f}  ROI={pnl/(n*BET_SIZE_USD):>7.2%}")

    out = pl.DataFrame(rows)
    out.write_csv(BT_DIR / "latency_decay.csv")
    print(f"\nWrote {BT_DIR / 'latency_decay.csv'}")


if __name__ == "__main__":
    main()
