"""
premarket_brief.py
8:30 AM IST daily pre-market brief for Indian swing traders.

Delivered 1 hour before market open via email.
Covers:
  - Global overnight moves (S&P, Nasdaq, Asia)
  - SGX Nifty / Gift Nifty indication
  - Dollar index and crude oil
  - FII/DII provisional data from previous day
  - Key events today
  - Carry-forward watchlist from yesterday's scan
  - Claude's specific action items for today's open
"""

import logging
import os
from datetime import date, datetime
from typing import Dict, List, Optional

from scanner.claude_client import call_json
from scanner.market_data import fetch_global_macro, fetch_fii_dii, get_upcoming_events
from scanner.persistence import load_history
from scanner.mailer import send_report

logger = logging.getLogger(__name__)

RECIPIENT = os.getenv("REPORT_RECIPIENT", "")

BG = "#0d1117"; SURFACE = "#161b22"; BORDER = "#21262d"
TEXT = "#c9d1d9"; MUTED = "#8b949e"; ACCENT = "#58a6ff"


def _pct_color(v: float) -> str:
    if v > 1:  return "#00c853"
    if v > 0:  return "#69f0ae"
    if v < -1: return "#ff1744"
    if v < 0:  return "#ff6d00"
    return "#8b949e"


# ═══════════════════════════════════════════════════════════════════════════════
# CLAUDE BRIEF GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

BRIEF_SYSTEM = """You are writing a concise pre-market brief for an Indian equity swing trader.
Be direct, specific, and actionable. No fluff. Focus on what matters for today's trading session.
Respond only with JSON."""

BRIEF_PROMPT = """Write a pre-market brief for Indian markets opening in 1 hour.

Global overnight data:
{macro_summary}

Key macro signals:
{macro_signals}

FII/DII previous day:
{fii_summary}

Yesterday's watchlist:
{watchlist}

Upcoming events this week:
{events}

Return JSON:
{{
  "market_mood": "Bullish" | "Cautious" | "Bearish" | "Neutral",
  "mood_color":  "#hex",
  "gap_up_down": "Gap up / Gap down / Flat open expected",
  "key_points": [
    "Most important thing for today — specific and actionable",
    "Second point",
    "Third point",
    "Fourth point",
    "Fifth point"
  ],
  "stocks_to_watch": [
    {{
      "ticker":    "NSE_TICKER",
      "action":    "Buy on dip" | "Sell on rally" | "Hold" | "Watch breakout" | "Exit if...",
      "key_level": "₹XXXX — what happens here today",
      "reason":    "specific reason for today"
    }}
  ],
  "stocks_to_avoid_today": [
    {{
      "ticker": "NSE_TICKER",
      "reason": "why to avoid specifically today"
    }}
  ],
  "sector_focus": "which sector to focus on today and why",
  "risk_warning": "biggest risk for Indian markets today — be specific",
  "one_liner":    "The single most important sentence for today's trading"
}}"""


def generate_claude_brief(
    macro: Dict,
    fii_dii: Dict,
    watchlist: List[str],
    events: List[Dict],
) -> Dict:
    """Generate AI pre-market brief using Claude."""
    signals_text = "\n".join([f"- {s}" for s in macro.get("signals", [])])
    watchlist_text = ", ".join(watchlist[:10]) if watchlist else "No prior watchlist"
    events_text = "\n".join([f"- {e['event']} in {e['days_away']} days" for e in events[:5]]) or "No major events"

    result = call_json(
        prompt=BRIEF_PROMPT.format(
            macro_summary = macro.get("summary", "Data unavailable"),
            macro_signals = signals_text or "No significant signals",
            fii_summary   = fii_dii.get("summary", "Data unavailable"),
            watchlist     = watchlist_text,
            events        = events_text,
        ),
        system=BRIEF_SYSTEM,
        max_tokens=800,
        fallback={
            "market_mood":  "Neutral",
            "mood_color":   "#ffd600",
            "gap_up_down":  "Flat open expected",
            "key_points":   ["Check SGX Nifty for gap indication", "Monitor FII flows at open"],
            "stocks_to_watch": [],
            "stocks_to_avoid_today": [],
            "sector_focus": "Monitor sector rotation",
            "risk_warning": "Check global cues before entering",
            "one_liner":    "Be cautious and wait for market direction to establish",
        }
    )
    return result or {}


