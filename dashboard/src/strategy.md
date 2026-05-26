---
title: Polymarket — Wallet-Following Strategy
toc: false
---

```js
const summary           = await FileAttachment("data/strategy/summary.json").json();
const walletFunnel      = await FileAttachment("data/strategy/wallet_funnel.json").json();
const categoryCompare   = await FileAttachment("data/strategy/category_compare.json").json();
const varianceCompare   = await FileAttachment("data/strategy/variance_compare.json").json();
const latency           = await FileAttachment("data/strategy/latency.csv").csv({typed: true});
const slippage          = await FileAttachment("data/strategy/slippage.csv").csv({typed: true});
const watchlistSizes    = await FileAttachment("data/strategy/watchlist_sizes.csv").csv({typed: true});
const oos               = await FileAttachment("data/strategy/oos.json").json();
const capacity          = await FileAttachment("data/strategy/capacity.json").json();
const cumPnl            = await FileAttachment("data/strategy/cum_pnl.csv").csv({typed: true});
const monthlyPnl        = await FileAttachment("data/strategy/monthly_pnl.csv").csv({typed: true});
```

```js
const fmtN     = (n) => d3.format(",")(n);
const fmtPct   = (p) => d3.format(".1%")(p);
const fmt$     = (v) => d3.format("$,.0f")(v);
const fmt$k    = (v) => v >= 1e6 ? `$${(v/1e6).toFixed(2)}M` : v >= 1e3 ? `$${(v/1e3).toFixed(0)}k` : `$${v.toFixed(0)}`;
const fmt$sign = (v) => `${v >= 0 ? "+" : "−"}${fmt$k(Math.abs(v))}`;
```

<div class="hero">
  <div class="kicker">Polymarket trading strategy</div>
  <h1>Following the top 1 % of sports-PnL wallets</h1>
  <div class="hero-sub">An end-to-end Phase-0 backtest of a copy-trading strategy on Polymarket. We walk through each design decision — who to follow, which markets, how much variance to tolerate, how fast to execute, how much slippage we can absorb — and how each one moves the numbers.</div>
  <div class="kpi-strip">
    <div class="kpi">
      <span class="kpi-n">${fmtN(summary.n_trades_backtest)}</span>
      <span class="kpi-l">copy-trades simulated</span>
    </div>
    <div class="kpi">
      <span class="kpi-n">${fmtPct(summary.hit_rate)}</span>
      <span class="kpi-l">hit rate</span>
    </div>
    <div class="kpi kpi-accent">
      <span class="kpi-n">${fmt$k(summary.total_pnl_usd)}</span>
      <span class="kpi-l">backtest PnL</span>
    </div>
    <div class="kpi kpi-accent">
      <span class="kpi-n">$${summary.pnl_per_100_bet.toFixed(2)}</span>
      <span class="kpi-l">per $100 bet</span>
    </div>
    <div class="kpi">
      <span class="kpi-n">${summary.n_wallets_after_filter}</span>
      <span class="kpi-l">wallets to follow</span>
    </div>
  </div>
</div>

<div class="lede">
  <p>
    Akey, Grégoire, Harvie &amp; Martineau (2026) show that <strong>the top 1 % of Polymarket wallets capture 76 % of all gains</strong>. The natural follow-on question is whether we can <em>copy</em> them. This dashboard walks through the design decisions that made that strategy work — and the ones that nearly killed it.
  </p>
</div>

---

## Decision 1 — Who to follow?

<div class="decision">

**Question.** Of 2.48 M Polymarket wallets, which should be on our watchlist?

**Tension.** Take a wide net (more signal, more noise) vs. a narrow one (less data, fewer adverse-selection traps). Top-by-cumulative-PnL is the obvious starting point, but it includes lottery winners and institutional market-makers whose profits don't actually transfer to a follower.

**Decision.** Apply a funnel of conservative filters. Drop wallets that can't pass *all* of them.

</div>

```js
const funnelTable = document.createElement("div");
funnelTable.className = "funnel";
for (let i = 0; i < walletFunnel.length; i++) {
  const s = walletFunnel[i];
  const prev = i === 0 ? walletFunnel[0].n : walletFunnel[i-1].n;
  const pct = s.n / walletFunnel[0].n;
  const row = document.createElement("div");
  row.className = "funnel-row";
  row.innerHTML = `
    <div class="funnel-step">${i + 1}. ${s.step}</div>
    <div class="funnel-bar-wrap">
      <div class="funnel-bar" style="width: ${(Math.log10(s.n+1) / Math.log10(walletFunnel[0].n)) * 100}%"></div>
      <div class="funnel-count">${fmtN(s.n)}</div>
    </div>
    <div class="funnel-why">${s.reason}</div>
  `;
  funnelTable.append(row);
}
display(funnelTable);
```

