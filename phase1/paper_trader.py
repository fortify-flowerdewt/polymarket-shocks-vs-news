"""Phase 1 Day-1 — Paper-trading shadow runner.

Polls Polymarket's data-api for new trades by the 5 watchlist wallets,
applies decision gates (category=Sports, market open, position cap),
and records a "would-have-traded" entry in SQLite. Logs only — no real
orders are placed.

Run:
    uv run python phase1/paper_trader.py

Stop with Ctrl-C; the ledger persists across restarts.

Configuration (`phase1/config.py`):
    BANKROLL_USD, BET_SIZE_USD, MAX_CONCURRENT, DAILY_PNL_STOP_USD,
    POLL_INTERVAL_S, WATCHLIST_PATH, CATEGORY_FILTER.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import sys
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PHASE1_DIR     = Path(__file__).parent
WATCHLIST_PATH = PHASE1_DIR / "watchlist.json"
LEDGER_PATH    = PHASE1_DIR / "ledger.sqlite"
LOG_PATH       = PHASE1_DIR / "paper_trader.log"

DATA_API   = "https://data-api.polymarket.com"
GAMMA_API  = "https://gamma-api.polymarket.com"
USER_AGENT = "polymarket-paper-trader/0.1 (tom.flowerdew@wearefortify.ai)"

# ---- Strategy parameters ------------------------------------------------ #
BANKROLL_USD       = 10_000
BET_SIZE_USD       = 100
MAX_CONCURRENT     = 100        # max simultaneous open paper positions
MAX_PER_MARKET     = 200        # max $ committed in any single market
DAILY_PNL_STOP_USD = -500       # halt on a 5% daily drawdown
POLL_INTERVAL_S    = 5
CATEGORY_FILTER    = {"sports"}
TRADE_LIMIT_PER_POLL = 50
# Polymarket prices outside this band have execution caveats; mirror the
# backtest filter so PnL is comparable.
MIN_PRICE          = 0.04
MAX_PRICE          = 0.96


# ---- Logging ------------------------------------------------------------ #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH),
    ],
)
log = logging.getLogger("paper-trader")


# ---- HTTP session ------------------------------------------------------- #
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    retries = Retry(
        total=5, backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.verify = "/etc/ssl/cert.pem"  # corp-proxy compatible
    return s


# ---- Ledger schema (SQLite) -------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS observed_trade (
    -- a raw trade by a watchlist wallet that we *saw* via the API
    trade_id          TEXT PRIMARY KEY,           -- proxyWallet|tx_hash|outcomeIdx|side
    proxy_wallet      TEXT NOT NULL,
    tx_hash           TEXT,
    timestamp_unix    INTEGER NOT NULL,
    condition_id      TEXT NOT NULL,
    asset_token_id    TEXT,
    title             TEXT,
    slug              TEXT,
    outcome           TEXT,
    outcome_index     INTEGER,
    side              TEXT,                       -- BUY or SELL
    price             REAL,
    size              REAL,
    observed_at_unix  INTEGER NOT NULL,
    seen              INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS paper_position (
    -- a paper-trade we'd have placed
    paper_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id           TEXT NOT NULL,             -- the observed_trade we mirrored
    opened_at_unix     INTEGER NOT NULL,
    condition_id       TEXT NOT NULL,
    asset_token_id     TEXT,
    title              TEXT,
    side               TEXT,
    entry_price        REAL,
    bet_usd            REAL,
    shares             REAL,
    status             TEXT NOT NULL DEFAULT 'open',  -- open | closed
    closed_at_unix     INTEGER,
    close_reason       TEXT,                          -- mirror_exit | resolution | stop
    realised_pnl_usd   REAL,
    FOREIGN KEY (trade_id) REFERENCES observed_trade(trade_id)
);

CREATE TABLE IF NOT EXISTS market_meta (
    -- cache of category / status lookups via Gamma
    condition_id   TEXT PRIMARY KEY,
    category       TEXT,
    closed         INTEGER,
    resolved       INTEGER,
    cached_at_unix INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    day              TEXT PRIMARY KEY,            -- YYYY-MM-DD UTC
    realised_usd     REAL DEFAULT 0,
    halted           INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS poll_state (
    -- last timestamp_unix we saw for each wallet (so polling can dedupe)
    proxy_wallet     TEXT PRIMARY KEY,
    last_seen_unix   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_obs_wallet_ts ON observed_trade(proxy_wallet, timestamp_unix);
CREATE INDEX IF NOT EXISTS idx_pos_status     ON paper_position(status);
CREATE INDEX IF NOT EXISTS idx_pos_condition  ON paper_position(condition_id);
"""