# ═══════════════════════════════════════════════════════════════════════════════
# HTML EMAIL BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_premarket_html(
    macro: Dict,
    fii_dii: Dict,
    brief: Dict,
    events: List[Dict],
    watchlist_details: List[Dict],
    run_date: str,
) -> str:
    """Build pre-market brief HTML email."""

    mood       = brief.get("market_mood", "Neutral")
    mood_color = brief.get("mood_color", "#ffd600")
    gap_str    = brief.get("gap_up_down", "")
    one_liner  = brief.get("one_liner", "")
    risk_warn  = brief.get("risk_warning", "")

    # Global macro table
    raw = macro.get("raw", {})
    sp500   = raw.get("sp500",  {})
    nasdaq  = raw.get("nasdaq", {})
    dxy     = raw.get("dxy",    {})
    crude   = raw.get("crude_wti", {})
    us10y   = raw.get("us10y", {})
    vix_us  = raw.get("vix_us", {})
    sgx     = raw.get("sgx_nifty", {})

    def macro_row(label, val, chg, unit=""):
        chg_c = _pct_color(chg)
        return f"""
<tr style="border-bottom:1px solid {BORDER}">
  <td style="padding:6px 10px;font-size:11px;color:{MUTED}">{label}</td>
  <td style="padding:6px 10px;font-family:monospace;font-size:11px;color:#e6edf3;text-align:right">{unit}{val:,.2f}</td>
  <td style="padding:6px 10px;font-family:monospace;font-size:11px;color:{chg_c};text-align:right">{chg:+.2f}%</td>
</tr>"""

    macro_rows = ""
    if sp500:   macro_rows += macro_row("S&P 500",   sp500.get("value",0),   sp500.get("chg_pct",0))
    if nasdaq:  macro_rows += macro_row("Nasdaq",    nasdaq.get("value",0),  nasdaq.get("chg_pct",0))
    if sgx:     macro_rows += macro_row("Nifty",     sgx.get("value",0),     sgx.get("chg_pct",0))
    if dxy:     macro_rows += macro_row("DXY",       dxy.get("value",0),     dxy.get("chg_pct",0))
    if crude:   macro_rows += macro_row("Crude WTI", crude.get("value",0),   crude.get("chg_pct",0), "$")
    if us10y:   macro_rows += macro_row("US 10Y",    us10y.get("value",0),   us10y.get("chg_pct",0), "")
    if vix_us:  macro_rows += macro_row("US VIX",    vix_us.get("value",0),  vix_us.get("chg_pct",0))

    # FII/DII
    fii_net  = fii_dii.get("fii_net_cr", 0)
    dii_net  = fii_dii.get("dii_net_cr", 0)
    fii_c    = "#00c853" if fii_net > 0 else "#ff1744"
    dii_c    = "#00c853" if dii_net > 0 else "#ff1744"
    fii_sig  = fii_dii.get("fii_signal", "?")

    # Key points
    key_points_html = "".join([
        f'<div style="border-left:3px solid {ACCENT};padding:6px 12px;margin-bottom:8px;font-size:12px;color:{TEXT}">{p}</div>'
        for p in brief.get("key_points", [])
    ])

    # Stocks to watch
    watch_html = ""
    for s in brief.get("stocks_to_watch", [])[:5]:
        action_c = {"Buy on dip": "#00c853", "Exit if...": "#ff1744",
                    "Sell on rally": "#ff6d00", "Watch breakout": "#ffd600"}.get(s.get("action", ""), MUTED)
        watch_html += f"""
<div style="background:{SURFACE};border:1px solid {BORDER};padding:10px 12px;margin-bottom:8px">
  <div style="display:flex;justify-content:space-between;margin-bottom:4px">
    <span style="font-family:monospace;font-size:13px;font-weight:600;color:#e6edf3">{s.get('ticker','')}</span>
    <span style="font-size:11px;color:{action_c};font-weight:600">{s.get('action','')}</span>
  </div>
  <div style="font-family:monospace;font-size:11px;color:{ACCENT};margin-bottom:3px">{s.get('key_level','')}</div>
  <div style="font-size:11px;color:{MUTED}">{s.get('reason','')}</div>
</div>"""

    # Avoid today
    avoid_html = ""
    for s in brief.get("stocks_to_avoid_today", [])[:3]:
        avoid_html += f'<div style="font-size:11px;color:#ff6d00;margin-bottom:4px">⚠ <strong>{s.get("ticker","")}</strong> — {s.get("reason","")}</div>'

    # Events
    events_html = ""
    for e in events[:5]:
        urgent_c = "#ff1744" if e.get("urgent") else MUTED
        events_html += f'<div style="font-size:11px;color:{urgent_c};margin-bottom:3px">{"⚠ " if e.get("urgent") else "📅 "}{e["event"]} — {e["days_away"]} days away ({e["date"]})</div>'

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pre-Market Brief — {run_date}</title></head>
<body style="margin:0;padding:0;background:{BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:{TEXT}">
<div style="max-width:700px;margin:0 auto;padding:20px 16px">

<!-- Header -->
<div style="border-bottom:1px solid {BORDER};padding-bottom:14px;margin-bottom:16px">
  <p style="font-family:monospace;font-size:10px;color:{MUTED};letter-spacing:0.15em;text-transform:uppercase;margin:0 0 4px">Pre-Market Brief / Indian Markets</p>
  <h1 style="font-size:20px;font-weight:600;color:#e6edf3;margin:0 0 4px">{run_date} — Market Opens in 1 Hour</h1>
  <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
    <span style="font-size:13px;font-weight:600;color:{mood_color}">{mood}</span>
    <span style="font-size:12px;color:{MUTED}">{gap_str}</span>
  </div>
</div>

