"""Match Polymarket shocks to Wikipedia revisions.

For each market in the shortlist:
  1. Search Wikipedia for an article matching the (cleaned) question text.
  2. Pull revision history during the market's lifetime ±7 days.
  3. Keep all "substantive" revisions (large size delta or substantive comment).

Output: output/news_wikipedia.parquet — one row per (market, revision).
Cache:  output/wiki_cache.json — searches and revisions, so reruns are cheap.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import time
from datetime import timedelta
from pathlib import Path

import polars as pl
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DATA_DIR = Path("/Users/tom/projects/polymarketFraud/data")
OUTPUT_DIR = Path("/Users/tom/projects/polymarketFraud/output")
CACHE_PATH = OUTPUT_DIR / "wiki_cache.json"

WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "polymarketFraud/0.1 (research; tom.flowerdew@wearefortify.ai)"
REQ_DELAY_S = 0.1   # polite throttle (~10 rps)
SUBSTANTIVE_BYTES = 200   # size delta floor in bytes
CONTEXT_DAYS = 7          # widen revision window beyond market lifetime


# --- HTTP session --------------------------------------------------------- #

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    retries = Retry(
        total=5, backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    # Use system cert bundle (corp proxy)
    s.verify = "/etc/ssl/cert.pem"
    return s


# --- Cache ---------------------------------------------------------------- #

class JsonCache:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = json.loads(path.read_text()) if path.exists() else {}
        self._dirty = False

    def get(self, key: str):
        return self.data.get(key)

    def put(self, key: str, value) -> None:
        self.data[key] = value
        self._dirty = True

    def flush(self) -> None:
        if self._dirty:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.data))
            tmp.replace(self.path)
            self._dirty = False


# --- Question cleaning ---------------------------------------------------- #

PREFIX_RE = re.compile(
    r"^\s*(?:will|did|is|are|does|do|has|have|can|should|could|would)\s+",
    re.IGNORECASE,
)
SUFFIX_RE = re.compile(r"\?+\s*$")


def clean_question(q: str) -> str:
    """Strip 'Will'/'Did' prefix and trailing '?' for better Wikipedia search."""
    if not q:
        return ""
    q = PREFIX_RE.sub("", q.strip())
    q = SUFFIX_RE.sub("", q)
    return q.strip()


# --- Wikipedia calls ------------------------------------------------------ #

def wiki_search(sess: requests.Session, query: str, limit: int = 3) -> list[str]:
    """Return list of page titles, highest-relevance first."""
    r = sess.get(WIKI_API, params={
        "action": "query", "list": "search", "srsearch": query,
        "srlimit": limit, "format": "json", "formatversion": 2,
    }, timeout=15)
    r.raise_for_status()
    return [h["title"] for h in r.json().get("query", {}).get("search", [])]


def wiki_revisions(sess: requests.Session, title: str, start, end) -> list[dict]:
    """Return all revisions of `title` in [start, end] UTC (ascending)."""
    out: list[dict] = []
    rvcontinue = None
    # rvstart is the NEWER bound; rvend is OLDER. With rvdir=newer they swap.
    for _ in range(50):  # safety cap
        params = {
            "action": "query", "prop": "revisions", "titles": title,
            "rvprop": "ids|timestamp|size|comment|user|tags",
            "rvlimit": 500, "rvdir": "newer",
            "rvstart": start.isoformat().replace("+00:00", "Z"),
            "rvend": end.isoformat().replace("+00:00", "Z"),
            "redirects": 1,
            "format": "json", "formatversion": 2,
        }
        if rvcontinue:
            params["rvcontinue"] = rvcontinue
        r = sess.get(WIKI_API, params=params, timeout=20)
        r.raise_for_status()
        body = r.json()
        pages = body.get("query", {}).get("pages", [])
        for p in pages:
            for rev in p.get("revisions", []):
                rev["_resolved_title"] = p.get("title")
                out.append(rev)
        if "continue" in body and "rvcontinue" in body["continue"]:
            rvcontinue = body["continue"]["rvcontinue"]
            time.sleep(REQ_DELAY_S)
        else:
            break
    return out


# --- Main pipeline -------------------------------------------------------- #

def main() -> None:
    shortlist = pl.read_parquet(OUTPUT_DIR / "shocks_shortlist.parquet")
    markets = (
        shortlist
        .select(["market_id", "question", "market_start_time", "close_time"])
        .unique("market_id")
        .sort("market_id")
    )
    print(f"Resolving Wikipedia pages for {markets.shape[0]} markets...")

    sess = make_session()
    cache = JsonCache(CACHE_PATH)
    rows: list[dict] = []
    flush_every = 25

    for i, row in enumerate(markets.iter_rows(named=True)):
        mid = row["market_id"]
        cleaned = clean_question(row["question"])
        cache_key_search = f"search::{cleaned}"

        titles = cache.get(cache_key_search)
        if titles is None:
            try:
                titles = wiki_search(sess, cleaned, limit=3)
            except Exception as e:
                print(f"  [search fail] {mid} {cleaned!r}: {e}")
                titles = []
            cache.put(cache_key_search, titles)
            time.sleep(REQ_DELAY_S)

        if not titles:
            continue

        start = row["market_start_time"] - timedelta(days=CONTEXT_DAYS)
        end = (row["close_time"] or row["market_start_time"] + timedelta(days=365)) + timedelta(days=CONTEXT_DAYS)

        # Pull revisions for each of the top-N candidate pages and tag by source rank.
        for rank, page in enumerate(titles[:3]):
            cache_key_revs = f"revs::{page}::{start.isoformat()}::{end.isoformat()}"
            revs = cache.get(cache_key_revs)
            if revs is None:
                try:
                    revs = wiki_revisions(sess, page, start, end)
                except Exception as e:
                    print(f"  [rev fail] {mid} {page!r}: {e}")
                    revs = []
                cache.put(cache_key_revs, revs)
                time.sleep(REQ_DELAY_S)

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

        if (i + 1) % flush_every == 0:
            cache.flush()
            kept_for_this = sum(1 for r in rows if r["market_id"] == mid)
            print(f"  [{i+1}/{markets.shape[0]}] {mid}: searched {len(titles)} pages, kept {kept_for_this} substantive revs")

    cache.flush()
    df = pl.DataFrame(rows) if rows else pl.DataFrame(schema={
        "market_id": pl.Int64, "wiki_page_query": pl.String, "wiki_page": pl.String,
        "page_rank": pl.Int64, "revision_id": pl.Int64,
        "revision_time": pl.String, "size": pl.Int64, "size_delta": pl.Int64,
        "comment": pl.String, "user": pl.String, "tags": pl.String,
    })
    df = df.with_columns(
        pl.col("revision_time").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC")
    )
    out_path = OUTPUT_DIR / "news_wikipedia.parquet"
    df.write_parquet(out_path)
    print(f"\nWrote {out_path}: {df.shape[0]:,} substantive revisions across {df['market_id'].n_unique()} markets")


if __name__ == "__main__":
    main()
