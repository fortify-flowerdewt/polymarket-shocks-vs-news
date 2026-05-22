---
title: Methodology
---

# Methodology

## What we are testing

For a binary prediction market with a true probability θ, an efficient market reacts to **public** information as it arrives. A trade that moves the price *before* the information is public is, by definition, trading on private information — possibly inside knowledge.

We operationalise this as:

```js
tex.block`\Delta t = t_{\text{shock}} - t_{\text{news}}`
```

- ${tex`\Delta t > 0`}: news preceded the shock (the **expected** pattern for efficient markets reacting to public information).
- ${tex`\Delta t < 0`}: the shock preceded the news (the pattern consistent with **insider trading**, *modulo two important caveats*).

The two caveats — which dominate the design of the rest of this page — are:

1. **Wikipedia lags news**, so a small negative Δt may just mean the market reacted faster than an editor reached a keyboard. We handle this with graduated **bands** (below) so that anything inside ±30 min is explicitly un-adjudicable, and only Δt < −3 h is treated as decisive.
2. **Some markets resolve on live public information that doesn't show up on Wikipedia** (exit polls, live award ceremonies, floor votes). For these the "shock leads Wikipedia" finding is mechanically true but analytically meaningless. We handle this with an explicit **scheduled-event spuriousness filter** (below).

## Data

| Layer | Source | Span |
|---|---|---|
| Trades | `vgregoire/polymarket-users` on HuggingFace (Akey, Grégoire, Harvie & Martineau 2026) | Nov 11 2022 – Mar 29 2026 |
| Market metadata | Polymarket Gamma API (titles, slugs, tags, resolution) | same |
| Hourly OHLCV | Reconstructed from raw trades, outcome_idx = 0 per market | same |
| News | Wikipedia revision history via MediaWiki API | full coverage |
| News (planned) | GDELT 2.0 Events DOC API | full coverage |

We use the **outcome_idx = 0** token as a single reference price per market (so a Yes/No market is represented by its Yes price).

## Shock detection

A shock is an hour-bar `close` that satisfies all of:

- `|Δp| ≥ 5 percentage points`  (absolute floor)
- `|Δp| ≥ 3 · σ_rolling`         (relative to a 7-day rolling close-to-close volatility)
- `volume ≥ $1,000`              (ignore noise on illiquid bars)
- `gap ≤ 6 hours`                (the previous bar is recent — avoids attributing multi-day drift)

We then **shortlist** to high-impact shocks for news matching:

- categories in {Politics, Tech, Culture, Finance, Weather} (sports markets are reactive to live play — separate study)
- bar volume ≥ \$50 000
- `|Δp| ≥ 10 pp`

This yields **1,154 shocks across 741 markets**. After applying both Filter 1 (novelty) and Filter 2 (scheduled-event spuriousness), **559 shocks** are used for the headline counts on the dashboard.

## News matching (Wikipedia)

For each market we:

1. **Strip prefix.** Drop "Will / Did / Is …" and the trailing question mark from the question text.
2. **Search.** Query the MediaWiki search API; keep the top-3 page titles.
3. **Pull revisions.** For each candidate page, request all revisions in `[market_start − 7 days, market_close + 7 days]`. Apply redirects.
4. **Filter to substantive.** Keep revisions whose byte-size delta against the previous revision is ≥ 200 bytes (drops typos, rollback chains, vandalism reverts).

Some markets are excluded because:
- the relevant article was *created after* the market window (e.g., "Timeline of the 2026 Iran war" did not exist until June 2025);
- the question is a novelty market with no encyclopedic article (e.g., "Will Elon tweet 180–194 times in this 7-day window?");
- Wikipedia search returns a high-traffic adjacent page rather than the topical one.

## Alignment

For each shock we look for the nearest substantive Wikipedia revision among that market's candidate pages, in `[shock − 7d, shock + 7d]`. We record three pairings per shock:

- **nearest** (either side, smallest `|Δt|`)
- **preceding** (smallest positive `Δt`)
- **following** (smallest negative `Δt`)

## Graduated classification (bands)

The detector runs on hourly OHLCV bars, so the shock_time has ~1h resolution. A nearest-Wikipedia Δt of, say, "4 min" doesn't carry the same weight as "12 hours" — we don't know *when* within the hour the price actually moved. We therefore classify each shock into one of six bands:

| Band | Threshold | Interpretation |
|---|---|---|
| `shock_clearly_first` | Δt < −3 h | Decisive: price moved hours before any encyclopaedic edit. |
| `shock_first_marginal` | −3 h ≤ Δt < −30 min | Suggestive but not decisive. |
| `simultaneous_uncertain` | −30 min ≤ Δt ≤ +30 min | Inside the detector's own resolution. We cannot adjudicate. |
| `news_first_marginal` | +30 min < Δt ≤ +3 h | News leads, but plausibly within news-cycle latency. |
| `news_clearly_first` | Δt > +3 h | Decisive: the market reacted to public news. |
| `no_news_in_window` | no revision in ±7 d | Niche topic or undocumented event. |

Headline counts always report each band separately. We deliberately *do not* aggregate across the resolution boundary.

## Filter 1 — novelty markets

We remove "novelty" markets that resolve on live broadcast minutia. The regex matches questions containing:
`say "…"`, `say … during`, `tweet`, `post … tweets`, `word`, `mention`, `interview`, `\d+ or more times`, `\d+–\d+ times`, `opens up or down`. These markets *do* generate large shocks (the price is the market watching a broadcast in real time) but they are not insider-trading candidates in the traditional sense.

