---
title: Polymarket — Shocks vs News
toc: false
---

```js
const summary = await FileAttachment("data/summary.json").json();
const histogram = await FileAttachment("data/histogram.csv").csv({typed: true});
const byCategory = await FileAttachment("data/by_category.csv").csv({typed: true});
const exemplars = await FileAttachment("data/exemplars.json").json();
const marketsIndex = await FileAttachment("data/markets_index.json").json();
const marketsDetail = await FileAttachment("data/markets_detail.json").json();
```

```js
const fmtCount = (n) => d3.format(",")(n);
const fmtPct = (p) => d3.format(".0%")(p);
const fmtPctSigned = (p) => `${p > 0 ? "+" : ""}${(p * 100).toFixed(1)} pp`;
const fmtBigUsd = (v) => v >= 1e6 ? `$${(v/1e6).toFixed(1)}M` : v >= 1e3 ? `$${(v/1e3).toFixed(0)}k` : `$${(v||0).toFixed(0)}`;
const fmtDate = (s) => s ? new Date(s).toUTCString().slice(5, 22) : "";
const fmtPriceDate = (s) => s ? new Date(s).toISOString().slice(0, 10) : "—";
const fmtHours = (h) => {
  if (h == null) return "—";
  const sign = h < 0 ? "−" : "+";
  const a = Math.abs(h);
  if (a < 1/60) return `${sign}${(a * 3600).toFixed(0)} s`;
  if (a < 1) return `${sign}${(a * 60).toFixed(0)} min`;
  if (a < 48) return `${sign}${a.toFixed(2)} h`;
  return `${sign}${(a / 24).toFixed(1)} d`;
};

// Parse our parquet-emitted timestamps robustly. Polars'.isoformat() on
// shock_t gives "YYYY-MM-DD HH:MM:SS+00:00" (space-separated), which Safari
// refuses to parse with `new Date(...)`. Series timestamps use "T". Normalize.
const parseTs = (s) => {
  if (s == null) return null;
  if (s instanceof Date) return s;
  const norm = typeof s === "string" && s.includes(" ") && !s.includes("T")
    ? s.replace(" ", "T") : s;
  return new Date(norm);
};

const cleanWikiComment = (raw) => {
  if (!raw) return null;
  let s = String(raw).trim();
  const sectionAnchors = [...s.matchAll(/\/\*\s*([^*]+?)\s*\*\//g)].map(m => m[1].trim());
  const afterAnchors = s.replace(/\/\*[^*]*\*\//g, "").trim();
  if (afterAnchors) s = afterAnchors;
  else if (sectionAnchors.length) return `section: ${sectionAnchors[0]}`;
  else return null;
  s = s.replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "$2");
  s = s.replace(/\[\[([^\]]+)\]\]/g, "$1");
  s = s.replace(/Special:Diff\/\d+/g, "");
  if (/^(Undid revision|Reverting|Reverted edits|Restored revision|Tag: ?)/i.test(s)) return null;
  s = s.replace(/\s+/g, " ").trim();
  if (s.length < 4) return null;
  if (s.length > 140) s = s.slice(0, 137) + "…";
  return s;
};
```

<div class="hero">
  <div class="hero-title">
    <div class="kicker">Polymarket</div>
    <h1>Shocks vs Wikipedia news</h1>
    <div class="hero-sub">Every large probability move on Polymarket, timed against the nearest Wikipedia edit on the relevant article. Negative Δt = price moved first.</div>
  </div>
  <button class="meth-trigger" onclick="document.getElementById('methodology-modal').showModal()">
    <span class="meth-icon">📖</span>
    <span>Methodology</span>
  </button>
  <div class="kpi-strip">
    <div class="kpi">
      <span class="kpi-n">${fmtCount(summary.n_shocks_in_shortlist_all)}</span>
      <span class="kpi-l">shortlist shocks</span>
    </div>
    <div class="kpi kpi-mute">
      <span class="kpi-n">${fmtCount(summary.n_excluded_novelty)}</span>
      <span class="kpi-l">novelty filtered</span>
    </div>
    <div class="kpi kpi-mute">
      <span class="kpi-n">${fmtCount(summary.n_excluded_spurious)}</span>
      <span class="kpi-l">scheduled-event filtered</span>
    </div>
    <div class="kpi kpi-accent">
      <span class="kpi-n">${fmtCount(summary.n_shocks_in_shortlist)}</span>
      <span class="kpi-l">substantive shocks</span>
    </div>
    <div class="kpi kpi-accent">
      <span class="kpi-n">${fmtHours(summary.median_dt_nearest_hours)}</span>
      <span class="kpi-l">median Δt vs Wiki</span>
    </div>
  </div>