def open_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(LEDGER_PATH))
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


# ---- Watchlist ---------------------------------------------------------- #
@dataclass
class Wallet:
    address: str
    historical_pnl: float
    historical_n_trades: int
    historical_hit_rate: float

def load_watchlist() -> list[Wallet]:
    with open(WATCHLIST_PATH) as f:
        data = json.load(f)
    return [Wallet(d["address"], d["historical_pnl"],
                   d["historical_n_trades"], d["historical_hit_rate"]) for d in data]


# ---- API helpers ------------------------------------------------------- #
def fetch_recent_trades(sess: requests.Session, wallet: str, limit: int = 50) -> list[dict]:
    """Return up to `limit` most-recent trades by this wallet."""
    r = sess.get(f"{DATA_API}/trades", params={"user": wallet, "limit": limit}, timeout=10)
    r.raise_for_status()
    return r.json() or []


import re

# Slug / title patterns that reliably identify sports markets. Polymarket
# doesn't populate the `category` field on markets, and event-level tags are
# often empty, so we classify by keyword on slug + title + event-ticker.
_SPORTS_RE = re.compile(
    r"\b("
    # Major leagues
    r"mlb|nba|nfl|nhl|ncaa|wnba|nascar|f1|formula\s*1|"
    # Football (soccer) leagues + competitions
    r"premier\s*league|epl|la\s*liga|serie\s*a|bundesliga|"
    r"ligue\s*1|eredivisie|mls|champions\s*league|ucl|europa\s*league|"
    r"world\s*cup|euro\s*\d{4}|fa\s*cup|copa|"
    # Sport-name words
    r"football|basketball|baseball|hockey|soccer|tennis|golf|"
    r"boxing|cricket|rugby|f1|mma|ufc|nascar|"
    # Esports
    r"counter-?strike|cs\s*:?go|cs2|league\s*of\s*legends|lol|dota|"
    r"valorant|overwatch|starcraft|fortnite|csgo|"
    # Big tournaments / cup events
    r"stanley\s*cup|super\s*bowl|world\s*series|wimbledon|"
    r"french\s*open|us\s*open|australian\s*open|masters\s*tournament|"
    r"olympic|grand\s*slam|grand\s*prix|"
    # Common verbs/phrases
    r"vs\.|map\s*\d|round\s*\d|semis?|finals?|quarter-?final|game\s*\d|"
    r"set\s*\d|spread|handicap|over/under|o/u"
    r")\b",
    re.IGNORECASE,
)


def looks_like_sports(*texts: str) -> bool:
    """Heuristic sports classifier from slug / title / ticker."""
    blob = " ".join(t for t in texts if t)
    return bool(_SPORTS_RE.search(blob))


def fetch_market_meta(sess: requests.Session, condition_id: str) -> dict | None:
    """Look up a market via Gamma to learn slug/event-ticker + closure state.

    Note: Gamma's `category` field is empty on most markets. We return
    enough fields for the caller to apply its own slug-based classifier.
    """
    r = sess.get(f"{GAMMA_API}/markets",
                 params={"condition_ids": condition_id},
                 timeout=10)
    if r.status_code != 200:
        log.warning(f"gamma lookup {condition_id[:12]}… returned {r.status_code}")
        return None
    arr = r.json() or []
    if not arr:
        return None
    m = arr[0] if isinstance(arr, list) else arr
    ev = (m.get("events") or [{}])[0]
    return {
        "slug":             m.get("slug") or "",
        "question":         m.get("question") or "",
        "event_ticker":     ev.get("ticker") or "",
        "event_title":      ev.get("title") or "",
        "category_from_event": (ev.get("category") or "").lower(),
        "closed":           bool(m.get("closed")),
        "resolved":         False if not m.get("umaResolutionStatuses")
                            else any(
                                s.lower() in ("resolved", "settled")
                                for s in (m.get("umaResolutionStatuses") or [])
                            ),
    }