<div class="callout">
  <strong>Result:</strong> 2.48 M → <strong>62 wallets</strong>. Aggregate $36 M of sports-resolved PnL. Median wallet has 2 300+ trades across 235 markets.
</div>

---

## Decision 2 — Which markets to trade?

<div class="decision">

**Question.** The same wallets trade across sports, politics, crypto, finance. Should we mirror them everywhere or pick a category?

**Tension.** Each category has different microstructure. Crypto markets in particular are dominated by high-frequency market makers whose "taker" trades are hedges, not directional alpha.

**Decision.** Run the same strategy across all categories, then per-category. Where the signal flips sign, you have your answer.

</div>

```js
const catData = categoryCompare;
const catGrid = document.createElement("div");
catGrid.className = "cat-grid";
for (const c of catData) {
  const card = document.createElement("div");
  card.className = "cat-card";
  card.style.borderLeftColor = c.color;
  card.innerHTML = `
    <div class="cat-name">${c.variant}</div>
    <div class="cat-stats">
      <span><b>${fmtN(c.n_trades)}</b> trades</span>
      <span><b style="color:${c.color}">${fmt$sign(c.pnl)}</b> total PnL</span>
      <span><b>$${c.pnl_per_100.toFixed(2)}</b> per $100 bet</span>
      <span><b>${(c.hit_rate * 100).toFixed(1)} %</b> hit rate</span>
    </div>
    <div class="cat-note">${c.note}</div>
  `;
  catGrid.append(card);
}
display(catGrid);
```

<div class="callout warn">
  <strong>The all-categories run loses $10 M.</strong> The all-categories hit rate (58 %) actually beats the sports-only hit rate (51 %), but the losses are concentrated in <em>crypto markets where wallets' wins come from their maker side, not their takes</em>. Lesson: hit rate alone is misleading; per-trade economics matter.
</div>

---

## Decision 3 — Filter out lucky wallets

<div class="decision">

**Question.** Of the 1 400 wallets that pass the basic filters, some have great PnL because they hit a few longshot bets. Do we keep them?

**Tension.** Drop them and the watchlist shrinks dramatically; keep them and PnL is dominated by a few outliers that may not repeat.

**Decision.** Apply a layered variance filter: drop wallets with high extreme-price exposure, high longshot share, or whose per-trade PnL distribution is dominated by a few big wins (Sharpe-analog &lt; 0.04). The set shrinks 390 → 62 — but per-trade economics improve 6×.

</div>

<div class="variance-grid">

```js
const wrap = document.createElement("div");
wrap.className = "variance-side";
wrap.innerHTML = `<h4>Before filter — top wallets</h4>`;
const tBefore = document.createElement("div");
tBefore.className = "wallet-list";
for (const w of varianceCompare.before) {
  const row = document.createElement("div");
  row.className = "wallet-row";
  row.innerHTML = `
    <span class="wmono">${w.wallet}</span>
    <span class="whit">${(w.hit_rate * 100).toFixed(0)} %</span>
    <span class="wpnl">${fmt$k(w.pnl)}</span>
    <span class="wstyle">${w.style}</span>
  `;
  tBefore.append(row);
}
wrap.append(tBefore);
display(wrap);
```

```js
const wrap2 = document.createElement("div");
wrap2.className = "variance-side";
wrap2.innerHTML = `<h4>After filter — top wallets</h4>`;
const tAfter = document.createElement("div");
tAfter.className = "wallet-list";
for (const w of varianceCompare.after) {
  const row = document.createElement("div");
  row.className = "wallet-row";
  row.innerHTML = `
    <span class="wmono">${w.wallet}</span>
    <span class="whit">${(w.hit_rate * 100).toFixed(0)} %</span>
    <span class="wpnl">${fmt$k(w.pnl)}</span>
    <span class="wstyle">${w.style} · Sharpe ${w.sharpe.toFixed(3)}</span>
  `;
  tAfter.append(row);
}
wrap2.append(tAfter);
display(wrap2);
```

