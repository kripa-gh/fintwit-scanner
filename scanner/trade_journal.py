"""
trade_journal.py
Google Sheets-based trade journal integration.

Connects to a user-specified Google Sheet to:
  1. Read open positions (for position management advice)
  2. Log new trade recommendations
  3. Track outcomes (mark win/loss when price hits target/stop)
  4. Compute personal win rate by setup type
  5. Feed performance data back into Claude for personalised recommendations

Setup:
  - User creates a Google Sheet with two tabs: "Positions" and "Log"
  - Share the sheet with the service account email or use public link
  - Set TRADE_JOURNAL_SHEET_ID in GitHub Secrets

Sheet format (Positions tab):
  Ticker | Entry Date | Entry Price | Qty | Stop Loss | Target | Setup Type | Status

Sheet format (Log tab):
  Date | Ticker | Recommendation | Score | Entry Zone | Stop | Target | Outcome | Return%
"""

import json
import logging
import os
import urllib.request
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SHEET_ID  = os.getenv("TRADE_JOURNAL_SHEET_ID", "")
API_KEY   = os.getenv("GOOGLE_SHEETS_API_KEY", "")   # optional read-only key
LOCAL_JOURNAL_PATH = Path(__file__).parent.parent / "data" / "trade_journal.json"

# ── A4: forward-return scoring of logged calls ────────────────────────────────
SCORE_HORIZONS   = (5, 20)   # trading days at which each call's forward return is measured
RETENTION_DAYS   = 120       # keep fully-scored calls this long for the rollup window
_MAX_LOG_ENTRIES = 5000      # hard safety cap on log size


# ═══════════════════════════════════════════════════════════════════════════════
# LOCAL JOURNAL (no Google Sheets dependency — works out of the box)
# ═══════════════════════════════════════════════════════════════════════════════

def load_journal() -> Dict:
    """Load local trade journal from JSON file."""
    if not LOCAL_JOURNAL_PATH.exists():
        return {
            "positions": [],
            "log":       [],
            "stats":     {},
            "last_updated": None,
        }
    try:
        with open(LOCAL_JOURNAL_PATH) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Journal load failed: {e}")
        return {"positions": [], "log": [], "stats": {}, "last_updated": None}


