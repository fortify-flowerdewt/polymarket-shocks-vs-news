---
title: Polymarket — Shocks vs News
toc: false
---

```js
const summary = await FileAttachment("data/summary.json").json();
const histogram = await FileAttachment("data/histogram.csv").csv({typed: true});
const byCategory = await FileAttachment("data/by_category.csv").csv({typed: true});
const exemplars = await FileAttachment("data/exemplars.json").json();
const allSeries = await FileAttachment("data/price_series.json").json();
const seriesById = new Map(
  Object.entries(allSeries).map(([mid, rows]) => [
    +mid,
    rows.map(r => ({timestamp: new Date(r.timestamp), close: +r.close})),
  ])
);
```

```js
const fmtCount = (n) => d3.format(",")(n);
const fmtPct = (p) => d3.format(".0%")(p);
const fmtPctSigned = (p) => `${p > 0 ? "+" : ""}${(p * 100).toFixed(1)} pp`;
const fmtDollars = (v) => d3.format("$,.0f")(v);
const fmtDate = (s) => s ? new Date(s).toUTCString().slice(5, 22) : "";
const fmtHours = (h) => {
  if (h == null) return "—";
  const sign = h < 0 ? "−" : "+";
  const a = Math.abs(h);
  if (a < 1/60) return `${sign}${(a * 3600).toFixed(0)} s`;
  if (a < 1) return `${sign}${(a * 60).toFixed(0)} min`;
  if (a < 48) return `${sign}${a.toFixed(2)} h`;
  return `${sign}${(a / 24).toFixed(1)} d`;
};

// Wikipedia edit comments are full of section anchors ("/* foo */"), wiki
// markup, and rollback chatter. Strip the noise; return a tidy human string,
// or null when the comment is purely procedural.
const cleanWikiComment = (raw) => {
  if (!raw) return null;
  let s = String(raw).trim();
  // Strip section anchors like "/* History */" — keep the section name if nothing else follows
  const sectionAnchors = [...s.matchAll(/\/\*\s*([^*]+?)\s*\*\//g)].map(m => m[1].trim());
  const afterAnchors = s.replace(/\/\*[^*]*\*\//g, "").trim();
  if (afterAnchors) {
    s = afterAnchors;
  } else if (sectionAnchors.length) {
    return `section: ${sectionAnchors[0]}`;
  } else {
    return null;
  }
  // Strip [[wiki links|display]] → display, [[plain link]] → plain link
  s = s.replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "$2");
  s = s.replace(/\[\[([^\]]+)\]\]/g, "$1");
  // Strip Special:Diff hashes
  s = s.replace(/Special:Diff\/\d+/g, "");
  // Drop pure rollback / template stubs
  if (/^(Undid revision|Reverting|Reverted edits|Restored revision|Tag: ?)/i.test(s)) return null;
  // Collapse whitespace and trim
  s = s.replace(/\s+/g, " ").trim();
  // Drop if it became too short
  if (s.length < 4) return null;
  // Truncate long comments
  if (s.length > 140) s = s.slice(0, 137) + "…";
  return s;
};
```

# Polymarket: who moves first, prices or news?

For each of **${fmtCount(summary.n_shocks_in_shortlist_all)}** high-impact shocks on Polymarket (large probability jumps with serious money behind them) we measured the timestamp gap to the nearest substantive Wikipedia edit on the relevant article. After excluding **${fmtCount(summary.n_excluded_novelty)}** novelty markets (tweet counts, etc.) and **${fmtCount(summary.n_excluded_spurious)}** scheduled-event markets (elections during exit polls, awards ceremonies, live floor votes, etc.) — where the price-vs-Wikipedia comparison is unreliable by construction — **${fmtCount(summary.n_shocks_in_shortlist)}** remain.

The median remaining shock leads Wikipedia by **${fmtHours(summary.median_dt_nearest_hours)}**.

## How decisive is the lead?

