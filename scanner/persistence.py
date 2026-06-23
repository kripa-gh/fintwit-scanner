"""
persistence.py — Gap 8 Fix
Cross-run history tracking committed to the repo after each run.

Tracks per ticker:
  - How many consecutive days it appeared on the watchlist
  - Historical recommendation changes
  - First seen date
  - Score trend (improving / deteriorating)

This enables:
  - "WABAG has been a BUY for 3 consecutive days" signals
  - "Score dropped from 8 to 5 since yesterday" alerts
  - Context the report wouldn't have from a single run
"""

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

HISTORY_PATH = Path(__file__).parent.parent / "data" / "run_history.json"
MAX_HISTORY_DAYS = 30   # keep 30 days of history


def load_history() -> Dict:
    """Load existing run history. Returns empty dict if no history yet."""
    if not HISTORY_PATH.exists():
        return {"runs": [], "tickers": {}}
    try:
        with open(HISTORY_PATH) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"History load failed: {e} — starting fresh")
        return {"runs": [], "tickers": {}}


def save_history(history: Dict) -> None:
    """Persist history to file. Called after each run."""
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(HISTORY_PATH, "w") as f:
            json.dump(history, f, indent=2, default=str)
        logger.info(f"History saved: {len(history.get('tickers', {}))} tickers tracked")
    except Exception as e:
        logger.error(f"History save failed: {e}")


def update_history(history: Dict, analysis_results: List[Dict], run_date: str = None) -> Dict:
    """
    Update history with today's results.
    Returns the updated history dict with enriched analysis results.
    """
    run_date = run_date or date.today().isoformat()

    # Prune runs older than MAX_HISTORY_DAYS
    cutoff = (
        datetime.strptime(run_date, "%Y-%m-%d") -
        __import__("datetime").timedelta(days=MAX_HISTORY_DAYS)
    ).strftime("%Y-%m-%d")

    history["runs"] = [r for r in history.get("runs", []) if r >= cutoff]
    if run_date not in history["runs"]:
        history["runs"].append(run_date)
        history["runs"].sort()

    ticker_history = history.setdefault("tickers", {})

    for result in analysis_results:
        ticker = result["ticker"]
        rec    = result["recommendation"]
        score  = result["score"]

        if ticker not in ticker_history:
            ticker_history[ticker] = {
                "first_seen":        run_date,
                "appearances":       [],
                "consecutive_days":  0,
                "last_seen":         None,
                "last_recommendation": None,
                "last_score":        None,
            }

        th = ticker_history[ticker]
        prev_rec   = th.get("last_recommendation")
        prev_score = th.get("last_score")

        # Track consecutive days
        # Check if last appearance was yesterday (or within the last 2 runs)
        runs = history["runs"]
        today_idx = runs.index(run_date) if run_date in runs else -1
        last_seen = th.get("last_seen")

        if last_seen and today_idx > 0 and runs[today_idx - 1] == last_seen:
            th["consecutive_days"] += 1
        else:
            th["consecutive_days"] = 1   # reset streak

        # Append today's snapshot
        th["appearances"].append({
            "date":           run_date,
            "recommendation": rec,
            "score":          score,
            "price":          result.get("current_price"),
            "rsi":            result.get("rsi"),
        })
        # Keep only last 30 appearances
        th["appearances"] = th["appearances"][-MAX_HISTORY_DAYS:]

        # Update metadata
        th["last_seen"]           = run_date
        th["last_recommendation"] = rec
        th["last_score"]          = score

        # Compute score trend
        if prev_score is not None:
            delta = score - prev_score
            if delta >= 2:
                score_trend = f"↑↑ +{delta} (improving fast)"
            elif delta == 1:
                score_trend = f"↑ +{delta} (improving)"
            elif delta == 0:
                score_trend = "→ unchanged"
            elif delta == -1:
                score_trend = f"↓ {delta} (weakening)"
            else:
                score_trend = f"↓↓ {delta} (deteriorating fast)"
        else:
            score_trend = "NEW — first appearance"

        # Recommendation change alert
        if prev_rec and prev_rec != rec:
            rec_change = f"{prev_rec} → {rec}"
        else:
            rec_change = None

        # Enrich the result dict with historical context
        result["consecutive_days"] = th["consecutive_days"]
        result["score_trend"]      = score_trend
        result["rec_change"]       = rec_change
        result["first_seen"]       = th["first_seen"]
        result["prev_score"]       = prev_score
        result["prev_rec"]         = prev_rec

        # Build history summary string for the report
        days = th["consecutive_days"]
        if days >= 3:
            result["history_badge"] = f"🔥 {days} consecutive days"
        elif days == 2:
            result["history_badge"] = f"📅 2nd consecutive day"
        elif th["first_seen"] == run_date:
            result["history_badge"] = "🆕 First appearance"
        else:
            result["history_badge"] = f"📅 Day {days}"

    return history


def get_ticker_history_summary(ticker: str, history: Dict, days: int = 7) -> List[Dict]:
    """Return last N days of ticker history for the report detail card."""
    th = history.get("tickers", {}).get(ticker, {})
    appearances = th.get("appearances", [])
    return appearances[-days:]


def get_consecutive_days(ticker: str, history: Dict) -> int:
    """Return how many consecutive days a ticker has appeared."""
    return history.get("tickers", {}).get(ticker, {}).get("consecutive_days", 0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Simulate 3 days of runs
    history = {"runs": [], "tickers": {}}

    mock_day1 = [
        {"ticker": "WABAG",    "recommendation": "BUY",   "score": 7, "current_price": 1750.0, "rsi": 65.0},
        {"ticker": "CLEANMAX", "recommendation": "WATCH", "score": 5, "current_price": 1200.0, "rsi": 58.0},
    ]
    mock_day2 = [
        {"ticker": "WABAG",    "recommendation": "BUY",   "score": 8, "current_price": 1820.0, "rsi": 67.0},
        {"ticker": "CLEANMAX", "recommendation": "BUY",   "score": 6, "current_price": 1280.0, "rsi": 62.0},
        {"ticker": "MTARTECH", "recommendation": "WATCH", "score": 5, "current_price": 8200.0, "rsi": 60.0},
    ]
    mock_day3 = [
        {"ticker": "WABAG",    "recommendation": "BUY",   "score": 8, "current_price": 1882.0, "rsi": 68.0},
        {"ticker": "CLEANMAX", "recommendation": "WATCH", "score": 4, "current_price": 1370.0, "rsi": 55.0},
        {"ticker": "MTARTECH", "recommendation": "CAUTION","score": 3,"current_price": 8374.0, "rsi": 62.0},
    ]

    for run_date, results in [
        ("2026-06-20", mock_day1),
        ("2026-06-21", mock_day2),
        ("2026-06-22", mock_day3),
    ]:
        history = update_history(history, results, run_date)

    print("=== HISTORY AFTER 3 DAYS ===\n")
    for ticker, th in history["tickers"].items():
        last = th["appearances"][-1]
        print(f"{ticker}:")
        print(f"  Consecutive days: {th['consecutive_days']}")
        print(f"  Score trend: {last.get('score', '?')}")
        print(f"  History badge will show: {mock_day3[[r['ticker'] for r in mock_day3].index(ticker)].get('history_badge', '?') if ticker in [r['ticker'] for r in mock_day3] else 'n/a'}")
        for ap in th["appearances"]:
            print(f"  {ap['date']}: {ap['recommendation']:12} score={ap['score']} price=₹{ap['price']}")
        print()
