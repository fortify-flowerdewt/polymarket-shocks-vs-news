# Polymarket: Shocks vs News + Wallet-Following Strategy

Two related research strands on Polymarket prediction-market data:

1. **Shocks vs News** — for every large probability move, compare the timestamp with the nearest substantive Wikipedia edit on the related article. Negative Δt = price moved before the encyclopedia did (consistent with insider trading); positive Δt = price reacted to public news. **[→ Live dashboard](https://fortify-flowerdewt.github.io/polymarket-shocks-vs-news/)**.

2. **Wallet-Following Strategy** — can we copy-trade the top sports-PnL wallets? Phase 0 backtest (+$1.05 M PnL on 84 k trades, hit rate 55.8 %, out-of-sample validated) lives in [`output/phase0/MEMO.md`](./output/phase0/MEMO.md). Phase 1 paper-trading shadow runner lives in [`phase1/`](./phase1/).

## What's in the repo

```
src/                            # Data-pipeline Python (polars, uv)
  shock_detection.py            # Hourly OHLCV → shock events
  news_wikipedia.py             # MediaWiki search + revision history
  alignment.py                  # Pair each shock with its nearest edit
  summarise.py                  # Bands, filters, dashboard data assets
  wallet_selection.py           # 2.48M → 62 candidate wallets (sports)
  copy_trade_backtest.py        # Simulate copying watchlist wallets
  filter_variance.py            # Post-hoc variance / Sharpe filter
  sensitivity.py                # Slippage / watchlist-size / OOS sweeps
  latency_decay.py              # Empirical $/trade vs copy latency
  phase0_memo.py                # Memo + charts generator
  strategy_dashboard_data.py    # Emits dashboard payload for /strategy

dashboard/                      # Observable Framework site
  src/index.md                  # Shocks vs News (published)
  src/methodology.md            # Method / filters / limitations (modal)
  src/data/                     # Compiled JSON/CSV — committed (deploy payload)
  _drafts/strategy.md           # Wallet-Following Strategy (hidden)

phase1/                         # Live paper-trading shadow runner
  paper_trader.py               # Daemon that watches top-5 wallets
  watchlist.json                # 5 wallets to follow
  README.md                     # How to run, what's in the ledger

output/phase0/                  # The Phase 0 memo + 3 PNG charts (committed)
```

Everything else under `output/` is **regenerable from the pipeline** and is gitignored — see [`.gitignore`](./.gitignore). The dashboard payload at `dashboard/src/data/` is the **one place** large data is committed, because the GitHub Pages deploy needs it and regenerating in CI would need the 5 GB upstream dataset.

## Reproducing the data pipeline

```bash
# 0. One-time setup
uv sync

# 1. Fetch the upstream HuggingFace dataset (~5 GB — markets, OHLCV, trades,
#    per-user aggregates from Akey, Grégoire, Harvie & Martineau 2026)
uv run hf download vgregoire/polymarket-users --repo-type dataset --local-dir data/
```

That single command pulls everything: `data/markets.parquet`, `data/events.parquet`, `data/predictions.parquet`, `data/ohlcv_1h/`, `data/user_pnl_summary.parquet`, `data/user_features.parquet`, `data/trades/` (partitioned by year/month/day).

### Strand 1 — Shocks vs News

```bash
# Build the dashboard inputs from the raw dataset (caches across re-runs)
uv run python src/shock_detection.py     # → output/shocks.parquet
uv run python src/news_wikipedia.py      # → output/news_wikipedia.parquet (+ wiki cache)
uv run python src/alignment.py           # → output/aligned_wiki.parquet
uv run python src/summarise.py           # → output/dashboard/* + dashboard/src/data/*

# Build the static site
cd dashboard && npx observable build     # → dashboard/dist/index.html, methodology.html
```

Each step is **resumable from cache** — the Wikipedia revision-history pull is the slow one (~30 min cold; ~30 s warm thanks to `output/wiki_cache.json`).

### Strand 2 — Wallet-Following Strategy backtest

```bash
# 1. Generate the candidate watchlist from per-user aggregates
CATEGORY=sports uv run python src/wallet_selection.py
#   → output/watchlist_sports.parquet (top 500 sports candidates)

# 2. Backtest copy-trading every taker fill by the watchlist
CATEGORY=sports uv run python src/copy_trade_backtest.py
#   → output/backtest_sports/{summary.json, per_wallet.csv, monthly.csv,
#                              by_category.csv, cum_pnl.parquet, trades.parquet}

# 3. Post-hoc variance filter (drops lottery wallets)
CATEGORY=sports uv run python src/filter_variance.py
#   → output/backtest_sports/{per_wallet_filtered.csv, filtered_summary.json}

# 4. Sensitivity tests
CATEGORY=sports uv run python src/sensitivity.py        # slippage + OOS + watchlist size
uv run python src/latency_decay.py                      # empirical $/trade vs delay

# 5. Generate the Phase 0 memo
uv run python src/phase0_memo.py
#   → output/phase0/{MEMO.md, cum_pnl.png, monthly_pnl.png, latency_decay.png}
```

### Strand 3 — Live paper-trading

See [`phase1/README.md`](./phase1/README.md) for the daemon. Briefly:

```bash
uv run python phase1/paper_trader.py
# polls Polymarket's data-api every 5 s for the top-5 watchlist wallets
# logs would-have-traded decisions to phase1/ledger.sqlite
# no real orders are placed
```

## Method (one-paragraph version)

For each market with a high-impact shock (≥ 10 pp move, ≥ \$50k volume, ≥ 3σ relative to its own 7-day rolling vol), we resolve up to three candidate Wikipedia articles via the MediaWiki search API, pull every revision in `[market_open − 7 d, market_close + 7 d]`, and keep "substantive" ones (≥ 200 bytes net change). For each shock we record the nearest preceding/following revision and a graduated band based on `Δt = shock_time − revision_time`:

| Band                     | Threshold       | Interpretation                                     |
| ------------------------ | --------------- | -------------------------------------------------- |
| `shock_clearly_first`    | Δt < −3 h       | Decisive: price moved hours before any edit.       |
| `shock_first_marginal`   | −3 h … −30 min  | Suggestive but not decisive.                       |
| `simultaneous_uncertain` | ±30 min         | Inside the detector's 1-hour resolution.           |
| `news_first_marginal`    | +30 min … +3 h  | News leads but within news-cycle latency.          |
| `news_clearly_first`     | Δt > +3 h       | Decisive: market reacted to public news.           |
| `no_news_in_window`      | no revision     | Niche topic or undocumented event.                 |

Two filters keep the headline clean:

1. **Novelty filter** — drops 327 markets that resolve on broadcast minutia (tweet counts, "say X during the SOTU", etc.).
2. **Scheduled-event spuriousness filter** — drops 286 markets where there's a public-but-sub-Wikipedia information feed (election day exit polls, award shows, US Speaker first-ballot votes, Year-in-Search, box-office). Motivating case: *"Will South Korea's presidential election winner get over 50% of the votes?"* moves on exit-poll data hours before any editor adds the official call.

After both filters, **559 substantive shocks remain**: 136 (29 %) decisive shock-first, 146 (31 %) within ±30 min, 103 (22 %) decisive news-first.

## Caveats

This is **not** a definitive insider-trading detector. The decisive-shock-first band identifies markets where the price moved hours before Wikipedia did; that's a necessary condition for trading on private information, but it's not sufficient — diplomatic leaks, fast news cycles, on-chain announcements, and analyst forecasts all generate the same signature. The methodology page enumerates the known limitations; the audit panel surfaces the most obvious false positives.

The wallet-following strategy has its own honest caveats: capacity ceiling around $200 k bankroll, no fee model post-March 2026, only one out-of-sample window (Jan 1–20 2026), sports-only.

Inspired by Akey, Grégoire, Harvie & Martineau (2026), [*Who Wins and Who Loses in Prediction Markets? Evidence from Polymarket*](https://ssrn.com/abstract=6443103). The trade data comes from the `vgregoire/polymarket-users` HuggingFace dataset that accompanies that paper.