</div>

<div class="callout">
  <strong>The "long-shot lottery" wallet (`0x1d94…9100`, 42 % hit rate, $700 k PnL) disappears.</strong> Its profit came from a handful of underdog wins that happened to pay off. By the time we reach the final filter, the surviving wallets all have 50 %+ hit rates and per-trade Sharpe-analog &gt; 0.04 — i.e., their edge survives normal trade-to-trade variance.
</div>

---

## Decision 4 — How fast do we need to execute?

<div class="decision">

**Question.** When our watchlist wallet hits the ask, the price might move before we can mirror them. How much edge do we lose to latency?

**Tension.** Sub-second execution is expensive (mempool monitoring, pre-signed tx, co-located node). 5-second execution is commodity infrastructure. Where's the break-even?

**Decision.** Empirically measure the decay curve. For each survivor trade, look up the next observed trade in the same market+outcome at delay $T$ and use that as our delayed entry.

</div>

```js
const latencyChart = (() => {
  const reliable  = latency.filter(d => d.reliable && d.delay_s <= 3600);
  const fallback  = latency.filter(d => !d.reliable || d.delay_s > 3600);
  return Plot.plot({
    height: 280,
    marginLeft: 50,
    marginBottom: 38,
    x: {
      type: "symlog",
      label: "Latency between watchlist fill and our copy (seconds)",
      ticks: [0, 5, 30, 60, 300, 900, 3600, 21600],
      tickFormat: t => t === 0 ? "0" : t < 60 ? `${t}s` : t < 3600 ? `${t/60}m` : t < 86400 ? `${t/3600}h` : `${t/86400}d`,
    },
    y: {label: "$ per $100 bet", grid: true},
    marks: [
      Plot.ruleY([0], {stroke: "#444", strokeOpacity: 0.4}),
      Plot.line(reliable, {x: "delay_s", y: "pnl_per_trade", stroke: "#1d4ed8", strokeWidth: 2.5}),
      Plot.dot(reliable, {x: "delay_s", y: "pnl_per_trade", r: 5, fill: "#1d4ed8", title: d => `${d.delay_s}s: $${d.pnl_per_trade.toFixed(2)} (match ${(d.match_rate*100).toFixed(1)}%)`}),
      Plot.line(fallback,  {x: "delay_s", y: "pnl_per_trade", stroke: "#94a3b8", strokeWidth: 1.2, strokeDasharray: "4,3"}),
      Plot.dot(fallback,  {x: "delay_s", y: "pnl_per_trade", r: 4, fill: "#94a3b8", opacity: 0.7}),
      Plot.ruleX([5], {stroke: "#dc2626", strokeDasharray: "5,3"}),
      Plot.text([{x: 5, y: latency.find(d => d.delay_s === 5).pnl_per_trade + 1}], {x: "x", y: "y", text: ["5 s target"], fill: "#dc2626", fontSize: 11, textAnchor: "start", dx: 6}),
    ],
  });
})();
display(latencyChart);
```

<div class="callout">
  <strong>The cliff is in the first 5 seconds.</strong> Going from instant to 5 s execution loses about half the edge — from $15.78 to $6.98 per $100 bet. After that, the curve is essentially flat until 15 minutes. This is sport-specific: information that moves the market reaches other fast actors within seconds. For Phase 1 we target <strong>5 s</strong>: achievable with off-the-shelf tooling (Goldsky subgraph polling), and still cleanly profitable.
</div>

---

## Decision 5 — How much slippage can we absorb?

<div class="decision">

**Question.** We mirror the watchlist wallet's price exactly, but we'll usually need to cross the spread again — slippage. How much can we eat before the edge dies?

**Tension.** Polymarket's standard tick is $0.01. Real-world execution might cost 1–3 ticks beyond the watchlist's fill price. Strategy needs to survive that.

**Decision.** Re-run the backtest at 0 / 1 / 2 / 3 ticks slippage and find the break-even.

</div>

