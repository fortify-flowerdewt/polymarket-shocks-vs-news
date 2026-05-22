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
const marketsIndex = await FileAttachment("data/markets_index.json").json();
// Heavy bundle — loaded once, cached by the browser.
const marketsDetail = await FileAttachment("data/markets_detail.json").json();
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

## Browse all markets

Search across every market with a substantive shock. Click a row to expand its full price history, all detected shocks, and the Wikipedia edits around them. Use the filters to focus on a band, a category, or the markets we excluded as spurious.

```js
const browseSearch = view(Inputs.text({
  label: "Search question text",
  placeholder: "Type a name, topic, country…",
  submit: false,
}));
```

```js
const browseCategoryOptions = ["(all)", ...Array.from(new Set(marketsIndex.map(m => m.cat))).sort()];
const browseCategory = view(Inputs.select(browseCategoryOptions, {label: "Category", value: "(all)"}));
const browseBand = view(Inputs.select(
  ["(all)", ...summary.band_order],
  {label: "Has at least one shock in band", value: "(all)",
   format: b => b === "(all)" ? "(all)" : summary.band_labels[b]}
));
const includeFiltered = view(Inputs.toggle({label: "Include filtered (novelty / scheduled-event)", value: false}));
const browseSort = view(Inputs.select(
  ["shock_vol_total", "max_abs_dp", "n_shocks", "start"],
  {label: "Sort by", value: "shock_vol_total",
   format: s => ({shock_vol_total: "Total shock volume", max_abs_dp: "Biggest |Δp|",
                  n_shocks: "Number of shocks", start: "Market opened (newest)"}[s])}
));
```

```js
const browseFiltered = (() => {
  const q = (browseSearch || "").toLowerCase().trim();
  let rows = marketsIndex.filter(m =>
    (!q || (m.q || "").toLowerCase().includes(q))
    && (browseCategory === "(all)" || m.cat === browseCategory)
    && (browseBand === "(all)" || (m.bands && (m.bands[browseBand] || 0) > 0))
    && (includeFiltered || (!m.is_novelty && !m.is_spurious))
  );
  if (browseSort === "start") {
    rows.sort((a, b) => (b.start || "").localeCompare(a.start || ""));
  } else {
    rows.sort((a, b) => (b[browseSort] || 0) - (a[browseSort] || 0));
  }
  return rows;
})();
```

```js
display(html`<div class="browse-summary muted">${browseFiltered.length.toLocaleString()} markets match · click a row for the deep-dive</div>`);
```

```js
const selectedMarketId = Mutable(null);
const setSelected = (id) => { selectedMarketId.value = id; };
```

```js
const fmtPriceDate = (s) => s ? new Date(s).toISOString().slice(0, 10) : "—";
const fmtBigUsd = (v) => v >= 1e6 ? `$${(v/1e6).toFixed(1)}M` : v >= 1e3 ? `$${(v/1e3).toFixed(0)}k` : `$${(v||0).toFixed(0)}`;

const bandChips = (counts) => {
  const span = document.createElement("span");
  span.className = "band-chips";
  for (const b of summary.band_order) {
    const n = counts[b] || 0;
    if (n === 0) continue;
    const chip = document.createElement("span");
    chip.className = "band-chip";
    chip.style.background = summary.band_colors[b];
    chip.textContent = n;
    chip.title = `${summary.band_labels[b]}: ${n}`;
    span.append(chip);
  }
  return span;
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

  // Cap the visible list to keep DOM cost reasonable; user can refine search.
  const cap = 250;
  for (const m of browseFiltered.slice(0, cap)) {
    const r = document.createElement("div");
    r.className = "mt-row";
    if (m.id === selectedMarketId) r.classList.add("selected");
    if (m.is_novelty || m.is_spurious) r.classList.add("filtered-out");
    r.onclick = () => setSelected(m.id === selectedMarketId ? null : m.id);

    const expand = document.createElement("div"); expand.textContent = m.id === selectedMarketId ? "▾" : "▸";
    const q = document.createElement("div");
    q.textContent = m.q;
    q.title = m.q;
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

const renderMarketDetail = (m) => {
  const det = marketsDetail[String(m.id)] || {series: [], shocks: [], wiki: []};
  const root = document.createElement("div");
  root.className = "md-root";

  // Header
  const head = document.createElement("div");
  head.className = "md-head";
  head.innerHTML = `<h3>${m.q}</h3>
    <div class="md-meta muted">
      ${m.cat} · opened ${fmtPriceDate(m.start)} → closed ${fmtPriceDate(m.end)} ·
      ${m.n_shocks} shock${m.n_shocks === 1 ? "" : "s"} ·
      ${m.is_novelty ? "novelty " : ""}${m.is_spurious ? "scheduled-event " : ""}
    </div>`;
  root.append(head);

  // Price + volume charts
  if (det.series.length) {
    const series = det.series.map(([t, c, v]) => ({timestamp: new Date(t), close: +c, volume: +v}));
    const shockTimes = det.shocks.map(s => ({
      timestamp: new Date(s.t),
      band: s.band || "no_news_in_window",
    }));
    const priceMark = Plot.plot({
      height: 220,
      width: 920,
      marginLeft: 50,
      x: {type: "utc", grid: true},
      y: {label: "Probability", domain: [0, 1], grid: true},
      color: {
        domain: summary.band_order,
        range: summary.band_order.map(b => summary.band_colors[b]),
      },
      marks: [
        Plot.areaY(series, {x: "timestamp", y: "close", fill: "#3b82f6", fillOpacity: 0.15, curve: "step"}),
        Plot.lineY(series, {x: "timestamp", y: "close", stroke: "#3b82f6", curve: "step"}),
        Plot.ruleX(shockTimes, {x: "timestamp", stroke: "band", strokeWidth: 1.5, strokeDasharray: "4,2"}),
      ],
    });
    const volMark = Plot.plot({
      height: 80, width: 920, marginLeft: 50,
      x: {type: "utc", axis: null}, y: {label: "Hourly volume (USDC)", grid: true},
      marks: [Plot.areaY(series, {x: "timestamp", y: "volume", fill: "#94a3b8", fillOpacity: 0.7})],
    });
    root.append(priceMark, volMark);
  }

  // Shocks table
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
      for (const v of cells) {
        const c = document.createElement("div"); c.innerHTML = v; row.append(c);
      }
      tbl.append(row);
    }
    root.append(tbl);
  }

  // Wikipedia revisions
  if (det.wiki.length) {
    const h = document.createElement("h4");
    h.innerHTML = `Substantive Wikipedia revisions <span class="muted small">(top ${det.wiki.length} by absolute byte-change)</span>`;
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
      delta.style.color = w.size_delta > 0 ? "#86efac" : "#fca5a5";
      const cmt = document.createElement("div"); cmt.className = "mw-cmt";
      cmt.textContent = cleaned || "—";
      wrap.append(t, page, delta, cmt);
      wikiList.append(wrap);
    }
    root.append(wikiList);
  }

  return root;
};

renderTable();
display(tableHost);
```