</div>

```js
// Selected band acts as a cross-cutting filter on the table below.
const selectedBand = Mutable(null);
const setSelectedBand = (b) => { selectedBand.value = (selectedBand.value === b ? null : b); };
```

```js
const bandStrip = document.createElement("div");
bandStrip.className = "band-strip";
for (const b of summary.band_order) {
  const n = summary.band_counts[b] || 0;
  const matched = summary.n_shocks_in_shortlist - (summary.band_counts.no_news_in_window || 0);
  const share = b === "no_news_in_window" ? n / summary.n_shocks_in_shortlist : n / matched;
  const card = document.createElement("button");
  card.className = "bandcard";
  if (selectedBand === b) card.classList.add("selected");
  card.style.setProperty("--band-color", summary.band_colors[b]);
  card.onclick = () => setSelectedBand(b);
  card.innerHTML = `
    <div class="bandcard-dot"></div>
    <div class="bandcard-n">${fmtCount(n)}</div>
    <div class="bandcard-share">${fmtPct(share)}</div>
    <div class="bandcard-l">${summary.band_labels[b]}</div>
  `;
  bandStrip.append(card);
}
display(bandStrip);
```

<div class="grid-2col">
<div class="panel">
  <div class="panel-h">Lead/lag distribution <span class="panel-sub">Δt = shock − Wikipedia edit, hours</span></div>

```js
// Human-friendly bin labels. Bins are at fixed log-spaced edges, so each
// is rendered as an equal-width bar on a band scale with a short text label.
const fmtEdge = (h) => {
  if (h === 0) return "0";
  const sign = h < 0 ? "−" : "+";
  const a = Math.abs(h);
  if (a < 1) return `${sign}${Math.round(a * 60)}m`;
  if (a < 24) return `${sign}${a}h`;
  if (a < 168) return `${sign}${Math.round(a / 24)}d`;
  return `${sign}${Math.round(a / 168)}w`;
};
const histogramLabeled = histogram.map(d => ({
  ...d,
  label: `${fmtEdge(d.lo)} → ${fmtEdge(d.hi)}`,
  fill:
    d.hi <= -3 ? "#dc2626" :
    d.hi <= -0.5 ? "#fb923c" :
    (d.hi <= 0.5 && d.lo >= -0.5) ? "#a3a3a3" :
    d.lo >= 3 ? "#16a34a" :
    d.lo >= 0.5 ? "#86efac" :
    "#888",
}));
const bandDomain = histogramLabeled.map(d => d.label);
```

```js
display(Plot.plot({
  height: 280,
  marginLeft: 45,
  marginRight: 10,
  marginBottom: 60,
  x: {
    label: null,
    domain: bandDomain,
    tickRotate: -35,
    tickSize: 0,
  },
  y: {label: "shocks", grid: true, ticks: 5},
  marks: [
    Plot.barY(histogramLabeled, {
      x: "label",
      y: "count",
      fill: "fill",
      inset: 2,
      title: d => `${d.label}: ${d.count} shocks`,
    }),
    Plot.text(histogramLabeled, {
      x: "label", y: "count", text: d => d.count || "",
      dy: -6, fill: "currentColor", fontSize: 10,
    }),
  ],
}))
```

<div class="axis-key muted small">
  ← shock first&nbsp;&nbsp;·&nbsp;&nbsp;Δt = shock − Wiki edit&nbsp;&nbsp;·&nbsp;&nbsp;news first →
</div>

  <div class="panel-foot muted">Ticks sit at the band thresholds (±30m, ±3h) and human time-units (1d, 3d, 1w). Grey centre = inside detector resolution.</div>
</div>

<div class="panel">
  <div class="panel-h">By market category <span class="panel-sub">share of shocks in each band</span></div>

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