# ---- Decision gates --------------------------------------------------- #
def get_or_cache_market_meta(db: sqlite3.Connection, sess: requests.Session,
                             condition_id: str) -> dict | None:
    row = db.execute(
        "SELECT category, closed, resolved FROM market_meta WHERE condition_id = ?",
        (condition_id,),
    ).fetchone()
    if row:
        return {"category": row["category"] or "",
                "closed": bool(row["closed"]),
                "resolved": bool(row["resolved"])}
    meta = fetch_market_meta(sess, condition_id)
    if meta is not None:
        # Classify by slug + question + event ticker / title.
        category = "sports" if looks_like_sports(
            meta["slug"], meta["question"], meta["event_ticker"], meta["event_title"]
        ) else (meta["category_from_event"] or "other")
        db.execute(
            "INSERT OR REPLACE INTO market_meta (condition_id, category, closed, resolved, cached_at_unix) "
            "VALUES (?, ?, ?, ?, ?)",
            (condition_id, category, int(meta["closed"]),
             int(meta["resolved"]), int(time.time())),
        )
        db.commit()
        return {"category": category, "closed": meta["closed"], "resolved": meta["resolved"]}
    return None


def decide(db: sqlite3.Connection, sess: requests.Session, trade: dict) -> tuple[bool, str]:
    """Return (should_copy, reason)."""
    # Price band
    price = float(trade.get("price") or 0)
    if not (MIN_PRICE <= price <= MAX_PRICE):
        return False, f"price {price} out of band"

    # Quick path: the data-api response already has slug + title — try the
    # slug heuristic first. If it matches, we don't need the Gamma lookup.
    if looks_like_sports(trade.get("slug"), trade.get("title"), trade.get("eventSlug")):
        meta = {"category": "sports", "closed": False, "resolved": False}
    else:
        # Market category + closure via Gamma
        meta = get_or_cache_market_meta(db, sess, trade["conditionId"])
        if meta is None:
            return False, "market metadata unavailable"

    if meta["category"] not in CATEGORY_FILTER:
        return False, f"category={meta['category']} (not in {CATEGORY_FILTER})"
    if meta["closed"] or meta["resolved"]:
        return False, "market closed or resolved"

    # Per-market position cap
    cm = db.execute(
        "SELECT COALESCE(SUM(bet_usd), 0) AS sum FROM paper_position "
        "WHERE condition_id = ? AND status = 'open'",
        (trade["conditionId"],),
    ).fetchone()
    if (cm["sum"] or 0) + BET_SIZE_USD > MAX_PER_MARKET:
        return False, f"per-market cap (already ${cm['sum']:.0f} in this market)"

    # Concurrent positions cap
    co = db.execute(
        "SELECT COUNT(*) AS n FROM paper_position WHERE status = 'open'",
    ).fetchone()
    if (co["n"] or 0) >= MAX_CONCURRENT:
        return False, f"concurrent cap (already {co['n']} open)"

    # Daily PnL stop
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dp = db.execute("SELECT halted FROM daily_pnl WHERE day = ?", (today,)).fetchone()
    if dp and dp["halted"]:
        return False, "daily stop tripped"

    return True, "ok"