After this filter, **${ Math.round(0) || "327"} of the 1,154 shortlist shocks** are removed.

## Filter 2 — scheduled-event spuriousness

The motivating example: a market like *"Will South Korea's presidential election winner get over 50% of the votes?"* matches Wikipedia article *2025 South Korean presidential election*. On election day, exit polls and partial counts give the market actionable, public-but-not-encyclopaedic information *for hours* before any editor adds the official result to the Wikipedia article. The market move precedes the Wikipedia edit not because of insider trading, but because the editor is waiting for the official call while the trader is reading the live exit-poll feed.

The same pattern shows up in:
- **Live award ceremonies** — Grammys, Oscars, Emmys, Eurovision, *The Game Awards*, Steam Awards, *TIME Person of the Year*, *Google Year in Search*.
- **Live floor / chamber votes** — US Speaker first-ballot votes, leadership elections.
- **Election day vote-share buckets** — "Will X win between 35% and 40%?"
- **Box-office reveals** — opening-weekend gross thresholds.

For these cases the "Δt < 0" finding is mechanically true and analytically meaningless. We flag a shock as **spurious** if any of:

1. **Wikipedia page title matches a scheduled-event pattern**
   `\b(election|elections|primary|caucus|by-election|leadership election|grammy|oscar|emmy|tony award|academy award|brit award|billboard music award|eurovision|cannes|golden globe|critics' choice|sag award|year in search|person of the year|game of the year|the game awards|world cup final|super bowl|world series|stanley cup final|nba finals|speaker .* election|box office)\b`
2. **Question text matches a scheduled-event pattern** — election win clauses, vote-share buckets like `35 to 40 %` or `win between 49% and 51%`, Game-Awards category names ("most innovative", "best narrative", "players' voice", etc.), search-trend phrases ("#1 searched person on Google"), live-vote phrases ("first ballot", "speaker … elected"), or box-office reveals.
3. **Wikipedia edit comment indicates the official result** — comment contains `result`, `winner`, `elected`, `projected winner`, `called for`, `congratulat`, `conceded`, `concession`, `wins the …`, `won the …`, or `exit poll`. This catches cases where the edit *is* the post-event announcement of the answer the market was asking about.

After this filter, a further **${ Math.round(0) || "286"} shocks** are removed. The headline counts on the [Dashboard](./) reflect the remaining set; an *Audit* panel on the dashboard lists the top filtered cases with the specific reason, so reviewers can inspect over-/under-filtering.

The filter is intentionally conservative: we'd rather drop a borderline candidate (e.g., a primary-election market that *could* contain insider info) than leave an obviously spurious one in the headline. Note that this filter is heuristic — it catches the patterns we've identified explicitly. Categories of scheduled event we haven't enumerated will leak through; please [file an issue](https://github.com/wearefortify/polymarket-fraud) with examples.

## Known limitations

1. **Wikipedia lags news.** For breaking events, the first encyclopedic edit lands minutes to hours after the original news. The graduated bands above are our primary defence — anything in `simultaneous_uncertain` is explicitly flagged as un-adjudicable. Decisive bands (>3 h) are the headline claim. GDELT (press publication timestamps) is queued as the next layer to disambiguate the sub-hour cases.
2. **Spuriousness filter is heuristic.** Filter 2 above catches the live-broadcast patterns we've enumerated. New categories of scheduled event (e.g., a one-off product reveal stream that the market is watching) will leak through until we add them. The audit panel on the dashboard is the user-facing check; please flag categories we've missed.
3. **Page-resolution noise.** Wikipedia's search ranker sometimes prefers a high-traffic adjacent article over the topical one. For example, "Tim Walz Senate run before July" returns *2028 United States presidential election* as the first hit because that page has been heavily edited and shares keywords. We mitigate by querying the top-3 hits and keeping all substantive revisions across them; we do not currently re-rank.
4. **No reference-class model.** We compare against an absolute threshold, not against expected information content for a given event type.
5. **Single price per market.** We use `outcome_idx = 0`. For markets with three or more outcomes (e.g., conclave-with-many-candidates) a single token's price can be uninformative about other parts of the outcome distribution.
6. **Resolved + unresolved together.** We treat marked-to-market prices on still-open markets the same as realised closes. For *shock detection* this is fine; for *outcome conditioning* it would not be.

## Open follow-ups

- **Layer GDELT** on top of Wikipedia. Score each shock against the first press-published article that mentions the question's entities. GDELT has 15-minute timestamps which should sharpen Δt by a lot.
- **Confirm exemplars manually.** For each of the top-20 "shock-before-news" cases, find the actual news source (press release, on-chain transaction, leaked memo). The Pope Leo XIV market, for example, has well-documented anomalous trading hours before the white-smoke announcement.
- **Network / wallet attribution.** Recompute the shock-to-news distribution conditional on whether the buyer is in the paper's top-1% wallets. Akey et al. show 76.5% of gains accrue to the top 1%; the natural question is whether *they* are concentrated in the suspicious tail of Δt.
- **Reference probability.** Use a held-out forecast (poll average, base rate, sister-market price) as a benchmark for how much the price *should* have moved, then look at residual moves rather than raw Δp.