```js
const slipChart = Plot.plot({
  height: 230,
  marginLeft: 60,
  x: {label: "Slippage (ticks beyond the watchlist's fill price)", domain: [-0.4, 3.4]},
  y: {label: "$ per $100 bet", grid: true, domain: [0, 17]},
  marks: [
    Plot.ruleY([0], {stroke: "#444", strokeOpacity: 0.3}),
    Plot.barY(slippage, {x: "ticks", y: "pnl_per_100", fill: d => d.pnl_per_100 >= 5 ? "#16a34a" : d.pnl_per_100 >= 0 ? "#fb923c" : "#dc2626", inset: 25}),
    Plot.text(slippage, {x: "ticks", y: "pnl_per_100", text: d => `$${d.pnl_per_100.toFixed(2)}`, dy: -10, fontSize: 11, fontWeight: 600}),
  ],
})
display(slipChart);
```

<div class="callout">
  <strong>The strategy survives 3 ticks of adverse slippage</strong> — $7.15 per $100 bet, still solidly positive. Real Polymarket sports markets are typically 1–2 ticks wide; we have margin.
</div>

---

## Validation — does it generalise out-of-sample?

<div class="decision">

**Question.** The 62 wallets were picked on data ending Dec 2025. Do they continue to deliver on Jan 2026 data they had no visibility into?

**Decision.** Take the same watchlist, point it at the 1–20 Jan 2026 trades, recompute. If hit rate and per-trade economics hold, the selection isn't an artefact of in-sample noise.

</div>

```js
const oosWrap = document.createElement("div");
oosWrap.className = "oos-grid";
for (const [k, d] of [["In-sample", oos.in_sample], ["Out-of-sample", oos.out_of_sample]]) {
  const card = document.createElement("div");
  card.className = "oos-card";
  if (k === "Out-of-sample") card.classList.add("oos-card-oos");
  card.innerHTML = `
    <h4>${d.label}</h4>
    <div class="oos-stats">
      <div><span>${fmtN(d.n_trades)}</span><label>trades</label></div>
      <div><span>${(d.hit_rate*100).toFixed(1)} %</span><label>hit rate</label></div>
      <div><span>${fmt$k(d.pnl_total)}</span><label>PnL</label></div>
      <div><span>$${d.pnl_per_100.toFixed(2)}</span><label>per $100 bet</label></div>
    </div>
  `;
  oosWrap.append(card);
}
display(oosWrap);
```

<div class="callout success">
  <strong>Hit rate is identical (55.8 % vs 55.7 %).</strong> Per-trade economics drop from $12.52 to $9.07 — within sampling noise and partly Jan seasonality (US sports calendar is light pre-NCAA tournament). The watchlist generalises.
</div>

---

## Cumulative PnL (in-sample + first 20 days OOS)

```js
const cumChart = Plot.plot({
  height: 280,
  marginLeft: 60,
  x: {type: "utc", label: null},
  y: {label: "Cumulative PnL ($)", grid: true, tickFormat: d => d3.format("$.2~s")(d).replace("G","B")},
  marks: [
    Plot.ruleY([0], {stroke: "#444", strokeOpacity: 0.4}),
    Plot.areaY(cumPnl, {x: "date", y: "cum_pnl", fill: "#1d4ed8", fillOpacity: 0.15}),
    Plot.lineY(cumPnl, {x: "date", y: "cum_pnl", stroke: "#1d4ed8", strokeWidth: 2}),
  ],
});
display(cumChart);
```

```js
const mChart = Plot.plot({
  height: 200,
  marginLeft: 60,
  x: {label: null, tickRotate: -40},
  y: {label: "Monthly PnL", grid: true, tickFormat: d => d3.format("$.2~s")(d).replace("G","B")},
  marks: [
    Plot.ruleY([0], {stroke: "#444", strokeOpacity: 0.5}),
    Plot.barY(monthlyPnl, {x: "month", y: "pnl", fill: d => d.pnl > 0 ? "#22c55e" : "#dc2626"}),
  ],
});
display(mChart);
```

---

## Reality check — realistic annual PnL by bankroll

<div class="decision">

**Question.** The backtest aggregates $1 M PnL across all hypothetical $100 bets. But how much capital can you actually deploy? Real-world capacity is bounded by concurrent positions and adverse-selection risk.

</div>

