"""Generate summary statistics and select exemplar shocks for the dashboard.

Reads:
  output/aligned_wiki.parquet
  output/shocks_shortlist.parquet
  output/shocks.parquet (full set, for per-market price series of exemplars)

Writes (everything the Observable dashboard needs):
  output/dashboard/summary.json          — top-level stats
  output/dashboard/histogram.csv         — Δt histogram bins
  output/dashboard/by_category.csv       — classification counts by category
  output/dashboard/exemplars.json        — top picks for each story
  output/dashboard/price_series/<id>.csv — hourly price series for exemplar markets
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

import polars as pl

OUTPUT_DIR = Path("/Users/tom/projects/polymarketFraud/output")
DASH_DIR = OUTPUT_DIR / "dashboard"
PRICE_DIR = DASH_DIR / "price_series"

# Markets that are "novelty" / live-broadcast tracking — exclude from headline insider analysis
NOVELTY_PATTERNS = [
    r"\bsay ['\"]", r"\bsay\b.*\bduring\b", r"\btweet", r"\bpost\b.*\btweets?\b",
    r"\bword\b", r"\bmention\b", r"\binterview\b",
    r"\b\d+ or more times\b", r"\b\d+-\d+ times\b",
    r"\bopens up or down\b",
]
NOVELTY_RE = re.compile("|".join(NOVELTY_PATTERNS), re.IGNORECASE)


def is_novelty(question: str) -> bool:
    return bool(NOVELTY_RE.search(question or ""))


# ---------------------------------------------------------------------------
# Spuriousness filter: scheduled-event / live-broadcast patterns where there
# is a public-but-not-yet-Wikipedified information feed that the market can
# react to while Wikipedia waits for the official call.
#
# Typical examples we DON'T want to flag as suspicious:
#   * Elections during/after exit polls and partial counts
#   * Live awards ceremonies (Grammy, Oscar, Eurovision, Year in Search…)
#   * Live floor votes (e.g. US Speaker first ballot)
#   * Sports outcome markets (already excluded by category filter, but belt-and-braces)
# ---------------------------------------------------------------------------

# Wikipedia page-title patterns that indicate the article is about the
# scheduled event itself rather than a long-lived encyclopaedic topic.
WIKI_SCHEDULED_RE = re.compile(
    r"\b("
    r"election|elections|primary|primaries|caucus|by-election|leadership election|"
    r"grammy|oscar|emmy|tony award|academy award|brit award|billboard music award|"
    r"eurovision|cannes|golden globe|critics' choice|sag award|"
    r"year in search|person of the year|game of the year|game awards|"
    r"the game awards|"
    r"world cup final|super bowl|world series|stanley cup final|nba finals|"
    r"speaker of the .* election|speaker election|"
    r"box office"
    r")\b",
    re.IGNORECASE,
)

# Market-question patterns that strongly suggest the market resolves on a
# live event with public sub-Wikipedia data feeds. Note: we use `\b...\b`
# word boundaries and lazy `.*?` so "win Most Innovative" (single space)
# matches the same as "win Most...Innovative" (multi-token gap).
Q_SCHEDULED_RE = re.compile(
    r"("
    # Elections, primaries, leadership votes
    r"\bwin\b.*?\b(election|primary|caucus|nomination|presidency|governorship)\b|"
    r"\b(presidential election|presidential race|governor of|senator (for|of))\b|"
    # Award shows — explicit name
    r"\b(win|claim|take|sweep)\b.*?\b(grammy|grammys|oscar|oscars|emmy|emmys|"
    r"academy award|tony award|golden globe|eurovision|"
    r"game of the year|year in search|person of the year|"
    r"box office|opening weekend)\b|"
    # Game Awards / Steam Awards categories (live broadcast announcement)
    r"\bwin\b.*?\b(most innovative|best (game|narrative|art direction|score|"
    r"music|sound|performance|game direction|esports|ongoing game|"
    r"community support|family game|indie game|action game|rpg|fighting|"
    r"sim|strategy|sports|racing)|players'? voice|game of the year|"
    r"labor of love|outstanding visual style|"
    r"better with friends|sit back and relax)\b|"
    # Year-in-Search / search trends — proxy for any public year-end reveal
    r"\b(#?1 (most )?searched|top searched|most searched|"
    r"top trending on (google|tiktok|twitter|x)|year in search)\b|"
    # Vote-share buckets and ranges (very common election-day pattern)
    r"\d+\s*to\s*\d+\s*%|"
    r"\bwin between \d+%? and \d+%?\b|"
    r"\bwin by \d+\s*-?\s*\d+%\b|"
    # Live floor / chamber votes
    r"\b(first ballot|on the first ballot)\b|"
    r"\bwin on (election|game) night\b|"
    r"\bspeaker\b.*?\b(elected|election|vote)\b|"
    r"\bwho will win\b|"
    # Box-office reveals
    r"\bopening (weekend|day) (gross|box office)\b|"
    r"\b(top|number 1|#1) at the box office\b"
    r")",
    re.IGNORECASE,
)

# Editing-comment patterns that suggest the Wikipedia edit itself is about
# adding the official result of a scheduled event (so the market's lead over
# Wikipedia is almost guaranteed by definition).
COMMENT_RESULT_RE = re.compile(
    r"\b(result|results|winner|winners|elected|projected winner|"
    r"called for|called the race|congratulat|conceded|concession|"
    r"projected to win|wins the|won the|exit poll)\b",
    re.IGNORECASE,
)


def spuriousness_reason(row) -> str | None:
    """Return a short reason string if this shock looks spurious, else None."""
    q = (row.get("question") or "")
    page = (row.get("nearest_wiki_page") or "")
    comment = (row.get("nearest_comment") or "")
    if WIKI_SCHEDULED_RE.search(page):
        return f"scheduled-event Wikipedia page ({WIKI_SCHEDULED_RE.search(page).group(0)})"
    if Q_SCHEDULED_RE.search(q):
        return f"scheduled-event question pattern ({Q_SCHEDULED_RE.search(q).group(0)})"
    if COMMENT_RESULT_RE.search(comment):
        return f"edit is the official result ({COMMENT_RESULT_RE.search(comment).group(0)})"
    return None


# Graduated classification bands. Δt = shock − news, in hours.
# Bar granularity is 1h, so anything inside ±0.5h is below detector resolution.
# Within ±3h we treat as "fast market / slow Wikipedia"; outside that is decisive.
def classify_dt(dt_hours):
    if dt_hours is None:
        return "no_news_in_window"
    if dt_hours < -3.0:
        return "shock_clearly_first"      # decisive: shock 3h+ before Wiki edit
    if dt_hours < -0.5:
        return "shock_first_marginal"     # 30 min – 3 h lead
    if dt_hours <= 0.5:
        return "simultaneous_uncertain"   # |Δt| ≤ 30 min — inside detector resolution
    if dt_hours <= 3.0:
        return "news_first_marginal"      # 30 min – 3 h news lead
    return "news_clearly_first"           # decisive: news 3h+ before shock


BAND_ORDER = [
    "shock_clearly_first", "shock_first_marginal",
    "simultaneous_uncertain",
    "news_first_marginal", "news_clearly_first",
    "no_news_in_window",
]
BAND_COLORS = {
    "shock_clearly_first":   "#dc2626",
    "shock_first_marginal":  "#fb923c",
    "simultaneous_uncertain":"#a3a3a3",
    "news_first_marginal":   "#86efac",
    "news_clearly_first":    "#16a34a",
    "no_news_in_window":     "#525252",
}
BAND_LABELS = {
    "shock_clearly_first":   "Shock clearly first (>3h before edit)",
    "shock_first_marginal":  "Shock first, marginal (30 min–3 h)",
    "simultaneous_uncertain":"Within ±30 min (below detector resolution)",
    "news_first_marginal":   "News first, marginal (30 min–3 h)",
    "news_clearly_first":    "News clearly first (>3h before shock)",
    "no_news_in_window":     "No Wikipedia edit in ±7 d",
}


def main() -> None:
    DASH_DIR.mkdir(parents=True, exist_ok=True)
    PRICE_DIR.mkdir(parents=True, exist_ok=True)

    aligned = pl.read_parquet(OUTPUT_DIR / "aligned_wiki.parquet")
    # Use a native polars expression for band assignment so nulls
    # propagate correctly to "no_news_in_window" rather than getting dropped.
    dt = pl.col("dt_nearest_hours")
    band_expr = (
        pl.when(dt.is_null()).then(pl.lit("no_news_in_window"))
        .when(dt < -3.0).then(pl.lit("shock_clearly_first"))
        .when(dt < -0.5).then(pl.lit("shock_first_marginal"))
        .when(dt <= 0.5).then(pl.lit("simultaneous_uncertain"))
        .when(dt <= 3.0).then(pl.lit("news_first_marginal"))
        .otherwise(pl.lit("news_clearly_first"))
        .alias("band")
    )
    aligned = aligned.with_columns(
        pl.col("question").map_elements(is_novelty, return_dtype=pl.Boolean).alias("is_novelty"),
        band_expr,
        (pl.col("dt_nearest_hours") * 60).alias("dt_nearest_minutes"),
        pl.col("dt_nearest_hours").abs().alias("abs_dt_hours"),
    )

    # Spuriousness flag — applied row-wise from the Python heuristic above.
    spur = aligned.select(["question", "nearest_wiki_page", "nearest_comment"]).to_dicts()
    reasons = [spuriousness_reason(r) for r in spur]
    aligned = aligned.with_columns(
        pl.Series("spurious_reason", reasons, dtype=pl.String),
        pl.Series("is_spurious", [r is not None for r in reasons], dtype=pl.Boolean),
    )
    n_spur = sum(1 for r in reasons if r is not None)
    print(f"Flagged {n_spur}/{aligned.shape[0]} shocks as scheduled-event / spurious")

    # Work with substantive (non-novelty) markets for headline stats
    sub = aligned.filter(~pl.col("is_novelty"))

    # 1. Top-level summary — `sub` excludes novelty AND scheduled-event spurious cases.
    sub_full = aligned.filter(~pl.col("is_novelty"))   # for context
    sub = aligned.filter(~pl.col("is_novelty") & ~pl.col("is_spurious"))   # headline view
    total = sub.shape[0]
    n_class = sub.group_by("classification").len().to_dict(as_series=False)
    class_counts = dict(zip(n_class["classification"], n_class["len"]))
    n_band = sub.group_by("band").len().to_dict(as_series=False)
    band_counts = dict(zip(n_band["band"], n_band["len"]))
    # Ensure every band key is present, even if zero
    for k in BAND_ORDER:
        band_counts.setdefault(k, 0)
    # Spurious-only counts for transparency
    n_band_spur = sub_full.filter(pl.col("is_spurious")).group_by("band").len().to_dict(as_series=False)
    band_counts_spurious = dict(zip(n_band_spur["band"], n_band_spur["len"]))
    for k in BAND_ORDER:
        band_counts_spurious.setdefault(k, 0)
    median_dt = sub.filter(pl.col("dt_nearest_hours").is_not_null()).select(pl.col("dt_nearest_hours").median()).item()
    matched = total - band_counts["no_news_in_window"]
    summary = {
        "n_shocks_in_shortlist": total,
        "n_shocks_in_shortlist_all": aligned.shape[0],
        "n_excluded_novelty": int(aligned["is_novelty"].sum()),
        "n_excluded_spurious": int(sub_full["is_spurious"].sum()),
        "classification_counts": class_counts,    # legacy binary
        "band_counts": band_counts,                # graduated, headline (excludes spurious)
        "band_counts_spurious": band_counts_spurious,  # transparency: what we filtered out
        "band_order": BAND_ORDER,
        "band_colors": BAND_COLORS,
        "band_labels": BAND_LABELS,
        "median_dt_nearest_hours": median_dt,
        "share_shock_clearly_first": band_counts["shock_clearly_first"] / max(matched, 1),
        "share_shock_first_marginal": band_counts["shock_first_marginal"] / max(matched, 1),
        "share_simultaneous_uncertain": band_counts["simultaneous_uncertain"] / max(matched, 1),
        "share_news_first_marginal": band_counts["news_first_marginal"] / max(matched, 1),
        "share_news_clearly_first": band_counts["news_clearly_first"] / max(matched, 1),
        "wiki_caveat": (
            "Δt is shock_time − Wikipedia_revision_time. Negative values mean the Polymarket "
            "shock preceded a Wikipedia edit. Two important nuances: "
            "(1) Wikipedia is itself edited only after a story breaks, so a small negative Δt "
            "is consistent with both insider trading AND a market reacting to public news faster "
            "than a Wikipedia editor reaches a keyboard. "
            "(2) Our shock_time is the start of an hourly OHLCV bar, so |Δt| ≤ 30 min is below "
            "the detector's own resolution and we mark these as 'simultaneous_uncertain'. "
            "Decisive cases are those with |Δt| > 3 h. GDELT (press-publication timestamps) is "
            "the next layer to disambiguate sub-hour cases."
        ),
    }
    (DASH_DIR / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print("Wrote summary.json:", json.dumps(summary, indent=2, default=str))

    # 2. Δt histogram (hours), with reasonable bins
    hist = sub.filter(pl.col("dt_nearest_hours").is_not_null()).select("dt_nearest_hours")
    bins = [-168, -72, -24, -8, -2, -0.5, 0, 0.5, 2, 8, 24, 72, 168]
    labels = [f"[{bins[i]}, {bins[i+1]})" for i in range(len(bins) - 1)]
    counts = []
    for i, label in enumerate(labels):
        lo, hi = bins[i], bins[i+1]
        c = hist.filter((pl.col("dt_nearest_hours") >= lo) & (pl.col("dt_nearest_hours") < hi)).shape[0]
        counts.append({"bin": label, "lo": lo, "hi": hi, "count": c})
    pl.DataFrame(counts).write_csv(DASH_DIR / "histogram.csv")
    print(f"\nWrote histogram.csv ({len(counts)} bins)")

    # 3. By-category band table (graduated)
    by_cat = (
        sub.group_by(["category", "band"]).len()
        .pivot(index="category", on="band", values="len", aggregate_function="first")
        .fill_null(0)
    )
    # Ensure every band column exists even if zero in this dataset
    for b in BAND_ORDER:
        if b not in by_cat.columns:
            by_cat = by_cat.with_columns(pl.lit(0).alias(b))
    by_cat = by_cat.select(["category", *BAND_ORDER])
    by_cat.write_csv(DASH_DIR / "by_category.csv")
    print("Wrote by_category.csv with graduated bands")

    # 4. Exemplars: top picks per story (drawn from `sub`, which already
    # excludes both novelty and scheduled-event spurious cases).
    def top(filter_expr, sort_expr, k=10):
        return (
            sub.filter(filter_expr)
            .sort(sort_expr)
            .head(k)
            .select([
                "market_id", "shock_t", "question", "category", "dp", "volume", "z_shock",
                "dt_nearest_hours", "dt_nearest_minutes", "abs_dt_hours",
                "nearest_wiki_page", "nearest_comment", "classification", "band",
                "is_spurious", "spurious_reason",
            ])
            .to_dicts()
        )

    # Also surface what we filtered out, so users can audit the heuristic.
    def top_spurious(filter_expr, sort_expr, k=10):
        return (
            sub_full.filter(filter_expr & pl.col("is_spurious"))
            .sort(sort_expr)
            .head(k)
            .select([
                "market_id", "shock_t", "question", "category", "dp", "volume",
                "dt_nearest_hours", "nearest_wiki_page", "nearest_comment",
                "band", "spurious_reason",
            ])
            .to_dicts()
        )

    exemplars = {
        # Decisive shock-first (>3h lead) — the cases that survive bar-resolution caveats.
        "shock_clearly_first_top10": top(
            pl.col("band") == "shock_clearly_first",
            pl.col("dp").abs() * -1,
        ),
        # Marginal shock-first (30 min – 3 h lead).
        "shock_first_marginal_top10": top(
            pl.col("band") == "shock_first_marginal",
            pl.col("dp").abs() * -1,
        ),
        # Near-simultaneous (|Δt| ≤ 30 min) — biggest-impact cases that we cannot adjudicate.
        "simultaneous_top10": top(
            pl.col("band") == "simultaneous_uncertain",
            pl.col("dp").abs() * -1,
        ),
        # Cleanest news-leads-shock (>3h news lead).
        "news_clearly_first_top10": top(
            pl.col("band") == "news_clearly_first",
            pl.col("dp").abs() * -1,
        ),
        # Marginal news-first (30 min – 3 h).
        "news_first_marginal_top10": top(
            pl.col("band") == "news_first_marginal",
            pl.col("dp").abs() * -1,
        ),
        # No-news in window (potential undocumented events).
        "no_news_top10": top(
            pl.col("band") == "no_news_in_window",
            pl.col("dp").abs() * -1,
        ),
        # Audit bucket: cases we removed because they look like scheduled events.
        "filtered_spurious_top10": top_spurious(
            (pl.col("band") == "shock_clearly_first") | (pl.col("band") == "shock_first_marginal"),
            pl.col("dp").abs() * -1,
        ),
    }
    (DASH_DIR / "exemplars.json").write_text(json.dumps(exemplars, indent=2, default=str))
    print(f"Wrote exemplars.json ({sum(len(v) for v in exemplars.values())} rows across {len(exemplars)} buckets)")

    # 5. Hourly price series for each exemplar market, by scanning OHLCV
    DATA_DIR = Path("/Users/tom/projects/polymarketFraud/data")
    pred = pl.read_parquet(DATA_DIR / "predictions.parquet").filter(pl.col("outcome_idx") == 0)
    exemplar_market_ids = {row["market_id"] for sublist in exemplars.values() for row in sublist}
    exemplar_pred = pred.filter(pl.col("market_id").is_in(exemplar_market_ids))
    pred_to_market = dict(zip(exemplar_pred["prediction_id"].to_list(), exemplar_pred["market_id"].to_list()))
    pred_ids = list(pred_to_market.keys())
    if pred_ids:
        ohlcv = (
            pl.scan_parquet(DATA_DIR / "ohlcv_1h" / "**" / "*.parquet")
            .filter(pl.col("prediction_id").is_in(pred_ids))
            .select(["prediction_id", "timestamp", "open", "high", "low", "close", "volume", "trade_count"])
            .collect()
        )
        # Per-market CSVs (kept for ad-hoc inspection / external use)
        for pid, mid in pred_to_market.items():
            series = ohlcv.filter(pl.col("prediction_id") == pid).sort("timestamp")
            if series.shape[0]:
                series.write_csv(PRICE_DIR / f"{mid}.csv")
        # Single combined JSON keyed by market_id — used by Observable Framework
        # because FileAttachment requires literal-string paths at build time.
        combined = {}
        for pid, mid in pred_to_market.items():
            series = ohlcv.filter(pl.col("prediction_id") == pid).sort("timestamp")
            if series.shape[0]:
                combined[str(mid)] = [
                    {"timestamp": ts.isoformat(), "close": close}
                    for ts, close in zip(
                        series["timestamp"].to_list(),
                        series["close"].to_list(),
                    )
                ]
        (DASH_DIR / "price_series.json").write_text(json.dumps(combined, default=str))
        print(f"Wrote {len(pred_to_market)} per-market price series (CSV + combined JSON)")


if __name__ == "__main__":
    main()
