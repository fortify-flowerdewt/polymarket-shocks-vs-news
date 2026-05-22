"""Match Polymarket shocks to GDELT article publications.

For each shock in the shortlist, query GDELT DOC API for articles mentioning
the entities from the market question, in a ±48h window around the shock.

GDELT is rate-limited aggressively (~1 query / 5s). This script is resumable:
results are cached per-shock in output/gdelt_cache.json.

Output: output/news_gdelt.parquet — one row per (shock, article).
"""

from __future__ import annotations

import json
import re
import time
from datetime import timedelta
from pathlib import Path

import polars as pl
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

OUTPUT_DIR = Path("/Users/tom/projects/polymarketFraud/output")
CACHE_PATH = OUTPUT_DIR / "gdelt_cache.json"

GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
USER_AGENT = "polymarketFraud/0.1 (research; tom.flowerdew@wearefortify.ai)"
REQ_DELAY_S = 5.0       # 1 req per 5s to stay under throttle
WINDOW_HOURS = 48       # ±48h around the shock
MAX_RECORDS = 75


PREFIX_RE = re.compile(
    r"^\s*(?:will|did|is|are|does|do|has|have|can|should|could|would|any)\s+",
    re.IGNORECASE,
)
STOP = set("the of for in on to a an and or by at with from be as is are was were "
          "this that these those today before after new next over under into out".split())


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    retries = Retry(
        total=8, backoff_factor=5.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.verify = "/etc/ssl/cert.pem"
    return s


class JsonCache:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = json.loads(path.read_text()) if path.exists() else {}
        self._dirty = False

    def get(self, k):
        return self.data.get(k)

    def put(self, k, v):
        self.data[k] = v
        self._dirty = True

    def flush(self):
        if self._dirty:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.data))
            tmp.replace(self.path)
            self._dirty = False


def question_to_keywords(q: str) -> str:
    """Build a GDELT search query from a market question.

    Strategy: strip 'Will'/'Did' prefix, drop stop words and 'before YYYY' /
    'by Month YYYY' clauses, AND-join the remaining capitalised tokens (or all
    tokens if none are capitalised).
    """
    if not q:
        return ""
    q = PREFIX_RE.sub("", q.strip()).rstrip("?").strip()
    # Drop trailing date clauses
    q = re.sub(r"\b(by|before|after|on|in)\s+[A-Z][a-z]+\s*\d{1,2},?\s*\d{4}\??", "", q)
    q = re.sub(r"\b(by|before|after|on|in)\s+\d{4}\??", "", q)
    q = re.sub(r"\b(today|tomorrow|yesterday|tonight|this\s+week|next\s+week)\b", "", q, flags=re.I)

    tokens = re.findall(r"[A-Za-zÀ-ÿ][A-Za-z0-9À-ÿ.'-]+", q)
    caps = [t for t in tokens if t[0].isupper() and t.lower() not in STOP]
    used = caps if len(caps) >= 2 else [t for t in tokens if t.lower() not in STOP]
    used = used[:4]  # GDELT struggles with very long AND chains
    if not used:
        return ""
    # Space-separated (GDELT treats as AND) without strict quoting — quoted
    # phrases must appear verbatim, which is brittle for personal names that
    # vary across articles ("Robert Francis Prevost" vs "Robert Prevost").
    return " ".join(used)


def gdelt_artlist(sess, query, start_dt, end_dt, max_records=MAX_RECORDS):
    params = {
        "query": query, "mode": "ArtList", "format": "json",
        "maxrecords": max_records, "sort": "DateAsc",
        "startdatetime": start_dt.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end_dt.strftime("%Y%m%d%H%M%S"),
    }
    r = sess.get(GDELT_DOC, params=params, timeout=45)
    if r.status_code == 429:
        # Retry with longer wait
        time.sleep(20)
        r = sess.get(GDELT_DOC, params=params, timeout=45)
    r.raise_for_status()
    body = r.text.strip()
    if not body:
        return {"articles": []}
    try:
        return json.loads(body)
    except Exception:
        return {"articles": []}


