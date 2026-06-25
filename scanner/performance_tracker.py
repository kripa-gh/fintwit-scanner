"""
performance_tracker.py — Tier 3 + Tier 4
Weekly performance debrief and anomaly detection.

Covers:
  1. Weekly performance debrief (Monday only)
  2. Recommendation accuracy over time
  3. Anomaly detection (sudden mention spikes, silent traders)
  4. Pre-market brief delivery (8:30am workflow)
"""

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from scanner.claude_client import call_json

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. WEEKLY PERFORMANCE DEBRIEF
# ═══════════════════════════════════════════════════════════════════════════════

DEBRIEF_SYSTEM = """You are a trading performance coach reviewing a week of stock recommendations.
Be honest and specific. Focus on patterns — what worked, what didn't, and why.
Respond only with JSON."""

DEBRIEF_PROMPT = """Review this week's stock recommendations and their outcomes.

Recommendations made this week:
{recommendations}

Market context this week:
- Nifty performance: {nifty_perf}%
- Market environment: {market_env}
- VIX range: {vix_range}

Return JSON:
{{
  "week_summary": "2-3 sentences summarising the week's calls",
  "what_worked": [
    {{
      "ticker": "TICKER",
      "recommendation": "BUY/WATCH/etc",
      "outcome": "what happened",
      "why_it_worked": "the key reason this call was right"
    }}
  ],
  "what_didnt_work": [
    {{
      "ticker": "TICKER",
      "recommendation": "BUY/WATCH/etc",
      "outcome": "what happened",
      "lesson": "what to do differently"
    }}
  ],
  "pattern_observations": [
    "observation about what types of setups worked this week",
    "observation about market conditions and their effect"
  ],
  "next_week_watch": ["TICKER1", "TICKER2"],
  "model_adjustment_suggestion": "one specific suggestion to improve the scoring model",
  "overall_grade": "A" | "B" | "C" | "D"
}}"""


def generate_weekly_debrief(
    history: Dict,
    market_env: Dict,
) -> Optional[Dict]:
    """
    Generate weekly performance debrief.
    Only runs on Mondays.
    Returns None if not Monday or insufficient history.
    """
    if date.today().weekday() != 0:   # 0 = Monday
        return None

    runs = history.get("runs", [])
    if len(runs) < 3:
        return None

    # Get last 5 trading days of recommendations
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    recent_runs = [r for r in runs if r >= week_ago]

    if not recent_runs:
        return None

    # Build recommendations list
    recs = []
    tickers = history.get("tickers", {})
    for ticker, th in tickers.items():
        for app in th.get("appearances", []):
            if app["date"] in recent_runs:
                recs.append(
                    f"{app['date']}: {ticker} — {app['recommendation']} "
                    f"(score {app['score']}) at ₹{app.get('price','?')}"
                )

    if not recs:
        return None

    nifty  = market_env.get("nifty", {})
    vix    = market_env.get("vix", {})

    result = call_json(
        prompt=DEBRIEF_PROMPT.format(
            recommendations = "\n".join(recs[-30:]),
            nifty_perf      = nifty.get("ret_1m_pct", 0),
            market_env      = market_env.get("label", "Unknown"),
            vix_range       = f"{vix.get('vix', 0):.1f}",
        ),
        system=DEBRIEF_SYSTEM,
        max_tokens=1000,
        fallback=None,
    )

    if result:
        logger.info(f"Weekly debrief generated: grade={result.get('overall_grade','?')}")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 2. ANOMALY DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

ANOMALY_SYSTEM = """You are a market surveillance analyst detecting unusual patterns in social media activity.
Identify genuine anomalies vs normal variation. Respond only with JSON."""

ANOMALY_PROMPT = """Detect anomalies in today's stock mention data vs historical patterns.

Today's mentions:
{today}

Historical average mentions (last 30 days):
{historical}

Trader activity changes:
{trader_activity}

Return JSON:
{{
  "anomalies": [
    {{
      "ticker": "TICKER or null",
      "type": "sudden_spike" | "silent_trader" | "coordinated_pump" | "unusual_sentiment_shift" | "volume_divergence",
      "description": "what is unusual",
      "severity": "high" | "medium" | "low",
      "action": "investigate" | "monitor" | "flag_for_removal"
    }}
  ],
  "clean_signals": ["list of tickers with organic, credible mention patterns"],
  "summary": "1 sentence overall anomaly assessment"
}}"""