```js
// Re-render the table whenever the inputs change.
selectedMarketId;  // dependency
browseSearch;
browseCategory;
browseBand;
includeFiltered;
browseSort;
renderTable();
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

/* --- Market browser --- */
.browse-summary { margin: .25rem 0 .75rem 0; font-size: 0.85rem; }
.markets-table { display: flex; flex-direction: column; gap: 2px; font-size: 0.85rem; }
.mt-row {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) 90px 110px 60px 70px 80px 90px;
  gap: 0.5rem;
  padding: .35rem .5rem;
  align-items: center;
  cursor: pointer;
  border-radius: 4px;
  background: var(--theme-foreground-faintest);
}
.mt-row:hover { background: rgba(255,255,255,0.05); }
.mt-row.selected { background: rgba(59,130,246,0.15); }
.mt-row.filtered-out { opacity: 0.55; }
.mt-row.mt-head {
  cursor: default;
  background: transparent;
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--theme-foreground-muted);
}
.mt-row > div { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mt-row > div:nth-child(2) { font-weight: 500; }
.mt-more { padding: .5rem; text-align: center; }
.band-chips { display: inline-flex; gap: 2px; }
.band-chip {
  display: inline-block;
  min-width: 1.4rem;
  padding: 1px 4px;
  border-radius: 3px;
  color: white;
  font-size: 0.7rem;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
.mt-detail {
  background: var(--theme-foreground-faintest);
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 6px;
  padding: 1rem;
  margin: 0.25rem 0 0.5rem 0;
}
.md-head h3 { margin: 0 0 .25rem 0; }
.md-meta { margin-bottom: .75rem; }
.md-shocks, .md-wiki { display: flex; flex-direction: column; gap: 2px; margin: .5rem 0; font-size: 0.82rem; }
.ms-row {
  display: grid;
  grid-template-columns: 140px 70px 80px 220px 110px minmax(0, 1fr);
  gap: .5rem;
  padding: .3rem .5rem;
  border-left: 3px solid #888;
  border-radius: 3px;
  background: rgba(255,255,255,0.03);
  align-items: center;
}
.ms-row.ms-head {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--theme-foreground-muted);
  border-left-color: transparent;
  background: transparent;
}
.ms-row > div { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ms-row a { color: #93c5fd; text-decoration: none; }
.ms-row a:hover { text-decoration: underline; }
.mw-row {
  display: grid;
  grid-template-columns: 140px 220px 90px minmax(0, 1fr);
  gap: .5rem;
  padding: .25rem .5rem;
  font-size: 0.82rem;
  align-items: center;
}
.mw-row:nth-child(odd) { background: rgba(255,255,255,0.025); }
.mw-row > div { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mw-time { color: var(--theme-foreground-muted); font-variant-numeric: tabular-nums; }
.mw-page a { color: #93c5fd; text-decoration: none; }
.mw-page a:hover { text-decoration: underline; }
.mw-delta { font-variant-numeric: tabular-nums; text-align: right; }
.mw-cmt { color: var(--theme-foreground-muted); font-style: italic; }
</style>