<div class="grid grid-cols-3" style="margin-top: 1rem;">
  <div class="card decisive shock">
    <h3>Decisive: shock first</h3>
    <span class="big">${fmtCount(summary.band_counts.shock_clearly_first)}</span>
    <span class="muted">${fmtPct(summary.share_shock_clearly_first)} of matched shocks</span>
    <p>Price moved <strong>more than 3 hours</strong> before the Wikipedia article was edited. Hardest to explain by "slow Wikipedia editor."</p>
  </div>
  <div class="card uncertain">
    <h3>Within ±30 min</h3>
    <span class="big">${fmtCount(summary.band_counts.simultaneous_uncertain)}</span>
    <span class="muted">${fmtPct(summary.share_simultaneous_uncertain)} of matched shocks</span>
    <p>Inside the detector's own 1-hour resolution. <strong>We cannot adjudicate</strong> these without sub-hour shock timing or finer-grained news data.</p>
  </div>
  <div class="card decisive news">
    <h3>Decisive: news first</h3>
    <span class="big">${fmtCount(summary.band_counts.news_clearly_first)}</span>
    <span class="muted">${fmtPct(summary.share_news_clearly_first)} of matched shocks</span>
    <p>Wikipedia was edited <strong>more than 3 hours</strong> before the price moved. The cleanest "market reacted to public news" cases.</p>
  </div>
</div>

<div class="grid grid-cols-3" style="margin-top: .5rem;">
  <div class="card marginal shock">
    <span class="band-label">Shock first, marginal (30 min – 3 h)</span>
    <span class="big">${fmtCount(summary.band_counts.shock_first_marginal)}</span>
    <span class="muted">${fmtPct(summary.share_shock_first_marginal)} of matched</span>
  </div>
  <div class="card marginal news">
    <span class="band-label">News first, marginal (30 min – 3 h)</span>
    <span class="big">${fmtCount(summary.band_counts.news_first_marginal)}</span>
    <span class="muted">${fmtPct(summary.share_news_first_marginal)} of matched</span>
  </div>
  <div class="card nodata">
    <span class="band-label">No Wikipedia edit in ±7 d</span>
    <span class="big">${fmtCount(summary.band_counts.no_news_in_window)}</span>
    <span class="muted">${fmtPct(summary.band_counts.no_news_in_window / summary.n_shocks_in_shortlist)} of substantive shocks</span>
  </div>
</div>

## Lead/lag distribution and category mix

<div class="grid grid-cols-2" style="margin-top: 1rem;">
<div>

```js
Plot.plot({
  height: 300,
  marginLeft: 50,
  x: {label: "Δt = shock − news (hours, symlog)", type: "symlog", domain: [-200, 200], grid: true},
  y: {label: "Count of shocks", grid: true},
  marks: [
    Plot.rectY(histogram, {
      x1: "lo", x2: "hi", y: "count",
      fill: d => {
        if (d.hi <= -3) return "#dc2626";
        if (d.hi <= -0.5) return "#fb923c";
        if (d.hi <= 0.5 && d.lo >= -0.5) return "#a3a3a3";
        if (d.lo >= 3) return "#16a34a";
        if (d.lo >= 0.5) return "#86efac";
        return "#888";
      },
      title: d => `${d.bin}: ${d.count} shocks`,
    }),
    Plot.ruleX([-3, -0.5, 0.5, 3], {stroke: "white", strokeDasharray: "2,3", strokeOpacity: 0.5}),
    Plot.ruleX([0], {stroke: "white", strokeDasharray: "4,4"}),
    Plot.text(histogram, {x: d => (d.lo + d.hi) / 2, y: "count", text: d => d.count || "", dy: -8, fill: "currentColor", fontSize: 10}),
  ],
})
```

</div>
<div>

```js
const byCategoryRows = (() => {
  const rows = [];
  for (const r of byCategory) {
    const total = summary.band_order.reduce((s, k) => s + (+(r[k] || 0)), 0);
    if (!total) continue;
    rows.push({
      category: r.category,
      total,
      bands: summary.band_order.map(k => ({
        band: k,
        n: +(r[k] || 0),
        share: (+(r[k] || 0)) / total,
        color: summary.band_colors[k],
        label: summary.band_labels[k],
      })),
    });
  }
  rows.sort((a, b) => b.total - a.total);
  return rows;
})();
```

```js
const legend = document.createElement("div");
legend.className = "band-legend";
for (const b of summary.band_order) {
  const span = document.createElement("span");
  const swatch = document.createElement("i");
  swatch.style.background = summary.band_colors[b];
  span.append(swatch, summary.band_labels[b]);
  legend.append(span);
}
display(legend);

const catbars = document.createElement("div");
catbars.className = "catbars";
for (const row of byCategoryRows) {
  const r = document.createElement("div");
  r.className = "catrow";
  const name = document.createElement("div");
  name.className = "catname";
  name.innerHTML = `${row.category} <span class="catn">(${row.total})</span>`;
  const bar = document.createElement("div");
  bar.className = "catbar";
  for (const b of row.bands) {
    if (b.n <= 0) continue;
    const seg = document.createElement("div");
    seg.className = "catseg";
    seg.style.background = b.color;
    seg.style.flexGrow = b.n;
    seg.style.flexBasis = "0";
    seg.title = `${row.category} · ${b.label}: ${b.n} (${(b.share * 100).toFixed(1)}%)`;
    bar.append(seg);
  }
  r.append(name, bar);
  catbars.append(r);
}
display(catbars);
```