def detect_anomalies(
    ticker_signals: Dict[str, Dict],
    history: Dict,
    trader_db: Dict,
) -> Dict:
    """Detect unusual patterns in today's ticker mentions."""

    # Build historical averages
    hist_mentions = {}
    for ticker, th in history.get("tickers", {}).items():
        apps = th.get("appearances", [])
        if apps:
            avg = len(apps) / max(len(history.get("runs", [1])), 1)
            hist_mentions[ticker] = round(avg, 1)

    today_text = "\n".join([
        f"{ticker}: {sig['mentions']} mentions, sentiment={sig.get('sentiment_label','?')}, "
        f"conviction={sig.get('avg_conviction',0):.1f}"
        for ticker, sig in ticker_signals.items()
    ])

    hist_text = "\n".join([
        f"{ticker}: avg {avg:.1f} mentions/day"
        for ticker, avg in list(hist_mentions.items())[:20]
    ])

    # Check for silent high-credibility traders
    accounts = trader_db.get("accounts", {})
    high_cred = [u for u, a in accounts.items()
                 if a.get("credibility_score", 0) >= 70]
    active_traders = list(set(
        u for sig in ticker_signals.values()
        for u in sig.get("traders", [])
    ))
    silent_high_cred = [u for u in high_cred if u not in active_traders]

    trader_text = (
        f"High-credibility traders silent today: {', '.join(silent_high_cred[:5]) or 'None'}\n"
        f"Active today: {len(active_traders)} traders"
    )

    result = call_json(
        prompt=ANOMALY_PROMPT.format(
            today           = today_text or "No mentions today",
            historical      = hist_text  or "No history yet",
            trader_activity = trader_text,
        ),
        system=ANOMALY_SYSTEM,
        max_tokens=1200,   # 600 truncated mid-description on multi-anomaly output
        fallback={"anomalies": [], "summary": "No anomalies detected"},
    )
    return result or {}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. POSITION MANAGEMENT (if user provides portfolio)
# ═══════════════════════════════════════════════════════════════════════════════

POSITION_SYSTEM = """You are a portfolio risk manager for an Indian equity swing trader.
Give specific, actionable position management advice. Respond only with JSON."""

POSITION_PROMPT = """Review current portfolio positions against today's analysis.

Current positions:
{positions}

Today's recommendations:
{recommendations}

Market environment: {market_env}
Account size: ₹{account_size}
Risk per trade: {risk_pct}% of account

Return JSON:
{{
  "position_actions": [
    {{
      "ticker": "TICKER",
      "action": "hold" | "add" | "trim" | "exit" | "stop_hit",
      "rationale": "specific reason",
      "suggested_size_change": "e.g. +2% or -50% of position"
    }}
  ],
  "new_entries": [
    {{
      "ticker": "TICKER",
      "entry_zone": "₹X–₹Y",
      "size_pct": number,
      "rationale": "why now"
    }}
  ],
  "portfolio_heat": "low" | "medium" | "high",
  "cash_recommendation": "how much cash to keep today",
  "summary": "2 sentence portfolio summary"
}}"""


def generate_position_advice(
    analysis_results: List[Dict],
    market_env: Dict,
    portfolio: Optional[List[Dict]] = None,
    account_size: int = 500000,
    risk_pct: float = 1.0,
) -> Optional[Dict]:
    """
    Generate position management advice.
    portfolio = [{"ticker": "WABAG", "entry": 1820, "qty": 10, "current_value": 18820}]
    Only runs if portfolio is provided.
    """
    if not portfolio:
        return None

    pos_text = "\n".join([
        f"{p['ticker']}: entry ₹{p.get('entry',0)}, qty {p.get('qty',0)}, "
        f"current ₹{p.get('current_value',0)}"
        for p in portfolio
    ])

    rec_text = "\n".join([
        f"{r['ticker']}: {r['recommendation']} score={r['score']}/{r['max_score']}"
        for r in analysis_results[:15]
    ])

    result = call_json(
        prompt=POSITION_PROMPT.format(
            positions    = pos_text,
            recommendations = rec_text,
            market_env   = market_env.get("label", "Unknown"),
            account_size = account_size,
            risk_pct     = risk_pct,
        ),
        system=POSITION_SYSTEM,
        max_tokens=800,
        fallback=None,
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import json

    # Test anomaly detection with mock data
    mock_signals = {
        "WABAG":    {"mentions": 8, "sentiment_label": "Strong Bullish", "avg_conviction": 4.5, "traders": ["VCPSwing","Fintech00"]},
        "FAKEPUMP": {"mentions": 25, "sentiment_label": "Strong Bullish", "avg_conviction": 5.0, "traders": ["acc1","acc2","acc3"]},
        "CLEANMAX": {"mentions": 3, "sentiment_label": "Bullish", "avg_conviction": 3.2, "traders": ["trader_x"]},
    }
    mock_history = {
        "runs": ["2026-06-16","2026-06-17","2026-06-18","2026-06-19","2026-06-20"],
        "tickers": {
            "WABAG": {"appearances": [{"date": d, "recommendation": "BUY", "score": 7, "price": 1800} for d in ["2026-06-18","2026-06-19","2026-06-20"]]},
            "CLEANMAX": {"appearances": [{"date": d, "recommendation": "WATCH", "score": 5, "price": 1300} for d in ["2026-06-19","2026-06-20"]]},
        }
    }
    mock_trader_db = {
        "accounts": {
            "VCPSwing": {"credibility_score": 82, "recommended_weight": 1.8},
            "HighCredAcc": {"credibility_score": 90, "recommended_weight": 2.0},
        }
    }

    anomalies = detect_anomalies(mock_signals, mock_history, mock_trader_db)
    print("=== ANOMALIES ===")
    print(json.dumps(anomalies, indent=2))