<!-- One-liner -->
{"<div style='background:" + mood_color + "11;border-left:3px solid " + mood_color + ";padding:10px 14px;margin-bottom:16px;font-size:13px;font-style:italic;color:#e6edf3'>" + one_liner + "</div>" if one_liner else ""}

<!-- Global Markets -->
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">
  <div>
    <div style="font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px">Global Markets (Overnight)</div>
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:{SURFACE};border:1px solid {BORDER}">
      {macro_rows}
    </table>
  </div>
  <div>
    <div style="font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px">FII/DII (Previous Day)</div>
    <div style="background:{SURFACE};border:1px solid {BORDER};padding:12px">
      <div style="font-size:12px;color:{fii_c};font-weight:600;margin-bottom:6px">{fii_sig}</div>
      <div style="font-family:monospace;font-size:11px;color:#e6edf3">FII: <span style="color:{fii_c}">₹{fii_net:+.0f} Cr</span></div>
      <div style="font-family:monospace;font-size:11px;color:#e6edf3">DII: <span style="color:{dii_c}">₹{dii_net:+.0f} Cr</span></div>
      <div style="font-size:10px;color:{MUTED};margin-top:6px">{fii_dii.get("summary","")[:80]}</div>
    </div>

    {"<div style='margin-top:12px'><div style='font-family:monospace;font-size:9px;color:" + MUTED + ";text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px'>Upcoming Events</div>" + events_html + "</div>" if events_html else ""}
  </div>
</div>

<!-- Key Points -->
<div style="margin-bottom:16px">
  <div style="font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px">🎯 Today's Action Points</div>
  {key_points_html}
</div>

<!-- Stocks to watch -->
{"<div style='margin-bottom:16px'><div style='font-family:monospace;font-size:9px;color:" + MUTED + ";text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px'>Stocks to Watch Today</div>" + watch_html + "</div>" if watch_html else ""}

<!-- Avoid today -->
{"<div style='margin-bottom:16px'><div style='font-family:monospace;font-size:9px;color:" + MUTED + ";text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px'>⚠ Avoid Today</div>" + avoid_html + "</div>" if avoid_html else ""}

<!-- Sector focus -->
{"<div style='background:" + SURFACE + ";border:1px solid " + BORDER + ";padding:10px 12px;margin-bottom:16px'><div style='font-family:monospace;font-size:9px;color:" + MUTED + ";text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px'>Sector Focus</div><div style='font-size:12px;color:" + TEXT + "'>" + brief.get("sector_focus","") + "</div></div>" if brief.get("sector_focus") else ""}

<!-- Risk warning -->
{"<div style='background:#2d0a0a;border-left:3px solid #ff1744;padding:8px 12px;margin-bottom:16px;font-size:12px;color:#ff6d00'><strong>Risk:</strong> " + risk_warn + "</div>" if risk_warn else ""}

<div style="margin-top:20px;padding-top:12px;border-top:1px solid {BORDER};font-size:10px;color:{MUTED};font-family:monospace">
  Pre-market brief generated at {datetime.now().strftime('%H:%M IST')} | Data: Yahoo Finance, NSE India
</div>
</div></body></html>"""

    return html


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def run_premarket_brief():
    """
    Entry point for the 8:30am workflow.
    Fetches data, generates Claude brief, sends email.
    """
    logger.info("=" * 50)
    logger.info("Pre-Market Brief — Starting")
    logger.info("=" * 50)

    run_date  = datetime.now().strftime("%d %B %Y")

    # 1. Fetch macro
    logger.info("Fetching global macro...")
    macro = fetch_global_macro()
    logger.info(f"  → {macro['summary']}")

    # 2. FII/DII
    logger.info("Fetching FII/DII...")
    fii_dii = fetch_fii_dii()

    # 3. Events
    events = get_upcoming_events(days_ahead=7)

    # 4. Yesterday's watchlist from history
    history = load_history()
    runs    = history.get("runs", [])
    tickers_today = history.get("tickers", {})
    watchlist = []
    if runs:
        last_run = runs[-1]
        watchlist = [
            t for t, th in tickers_today.items()
            if th.get("last_seen") == last_run
        ]

    # 5. Generate Claude brief
    logger.info("Generating AI brief...")
    brief = generate_claude_brief(macro, fii_dii, watchlist, events)
    logger.info(f"  → {brief.get('market_mood','?')}: {brief.get('one_liner','')[:60]}")

    # 6. Build and send email
    html = build_premarket_html(macro, fii_dii, brief, events, [], run_date)

    mood = brief.get("market_mood", "Neutral")
    mood_icons = {"Bullish": "🟢", "Bearish": "🔴", "Cautious": "🟡", "Neutral": "⚪"}
    icon = mood_icons.get(mood, "⚪")
    gap  = brief.get("gap_up_down", "")

    subject = f"{icon} Pre-Market Brief {datetime.now().strftime('%d %b')} — {mood} | {gap}"

    from scanner.mailer import send_report
    ok = send_report(html, subject=subject)
    logger.info(f"{'✅' if ok else '❌'} Pre-market brief {'sent' if ok else 'failed'}")
    return ok


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_premarket_brief()