```js
const capWrap = document.createElement("div");
capWrap.className = "cap-table";
const head = document.createElement("div");
head.className = "cap-row cap-head";
head.innerHTML = `<div>Bankroll</div><div>$/trade</div><div>Concurrent</div><div>Daily PnL</div><div>Annual PnL (range)</div><div></div>`;
capWrap.append(head);
for (const c of capacity) {
  const row = document.createElement("div");
  row.className = "cap-row";
  row.innerHTML = `
    <div>${fmt$k(c.bankroll)}</div>
    <div>$${c.bet}</div>
    <div>${c.concurrent}</div>
    <div>${fmt$k(c.daily_pnl)}</div>
    <div>${fmt$k(c.annual_pnl_low)} – ${fmt$k(c.annual_pnl_high)}</div>
    <div class="cap-note">${c.note ?? ""}</div>
  `;
  capWrap.append(row);
}
display(capWrap);
```

<div class="callout warn">
  <strong>Capacity ceiling around $200 k bankroll.</strong> Beyond that, the strategy degrades because we'd consume too much of the daily flow ourselves, creating adverse selection. Phase 1 starts at $10 k — well within the capacity envelope.
</div>

---

## Caveats

<div class="caveats">

* **No fees modeled.** Polymarket was 0 % takers through Mar 2026. Post-fee economics would knock ~$1 / $100 off the per-trade edge.
* **Latency-decay coverage.** The 5 s cliff is computed on the cleanly-typed subset (15.7 k of 84 k trades — those with non-null `outcome` labels). Extrapolating to the full 84 k assumes similar dynamics.
* **One out-of-sample window.** Jan 1–20 2026 is 20 days. A longer OOS run is needed before scaling to real money — that's the next thing the Phase-1 paper trader is for.
* **Sports only.** The same selection logic on crypto / politics gave the opposite sign on PnL.
* **Capacity ceiling.** $200 k is approximate; beyond it the strategy degrades on adverse selection.
* **Mirror-exit assumption.** Phase 0 backtest treats each taker trade as a hold-to-resolution bet. Phase 1 implements true mirror-exit (close when watchlist wallet exits) — economics may differ.

</div>

---

## What we're doing about it — Phase 1

A paper-trading shadow runner is live:

* Polls Polymarket's data-api every 5 s for new trades by the **top-5 watchlist** wallets
* Applies the same gates we used in the backtest (sports, price band 0.04–0.96, market open, $200 per-market cap, $500/day stop)
* Logs every decision to SQLite — **no real orders** placed
* Mirrors exits when the watchlist wallet flips direction