const catbars = document.createElement("div");
catbars.className = "catbars";
for (const row of byCategoryRows) {
  const r = document.createElement("div"); r.className = "catrow";
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

  <div class="panel-foot muted">Politics dominates the decisive-shock-first bucket. Finance is split; Weather is cleanly news-led.</div>
</div>
</div>

<div class="panel panel-main exemplars-panel">
  <div class="panel-h panel-h-big">Top cases per band <span class="panel-sub">cleanest 10 examples for each lead/lag bucket — each card shows the ±72 h price window with the shock marked</span></div>

```js
const exemplarBand = view(Inputs.radio(
  summary.band_order,
  {
    label: html`<strong>Show top cases for band</strong>`,
    value: "shock_clearly_first",
    format: b => summary.band_labels[b],
  }
));
```

```js
const bandToKey = {
  shock_clearly_first: "shock_clearly_first_top10",
  shock_first_marginal: "shock_first_marginal_top10",
  simultaneous_uncertain: "simultaneous_top10",
  news_first_marginal: "news_first_marginal_top10",
  news_clearly_first: "news_clearly_first_top10",
  no_news_in_window: "no_news_top10",
};
const currentExemplars = exemplars[bandToKey[exemplarBand]] || [];
```

```js
// Per-exemplar price chart: ±72h window around the shock, with the shock
// (band-coloured dashed rule) and any Wikipedia revisions in the window
// (yellow ticks) overlaid on the price step-line.
const exemplarPriceChart = (mid, shockT) => {
  const det = marketsDetail[String(mid)];
  if (!det || !det.series || !det.series.length) {
    const d = document.createElement("div");
    d.className = "muted small ex-empty";
    d.textContent = `no price series for market ${mid}`;
    return d;
  }
  const shockDate = parseTs(shockT);
  const t0 = shockDate ? +shockDate : NaN;
  if (isNaN(t0)) {
    const d = document.createElement("div");
    d.className = "muted small ex-empty";
    d.textContent = `could not parse shock_t = ${shockT}`;
    return d;
  }
  const winMs = 72 * 3600 * 1000;
  const series = det.series
    .map(([ts, close, vol]) => ({timestamp: parseTs(ts), close: +close, volume: +vol}))
    .filter(r => r.timestamp && Math.abs(+r.timestamp - t0) <= winMs);
  if (!series.length) {
    const d = document.createElement("div");
    d.className = "muted small ex-empty";
    d.textContent = `no bars in ±72 h of ${shockDate.toUTCString()}`;
    return d;
  }
  const wikiIn = (det.wiki || [])
    .map(w => ({timestamp: parseTs(w.t)}))
    .filter(w => w.timestamp && Math.abs(+w.timestamp - t0) <= winMs);
  return Plot.plot({
    height: 200,
    width: 1100,
    marginLeft: 45,
    marginBottom: 28,
    x: {type: "utc", grid: true},
    y: {label: "Probability", domain: [0, 1], grid: true},
    marks: [
      Plot.areaY(series, {x: "timestamp", y: "close", fill: "#3b82f6", fillOpacity: 0.15, curve: "step"}),
      Plot.lineY(series, {x: "timestamp", y: "close", stroke: "#1d4ed8", strokeWidth: 1.6, curve: "step"}),
      Plot.tickX(wikiIn, {x: "timestamp", stroke: "#d97706", strokeOpacity: 0.85, strokeWidth: 1.6, y: 0.03}),
      Plot.ruleX([shockDate], {stroke: "#dc2626", strokeDasharray: "5,3", strokeWidth: 1.6}),
    ],
  });
};

const renderExemplar = (d) => {
  const colour = summary.band_colors[d.band];
  const card = document.createElement("div");
  card.className = "exemplar";
  card.style.borderLeftColor = colour;

  const row = document.createElement("div");
  row.className = "ex-row";
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.style.background = colour;
  badge.textContent = summary.band_labels[d.band];
  const cat = document.createElement("span"); cat.className = "ex-cat"; cat.textContent = d.category;
  const date = document.createElement("span"); date.className = "ex-date"; date.textContent = fmtDate(d.shock_t);
  row.append(badge, cat, date);

  const title = document.createElement("h3"); title.textContent = d.question;

  const stats = document.createElement("div"); stats.className = "ex-stats";
  for (const [label, value, color] of [
    ["Δ price", fmtPctSigned(d.dp), null],
    ["Volume", fmtBigUsd(d.volume), null],
    ["Δt vs Wiki", fmtHours(d.dt_nearest_hours), colour],
    ["Wikipedia page", d.nearest_wiki_page
      ? `<a href="https://en.wikipedia.org/wiki/${encodeURIComponent(d.nearest_wiki_page.replace(/ /g, "_"))}" target="_blank" rel="noopener">${d.nearest_wiki_page}</a>`
      : "—", null],
  ]) {
    const b = document.createElement("div");
    b.innerHTML = `<span class="muted">${label}</span><br><strong${color ? ` style="color:${color}"` : ""}>${value}</strong>`;
    stats.append(b);
  }
  card.append(row, title, stats);

  const cleaned = cleanWikiComment(d.nearest_comment);
  if (cleaned) {
    const c = document.createElement("div");
    c.className = "ex-comment";
    c.innerHTML = `<span class="ex-comment-label">Wikipedia edit summary</span> <span class="ex-comment-text">${cleaned}</span>`;
    card.append(c);
  }
  card.append(exemplarPriceChart(d.market_id, d.shock_t));
  return card;
};

const exemplarGrid = document.createElement("div");
exemplarGrid.className = "exemplar-grid";
for (const e of currentExemplars) exemplarGrid.append(renderExemplar(e));
display(exemplarGrid);
```

</div>

<div class="panel panel-main">
  <div class="panel-h panel-h-big">Browse all markets
    ${selectedBand ? html`<span class="filter-pill" onclick=${() => setSelectedBand(null)}>filtered to <strong style="color:${summary.band_colors[selectedBand]}">${summary.band_labels[selectedBand]}</strong> · clear ×</span>` : html`<span class="panel-sub">click any band above to filter · click a row to deep-dive</span>`}
  </div>

```js
const browseSearch = view(Inputs.text({placeholder: "Search question text…", submit: false, width: 280}));
```

```js
const browseCategoryOptions = ["(all)", ...Array.from(new Set(marketsIndex.map(m => m.cat))).sort()];
const browseCategory = view(Inputs.select(browseCategoryOptions, {label: "Category", value: "(all)"}));
const includeFiltered = view(Inputs.toggle({label: "Include filtered (novelty / scheduled)", value: false}));
const browseSort = view(Inputs.select(
  ["shock_vol_total", "max_abs_dp", "n_shocks", "start"],
  {label: "Sort", value: "shock_vol_total",
   format: s => ({shock_vol_total: "Volume", max_abs_dp: "Biggest |Δp|",
                  n_shocks: "# shocks", start: "Newest"}[s])}
));
```

```js
const browseFiltered = (() => {
  const q = (browseSearch || "").toLowerCase().trim();
  const band = selectedBand;
  let rows = marketsIndex.filter(m =>
    (!q || (m.q || "").toLowerCase().includes(q))
    && (browseCategory === "(all)" || m.cat === browseCategory)
    && (!band || (m.bands && (m.bands[band] || 0) > 0))
    && (includeFiltered || (!m.is_novelty && !m.is_spurious))
  );
  if (browseSort === "start") rows.sort((a, b) => (b.start || "").localeCompare(a.start || ""));
  else rows.sort((a, b) => (b[browseSort] || 0) - (a[browseSort] || 0));
  return rows;
})();
```

```js
const selectedMarketId = Mutable(null);
const setSelected = (id) => { selectedMarketId.value = id; };
```

```js
const bandChips = (counts) => {
  const span = document.createElement("span"); span.className = "band-chips";
  for (const b of summary.band_order) {
    const n = counts[b] || 0;
    if (n === 0) continue;
    const chip = document.createElement("span");
    chip.className = "band-chip"; chip.style.background = summary.band_colors[b];
    chip.textContent = n; chip.title = `${summary.band_labels[b]}: ${n}`;
    span.append(chip);
  }
  return span;
};

const renderMarketDetail = (m) => {
  const det = marketsDetail[String(m.id)] || {series: [], shocks: [], wiki: []};
  const root = document.createElement("div"); root.className = "md-root";

  const head = document.createElement("div"); head.className = "md-head";
  head.innerHTML = `<h3>${m.q}</h3>
    <div class="md-meta muted">${m.cat} · opened ${fmtPriceDate(m.start)} → closed ${fmtPriceDate(m.end)} ·
      ${m.n_shocks} shock${m.n_shocks === 1 ? "" : "s"}
      ${m.is_novelty ? " · <span style='color:#fb923c'>novelty</span>" : ""}${m.is_spurious ? " · <span style='color:#fb923c'>scheduled-event</span>" : ""}</div>`;
  root.append(head);

  if (det.series.length) {
    const series = det.series.map(([t, c, v]) => ({timestamp: parseTs(t), close: +c, volume: +v}));
    const shockTimes = det.shocks.map(s => ({timestamp: parseTs(s.t), band: s.band || "no_news_in_window"}));
    const wikiTimes = det.wiki.slice(0, 50).map(w => ({timestamp: parseTs(w.t)}));
    root.append(Plot.plot({
      height: 220, width: 880, marginLeft: 50,
      x: {type: "utc", grid: true}, y: {label: "Probability", domain: [0, 1], grid: true},
      color: {domain: summary.band_order, range: summary.band_order.map(b => summary.band_colors[b])},
      marks: [
        Plot.areaY(series, {x: "timestamp", y: "close", fill: "#3b82f6", fillOpacity: 0.15, curve: "step"}),
        Plot.lineY(series, {x: "timestamp", y: "close", stroke: "#3b82f6", curve: "step"}),
        Plot.tickX(wikiTimes, {x: "timestamp", stroke: "#fbbf24", strokeOpacity: 0.6, strokeWidth: 1, y: 0.02}),
        Plot.ruleX(shockTimes, {x: "timestamp", stroke: "band", strokeWidth: 1.5, strokeDasharray: "4,2"}),
      ],
    }));
    root.append(Plot.plot({
      height: 70, width: 880, marginLeft: 50,
      x: {type: "utc", axis: null}, y: {label: "Vol", grid: false, ticks: 2},
      marks: [Plot.areaY(series, {x: "timestamp", y: "volume", fill: "#94a3b8", fillOpacity: 0.7})],
    }));
    const legend = document.createElement("div");
    legend.className = "md-legend muted small";
    legend.innerHTML = `<span style="color:#3b82f6">━</span> price · <span style="color:#fbbf24">┃</span> Wiki edit · <span>┊</span> shock (band-coloured)`;
    root.append(legend);
  }

  if (det.shocks.length) {
    const h = document.createElement("h4"); h.textContent = `Shocks (${det.shocks.length})`; root.append(h);
    const tbl = document.createElement("div"); tbl.className = "md-shocks";
    const head = document.createElement("div"); head.className = "ms-row ms-head";
    for (const lbl of ["Time (UTC)", "Δ price", "Volume", "Band", "Δt vs Wiki", "Wikipedia page"]) {
      const c = document.createElement("div"); c.textContent = lbl; head.append(c);
    }
    tbl.append(head);
    const sorted = [...det.shocks].sort((a, b) => Math.abs(b.dp) - Math.abs(a.dp));
    for (const s of sorted) {
      const row = document.createElement("div"); row.className = "ms-row";
      if (s.band) row.style.borderLeftColor = summary.band_colors[s.band];
      const cells = [
        fmtDate(s.t),
        `<strong>${fmtPctSigned(s.dp)}</strong>`,
        fmtBigUsd(s.vol),
        s.band ? `<span class="band-chip" style="background:${summary.band_colors[s.band]}">${summary.band_labels[s.band]}</span>` : "—",
        s.dt_hours == null ? "—" : `<span style="color:${summary.band_colors[s.band] || '#888'}">${fmtHours(s.dt_hours)}</span>`,
        s.wiki_page ? `<a href="https://en.wikipedia.org/wiki/${encodeURIComponent(s.wiki_page.replace(/ /g, "_"))}" target="_blank" rel="noopener">${s.wiki_page}</a>` : "—",
      ];
      for (const v of cells) { const c = document.createElement("div"); c.innerHTML = v; row.append(c); }
      tbl.append(row);
    }
    root.append(tbl);
  }

  if (det.wiki.length) {
    const h = document.createElement("h4");
    h.innerHTML = `Wikipedia revisions <span class="muted small">(top ${det.wiki.length} by |byte-change|)</span>`;
    root.append(h);
    const wikiList = document.createElement("div"); wikiList.className = "md-wiki";
    for (const w of det.wiki) {
      const cleaned = cleanWikiComment(w.comment);
      const wrap = document.createElement("div"); wrap.className = "mw-row";
      const t = document.createElement("div"); t.className = "mw-time"; t.textContent = fmtDate(w.t);
      const page = document.createElement("div"); page.className = "mw-page";
      page.innerHTML = `<a href="https://en.wikipedia.org/wiki/${encodeURIComponent((w.page||"").replace(/ /g, "_"))}" target="_blank" rel="noopener">${w.page || "—"}</a>`;
      const delta = document.createElement("div"); delta.className = "mw-delta";
      delta.textContent = `${w.size_delta > 0 ? "+" : ""}${w.size_delta} B`;
      delta.style.color = w.size_delta > 0 ? "#15803d" : "#b91c1c";
      const cmt = document.createElement("div"); cmt.className = "mw-cmt"; cmt.textContent = cleaned || "—";
      wrap.append(t, page, delta, cmt);
      wikiList.append(wrap);
    }
    root.append(wikiList);
  }
  return root;
};

const tableHost = document.createElement("div");
tableHost.className = "markets-table";

const renderTable = () => {
  tableHost.replaceChildren();
  const head = document.createElement("div");
  head.className = "mt-row mt-head";
  for (const h of ["", "Market", "Category", "Bands", "Shocks", "Max |Δp|", "Shock vol", "Opened"]) {
    const c = document.createElement("div"); c.textContent = h; head.append(c);
  }
  tableHost.append(head);

  const cap = 250;
  for (const m of browseFiltered.slice(0, cap)) {
    const r = document.createElement("div");
    r.className = "mt-row";
    if (m.id === selectedMarketId) r.classList.add("selected");
    if (m.is_novelty || m.is_spurious) r.classList.add("filtered-out");
    r.onclick = () => setSelected(m.id === selectedMarketId ? null : m.id);

    const expand = document.createElement("div"); expand.textContent = m.id === selectedMarketId ? "▾" : "▸";
    const q = document.createElement("div"); q.textContent = m.q; q.title = m.q;
    const cat = document.createElement("div"); cat.textContent = m.cat;
    const bands = document.createElement("div"); bands.append(bandChips(m.bands || {}));
    const ns = document.createElement("div"); ns.textContent = m.n_shocks;
    const dp = document.createElement("div"); dp.textContent = `${(m.max_abs_dp * 100).toFixed(0)} pp`;
    const vol = document.createElement("div"); vol.textContent = fmtBigUsd(m.shock_vol_total);
    const start = document.createElement("div"); start.textContent = fmtPriceDate(m.start);
    r.append(expand, q, cat, bands, ns, dp, vol, start);
    tableHost.append(r);

    if (m.id === selectedMarketId) {
      const detail = document.createElement("div");
      detail.className = "mt-detail";
      detail.append(renderMarketDetail(m));
      tableHost.append(detail);
    }
  }
  if (browseFiltered.length > cap) {
    const more = document.createElement("div");
    more.className = "mt-more muted small";
    more.textContent = `… ${(browseFiltered.length - cap).toLocaleString()} more — refine the search to see them`;
    tableHost.append(more);
  }
};

renderTable();
display(html`<div class="browse-summary muted">${browseFiltered.length.toLocaleString()} markets match · click a row for the deep-dive</div>`);
display(tableHost);
```

```js
// Re-render the table whenever any input changes.
selectedMarketId; selectedBand; browseSearch; browseCategory; includeFiltered; browseSort;
renderTable();
```

</div>

<details class="audit-details">
<summary>Audit: top 10 cases excluded by the scheduled-event filter</summary>

```js
const auditList = document.createElement("div"); auditList.className = "audit-list";
for (const e of (exemplars.filtered_spurious_top10 || [])) {
  const row = document.createElement("div"); row.className = "audit-row";
  row.innerHTML = `<div class="audit-q">${e.question}</div>
    <div class="audit-meta"><span class="muted">${e.category}</span> ·
      <strong>${fmtPctSigned(e.dp)}</strong> ·
      <span style="color:#fb923c">${fmtHours(e.dt_nearest_hours)}</span> ·
      filtered because <em>${e.spurious_reason}</em></div>`;
  auditList.append(row);
}
display(auditList);
```

</details>

<dialog id="methodology-modal" class="meth-modal">
  <div class="meth-modal-head">
    <strong>Methodology</strong>
    <button class="meth-close" onclick="document.getElementById('methodology-modal').close()" aria-label="Close">×</button>
  </div>
  <iframe src="./methodology" class="meth-iframe" title="Methodology"></iframe>
</dialog>

<style>
/* Layout only. All colors and backgrounds live in custom-style.css so the
   theme can be swapped (light / dark) by editing one file. */
.hero { display: grid; grid-template-columns: 1fr auto; grid-template-rows: auto auto;
        gap: 1rem 1.5rem; padding: 1.25rem 1.5rem; border-radius: 10px; margin-bottom: 1.25rem;
        align-items: start; }
.hero-title { grid-column: 1; grid-row: 1; }
.meth-trigger {
  grid-column: 2; grid-row: 1; align-self: start;
  display: inline-flex; align-items: center; gap: 0.5rem;
  padding: 0.5rem 0.85rem;
  border-radius: 6px;
  font-size: 0.85rem;
  cursor: pointer;
  font-family: inherit;
}
.meth-icon { font-size: 1rem; }
.kpi-strip { grid-column: 1 / -1; grid-row: 2; }
.meth-modal {
  border: none;
  border-radius: 12px;
  padding: 0;
  width: min(1100px, 92vw);
  height: min(85vh, 900px);
  max-width: 92vw;
  max-height: 90vh;
  overflow: hidden;
}
.meth-modal::backdrop { background: rgba(15, 23, 42, 0.45); backdrop-filter: blur(2px); }
.meth-modal-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0.65rem 1rem;
  font-size: 0.9rem;
}
.meth-close {
  background: transparent; border: none; cursor: pointer;
  font-size: 1.5rem; line-height: 1; padding: 0.2rem 0.55rem;
  border-radius: 6px;
}
.meth-iframe { width: 100%; height: calc(100% - 48px); border: 0; display: block; }
.hero .kicker { font-size: 0.75rem; letter-spacing: 0.12em; text-transform: uppercase;
        margin-bottom: 0.25rem; }
