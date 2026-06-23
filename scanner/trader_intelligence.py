"""
trader_intelligence.py — Tier 2 + Tier 3
Claude-powered trader credibility and account management.

Covers:
  1. Account classification (serious analyst vs noise vs pump)
  2. Credibility scoring based on call history
  3. Accuracy tracking (logged trades vs outcomes)
  4. Watchlist management suggestions (add/remove accounts)
  5. Performance attribution (which traders called what correctly)
"""

import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from scanner.claude_client import call_json

logger = logging.getLogger(__name__)

TRADER_DB_PATH = Path(__file__).parent.parent / "data" / "trader_intelligence.json"
RECLASS_DAYS   = 14   # reclassify accounts every 14 days


# ═══════════════════════════════════════════════════════════════════════════════
# ACCOUNT CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

CLASSIFY_SYSTEM = """You are evaluating Indian stock market Twitter accounts to determine signal quality.
Be analytical and conservative. Respond only with JSON."""

CLASSIFY_PROMPT = """Evaluate this Twitter account based on their recent tweets.

Username: @{username}
Followers: {followers}
Recent tweets (last 20):
{tweets}

Return JSON:
{{
  "account_type": "serious_analyst" | "active_trader" | "swing_trader" | "noise" | "pump_dump" | "educator" | "aggregator",
  "signal_quality": 1-5,
  "specificity": 1-5,
  "credibility_score": 1-100,
  "gives_price_levels": true | false,
  "gives_stop_loss": true | false,
  "typical_timeframe": "intraday" | "swing" | "positional" | "mixed",
  "posts_charts": true | false,
  "reasoning": "one sentence why",
  "recommended_weight": 0.0-2.0,
  "should_remove": true | false,
  "remove_reason": "reason or null"
}}

Weight guide: 2.0=top signal, 1.0=normal, 0.5=low quality, 0.0=remove"""


def classify_account(
    username: str,
    followers: int,
    recent_tweets: List[str],
    trader_db: Dict,
) -> Dict:
    """
    Classify a single Twitter account.
    Uses cached classification if < RECLASS_DAYS old.
    """
    # Check cache
    cached = trader_db.get("accounts", {}).get(username, {})
    if cached:
        last_classified = cached.get("last_classified", "2000-01-01")
        days_ago = (date.today() - date.fromisoformat(last_classified)).days
        if days_ago < RECLASS_DAYS:
            return cached

    tweets_text = "\n".join([f"- {t[:150]}" for t in recent_tweets[:20]])

    result = call_json(
        prompt=CLASSIFY_PROMPT.format(
            username=username,
            followers=followers,
            tweets=tweets_text,
        ),
        system=CLASSIFY_SYSTEM,
        max_tokens=400,
        fallback={
            "account_type":       "active_trader",
            "credibility_score":  50,
            "recommended_weight": 1.0,
            "should_remove":      False,
        }
    )

    if isinstance(result, dict):
        result["username"]        = username
        result["last_classified"] = date.today().isoformat()
        result["followers"]       = followers
    return result


def classify_all_accounts(
    members: List[Dict],
    tweets: List[Dict],
    trader_db: Dict,
) -> Dict:
    """
    Classify all list members.
    Skips recently classified accounts (cache).
    Returns updated trader_db.
    """
    # Group tweets by username
    tweets_by_user = {}
    for t in tweets:
        u = t.get("username", "")
        if u not in tweets_by_user:
            tweets_by_user[u] = []
        tweets_by_user[u].append(t.get("text", ""))

    accounts_db = trader_db.setdefault("accounts", {})
    to_classify  = []

    for member in members:
        username = member.get("username", "")
        cached   = accounts_db.get(username, {})
        if cached:
            last = cached.get("last_classified", "2000-01-01")
            if (date.today() - date.fromisoformat(last)).days < RECLASS_DAYS:
                continue
        to_classify.append(member)

    logger.info(f"Classifying {len(to_classify)}/{len(members)} accounts (rest cached)")

    for i, member in enumerate(to_classify):
        username  = member.get("username", "")
        followers = member.get("followers", 0)
        recent    = tweets_by_user.get(username, [])

        if not recent:
            # No recent tweets — use cached or default
            if username not in accounts_db:
                accounts_db[username] = {
                    "username":         username,
                    "account_type":     "active_trader",
                    "credibility_score":50,
                    "recommended_weight": 1.0,
                    "should_remove":    False,
                    "last_classified":  date.today().isoformat(),
                }
            continue

        result = classify_account(username, followers, recent, trader_db)
        accounts_db[username] = result
        logger.debug(f"  @{username}: {result.get('account_type')} credibility={result.get('credibility_score')}")

        if (i + 1) % 10 == 0:
            logger.info(f"  Classified {i+1}/{len(to_classify)}...")
        time.sleep(0.3)

    trader_db["accounts"] = accounts_db
    return trader_db