def main() -> None:
    import os, sys
    limit = int(os.environ.get("GDELT_LIMIT", "200"))
    # Pull aligned_wiki to prioritize: largest |dp| × volume among
    # non-novelty shocks classified shock_before_news OR no_news_in_window.
    aligned = pl.read_parquet(OUTPUT_DIR / "aligned_wiki.parquet")
    import re
    NOV = re.compile(r"\bsay ['\"]|\bsay\b.*\bduring\b|\btweet|\bpost\b.*\btweets?\b|\bword\b|\bmention\b|\binterview\b|\b\d+ or more times\b|\b\d+-\d+ times\b|\bopens up or down\b", re.IGNORECASE)
    aligned = aligned.with_columns(
        pl.col("question").map_elements(lambda s: bool(NOV.search(s or "")), return_dtype=pl.Boolean).alias("is_nov"),
        (pl.col("dp").abs() * pl.col("volume")).alias("impact"),
    )
    prio = aligned.filter(~pl.col("is_nov")).sort("impact", descending=True).head(limit)
    shortlist = prio.select([
        "market_id", "shock_t", "question", "category",
        "prev_close", "close", "dp", "volume", "z_shock",
    ]).rename({"shock_t": "shock_time"})
    print(f"Querying GDELT for top {shortlist.shape[0]} substantive shocks by |Δp|·volume...")

    sess = make_session()
    cache = JsonCache(CACHE_PATH)
    rows: list[dict] = []
    last_call = 0.0

    for i, row in enumerate(shortlist.iter_rows(named=True)):
        shock_id = f"{row['market_id']}::{row['shock_time'].isoformat()}"
        query = question_to_keywords(row["question"])
        if not query:
            continue

        start = row["shock_time"] - timedelta(hours=WINDOW_HOURS)
        end = row["shock_time"] + timedelta(hours=WINDOW_HOURS)
        key = f"{shock_id}::{query}"

        cached = cache.get(key)
        if cached is None:
            # Rate-limit
            wait = REQ_DELAY_S - (time.time() - last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                res = gdelt_artlist(sess, query, start, end)
            except Exception as e:
                print(f"  [{i+1}] FAIL {shock_id} q={query!r}: {e}")
                res = {"articles": []}
            last_call = time.time()
            cache.put(key, res)
            if (i + 1) % 10 == 0:
                cache.flush()

        articles = (cached or cache.get(key) or {}).get("articles", []) or []
        for a in articles:
            rows.append({
                "market_id": row["market_id"],
                "shock_time": row["shock_time"],
                "query": query,
                "seendate": a.get("seendate"),  # YYYYMMDDTHHMMSSZ
                "title": a.get("title", ""),
                "domain": a.get("domain", ""),
                "url": a.get("url", ""),
                "language": a.get("language", ""),
                "sourcecountry": a.get("sourcecountry", ""),
            })

        if (i + 1) % 25 == 0:
            print(f"  [{i+1}/{shortlist.shape[0]}] {shock_id} q={query!r} -> {len(articles)} articles")

    cache.flush()

    df = pl.DataFrame(rows) if rows else pl.DataFrame(schema={
        "market_id": pl.Int64, "shock_time": pl.Datetime("ns", "UTC"),
        "query": pl.String, "seendate": pl.String, "title": pl.String,
        "domain": pl.String, "url": pl.String, "language": pl.String, "sourcecountry": pl.String,
    })
    df = df.with_columns(
        pl.col("seendate").str.to_datetime(format="%Y%m%dT%H%M%SZ", time_zone="UTC", strict=False).alias("article_time")
    )
    out_path = OUTPUT_DIR / "news_gdelt.parquet"
    df.write_parquet(out_path)
    print(f"\nWrote {out_path}: {df.shape[0]:,} articles across {df['market_id'].n_unique()} markets")


if __name__ == "__main__":
    main()
