"""Shock detection on Polymarket hourly OHLCV.

For each market, take the first-indexed outcome token (outcome_idx=0) and detect
"shocks" as hour-over-hour close-to-close moves above a threshold, subject to a
volume floor and a max time gap from the previous bar (to avoid attributing
multi-day drift to a single bar).

Output: output/shocks.parquet
"""

from __future__ import annotations

import polars as pl

DATA_DIR = "/Users/tom/projects/polymarketFraud/data"
OUTPUT_DIR = "/Users/tom/projects/polymarketFraud/output"

# Detection parameters. These are the tunable knobs.
ABS_FLOOR_PP = 0.05          # |Δp| ≥ 5 pp required
MIN_BAR_VOLUME_USDC = 1_000  # ignore tiny-volume bars (likely noise)
MAX_GAP_HOURS = 6            # adjacent bars must be ≤6h apart for a 1-bar shock
ROLLING_SIGMA_HOURS = 7 * 24 # 7-day rolling window
SIGMA_MULT = 3.0             # |Δp| > k * σ_rolling


def select_reference_tokens() -> pl.DataFrame:
    """One token per market: outcome_idx=0 (deterministic across binary and
    multi-outcome markets)."""
    pred = pl.read_parquet(f"{DATA_DIR}/predictions.parquet")
    return (
        pred.filter(pl.col("outcome_idx") == 0)
        .select(["market_id", "prediction_id", "outcome", "winner"])
        .rename({"outcome": "ref_outcome", "winner": "ref_won"})
    )


def load_ohlcv_for_reference_tokens(ref: pl.DataFrame) -> pl.LazyFrame:
    """Lazy-scan all hourly OHLCV bars, restricted to the reference tokens."""
    ref_ids = set(ref["prediction_id"].to_list())
    return (
        pl.scan_parquet(f"{DATA_DIR}/ohlcv_1h/**/*.parquet", hive_partitioning=False)
        .filter(pl.col("prediction_id").is_in(ref_ids))
        .select([
            "prediction_id", "market_id", "timestamp",
            "open", "high", "low", "close", "volume", "trade_count",
        ])
        .with_columns(pl.col("market_id").cast(pl.Int64))
    )


def detect_shocks(bars: pl.LazyFrame) -> pl.LazyFrame:
    """Per-token: compute hour-over-hour change, rolling volatility, and flag
    bars where the move clears both an absolute floor and a vol-relative gate,
    on volume above the floor, within MAX_GAP_HOURS of the previous bar."""
    return (
        bars
        .sort(["prediction_id", "timestamp"])
        .with_columns([
            pl.col("close").diff().over("prediction_id").alias("dp"),
            (
                (pl.col("timestamp") - pl.col("timestamp").shift(1).over("prediction_id"))
                .dt.total_minutes() / 60
            ).alias("hours_gap"),
            pl.col("close").shift(1).over("prediction_id").alias("prev_close"),
        ])
        # Rolling sigma of |dp| over the last 7 days of bars (active hours only)
        .with_columns(
            pl.col("dp").abs()
            .rolling_std(window_size=ROLLING_SIGMA_HOURS, min_samples=12)
            .over("prediction_id")
            .alias("sigma_dp_rolling")
        )
        .with_columns([
            (pl.col("dp").abs() / pl.col("sigma_dp_rolling")).alias("z_shock"),
        ])
        .filter(
            (pl.col("dp").abs() >= ABS_FLOOR_PP)
            & (pl.col("hours_gap") <= MAX_GAP_HOURS)
            & (pl.col("volume") >= MIN_BAR_VOLUME_USDC)
            & (
                # vol-relative gate, but only when we have enough history
                pl.col("sigma_dp_rolling").is_null()
                | (pl.col("z_shock") >= SIGMA_MULT)
            )
        )
        .select([
            "market_id", "prediction_id", "timestamp",
            "prev_close", "close", "dp", "hours_gap", "volume",
            "trade_count", "sigma_dp_rolling", "z_shock",
        ])
        .rename({"timestamp": "shock_time"})
    )


def main() -> None:
    print("Loading reference tokens (outcome_idx=0)...")
    ref = select_reference_tokens()
    print(f"  {ref.shape[0]:,} reference tokens (one per market)")

    print("Scanning hourly OHLCV bars...")
    bars = load_ohlcv_for_reference_tokens(ref)

    print("Detecting shocks...")
    shocks = detect_shocks(bars).collect()
    print(f"  {shocks.shape[0]:,} shock events")

    # Attach market metadata for downstream analysis
    markets = pl.read_parquet(f"{DATA_DIR}/markets.parquet").select([
        "market_id", "question", "category", "event_id",
        "market_start_time", "close_time",
    ])
    shocks = shocks.join(markets, on="market_id", how="left")

    out_path = f"{OUTPUT_DIR}/shocks.parquet"
    shocks.write_parquet(out_path)
    print(f"Wrote {out_path}")

    # Headline numbers
    print("\nBy category:")
    print(shocks.group_by("category").len().sort("len", descending=True).to_pandas().to_string())
    print("\nDistribution of |dp| (pp):")
    desc = shocks.select((pl.col("dp").abs() * 100).alias("abs_dp_pp")).describe()
    print(desc.to_pandas().to_string())


if __name__ == "__main__":
    main()