def get_account_weight(username: str, trader_db: Dict) -> float:
    """Return the recommended weight for a trader's mentions."""
    acc = trader_db.get("accounts", {}).get(username, {})
    return float(acc.get("recommended_weight", 1.0))


def get_accounts_to_remove(trader_db: Dict) -> List[Dict]:
    """Return list of accounts Claude recommends removing."""
    removals = []
    for username, acc in trader_db.get("accounts", {}).items():
        if acc.get("should_remove") and acc.get("remove_reason"):
            removals.append({
                "username":      username,
                "reason":        acc["remove_reason"],
                "account_type":  acc.get("account_type", "unknown"),
            })
    return removals


# ═══════════════════════════════════════════════════════════════════════════════
# ACCURACY TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

def log_trader_call(
    trader_db: Dict,
    username: str,
    ticker: str,
    sentiment: str,
    entry_price: Optional[float],
    target_price: Optional[float],
    run_date: str,
) -> Dict:
    """Log a trader's call for future accuracy tracking."""
    calls = trader_db.setdefault("calls", [])
    calls.append({
        "username":    username,
        "ticker":      ticker,
        "sentiment":   sentiment,
        "entry_price": entry_price,
        "target_price":target_price,
        "date":        run_date,
        "outcome":     None,   # filled in later by performance tracker
        "outcome_date":None,
        "return_pct":  None,
    })
    return trader_db


def update_call_outcomes(trader_db: Dict, price_data: Dict[str, float]) -> Dict:
    """
    Update outcomes for open calls using current prices.
    price_data: {ticker: current_price}
    """
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    updated = 0

    for call in trader_db.get("calls", []):
        if call.get("outcome") is not None:
            continue   # already resolved
        if call.get("date", "") < cutoff:
            call["outcome"] = "expired"
            continue

        ticker = call.get("ticker", "")
        entry  = call.get("entry_price")
        target = call.get("target_price")
        sentiment = call.get("sentiment", "")
        current = price_data.get(ticker)

        if not all([entry, current]):
            continue

        ret_pct = ((current - entry) / entry) * 100

        # Resolve if target hit or 30 days passed
        if sentiment == "bullish" and target and current >= target:
            call["outcome"]      = "win"
            call["outcome_date"] = date.today().isoformat()
            call["return_pct"]   = round(ret_pct, 2)
            updated += 1
        elif sentiment == "bearish" and target and current <= target:
            call["outcome"]      = "win"
            call["outcome_date"] = date.today().isoformat()
            call["return_pct"]   = round(ret_pct, 2)
            updated += 1

    if updated:
        logger.info(f"Updated {updated} trader call outcomes")

    # Recompute accuracy per trader
    _recompute_accuracy(trader_db)
    return trader_db


def _recompute_accuracy(trader_db: Dict) -> None:
    """Recompute win rate per trader from resolved calls."""
    stats: Dict[str, Dict] = {}

    for call in trader_db.get("calls", []):
        outcome = call.get("outcome")
        if outcome not in ("win", "loss", "expired"):
            continue
        username = call.get("username", "")
        if username not in stats:
            stats[username] = {"wins": 0, "losses": 0, "expired": 0, "total_return": 0.0}

        if outcome == "win":
            stats[username]["wins"] += 1
            stats[username]["total_return"] += call.get("return_pct", 0) or 0
        elif outcome == "loss":
            stats[username]["losses"] += 1
            stats[username]["total_return"] += call.get("return_pct", 0) or 0
        elif outcome == "expired":
            stats[username]["expired"] += 1

    # Update account records
    for username, s in stats.items():
        total = s["wins"] + s["losses"]
        win_rate = round(s["wins"] / total * 100, 1) if total > 0 else None
        if username in trader_db.get("accounts", {}):
            trader_db["accounts"][username]["win_rate"]    = win_rate
            trader_db["accounts"][username]["total_calls"] = total
            trader_db["accounts"][username]["avg_return"]  = round(s["total_return"] / max(total, 1), 2)


