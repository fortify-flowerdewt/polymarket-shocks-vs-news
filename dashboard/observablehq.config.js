export default {
  title: "Polymarket: Shocks vs News",
  // Methodology is reached via an in-page modal, so no sidebar entry needed.
  // /methodology still exists as a route (the modal loads it via iframe).
  // /strategy (Wallet-Following) is hidden until publication — file lives at
  // src/_strategy.md (underscore prefix = excluded from build by Observable).
  pages: [
    {name: "Shocks vs News", path: "/"},
  ],
  theme: "air",
  footer: "Source: Polymarket on-chain trades (Nov 2022 – Mar 2026) via vgregoire/polymarket-users; news: Wikipedia revisions (GDELT to follow).",
  toc: false,
  search: false,
  // Sidebar is back so visitors can flip between the two dashboards.
  // /methodology is reachable only via the in-page modal — not listed here.
  sidebar: true,
  style: "custom-style.css",
};