</div>
</div>

## Exemplars — top cases for each band

```js
const bandKeys = [
  ["shock_clearly_first", "shock_clearly_first_top10"],
  ["shock_first_marginal", "shock_first_marginal_top10"],
  ["simultaneous_uncertain", "simultaneous_top10"],
  ["news_first_marginal", "news_first_marginal_top10"],
  ["news_clearly_first", "news_clearly_first_top10"],
  ["no_news_in_window", "no_news_top10"],
];
const selectedBand = view(Inputs.radio(
  bandKeys.map(([b]) => b),
  {
    label: html`<strong>Show band</strong>`,
    value: "shock_clearly_first",
    format: b => summary.band_labels[b],
  }
));
```

```js
const exemplarKey = bandKeys.find(([b]) => b === selectedBand)[1];
const currentExemplars = exemplars[exemplarKey] || [];
```

```js
const priceChart = (mid, shockT) => {
  const series = seriesById.get(mid);
  if (!series || !series.length) {
    const d = document.createElement("div");
    d.className = "muted small";
    d.textContent = "no price series";
    return d;
  }
  const t0 = new Date(shockT).getTime();
  const winMs = 72 * 3600 * 1000;
  const win = series.filter(r => Math.abs(+r.timestamp - t0) <= winMs);
  if (!win.length) {
    const d = document.createElement("div");
    d.className = "muted small";
    d.textContent = "no bars in ±72h window";
    return d;
  }
  return Plot.plot({
    height: 150,
    width: 700,
    marginLeft: 40,
    x: {type: "utc", grid: true},
    y: {label: "Probability", domain: [0, 1], grid: true},
    marks: [
      Plot.areaY(win, {x: "timestamp", y: "close", fill: "#3b82f6", fillOpacity: 0.15, curve: "step"}),
      Plot.lineY(win, {x: "timestamp", y: "close", stroke: "#3b82f6", curve: "step"}),
      Plot.ruleX([new Date(shockT)], {stroke: "#ef4444", strokeDasharray: "4,2"}),
    ],
  });
};

const renderExemplar = (d) => {
  const colour = summary.band_colors[d.band];
  const card = document.createElement("div");
  card.className = "exemplar";
  card.style.borderLeftColor = colour;

  const meta = document.createElement("div");
  meta.className = "row";
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.style.background = colour;
  badge.textContent = summary.band_labels[d.band];
  const cat = document.createElement("span");
  cat.className = "cat";
  cat.textContent = d.category;
  const date = document.createElement("span");
  date.className = "date";
  date.textContent = fmtDate(d.shock_t);
  meta.append(badge, cat, date);

  const title = document.createElement("h3");
  title.textContent = d.question;

  const stats = document.createElement("div");
  stats.className = "stats";
  const statBlocks = [
    ["Δ price", fmtPctSigned(d.dp)],
    ["Volume", fmtDollars(d.volume)],
    ["Δt vs Wiki", `<strong style="color:${colour}">${fmtHours(d.dt_nearest_hours)}</strong>`],
    ["Wiki page", d.nearest_wiki_page || "—"],
  ];
  for (const [label, value] of statBlocks) {
    const block = document.createElement("div");
    block.innerHTML = `<span class="muted">${label}</span><br>${value}`;
    stats.append(block);
  }

  card.append(meta, title, stats);
  const cleaned = cleanWikiComment(d.nearest_comment);
  if (cleaned) {
    const c = document.createElement("div");
    c.className = "comment";
    const lbl = document.createElement("span");
    lbl.className = "comment-label";
    lbl.textContent = "Wikipedia edit summary:";
    const txt = document.createElement("span");
    txt.className = "comment-text";
    txt.textContent = cleaned;
    c.append(lbl, document.createTextNode(" "), txt);
    card.append(c);
  }
  card.append(priceChart(d.market_id, d.shock_t));
  return card;
};

const grid = document.createElement("div");
grid.className = "exemplar-grid";
for (const e of currentExemplars) grid.append(renderExemplar(e));
display(grid);
```

## Audit: cases we filtered out as scheduled-event spurious