# ---- Mirror-exit logic ------------------------------------------------ #
def maybe_close_paper_positions(db: sqlite3.Connection, observed: dict) -> None:
    """When a watchlist wallet exits a market, close our mirroring position.

    Definition of "exits": a SELL trade on a token where we have an open BUY
    paper position, or a BUY on a token where we have an open SELL.
    """
    # Find open positions on the same asset where direction is opposite.
    opp_side = "BUY" if observed["side"] == "SELL" else "SELL"
    open_paper = db.execute(
        "SELECT * FROM paper_position "
        "WHERE asset_token_id = ? AND side = ? AND status = 'open'",
        (observed["asset"], opp_side),
    ).fetchall()
    if not open_paper:
        return
    exit_price = float(observed["price"])
    for p in open_paper:
        # Realised PnL when closing:
        #   for original BUY:  pnl_usd = shares * (exit_price - entry_price)
        #   for original SELL: pnl_usd = shares * (entry_price - exit_price)
        # (signed-position semantics; same formula either side since `shares` is unsigned
        # and we just flip the sign.)
        if p["side"] == "BUY":
            pnl = p["shares"] * (exit_price - p["entry_price"])
        else:  # SELL
            pnl = p["shares"] * (p["entry_price"] - exit_price)
        db.execute(
            "UPDATE paper_position SET status='closed', closed_at_unix=?, "
            "close_reason='mirror_exit', realised_pnl_usd=? WHERE paper_id=?",
            (int(observed["timestamp"]), pnl, p["paper_id"]),
        )
        # Update daily PnL
        day = datetime.fromtimestamp(observed["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d")
        db.execute(
            "INSERT INTO daily_pnl (day, realised_usd) VALUES (?, ?) "
            "ON CONFLICT(day) DO UPDATE SET realised_usd = realised_usd + excluded.realised_usd",
            (day, pnl),
        )
        # Halt if daily stop breached
        row = db.execute("SELECT realised_usd FROM daily_pnl WHERE day = ?", (day,)).fetchone()
        if row and row["realised_usd"] <= DAILY_PNL_STOP_USD:
            db.execute("UPDATE daily_pnl SET halted = 1 WHERE day = ?", (day,))
            log.warning(f"DAILY STOP TRIPPED — realised ${row['realised_usd']:.0f} on {day}")
        log.info(
            f"  ↩ MIRROR-EXIT  paper_id={p['paper_id']}  "
            f"entry=${p['entry_price']:.3f} exit=${exit_price:.3f}  "
            f"PnL=${pnl:+,.2f}  ({p['title'][:50]})"
        )
    db.commit()


# ---- Trade ingestion ---------------------------------------------------- #
def trade_id(t: dict) -> str:
    """Stable ID for dedupe."""
    return f"{t.get('proxyWallet','')}|{t.get('transactionHash','')}|{t.get('outcomeIndex','')}|{t.get('side','')}"


def ingest_wallet_trades(db: sqlite3.Connection, sess: requests.Session, wallet: Wallet) -> int:
    """Poll and process new trades for one wallet. Returns # new trades found."""
    # Last-seen timestamp. If this is the first time we've seen this wallet,
    # set last_seen = now so we don't retroactively "trade" against history.
    row = db.execute("SELECT last_seen_unix FROM poll_state WHERE proxy_wallet = ?",
                     (wallet.address,)).fetchone()
    if row is None:
        bootstrap_ts = int(time.time())
        db.execute(
            "INSERT INTO poll_state (proxy_wallet, last_seen_unix) VALUES (?, ?)",
            (wallet.address, bootstrap_ts),
        )
        db.commit()
        last_seen = bootstrap_ts
        log.info(f"  ⏩ bootstrap: {wallet.address[:12]} last_seen set to {bootstrap_ts} (now)")
    else:
        last_seen = row["last_seen_unix"] or 0

    try:
        trades = fetch_recent_trades(sess, wallet.address, limit=TRADE_LIMIT_PER_POLL)
    except Exception as e:
        log.warning(f"  fetch failed for {wallet.address[:10]}: {e}")
        return 0

    new_count = 0
    for t in trades:
        ts = int(t.get("timestamp") or 0)
        if ts <= last_seen:
            continue
        tid = trade_id(t)
        if db.execute("SELECT 1 FROM observed_trade WHERE trade_id = ?", (tid,)).fetchone():
            continue
        # Insert observation
        db.execute(
            "INSERT OR IGNORE INTO observed_trade "
            "(trade_id, proxy_wallet, tx_hash, timestamp_unix, condition_id, asset_token_id, "
            " title, slug, outcome, outcome_index, side, price, size, observed_at_unix) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, wallet.address, t.get("transactionHash"), ts,
             t.get("conditionId"), t.get("asset"), t.get("title"), t.get("slug"),
             t.get("outcome"), t.get("outcomeIndex"), t.get("side"),
             float(t.get("price") or 0), float(t.get("size") or 0), int(time.time())),
        )
        new_count += 1
        log.info(
            f"  • {wallet.address[:10]}  {t['side']:4s}  ${t.get('price',0):.3f}×{t.get('size',0):.1f}  "
            f"{(t.get('title') or '')[:60]}"
        )
        # First check: does this trade EXIT one of our paper positions?
        maybe_close_paper_positions(db, t)
        # Then decision: would we OPEN a new paper position?
        ok, reason = decide(db, sess, t)
        if not ok:
            log.info(f"      gate: skip ({reason})")
        else:
            entry = float(t["price"])
            shares = BET_SIZE_USD / entry
            db.execute(
                "INSERT INTO paper_position "
                "(trade_id, opened_at_unix, condition_id, asset_token_id, title, side, "
                " entry_price, bet_usd, shares) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tid, ts, t["conditionId"], t["asset"], t.get("title"),
                 t["side"], entry, BET_SIZE_USD, shares),
            )
            log.info(f"      ✓ OPEN paper position  entry=${entry:.3f}  shares={shares:.2f}")

    if new_count:
        # Update last_seen
        max_ts = max(int(t.get("timestamp") or 0) for t in trades)
        db.execute(
            "INSERT INTO poll_state (proxy_wallet, last_seen_unix) VALUES (?, ?) "
            "ON CONFLICT(proxy_wallet) DO UPDATE SET last_seen_unix = excluded.last_seen_unix",
            (wallet.address, max_ts),
        )
    db.commit()
    return new_count


