# Polymarket: Shocks vs News

Does Polymarket move before public news, or in response to it? For every large probability shock on the platform we compare the timestamp of the price move with the nearest substantive edit to the related Wikipedia article. Negative Δt = shock before edit (the pattern *consistent with* insider trading); positive Δt = edit before shock (a market reacting to public news).

**[→ Live dashboard](./dashboard/dist/index.html)** · **[→ Methodology](./dashboard/dist/methodology.html)**

## What's in the repo

```
src/                    # Data pipeline (Python, polars, uv)
  shock_detector.py     # Hourly OHLCV → shock events
  news_wikipedia.py     # MediaWiki search + revision history pull
  news_gdelt.py         # GDELT 2.0 Events DOC API (rate-limited)
  alignment.py          # Pair each shock with its nearest news event
  summarise.py          # Bands, filters, dashboard data assets

dashboard/              # Observable Framework single-page app
  src/index.md          # Hero + histogram + category + tabbed exemplars
  src/methodology.md    # Method, filters, limitations
  src/data/             # Compiled summary/exemplars/price-series JSON+CSV
  dist/                 # Static build (open dist/index.html in any browser)

output/dashboard/       # The same data assets that get copied into dashboard/src/data
pyproject.toml          # uv-managed Python deps
observablehq.config.js  # Observable Framework config
```

The bulky inputs (HuggingFace dataset, intermediate parquets, ~167 MB Wikipedia cache) are *not* checked in — see [`.gitignore`](./.gitignore). The pipeline is fully resumable from cached files when you re-run.

## Method (one-paragraph version)

For each market with a high-impact shock (≥ 10 pp move, ≥ \$50k volume, ≥ 3σ relative to its own 7-day rolling vol), we resolve up to three candidate Wikipedia articles via the MediaWiki search API, pull every revision in `[market_open − 7 d, market_close + 7 d]`, and keep "substantive" ones (≥ 200 bytes net change). For each shock we record the nearest preceding/following revision and a graduated band based on `Δt = shock_time − revision_time`:

| Band                           | Threshold       | Interpretation                                     |
| ------------------------------ | --------------- | -------------------------------------------------- |
| `shock_clearly_first`          | Δt < −3 h       | Decisive: price moved hours before any edit.       |
| `shock_first_marginal`         | −3 h … −30 min  | Suggestive but not decisive.                       |
| `simultaneous_uncertain`       | ±30 min         | Inside the detector's 1-hour resolution.           |
| `news_first_marginal`          | +30 min … +3 h  | News leads but within news-cycle latency.          |
| `news_clearly_first`           | Δt > +3 h       | Decisive: market reacted to public news.           |
| `no_news_in_window`            | no revision     | Niche topic or undocumented event.                 |

Two filters keep the headline clean:

1. **Novelty filter** — drops 327 markets that resolve on broadcast minutia (tweet counts, "say X during the SOTU", etc.).
2. **Scheduled-event spuriousness filter** — drops 286 markets where there's a public-but-sub-Wikipedia information feed: elections (exit polls, partial counts), award shows, US Speaker first-ballot votes, Year-in-Search reveals, box-office unveilings. The motivating case: *"Will South Korea's presidential election winner get over 50% of the votes?"* moves on exit-poll data hours before any Wikipedia editor adds the official call.

After both filters, **559 substantive shocks remain**:

- 136 (29%) — decisive shock-first
- 146 (31%) — within ±30 min, un-adjudicable
- 103 (22%) — decisive news-first
- 91, 62, 29 across the other three bands

A separate audit panel on the dashboard lists the top filtered-out cases with the specific reason ("scheduled-event Wikipedia page (election)", "scheduled-event question pattern (win 3 Grammys)") so reviewers can sanity-check over-/under-filtering.

## Reproducing

```bash
# 1. Python deps
uv sync

# 2. Fetch the HuggingFace dataset (~900 MB)
uv run huggingface-cli download vgregoire/polymarket-users --repo-type dataset --local-dir data/

# 3. Run the pipeline (each step is resumable via local cache)
uv run python src/shock_detector.py        # data/ohlcv_1h/* + output/shocks.parquet
uv run python src/news_wikipedia.py        # output/news_wikipedia.parquet
uv run python src/alignment.py             # output/aligned_wiki.parquet
uv run python src/summarise.py             # output/dashboard/* (json + csv)

# 4. Build the dashboard
cd dashboard
cp -r ../output/dashboard/* src/data/      # if regenerating data
npx observable build                       # → dashboard/dist/

# 5. Optional: live preview during development
npx observable preview
```

The GDELT layer (`src/news_gdelt.py`) is queued as future work — when it lands, it'll layer press-publication timestamps onto the same shocks for sharper Δt resolution.

## Caveats

This is **not** a definitive insider-trading detector. The decisive-shock-first band identifies markets where the price moved hours before Wikipedia did; that's a necessary condition for trading on private information, but it's not sufficient — diplomatic leaks, fast news cycles, on-chain announcements, and analyst forecasts all generate the same signature. The methodology page enumerates the known limitations; the audit panel surfaces the most obvious false positives.

Inspired by Akey, Grégoire, Harvie & Martineau (2026), [*Who Wins and Who Loses in Prediction Markets? Evidence from Polymarket*](https://ssrn.com/abstract=6443103). The trade data comes from the `vgregoire/polymarket-users` HuggingFace dataset that accompanies that paper.
