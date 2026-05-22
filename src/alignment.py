"""Align Polymarket shocks to Wikipedia (and later GDELT) news events.

For each shock, find the nearest substantive Wikipedia revision among the
candidate pages for that market, within a configurable window.

Convention: Δt = shock_time − news_time.
  * Δt > 0  ⇒ news preceded shock  ⇒ normal market behaviour
  * Δt < 0  ⇒ shock preceded news  ⇒ suggestive of insider/private information

Outputs:
  output/aligned_wiki.parquet — one row per shock with nearest-news columns
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

OUTPUT_DIR = Path("/Users/tom/projects/polymarketFraud/output")

# How far either side of a shock to look for a relevant news event.
WINDOW_HOURS_BEFORE = 7 * 24    # news up to 7 days before the shock
WINDOW_HOURS_AFTER = 7 * 24     # news up to 7 days after the shock
MIN_SIZE_DELTA_BYTES = 200      # already filtered in news_wikipedia, but enforce


def main() -> None:
    shocks = pl.read_parquet(OUTPUT_DIR / "shocks_shortlist.parquet").with_columns(
        pl.col("shock_time").alias("shock_t")
    )
    wiki = pl.read_parquet(OUTPUT_DIR / "news_wikipedia.parquet").filter(
        pl.col("size_delta").abs() >= MIN_SIZE_DELTA_BYTES
    )
    print(f"{shocks.shape[0]:,} shocks × {wiki.shape[0]:,} revisions")

    # Build a long table: (shock_id, revision_time, size_delta, ...) by joining on market_id
    # then computing Δt and filtering to the window. Inner join.
    shocks_keyed = shocks.with_row_index("shock_idx").select([
        "shock_idx", "market_id", "shock_t", "question", "category",
        "prev_close", "close", "dp", "volume", "z_shock",
    ])

    joined = (
        shocks_keyed
        .join(wiki, on="market_id", how="left")
        .with_columns(
            ((pl.col("shock_t") - pl.col("revision_time")).dt.total_minutes() / 60).alias("dt_hours")
        )
    )

    # Subset to window
    in_window = joined.filter(
        (pl.col("dt_hours").is_not_null())
        & (pl.col("dt_hours") >= -WINDOW_HOURS_AFTER)
        & (pl.col("dt_hours") <= WINDOW_HOURS_BEFORE)
    )

    # Two views: nearest preceding (dt > 0, smallest dt) and nearest following (dt < 0, smallest |dt|)
    preceding = (
        in_window.filter(pl.col("dt_hours") >= 0)
        .sort(["shock_idx", "dt_hours"])
        .group_by("shock_idx").head(1)
        .select([
            "shock_idx",
            pl.col("dt_hours").alias("dt_pre_hours"),
            pl.col("revision_time").alias("pre_revision_time"),
            pl.col("wiki_page").alias("pre_wiki_page"),
            pl.col("size_delta").alias("pre_size_delta"),
            pl.col("comment").alias("pre_comment"),
        ])
    )
    following = (
        in_window.filter(pl.col("dt_hours") < 0)
        .sort(["shock_idx", pl.col("dt_hours").abs()])
        .group_by("shock_idx").head(1)
        .select([
            "shock_idx",
            pl.col("dt_hours").alias("dt_post_hours"),
            pl.col("revision_time").alias("post_revision_time"),
            pl.col("wiki_page").alias("post_wiki_page"),
            pl.col("size_delta").alias("post_size_delta"),
            pl.col("comment").alias("post_comment"),
        ])
    )

    # Nearest revision either side
    nearest_either = (
        in_window
        .with_columns(pl.col("dt_hours").abs().alias("abs_dt"))
        .sort(["shock_idx", "abs_dt"])
        .group_by("shock_idx").head(1)
        .select([
            "shock_idx",
            pl.col("dt_hours").alias("dt_nearest_hours"),
            pl.col("revision_time").alias("nearest_revision_time"),
            pl.col("wiki_page").alias("nearest_wiki_page"),
            pl.col("size_delta").alias("nearest_size_delta"),
            pl.col("comment").alias("nearest_comment"),
        ])
    )

    aligned = (
        shocks_keyed
        .join(nearest_either, on="shock_idx", how="left")
        .join(preceding, on="shock_idx", how="left")
        .join(following, on="shock_idx", how="left")
        .with_columns(
            pl.when(pl.col("dt_nearest_hours") > 0).then(pl.lit("news_before_shock"))
            .when(pl.col("dt_nearest_hours") < 0).then(pl.lit("shock_before_news"))
            .when(pl.col("dt_nearest_hours") == 0).then(pl.lit("simultaneous"))
            .otherwise(pl.lit("no_news_in_window"))
            .alias("classification")
        )
    )

    out_path = OUTPUT_DIR / "aligned_wiki.parquet"
    aligned.write_parquet(out_path)
    print(f"Wrote {out_path}: {aligned.shape}")

    print("\nClassification counts:")
    print(aligned.group_by("classification").len().sort("len", descending=True).to_pandas().to_string())

    print("\nΔt summary (hours), nearest revision either side:")
    summary = (
        aligned.filter(pl.col("dt_nearest_hours").is_not_null())
        .select(pl.col("dt_nearest_hours"))
        .describe()
    )
    print(summary.to_pandas().to_string())


if __name__ == "__main__":
    main()