def print_summary(db: sqlite3.Connection, wallets: list[Wallet]) -> None:
    obs = db.execute("SELECT COUNT(*) AS n FROM observed_trade").fetchone()["n"]
    by_w = db.execute(
        "SELECT proxy_wallet, COUNT(*) AS n FROM observed_trade GROUP BY proxy_wallet ORDER BY n DESC"
    ).fetchall()
    open_p = db.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(bet_usd), 0) AS exposure FROM paper_position WHERE status='open'"
    ).fetchone()
    closed = db.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(realised_pnl_usd), 0) AS pnl FROM paper_position WHERE status='closed'"
    ).fetchone()
    log.info("=" * 72)
    log.info(f"Observed trades:          {obs:,}")
    for r in by_w:
        log.info(f"  {r['proxy_wallet'][:10]}…  {r['n']:>6,} trades observed")
    log.info(f"Open paper positions:     {open_p['n']}  (exposure ${open_p['exposure']:,.0f})")
    log.info(f"Closed paper positions:   {closed['n']}  (cumulative PnL ${closed['pnl']:+,.2f})")
    log.info("=" * 72)


# ---- Main loop --------------------------------------------------------- #
_running = True
def _on_sigint(signum, frame):
    global _running
    log.info("Caught signal — shutting down after current poll…")
    _running = False


def main() -> None:
    signal.signal(signal.SIGINT, _on_sigint)
    signal.signal(signal.SIGTERM, _on_sigint)

    log.info(f"Phase 1 paper-trader starting  (bet=${BET_SIZE_USD}  bankroll=${BANKROLL_USD})")
    log.info(f"Ledger: {LEDGER_PATH}")
    db = open_db()
    sess = make_session()
    wallets = load_watchlist()
    log.info(f"Watchlist: {len(wallets)} wallet(s)")
    for w in wallets:
        log.info(f"  {w.address}  (hist PnL ${w.historical_pnl:,.0f}, "
                 f"{w.historical_n_trades} trades, hit {w.historical_hit_rate:.1%})")

    last_summary = 0
    while _running:
        loop_start = time.time()
        total_new = 0
        for w in wallets:
            if not _running:
                break
            total_new += ingest_wallet_trades(db, sess, w)
        if total_new:
            log.info(f"Poll complete: {total_new} new trade(s)")
        if time.time() - last_summary > 60:    # summary every minute
            print_summary(db, wallets)
            last_summary = time.time()
        elapsed = time.time() - loop_start
        sleep_for = max(0, POLL_INTERVAL_S - elapsed)
        if _running and sleep_for > 0:
            time.sleep(sleep_for)
    print_summary(db, wallets)
    db.close()
    log.info("Stopped cleanly.")


if __name__ == "__main__":
    main()