Code: [`phase1/paper_trader.py`](https://github.com/fortify-flowerdewt/polymarket-shocks-vs-news/blob/main/phase1/paper_trader.py).

After **1–2 weeks of live data collection**, we compare paper-trade PnL against the $7 / $100 bet target predicted by the latency-decay analysis. If they match, the next step is a live-execution module against the Polymarket CLOB API.

<style>
.hero { background: linear-gradient(135deg, rgba(59,130,246,0.06), rgba(220,38,38,0.04));
        border-radius: 12px; padding: 1.5rem 1.75rem; margin-bottom: 1.5rem;
        border: 1px solid var(--dash-border); box-shadow: var(--dash-shadow); }
.hero .kicker { font-size: 0.78rem; letter-spacing: 0.14em; text-transform: uppercase;
        color: var(--dash-fg-muted); margin-bottom: 0.5rem; }
.hero h1 { margin: 0; font-size: 1.9rem; line-height: 1.1; }
.hero-sub { color: var(--dash-fg-muted); margin: .5rem 0 1.25rem 0; max-width: 80ch; font-size: 0.95rem; }
.kpi-strip { display: grid; grid-template-columns: repeat(5, 1fr); gap: .5rem; }
.kpi { padding: .65rem .8rem; border-radius: 6px; background: var(--dash-panel);
       border: 1px solid var(--dash-border); display: flex; flex-direction: column; gap: .15rem; }
.kpi.kpi-accent { background: rgba(59,130,246,0.08); border-color: rgba(59,130,246,0.25); }
.kpi-n { font-size: 1.5rem; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1; }
.kpi-l { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--dash-fg-muted); }
.lede { font-size: 1.05rem; line-height: 1.55; color: #334155; margin: 1rem 0 2rem 0; max-width: 80ch; }

.decision { background: rgba(15,23,42,0.04); border-left: 4px solid #1d4ed8;
            padding: 0.9rem 1.1rem; border-radius: 4px; margin: 1rem 0 1.25rem 0;
            font-size: 0.95rem; line-height: 1.55; }
.decision p:first-child { margin-top: 0; } .decision p:last-child { margin-bottom: 0; }

.callout { padding: 0.7rem 1rem; border-radius: 6px; background: #f0fdf4;
           border-left: 4px solid #16a34a; margin: 1rem 0 2rem 0; font-size: 0.95rem; }
.callout.warn    { background: #fff7ed; border-left-color: #fb923c; }
.callout.success { background: #ecfdf5; border-left-color: #10b981; }

.funnel { display: flex; flex-direction: column; gap: 0.3rem; margin: 1rem 0; }
.funnel-row { display: grid; grid-template-columns: 240px 1fr 1.8fr; gap: 0.75rem; align-items: center;
              padding: .35rem .5rem; background: var(--dash-panel); border-radius: 4px;
              border: 1px solid var(--dash-divider); font-size: 0.85rem; }
.funnel-step { font-weight: 500; }
.funnel-bar-wrap { position: relative; height: 22px; }
.funnel-bar { background: linear-gradient(to right, #1d4ed8, #60a5fa);
              height: 100%; border-radius: 3px; }
.funnel-count { position: absolute; right: 6px; top: 50%; transform: translateY(-50%);
                font-size: 0.78rem; color: #fff; font-variant-numeric: tabular-nums;
                text-shadow: 0 1px 1px rgba(0,0,0,0.4); }
.funnel-why { color: var(--dash-fg-muted); font-size: 0.82rem; }

.cat-grid { display: flex; flex-direction: column; gap: 0.6rem; margin: 1rem 0; }
.cat-card { background: var(--dash-panel); padding: .75rem 1rem; border-radius: 6px;
            border: 1px solid var(--dash-divider); border-left-width: 4px;
            box-shadow: var(--dash-shadow); }
.cat-name  { font-weight: 600; font-size: 0.95rem; margin-bottom: .3rem; }
.cat-stats { display: flex; gap: 1.25rem; font-size: 0.85rem; flex-wrap: wrap; margin-bottom: .35rem; }
.cat-note  { font-size: 0.82rem; color: var(--dash-fg-muted); }

.variance-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 1rem 0; }
.variance-side h4 { margin: 0 0 .5rem 0; font-size: 0.95rem; }
.wallet-list { display: flex; flex-direction: column; gap: 2px; }
.wallet-row { display: grid; grid-template-columns: 110px 50px 65px 1fr; gap: 0.5rem;
              padding: .35rem .5rem; background: var(--dash-panel); border-radius: 3px;
              border: 1px solid var(--dash-divider); font-size: 0.82rem; align-items: center; }
.wmono  { font-family: monospace; }
.whit, .wpnl { font-variant-numeric: tabular-nums; }
.wpnl   { font-weight: 600; }
.wstyle { color: var(--dash-fg-muted); font-size: 0.78rem; }

.oos-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 1rem 0; }
.oos-card { background: var(--dash-panel); padding: 1rem; border-radius: 8px;
            border: 1px solid var(--dash-divider); box-shadow: var(--dash-shadow); }
.oos-card-oos { border-left: 4px solid #16a34a; }
.oos-card h4 { margin: 0 0 .75rem 0; font-size: 1rem; }
.oos-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.5rem; }
.oos-stats div { display: flex; flex-direction: column; }
.oos-stats span { font-size: 1.2rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.oos-stats label { font-size: 0.7rem; color: var(--dash-fg-muted); text-transform: uppercase; letter-spacing: 0.04em; }

.cap-table { display: flex; flex-direction: column; gap: 1px; margin: 1rem 0; }
.cap-row { display: grid; grid-template-columns: 120px 100px 100px 110px 200px 1fr; gap: 0.5rem;
           padding: .5rem .75rem; background: var(--dash-panel);
           border: 1px solid var(--dash-divider); border-radius: 3px;
           font-variant-numeric: tabular-nums; font-size: 0.9rem; align-items: center; }
.cap-row.cap-head { background: transparent; border: none; font-size: 0.72rem;
                    text-transform: uppercase; letter-spacing: 0.04em;
                    color: var(--dash-fg-muted); font-weight: 600; }
.cap-note { color: var(--dash-fg-muted); font-size: 0.78rem; font-style: italic; }

.caveats { background: rgba(15,23,42,0.03); border-radius: 6px; padding: 1rem 1rem .75rem 2rem;
           font-size: 0.9rem; line-height: 1.5; }
.caveats li { margin-bottom: 0.4rem; }
</style>
