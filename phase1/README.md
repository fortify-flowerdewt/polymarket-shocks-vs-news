# Phase 1 — Paper-trading shadow runner

A daemon that watches the top-5 sports-PnL Polymarket wallets in real time
and records what a copy-trader would have done. **No real orders are placed.**

## How it works

```
        ┌─────────────────────────────────┐
        │ Polymarket data-api/trades      │
        │ poll every 5 s per wallet       │
        └──────────────┬──────────────────┘
                       │
        ┌──────────────▼─────────────────────────┐
        │ Trade reconstructor                    │
        │ side / price / size / outcome / market │
        └──────────────┬─────────────────────────┘
                       │
        ┌──────────────▼─────────────────────────┐
        │ Decision gates                         │
        │ category=Sports                        │
        │ price ∈ [0.04, 0.96]                   │
        │ market open / unresolved               │
        │ per-market cap, concurrent cap         │
        │ daily PnL stop                         │
        └──────────────┬─────────────────────────┘
                       │ pass
        ┌──────────────▼─────────────────────────┐
        │ Insert into SQLite paper_position      │
        │ (no real order)                        │
        └──────────────┬─────────────────────────┘
                       │
        ┌──────────────▼─────────────────────────┐
        │ When watched wallet exits the market,  │
        │ close the paper position (mirror-exit) │
        └────────────────────────────────────────┘
```

## Quick start

```bash
cd /Users/tom/projects/polymarketFraud
uv run python phase1/paper_trader.py
```

Tail the log:

```bash
tail -f phase1/paper_trader.log
```

Inspect the ledger any time:

```bash
sqlite3 phase1/ledger.sqlite
> SELECT COUNT(*), SUM(realised_pnl_usd) FROM paper_position WHERE status='closed';
> SELECT * FROM paper_position WHERE status='open' ORDER BY opened_at_unix DESC LIMIT 10;
> SELECT day, realised_usd FROM daily_pnl ORDER BY day DESC;
```

Stop with Ctrl-C; the daemon flushes the ledger and exits cleanly. State
persists across restarts (last-seen timestamp per wallet is stored).

## Configuration

Constants at the top of `paper_trader.py`:

| Constant | Default | Notes |
|---|---:|---|
| `BANKROLL_USD` | 10 000 | Used only for sizing math; no real funds |
| `BET_SIZE_USD` | 100 | 1 % of bankroll |
| `MAX_CONCURRENT` | 100 | Cap on simultaneous open paper positions |
| `MAX_PER_MARKET` | 200 | Cap on $ in any single market |
| `DAILY_PNL_STOP_USD` | −500 | Halt for the rest of the UTC day at this drawdown |
| `POLL_INTERVAL_S` | 5 | Per the latency-decay analysis, 5 s captures ~50 % of edge |
| `CATEGORY_FILTER` | `{"sports"}` | Phase 0 only validated sports |
| `MIN_PRICE` / `MAX_PRICE` | 0.04 / 0.96 | Mirrors backtest filter |

## What success looks like

After 1–2 weeks of running:

1. Open the ledger and compute net realised PnL.
2. Compare against the backtest expectation:
   * **~$7 / $100 bet** is the latency-decay prediction at 5 s, so ~$140
     per 20 paper-trades, etc.
3. Validate hit rate is in the **52–58 %** range (within sampling noise of
   the in-sample 55.8 % and out-of-sample 55.7 %).
4. If realised PnL is positive AND hit rate is in range → flip to real money
   with a small bankroll.

## Validation checks

* The daemon prints a summary every 60 seconds while running.
* Compare daily PnL to a fresh backtest run over the same dates (re-running
  `src/copy_trade_backtest.py` and filtering trades to the date range).
* Spot-check 5–10 individual paper positions: open the market on Polymarket
  and verify the side / price / outcome match what the daemon recorded.

## Known limitations

* **First-run "bootstrap"**: on first sighting of a wallet, `last_seen_unix`
  is set to *now*. Historical trades visible in the API are NOT
  retroactively processed — that would be back-fitting decisions to
  already-resolved trades. The daemon only acts on trades that arrive
  *after* the first poll for each wallet. Wipe `ledger.sqlite` to bootstrap
  again on a clean slate.
* **Mirror-exit fires on same-asset opposite-direction trades only.**
  Polymarket's Yes and No tokens have different `asset` IDs even though
  they're complementary positions in the same `conditionId`. If a wallet
  exits a Yes position by buying No instead of selling Yes, the daemon
  will not detect that as an exit. A planned upgrade groups by
  `(conditionId, direction-in-conditional-space)` instead. Treat the
  current realised PnL as **noisy** for that reason.
* **Market category** is determined by slug + title + event-ticker
  heuristics (`_SPORTS_RE` regex) because Gamma's `category` field is
  often empty. Non-sports trades by the same wallets are silently
  skipped, which is correct for Phase 0's sports-only scope.
* The Polymarket `data-api/trades` endpoint returns the **proxy wallet**'s
  trades. The 5 watchlist wallets are proxy wallets in the dataset, so this
  works directly — but if Polymarket later changes the proxy model the
  reconstruction logic would need to follow.
* The daemon polls once per 5 s per wallet. Bursts of >50 trades by a
  single wallet within a 5 s window would be truncated. The `limit=50`
  per-poll setting can be bumped if needed.
* No housekeeping job yet for markets that resolve while we still hold
  a paper position. PnL on those positions is unrealised until a wallet
  triggers a mirror-exit or until we add a "close on resolution" worker.

## Roadmap (next 1–2 weeks)

1. Run for 5 days, verify trades are flowing and the ledger is growing.
2. Build a simple Streamlit/Observable dashboard against `ledger.sqlite`
   (cumulative PnL, hit rate, per-wallet contribution, current open positions).
3. Compare paper PnL vs the backtest's predicted $7/$100/trade. If they
   match within tolerance, draft the live-trading switch.
4. Build the live-execution module (Polymarket CLOB API + signer).
5. Flip to live at 10 % of paper size, scale up if economics hold.