.hero h1 { margin: 0; font-size: 1.85rem; line-height: 1.1; }
.hero-sub { font-size: 0.92rem; margin-top: .35rem; max-width: 70ch; }
.kpi-strip { display: grid; grid-template-columns: repeat(5, 1fr); gap: .5rem; }
.kpi { padding: .65rem .8rem; border-radius: 6px;
       display: flex; flex-direction: column; gap: .15rem; }
.kpi.kpi-mute { opacity: 0.7; }
.kpi-n { font-size: 1.5rem; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1; }
.kpi-l { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; }

.band-strip { display: grid; grid-template-columns: repeat(6, 1fr); gap: .5rem; margin-bottom: 1.25rem; }
.bandcard { border-radius: 6px; padding: .65rem .75rem; cursor: pointer; text-align: left;
            transition: transform 0.08s ease, border-color 0.1s ease, background 0.12s ease;
            display: flex; flex-direction: column; gap: .2rem; font: inherit; }
.bandcard:hover { transform: translateY(-1px); border-color: var(--band-color); }
.bandcard-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--band-color); }
.bandcard-n { font-size: 1.35rem; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1; }
.bandcard-share { font-size: 0.75rem; font-variant-numeric: tabular-nums; }
.bandcard-l { font-size: 0.7rem; line-height: 1.2; }