These shocks were excluded from the headline counts above because the question text or Wikipedia article suggests a public-but-sub-Wikipedia information feed (exit polls, live award ceremony, live floor vote, year-end search rankings, etc.). Inspect the heuristic in the methodology.

```js
const auditList = document.createElement("div");
auditList.className = "audit-list";
for (const e of (exemplars.filtered_spurious_top10 || [])) {
  const row = document.createElement("div");
  row.className = "audit-row";
  row.innerHTML = `
    <div class="audit-q">${e.question}</div>
    <div class="audit-meta">
      <span class="muted">${e.category}</span> ·
      <strong>${fmtPctSigned(e.dp)}</strong> ·
      <span style="color:#fb923c">${fmtHours(e.dt_nearest_hours)}</span> ·
      filtered because <em>${e.spurious_reason}</em>
    </div>`;
  auditList.append(row);
}
display(auditList);
```

<style>
.card { background: var(--theme-foreground-faintest); border-radius: 8px; padding: 1rem; border-left: 4px solid #888; }
.card.decisive.shock { border-left-color: #dc2626; }
.card.decisive.news { border-left-color: #16a34a; }
.card.marginal.shock { border-left-color: #fb923c; }
.card.marginal.news { border-left-color: #86efac; }
.card.uncertain { border-left-color: #a3a3a3; }
.card.nodata { border-left-color: #525252; }
.card .big { font-size: 2rem; font-weight: 600; display: block; line-height: 1.1; }
.card h3 { margin: 0 0 .25rem 0; font-size: 1rem; }
.card .band-label { font-size: 0.85rem; font-weight: 600; display: block; margin-bottom: .25rem; }
.card p { margin: .5rem 0 0 0; font-size: .85rem; }
.muted { color: var(--theme-foreground-muted); font-size: 0.85rem; }

.band-legend { display: flex; flex-wrap: wrap; gap: .75rem; margin: .5rem 0; font-size: 0.8rem; }
.band-legend span { display: inline-flex; align-items: center; gap: 0.35rem; }
.band-legend i { display: inline-block; width: 12px; height: 12px; border-radius: 2px; }
.catbars { display: flex; flex-direction: column; gap: 0.4rem; margin: .75rem 0; }
.catrow { display: grid; grid-template-columns: 90px 1fr; align-items: center; gap: 0.5rem; }
.catname { font-size: 0.85rem; text-align: right; }
.catn { color: var(--theme-foreground-muted); font-size: 0.7rem; }
.catbar { display: flex; height: 28px; border-radius: 4px; overflow: hidden; background: var(--theme-foreground-faintest); }
.catseg { transition: filter 0.15s ease; }
.catseg:hover { filter: brightness(1.2); }

.exemplar-grid { display: grid; grid-template-columns: 1fr; gap: 0.75rem; margin-top: 1rem; }
.exemplar { background: var(--theme-foreground-faintest); border-radius: 8px; padding: 1rem; border-left: 4px solid #888; }
.exemplar .row { display: flex; gap: .75rem; align-items: center; margin-bottom: .25rem; font-size: 0.85rem; }
.exemplar .badge { color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; }
.exemplar .cat { color: var(--theme-foreground-muted); }
.exemplar .date { color: var(--theme-foreground-muted); margin-left: auto; font-variant-numeric: tabular-nums; }
.exemplar h3 { margin: .25rem 0 .75rem 0; }
.exemplar .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: .75rem; margin-bottom: .5rem; }
.exemplar .stats .muted { font-size: 0.75rem; }
.exemplar .comment { font-size: 0.8rem; color: var(--theme-foreground-muted); margin: .25rem 0 .5rem 0; padding: .35rem .55rem; background: rgba(255,255,255,0.03); border-radius: 4px; border-left: 2px solid var(--theme-foreground-muted); }
.exemplar .comment .comment-label { text-transform: uppercase; letter-spacing: 0.04em; font-size: 0.65rem; font-weight: 600; color: var(--theme-foreground-muted); }
.exemplar .comment .comment-text { font-style: italic; color: var(--theme-foreground); }

.audit-list { display: flex; flex-direction: column; gap: 0.4rem; margin-top: .5rem; }
.audit-row { padding: .5rem .75rem; border-radius: 4px; background: var(--theme-foreground-faintest); border-left: 3px solid #fb923c; }
.audit-q { font-size: 0.95rem; }
.audit-meta { font-size: 0.8rem; color: var(--theme-foreground-muted); margin-top: .15rem; }
</style>