def save_journal(journal: Dict) -> None:
    """Save journal to local JSON file (committed to repo by GitHub Actions)."""
    LOCAL_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    journal["last_updated"] = datetime.now().isoformat()
    try:
        with open(LOCAL_JOURNAL_PATH, "w") as f:
            json.dump(journal, f, indent=2, default=str)
        logger.info(f"Journal saved: {len(journal.get('positions',[]))} positions, {len(journal.get('log',[]))} log entries")
    except Exception as e:
        logger.error(f"Journal save failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# POSITION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def get_open_positions(journal: Dict) -> List[Dict]:
    """Return currently open positions."""
    return [p for p in journal.get("positions", []) if p.get("status") == "OPEN"]


def add_position(
    journal: Dict,
    ticker: str,
    entry_price: float,
    qty: int,
    stop_loss: float,
    target: float,
    setup_type: str,
    entry_date: str = None,
) -> Dict:
    """Log a new position entry."""
    entry_date = entry_date or date.today().isoformat()
    position = {
        "id":          f"{ticker}_{entry_date}",
        "ticker":      ticker,
        "entry_date":  entry_date,
        "entry_price": entry_price,
        "qty":         qty,
        "stop_loss":   stop_loss,
        "target":      target,
        "setup_type":  setup_type,
        "status":      "OPEN",
        "exit_price":  None,
        "exit_date":   None,
        "return_pct":  None,
        "outcome":     None,
    }
    journal.setdefault("positions", []).append(position)
    return journal


def update_position_outcomes(journal: Dict, current_prices: Dict[str, float]) -> Dict:
    """
    Check open positions against current prices.
    Auto-close positions where stop or target is hit.
    """
    positions = journal.get("positions", [])
    updated   = 0

    for pos in positions:
        if pos.get("status") != "OPEN":
            continue

        ticker  = pos["ticker"]
        current = current_prices.get(ticker)
        if not current:
            continue

        sl     = pos.get("stop_loss", 0)
        target = pos.get("target", float("inf"))
        entry  = pos.get("entry_price", current)

        if current <= sl:
            pos["status"]     = "CLOSED"
            pos["exit_price"] = current
            pos["exit_date"]  = date.today().isoformat()
            pos["return_pct"] = round(((current - entry) / entry) * 100, 2)
            pos["outcome"]    = "STOP HIT"
            updated += 1
        elif current >= target:
            pos["status"]     = "CLOSED"
            pos["exit_price"] = current
            pos["exit_date"]  = date.today().isoformat()
            pos["return_pct"] = round(((current - entry) / entry) * 100, 2)
            pos["outcome"]    = "TARGET HIT"
            updated += 1

    if updated:
        logger.info(f"Closed {updated} positions (stop/target hit)")
        _recompute_stats(journal)

    return journal


def _recompute_stats(journal: Dict) -> None:
    """Recompute win rate and other stats from closed positions."""
    closed = [p for p in journal.get("positions", []) if p.get("status") == "CLOSED"]
    if not closed:
        return

    wins   = [p for p in closed if p.get("outcome") == "TARGET HIT"]
    losses = [p for p in closed if p.get("outcome") == "STOP HIT"]
    total  = len(wins) + len(losses)

    win_rate     = round(len(wins) / max(total, 1) * 100, 1)
    avg_win      = round(sum(p.get("return_pct", 0) for p in wins)   / max(len(wins), 1), 2)
    avg_loss     = round(sum(p.get("return_pct", 0) for p in losses) / max(len(losses), 1), 2)
    expectancy   = round((win_rate/100 * avg_win) + ((1-win_rate/100) * avg_loss), 2)

    # By setup type
    by_setup = {}
    for p in closed:
        st = p.get("setup_type", "Unknown")
        if st not in by_setup:
            by_setup[st] = {"wins": 0, "losses": 0, "returns": []}
        if p.get("outcome") == "TARGET HIT":
            by_setup[st]["wins"] += 1
        else:
            by_setup[st]["losses"] += 1
        by_setup[st]["returns"].append(p.get("return_pct", 0))

    setup_stats = {}
    for st, data in by_setup.items():
        total_st = data["wins"] + data["losses"]
        setup_stats[st] = {
            "win_rate":  round(data["wins"] / max(total_st, 1) * 100, 1),
            "trades":    total_st,
            "avg_return":round(sum(data["returns"]) / max(len(data["returns"]),1), 2),
        }

    journal["stats"] = {
        "total_closed":  total,
        "win_rate":      win_rate,
        "avg_win_pct":   avg_win,
        "avg_loss_pct":  avg_loss,
        "expectancy":    expectancy,
        "by_setup":      setup_stats,
        "last_updated":  date.today().isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# LOG RECOMMENDATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def log_recommendation(
    journal: Dict,
    ticker: str,
    recommendation: str,
    score: int,
    entry_zone: str,
    stop: float,
    target: float,
    setup_type: str,
    run_date: str = None,
    entry_price: float = None,
) -> Dict:
    """Log a system recommendation to the journal log.

    entry_price (the price at flag time) is what makes the call measurable later:
    score_pending_calls() reads it to compute forward returns. A call logged without
    it can never be scored, so always pass current_price here.
    """
    run_date = run_date or date.today().isoformat()
    entry = {
        "date":           run_date,
        "ticker":         ticker,
        "recommendation": recommendation,
        "score":          score,
        "entry_price":    round(float(entry_price), 2) if entry_price else None,
        "entry_zone":     entry_zone,
        "stop":           stop,
        "target":         target,
        "setup_type":     setup_type,
        "acted_on":       None,   # user can manually update
        "ret_5d":         None,   # forward returns (%), filled by score_pending_calls()
        "ret_20d":        None,
        "outcome":        None,   # "win"/"loss" at 20d — was the call directionally right?
        "scored":         False,  # True once the 20d return has been recorded
    }
    journal.setdefault("log", []).append(entry)
    # Retention: NEVER drop a call before it is fully scored. Forward returns need up
    # to 20 trading days to mature; the old [-200:] cap deleted calls in ~6 days, which
    # is exactly why nothing was ever measured. Keep every unscored call, plus scored
    # calls within RETENTION_DAYS for the rollup window. Hard cap is a safety net only.
    cutoff = (date.today() - timedelta(days=RETENTION_DAYS)).isoformat()
    journal["log"] = [
        e for e in journal["log"]
        if (not e.get("scored")) or (e.get("date", "") >= cutoff)
    ][-_MAX_LOG_ENTRIES:]
    return journal


# ═══════════════════════════════════════════════════════════════════════════════
# A4: FORWARD-RETURN SCORING — is the scanner's judgment actually any good?
# Every run already logs each call (ticker, date, score, recommendation, entry price).
# This scores those calls against forward price moves so accuracy becomes a NUMBER,
# split by score bucket and recommendation. Returns come from yfinance price history —
# no broker token, no manual logging.
# ═══════════════════════════════════════════════════════════════════════════════

def _call_outcome(entry: Dict) -> Optional[str]:
    """Was the call directionally right at the longest horizon? -> win / loss / None."""
    ret = entry.get(f"ret_{max(SCORE_HORIZONS)}d")
    if ret is None:
        return None
    rec     = (entry.get("recommendation") or "").upper()
    bullish = any(k in rec for k in ("BUY", "WATCH", "ACCUMULATE", "ADD", "LONG"))
    bearish = any(k in rec for k in ("AVOID", "SHORT", "SELL", "EXIT", "REDUCE"))
    if bullish and not bearish:
        return "win" if ret > 0 else "loss"
    if bearish and not bullish:
        return "win" if ret < 0 else "loss"
    return None   # neutral / unclassifiable recommendation — not scored for direction


def _fill_forward_returns(symbol: str, entries: List[Dict]) -> None:
    """Fill ret_5d / ret_20d for one ticker's pending entries, in place.
    Uses the price series' own index as the trading-day calendar, so 'N trading days
    later' = N rows after the flag date — no holiday arithmetic, and the base is the
    captured entry_price (the exact price the scanner acted on)."""
    import yfinance as yf
    import pandas as pd
    try:
        df = yf.download(f"{symbol}.NS", period="1y", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or len(df) == 0:
            return
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        close = pd.to_numeric(df["Close"], errors="coerce").dropna()
    except Exception as e:
        logger.debug(f"Scoreboard price fetch failed for {symbol}: {e}")
        return
    if len(close) == 0:
        return
    idx_dates = [d.date() for d in close.index]
    for e in entries:
        entry_px = e.get("entry_price")
        if not entry_px:
            continue
        try:
            entry_d = date.fromisoformat(e["date"])
        except Exception:
            continue
        pos = next((i for i, d in enumerate(idx_dates) if d >= entry_d), None)
        if pos is None:
            continue
        for h in SCORE_HORIZONS:
            key = f"ret_{h}d"
            if e.get(key) is not None:
                continue
            if pos + h < len(close):
                fwd = float(close.iloc[pos + h])
                e[key] = round((fwd - entry_px) / entry_px * 100, 2)
        if e.get(f"ret_{max(SCORE_HORIZONS)}d") is not None and not e.get("scored"):
            e["scored"]  = True
            e["outcome"] = _call_outcome(e)


def score_pending_calls(journal: Dict, today: date = None) -> Dict:
    """Fill forward returns for matured calls. Cheap and idempotent: only fetches
    tickers with at least one unscored call old enough to (possibly) score, and only
    fills horizons whose forward bar already exists. Safe to call every run."""
    today = today or date.today()
    pending: Dict[str, List[Dict]] = {}
    for e in journal.get("log", []):
        if e.get("scored") or e.get("entry_price") is None:
            continue
        try:
            age = (today - date.fromisoformat(e["date"])).days
        except Exception:
            continue
        if age < min(SCORE_HORIZONS):   # too soon for even the 5-day bar
            continue
        pending.setdefault(e["ticker"], []).append(e)
    if not pending:
        return journal
    n = sum(len(v) for v in pending.values())
    logger.info(f"Scoreboard: scoring {n} matured call(s) across {len(pending)} ticker(s)")
    for symbol, entries in pending.items():
        _fill_forward_returns(symbol, entries)
    journal["last_scored"] = today.isoformat()
    return journal


def _score_bucket(score) -> str:
    if score is None:  return "unknown"
    if score >= 7:     return "high (score ≥7)"
    if score >= 4:     return "mid (score 4–6)"
    return "low (score ≤3)"


def _agg(entries: List[Dict]) -> Optional[Dict]:
    n = len(entries)
    if n == 0:
        return None
    wins = sum(1 for e in entries if e.get("outcome") == "win")
    r5   = [e["ret_5d"]  for e in entries if e.get("ret_5d")  is not None]
    r20  = [e["ret_20d"] for e in entries if e.get("ret_20d") is not None]
    return {
        "n":           n,
        "hit_rate":    round(wins / n * 100, 1),
        "avg_ret_5d":  round(sum(r5)  / len(r5),  2) if r5  else None,
        "avg_ret_20d": round(sum(r20) / len(r20), 2) if r20 else None,
    }


def compute_call_scoreboard(journal: Dict) -> Dict:
    """Aggregate scored calls into hit-rate + avg forward return, split by score
    bucket and recommendation. This is the 'is the scanner any good' number — the
    thing no display fix could ever tell you."""
    log     = journal.get("log", [])
    scored  = [e for e in log if e.get("outcome") in ("win", "loss")]
    pending = sum(1 for e in log if e.get("entry_price") is not None and not e.get("scored"))

    by_score = {}
    for b in ("high (score ≥7)", "mid (score 4–6)", "low (score ≤3)", "unknown"):
        a = _agg([e for e in scored if _score_bucket(e.get("score")) == b])
        if a:
            by_score[b] = a

    groups: Dict[str, List[Dict]] = {}
    for e in scored:
        groups.setdefault((e.get("recommendation") or "?").upper(), []).append(e)
    by_rec = {k: _agg(v) for k, v in groups.items()}

    logged_dates = [e["date"] for e in log if e.get("entry_price") is not None]
    return {
        "scored_count":  len(scored),
        "pending_count": pending,
        "overall":       _agg(scored),
        "by_score":      by_score,
        "by_rec":        by_rec,
        "first_logged":  min(logged_dates) if logged_dates else None,
        "has_data":      len(scored) >= 10,   # below this, the numbers are noise
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PERFORMANCE ANALYSIS FOR CLAUDE
# ═══════════════════════════════════════════════════════════════════════════════

def get_performance_context(journal: Dict) -> Dict:
    """
    Build a performance context dict for Claude to use in personalised recommendations.
    Tells Claude what's working and what isn't for this specific trader.
    """
    stats    = journal.get("stats", {})
    positions= journal.get("positions", [])
    log      = journal.get("log", [])

    open_pos = [p for p in positions if p.get("status") == "OPEN"]
    open_tickers = [p["ticker"] for p in open_pos]

    # Best and worst setup types
    by_setup = stats.get("by_setup", {})
    if by_setup:
        best_setup  = max(by_setup, key=lambda s: by_setup[s].get("win_rate", 0))
        worst_setup = min(by_setup, key=lambda s: by_setup[s].get("win_rate", 0))
    else:
        best_setup = worst_setup = None

    # Recent performance (last 10 closed)
    recent_closed = [
        p for p in reversed(positions)
        if p.get("status") == "CLOSED"
    ][:10]
    recent_win_rate = (
        sum(1 for p in recent_closed if p.get("outcome") == "TARGET HIT") /
        max(len(recent_closed), 1) * 100
    )

    return {
        "open_positions":      open_tickers,
        "open_count":          len(open_pos),
        "total_trades":        stats.get("total_closed", 0),
        "overall_win_rate":    stats.get("win_rate"),
        "recent_win_rate":     round(recent_win_rate, 1),
        "avg_win_pct":         stats.get("avg_win_pct"),
        "avg_loss_pct":        stats.get("avg_loss_pct"),
        "expectancy":          stats.get("expectancy"),
        "best_setup":          best_setup,
        "worst_setup":         worst_setup,
        "setup_stats":         by_setup,
        "has_data":            stats.get("total_closed", 0) >= 5,
    }


def get_position_context(journal: Dict, ticker: str) -> Optional[Dict]:
    """Return open position details for a specific ticker, if any."""
    for pos in journal.get("positions", []):
        if pos["ticker"] == ticker and pos.get("status") == "OPEN":
            return pos
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE SHEETS SYNC (optional — requires TRADE_JOURNAL_SHEET_ID)
# ═══════════════════════════════════════════════════════════════════════════════

def sync_from_google_sheets(journal: Dict) -> Dict:
    """
    Optional: sync positions from a Google Sheet.
    Only runs if TRADE_JOURNAL_SHEET_ID is set.
    Sheet must be publicly readable or share with service account.
    """
    if not SHEET_ID:
        return journal

    try:
        # Read Positions tab via Google Sheets API v4
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}"
            f"/values/Positions!A:H"
        )
        if API_KEY:
            url += f"?key={API_KEY}"

        req  = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        rows = data.get("values", [])
        if len(rows) < 2:   # header only
            return journal

        header = rows[0]
        positions_from_sheet = []

        for row in rows[1:]:
            if not row or not row[0]:
                continue
            pos = {}
            for i, col in enumerate(header):
                pos[col.lower().replace(" ", "_")] = row[i] if i < len(row) else ""

            # Normalise
            positions_from_sheet.append({
                "id":          f"{pos.get('ticker','')}_{pos.get('entry_date','')}",
                "ticker":      pos.get("ticker", "").upper(),
                "entry_date":  pos.get("entry_date", ""),
                "entry_price": float(pos.get("entry_price", 0) or 0),
                "qty":         int(pos.get("qty", 0) or 0),
                "stop_loss":   float(pos.get("stop_loss", 0) or 0),
                "target":      float(pos.get("target", 0) or 0),
                "setup_type":  pos.get("setup_type", ""),
                "status":      pos.get("status", "OPEN"),
                "exit_price":  None,
                "exit_date":   None,
                "return_pct":  None,
                "outcome":     None,
            })

        # Merge sheet positions into journal (sheet is source of truth for positions)
        sheet_ids = {p["id"] for p in positions_from_sheet}
        existing  = [p for p in journal.get("positions", []) if p["id"] not in sheet_ids]
        journal["positions"] = existing + positions_from_sheet
        logger.info(f"Synced {len(positions_from_sheet)} positions from Google Sheets")

    except Exception as e:
        logger.warning(f"Google Sheets sync failed: {e}")

    return journal


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test journal operations
    journal = load_journal()

    # Add a sample position
    journal = add_position(journal, "WABAG", 1820.0, 5, 1700.0, 2100.0, "breakout", "2026-06-20")
    journal = add_position(journal, "CLEANMAX", 1200.0, 3, 1100.0, 1450.0, "pullback", "2026-06-19")

    # Simulate price updates
    journal = update_position_outcomes(journal, {"WABAG": 2110.0, "CLEANMAX": 1095.0})

    print("=== JOURNAL STATS ===")
    print(json.dumps(journal["stats"], indent=2))

    print("\n=== OPEN POSITIONS ===")
    for p in get_open_positions(journal):
        print(f"  {p['ticker']}: entry ₹{p['entry_price']} | SL ₹{p['stop_loss']} | T ₹{p['target']}")

    print("\n=== PERFORMANCE CONTEXT ===")
    ctx = get_performance_context(journal)
    print(json.dumps(ctx, indent=2))