.grid-2col { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1.25rem; }
.panel { border-radius: 8px; padding: 1rem; }
.panel-main { padding: 1rem 1.25rem 1.25rem 1.25rem; }
.panel-h { font-weight: 600; font-size: 0.95rem; margin-bottom: .5rem;
           display: flex; align-items: baseline; gap: .75rem; flex-wrap: wrap; }
.panel-h-big { font-size: 1.1rem; }
.panel-sub { font-size: 0.78rem; font-weight: 400; }
.panel-foot { font-size: 0.75rem; margin-top: .4rem; }
.filter-pill { padding: 2px 8px; border-radius: 4px; font-size: 0.78rem; font-weight: 400; cursor: pointer; }

.catbars { display: flex; flex-direction: column; gap: 0.35rem; }
.catrow { display: grid; grid-template-columns: 90px 1fr; align-items: center; gap: 0.5rem; }
.catname { font-size: 0.82rem; text-align: right; }
.catn { font-size: 0.7rem; }
.catbar { display: flex; height: 24px; border-radius: 4px; overflow: hidden; }
.catseg { transition: filter 0.15s ease; }
.catseg:hover { filter: brightness(1.1); }

.browse-summary { margin: .25rem 0 .75rem 0; font-size: 0.8rem; }
.markets-table { display: flex; flex-direction: column; gap: 2px; font-size: 0.82rem; }
.mt-row { display: grid; grid-template-columns: 20px minmax(0, 1fr) 80px 100px 50px 60px 70px 80px;
          gap: 0.5rem; padding: .35rem .5rem; align-items: center; cursor: pointer; border-radius: 4px; }