# ═══════════════════════════════════════════════════════════════════════════════
# WATCHLIST MANAGEMENT SUGGESTIONS
# ═══════════════════════════════════════════════════════════════════════════════

WATCHLIST_SYSTEM = """You are managing a curated list of Indian stock market traders on Twitter.
Suggest improvements based on account quality data. Be specific and actionable. Respond with JSON."""

WATCHLIST_PROMPT = """Based on this trader database, suggest watchlist improvements.

Current accounts summary:
{summary}

Accounts flagged for removal:
{removals}

Top performing accounts:
{top_performers}

Return JSON:
{{
  "remove": [
    {{"username": "...", "reason": "..."}}
  ],
  "upgrade_weight": [
    {{"username": "...", "reason": "...", "new_weight": 2.0}}
  ],
  "downgrade_weight": [
    {{"username": "...", "reason": "...", "new_weight": 0.5}}
  ],
  "find_similar_to": [
    {{"reference_account": "...", "why": "high accuracy, specific levels"}}
  ],
  "summary": "2-line summary of list health"
}}"""


def generate_watchlist_suggestions(trader_db: Dict) -> Dict:
    """Generate watchlist management suggestions using Claude."""
    accounts = trader_db.get("accounts", {})
    if not accounts:
        return {}

    # Build summary
    type_counts = {}
    for acc in accounts.values():
        t = acc.get("account_type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    summary = f"Total: {len(accounts)} accounts. Types: {type_counts}"

    removals = [
        f"@{u}: {a.get('remove_reason','')}"
        for u, a in accounts.items()
        if a.get("should_remove")
    ]

    top_performers = [
        f"@{u}: win_rate={a.get('win_rate','?')}% credibility={a.get('credibility_score',0)}"
        for u, a in sorted(
            accounts.items(),
            key=lambda x: x[1].get("credibility_score", 0),
            reverse=True
        )[:5]
    ]

    result = call_json(
        prompt=WATCHLIST_PROMPT.format(
            summary=summary,
            removals="\n".join(removals[:10]) or "None",
            top_performers="\n".join(top_performers),
        ),
        system=WATCHLIST_SYSTEM,
        max_tokens=800,
        fallback={"summary": "Watchlist analysis unavailable"},
    )

    return result or {}


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════

def load_trader_db() -> Dict:
    if not TRADER_DB_PATH.exists():
        return {"accounts": {}, "calls": [], "last_updated": None}
    try:
        with open(TRADER_DB_PATH) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Trader DB load failed: {e}")
        return {"accounts": {}, "calls": [], "last_updated": None}


def save_trader_db(db: Dict) -> None:
    TRADER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db["last_updated"] = date.today().isoformat()
    try:
        with open(TRADER_DB_PATH, "w") as f:
            json.dump(db, f, indent=2, default=str)
        logger.info(f"Trader DB saved: {len(db.get('accounts',{}))} accounts, {len(db.get('calls',[]))} calls")
    except Exception as e:
        logger.error(f"Trader DB save failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    db = load_trader_db()
    sample_members = [
        {"username": "VCPSwing",    "followers": 45000},
        {"username": "Fintech00",   "followers": 12000},
        {"username": "noise_acc",   "followers": 500},
    ]
    sample_tweets = [
        {"username": "VCPSwing",  "text": "#MTARTECH VCP setup. Entry 8200, SL 7800, T1 9000, T2 10000. Adding 5% of portfolio."},
        {"username": "VCPSwing",  "text": "#WABAG Kuwait order is a game changer. 1820 is the breakout level. SL 1700."},
        {"username": "Fintech00", "text": "#WABAG Going mad! Great stock #CLEANMAX too"},
        {"username": "noise_acc", "text": "Buy these 10 stocks and become rich! DM for tips. #stockmarket"},
    ]

    db = classify_all_accounts(sample_members, sample_tweets, db)
    for username, acc in db["accounts"].items():
        print(f"@{username}: {acc.get('account_type')} weight={acc.get('recommended_weight')} credibility={acc.get('credibility_score')}")
