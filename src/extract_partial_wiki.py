"""Extract whatever's currently cached in wiki_cache.json into news_wikipedia.parquet,
without making any further API calls. Useful while the full pull is still running."""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).parent))
from news_wikipedia import clean_question  # noqa: E402

OUTPUT_DIR = Path("/Users/tom/projects/polymarketFraud/output")
CACHE_PATH = OUTPUT_DIR / "wiki_cache.json"
SUBSTANTIVE_BYTES = 200


def main(out_name: str = "news_wikipedia_partial.parquet") -> None:
    shortlist = pl.read_parquet(OUTPUT_DIR / "shocks_shortlist.parquet")
    markets = (
        shortlist
        .select(["market_id", "question", "market_start_time", "close_time"])
        .unique("market_id")
    )

    cache = json.loads(CACHE_PATH.read_text())

    rows: list[dict] = []
    covered_markets = 0

    for row in markets.iter_rows(named=True):
        mid = row["market_id"]
        cleaned = clean_question(row["question"])
        search_key = f"search::{cleaned}"
        titles = cache.get(search_key)
        if not titles:
            continue

        start = (row["market_start_time"] - timedelta(days=7)).isoformat()
        end_dt = (row["close_time"] or row["market_start_time"] + timedelta(days=365)) + timedelta(days=7)
        end = end_dt.isoformat()

        any_cached = False
        for rank, page in enumerate(titles[:3]):
            rev_key = f"revs::{page}::{start}::{end}"
            revs = cache.get(rev_key)
            if revs is None:
                continue
            any_cached = True
            prev_size = None
            for rev in revs:
                cur_size = rev.get("size") or 0
                size_delta = cur_size - prev_size if prev_size is not None else cur_size
                prev_size = cur_size
                if abs(size_delta) < SUBSTANTIVE_BYTES:
                    continue
                rows.append({
                    "market_id": mid,
                    "wiki_page_query": page,
                    "wiki_page": rev.get("_resolved_title") or page,
                    "page_rank": rank,
                    "revision_id": rev.get("revid"),
                    "revision_time": rev.get("timestamp"),
                    "size": cur_size,
                    "size_delta": size_delta,
                    "comment": rev.get("comment", "") or "",
                    "user": rev.get("user", "") or "",
                    "tags": ",".join(rev.get("tags") or []),
                })
        if any_cached:
            covered_markets += 1

    if rows:
        df = pl.DataFrame(rows).with_columns(
            pl.col("revision_time").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC")
        )
    else:
        df = pl.DataFrame(schema={
            "market_id": pl.Int64, "wiki_page_query": pl.String, "wiki_page": pl.String,
            "page_rank": pl.Int64, "revision_id": pl.Int64,
            "revision_time": pl.Datetime("us", "UTC"), "size": pl.Int64, "size_delta": pl.Int64,
            "comment": pl.String, "user": pl.String, "tags": pl.String,
        })
    out_path = OUTPUT_DIR / out_name
    df.write_parquet(out_path)
    print(f"Wrote {out_path}: {df.shape[0]:,} substantive revisions, {covered_markets} markets covered, {df['market_id'].n_unique()} markets with revisions")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "news_wikipedia_partial.parquet")