.mt-row.filtered-out { opacity: 0.55; }
.mt-row.mt-head { cursor: default; font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.04em; }
.mt-row > div { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mt-row > div:nth-child(2) { font-weight: 500; }
.mt-more { padding: .5rem; text-align: center; }
.band-chips { display: inline-flex; gap: 2px; }
.band-chip { display: inline-block; min-width: 1.3rem; padding: 1px 4px; border-radius: 3px;
             color: white; font-size: 0.7rem; text-align: center; font-variant-numeric: tabular-nums; }

.mt-detail { border-radius: 6px; padding: 1rem; margin: 0.25rem 0 0.5rem 0; }
.md-head h3 { margin: 0 0 .25rem 0; font-size: 1.1rem; }
.md-meta { margin-bottom: .75rem; font-size: 0.85rem; }
.md-legend { margin-top: .25rem; }
.md-shocks, .md-wiki { display: flex; flex-direction: column; gap: 2px; margin: .75rem 0; font-size: 0.8rem; }
.ms-row { display: grid; grid-template-columns: 140px 70px 70px 200px 100px minmax(0, 1fr);
          gap: .5rem; padding: .3rem .5rem; border-left: 3px solid #888; border-radius: 3px; align-items: center; }
.ms-row.ms-head { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.04em; border-left-color: transparent; }
.ms-row > div { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ms-row a { text-decoration: none; }
.ms-row a:hover { text-decoration: underline; }
.mw-row { display: grid; grid-template-columns: 140px 200px 80px minmax(0, 1fr);
          gap: .5rem; padding: .25rem .5rem; font-size: 0.78rem; align-items: center; }
.mw-row > div { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mw-time { font-variant-numeric: tabular-nums; }
.mw-page a { text-decoration: none; }
.mw-page a:hover { text-decoration: underline; }
.mw-delta { font-variant-numeric: tabular-nums; text-align: right; }
.mw-cmt { font-style: italic; }

.audit-details { margin-top: 1rem; }
.audit-details summary { cursor: pointer; padding: .5rem .75rem; border-radius: 4px; font-size: 0.85rem; }
.audit-list { display: flex; flex-direction: column; gap: 0.3rem; margin-top: .5rem; }
.audit-row { padding: .4rem .65rem; border-radius: 3px; }
.audit-q { font-size: 0.85rem; }
.audit-meta { font-size: 0.75rem; margin-top: .1rem; }

.axis-key { text-align: center; margin-top: .25rem; font-size: 0.78rem; }
.small { font-size: 0.8rem; }

/* --- Exemplars (top cases per band) --- */
.exemplars-panel { margin-bottom: 1.25rem; }
.exemplar-grid { display: grid; grid-template-columns: 1fr; gap: 0.75rem; margin-top: 1rem; }
.exemplar { border-radius: 8px; padding: 1rem; border-left: 4px solid #888; }
.exemplar .ex-row { display: flex; gap: .75rem; align-items: center; margin-bottom: .25rem; font-size: 0.85rem; }
.exemplar .badge { color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; }
.exemplar h3 { margin: .25rem 0 .75rem 0; font-size: 1rem; }
.exemplar .ex-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: .75rem; margin-bottom: .5rem; }
.exemplar .ex-stats .muted { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; }
.exemplar .ex-comment { font-size: 0.82rem; margin: .25rem 0 .5rem 0; padding: .4rem .6rem; border-radius: 4px; }
.exemplar .ex-comment-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600; }
.exemplar .ex-comment-text { font-style: italic; }

@media (max-width: 900px) {
  .kpi-strip { grid-template-columns: repeat(2, 1fr); }
  .band-strip { grid-template-columns: repeat(2, 1fr); }
  .grid-2col { grid-template-columns: 1fr; }
}
</style>