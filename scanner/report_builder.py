"""
report_builder.py v4
Full HTML email report incorporating all Claude intelligence outputs.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

IST = timezone(timedelta(hours=5, minutes=30))


def _pct_color(v):
    if v > 3:  return "#00c853"
    if v > 0:  return "#69f0ae"
    if v < -3: return "#ff1744"
    if v < 0:  return "#ff6d00"
    return "#8b949e"

def _bool_icon(v): return "✅" if v else "❌"
def _tv_url(t): return f"https://www.tradingview.com/chart/?symbol=NSE%3A{t}"
def _sc_url(t): return f"https://www.screener.in/company/{t}/consolidated/"

def _sent_color(label):
    return {"Strong Bullish":"#00c853","Bullish":"#69f0ae","Mixed / Neutral":"#ffd600",
            "Bearish":"#ff6d00","Strong Bearish":"#ff1744"}.get(label,"#8b949e")

def _cat_color(cat):
    return {"Order Win":"#00c853","Earnings Beat":"#00c853","Analyst Upgrade":"#69f0ae",
            "Partnership":"#58a6ff","Fundraise":"#58a6ff","Analyst Downgrade":"#ff6d00",
            "Negative Event":"#ff1744","Regulatory Risk":"#f5a623",
            "Corporate Action":"#ffd600","Sector Tailwind":"#69f0ae"}.get(cat,"#8b949e")

BG="#0d1117"; SURFACE="#161b22"; BORDER="#21262d"
TEXT="#c9d1d9"; MUTED="#8b949e"; ACCENT="#58a6ff"

def _section(title, content):
    return f"""
<div style="margin-bottom:24px">
  <div style="font-family:monospace;font-size:10px;color:{MUTED};letter-spacing:0.12em;
       text-transform:uppercase;border-bottom:1px solid {BORDER};padding-bottom:6px;margin-bottom:14px">
    {title}
  </div>
  {content}
</div>"""

def _market_env_panel(market_env, macro):
    if not market_env or not market_env.get("data_available"):
        return ""
    nifty  = market_env.get("nifty",{})
    vix    = market_env.get("vix",{})
    breadth= market_env.get("breadth",{})
    sectors= market_env.get("sectors",{})
    env_color = market_env.get("color", MUTED)
    leaders  = sectors.get("leaders",[])
    laggards = sectors.get("laggards",[])
    leaders_html  = " ".join([f'<span style="background:#0d2818;color:#00c853;font-family:monospace;font-size:10px;padding:2px 7px;margin-right:4px">{s} +{d["vs_nifty"]:.1f}%</span>' for s,d in leaders[:3]])
    laggards_html = " ".join([f'<span style="background:#2d0a0a;color:#ff1744;font-family:monospace;font-size:10px;padding:2px 7px;margin-right:4px">{s} {d["vs_nifty"]:.1f}%</span>' for s,d in laggards[:3]])
    macro_headline  = macro.get("macro_headline","")
    sector_rotation = macro.get("sector_rotation","")
    watchlist_impact= macro.get("watchlist_impact","")
    return f"""
<div style="background:{SURFACE};border:1px solid {BORDER};border-left:3px solid {env_color};padding:16px 18px;margin-bottom:20px">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px">
    <div>
      <span style="font-family:monospace;font-size:13px;font-weight:600;color:{env_color}">{market_env.get("label","?")} Market</span>
      <span style="font-family:monospace;font-size:11px;color:{MUTED};margin-left:12px">Nifty: {nifty.get("trend","?")} &nbsp;|&nbsp; VIX: {vix.get("vix",0):.1f} ({vix.get("signal","?")}) &nbsp;|&nbsp; Breadth: {breadth.get("breadth","?")}</span>
    </div>
    <div style="font-family:monospace;font-size:11px;color:{MUTED}">1M: <span style="color:{_pct_color(nifty.get("ret_1m_pct",0))}">{nifty.get("ret_1m_pct",0):+.1f}%</span> &nbsp;|&nbsp; ATH: {nifty.get("pct_from_ath",0):.1f}%</div>
  </div>
  {"<p style='font-size:12px;color:#e6edf3;margin-bottom:10px;font-style:italic'>" + macro_headline + "</p>" if macro_headline else ""}
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px">
    <div><div style="font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">Leaders</div>{leaders_html or "—"}</div>
    <div><div style="font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">Laggards</div>{laggards_html or "—"}</div>
  </div>
  {"<p style='font-size:11px;color:" + MUTED + ";margin-bottom:4px'>" + sector_rotation + "</p>" if sector_rotation else ""}
  {"<p style='font-size:11px;color:" + MUTED + "'>" + watchlist_impact + "</p>" if watchlist_impact else ""}
</div>"""

def _anomaly_panel(anomalies):
    items = anomalies.get("anomalies",[])
    if not items: return ""
    rows = ""
    for a in items[:5]:
        sc = {"high":"#ff1744","medium":"#f5a623","low":"#ffd600"}.get(a.get("severity","low"),"#8b949e")
        rows += f'<div style="border-left:3px solid {sc};padding:8px 12px;margin-bottom:8px;background:#0d1117"><div style="font-family:monospace;font-size:11px;font-weight:600;color:{sc}">⚠ {a.get("type","").replace("_"," ").upper()}{" — " + a["ticker"] if a.get("ticker") else ""}</div><div style="font-size:11px;color:{MUTED};margin-top:3px">{a.get("description","")}</div></div>'
    return _section("⚠ Anomaly Alerts", rows)

def _correlation_panel(correlations):
    groups = correlations.get("correlated_groups",[])
    note   = correlations.get("portfolio_note","")
    if not groups and not note: return ""
    content = ""
    for g in groups[:3]:
        stocks = " + ".join(g.get("stocks",[]))
        risk_div = ("<div style='font-size:10px;color:#f5a623;margin-top:3px'>Risk: " + g.get("risk","") + "</div>") if g.get("risk") else ""
        content += f'<div style="background:#0d1117;border:1px solid {BORDER};padding:8px 12px;margin-bottom:6px"><span style="font-family:monospace;font-size:11px;color:#58a6ff">{stocks}</span><span style="font-size:11px;color:{MUTED};margin-left:8px">— {g.get("reason","")}</span>{risk_div}</div>'
    if note: content += f'<p style="font-size:11px;color:{MUTED};margin-top:8px">{note}</p>'
    return _section("🔗 Correlation & Concentration", content)

def _stock_card(r, news_map, history):
    ticker = r["ticker"]
    ts     = r.get("tweet_signal",{})
    narr   = r.get("narration",{})
    ns     = r.get("news_summary",{})
    adv    = r.get("advanced",{})
    chart  = ts.get("chart_analysis")
    dc     = _pct_color(r.get("day_change_pct",0))
    ema_count = sum([r.get("above_ema20",False),r.get("above_ema50",False),r.get("above_ema200",False)])
    ema_label = ("Price > all 3 EMAs" if ema_count == 3
                 else "Price < all 3 EMAs" if ema_count == 0
                 else f"Price > {ema_count}/3 EMAs")
    sent_label= ts.get("sentiment_label","")
    sent_c    = _sent_color(sent_label)
    consec    = r.get("consecutive_days",1)
    rec_chg   = r.get("rec_change")

    # Warnings
    warnings = ""
    if r.get("is_stale_data"): warnings += f'<div style="background:#2d1b00;border-left:3px solid #f5a623;padding:5px 10px;margin-bottom:8px;font-size:11px;color:#f5a623">⚠ {r.get("data_freshness","Stale")}</div>'
    if r.get("corp_action_warning"): warnings += f'<div style="background:#1a1a2e;border-left:3px solid #7c3aed;padding:5px 10px;margin-bottom:8px;font-size:11px;color:#a78bfa">{r["corp_action_warning"]}</div>'
    if consec >= 3: warnings += f'<div style="background:#0d2818;border-left:3px solid #00c853;padding:5px 10px;margin-bottom:8px;font-size:11px;color:#00c853">🔥 On watchlist {consec} consecutive days</div>'
    if rec_chg: warnings += f'<div style="background:#2d1a00;border-left:3px solid #f5a623;padding:5px 10px;margin-bottom:8px;font-size:11px;color:#f5a623">⚡ Recommendation changed: <strong>{rec_chg}</strong></div>'

    # Claude narration
    narr_html = ""
    if narr.get("setup_description"):
        narr_html = f"""
<div style="background:#0a0e14;border:1px solid {BORDER};padding:12px 14px;margin-bottom:12px">
  <div style="font-family:monospace;font-size:10px;color:{MUTED};text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">🤖 AI Setup Analysis</div>
  {"<div style='font-size:13px;font-weight:600;color:#e6edf3;margin-bottom:6px'>" + narr.get("setup_title","") + "</div>" if narr.get("setup_title") else ""}
  <p style="font-size:12px;color:{TEXT};margin-bottom:6px;line-height:1.6">{narr.get("setup_description","")}</p>
  {"<p style='font-size:12px;color:#69f0ae;margin-bottom:4px'>✅ " + narr.get("entry_rationale","") + "</p>" if narr.get("entry_rationale") else ""}
  {"<p style='font-size:12px;color:#ff6d00;margin-bottom:4px'>⚠ " + narr.get("risk_statement","") + "</p>" if narr.get("risk_statement") else ""}
  {"<p style='font-size:12px;font-style:italic;color:#58a6ff;border-top:1px solid " + BORDER + ";padding-top:8px;margin-top:6px'>" + narr.get("one_line_verdict","") + "</p>" if narr.get("one_line_verdict") else ""}
</div>"""

    # News summary
    news_sum_html = ""
    if ns.get("catalyst_summary") and ns.get("catalyst_type") != "No Catalyst":
        cat_c = _cat_color(ns.get("catalyst_type",""))
        risks_html = "".join([f'<span style="background:#2d0a0a;color:#ff4d6d;font-size:10px;padding:2px 7px;margin-right:4px">{risk}</span>' for risk in ns.get("key_risks",[])[:3]])
        news_sum_html = f"""
<div style="background:#0a0e14;border-left:3px solid {cat_c};padding:10px 12px;margin-bottom:10px">
  <div style="font-family:monospace;font-size:10px;color:{cat_c};text-transform:uppercase;margin-bottom:4px">[{ns.get("catalyst_type","")}] — {ns.get("catalyst_magnitude","").upper()}</div>
  <p style="font-size:12px;color:{TEXT};margin-bottom:6px">{ns.get("catalyst_summary","")}</p>
  {"<p style='font-size:11px;color:#69f0ae;margin-bottom:3px'>🐂 " + ns.get("bull_case","") + "</p>" if ns.get("bull_case") else ""}
  {"<p style='font-size:11px;color:#ff6d00;margin-bottom:3px'>🐻 " + ns.get("bear_case","") + "</p>" if ns.get("bear_case") else ""}
  {"<div style='margin-top:6px'>" + risks_html + "</div>" if risks_html else ""}
</div>"""

    # Trader sentiment
    bull = ts.get("bullish_count",0); bear = ts.get("bearish_count",0)
    trader_html = ""
    if ts:
        levels = f'<div style="font-family:monospace;font-size:11px;color:#58a6ff;margin-bottom:6px">Trader levels: Entry ₹{ts["trader_entry"]:,.0f} | SL ₹{ts["trader_sl"]:,.0f} | Target ₹{ts["trader_target"]:,.0f}</div>' if all([ts.get("trader_entry"),ts.get("trader_sl"),ts.get("trader_target")]) else ""
        reasons_html = "".join([f'<div style="font-size:11px;color:{MUTED};margin-bottom:2px">→ {r}</div>' for r in ts.get("key_reasons",[])[:3]])
        trader_html = f"""
<div style="background:#0a0e14;border:1px solid {BORDER};padding:10px 12px;margin-bottom:10px">
  <div style="font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">Trader Sentiment ({ts.get("mentions",0)} mentions)</div>
  <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:8px">
    <span style="font-size:13px;font-weight:600;color:{sent_c}">{sent_label}</span>
    <span style="font-family:monospace;font-size:11px;color:{MUTED}">🟢 {bull} bullish &nbsp; 🔴 {bear} bearish &nbsp; ⭐ {ts.get("avg_conviction",0):.1f}/5</span>
  </div>
  {levels}{reasons_html}
  {"<div style='font-size:11px;color:#a78bfa;margin-top:6px;font-style:italic'>📊 " + ts.get("trader_consensus","") + "</div>" if ts.get("trader_consensus") else ""}
</div>"""

    # Chart analysis
    chart_html = ""
    if chart:
        flags = " | ".join(chart.get("caution_flags",[]))
        flags_html = ("<br><span style='color:#f5a623'>⚠ " + flags + "</span>") if flags else ""
        chart_html = f'<div style="background:#0a0e14;border-left:3px solid #7c3aed;padding:8px 12px;margin-bottom:10px;font-size:11px"><span style="color:#a78bfa;font-family:monospace;font-size:9px;text-transform:uppercase">📈 Chart Analysis</span><br><span style="color:{TEXT}">Pattern: {chart.get("pattern","?")} | Trend: {chart.get("trend","?")} | Quality: {chart.get("setup_quality","?")}</span><br><span style="color:{MUTED}">{chart.get("trader_likely_pointing_to","")}</span>{flags_html}</div>'

    # Raw news
    news = news_map.get(ticker,{})
    news_items = news.get("news",[]) + news.get("filings",[])
    news_html = "".join([f'<div style="margin-bottom:5px;font-size:11px"><a href="{n.get("url","#")}" style="color:{ACCENT};text-decoration:none">{"📋" if "NSE" in n.get("source","") else "📰"} {n.get("title","")[:90]}</a><span style="color:{MUTED};margin-left:6px">{n.get("source","")[:25]} · {n.get("date","")}</span></div>' for n in news_items[:3]])
    if not news_html: news_html = f'<a href="{_sc_url(ticker)}" style="color:{ACCENT};font-size:11px">Check Screener →</a>'

    # Tweets
    tweets_html = "".join([f'<div style="border-left:2px solid {BORDER};padding:5px 10px;margin-bottom:5px;font-size:11px;color:{MUTED}"><a href="{tw.get("url","#")}" style="color:{ACCENT};text-decoration:none;font-family:monospace">@{tw.get("username","")}</a>&nbsp;{tw.get("text","")[:150]}</div>' for tw in r.get("tweets",[])[:2]])

    # Technical reasons
    reasons_html = "".join([f'<span style="background:#161b22;border:1px solid {BORDER};font-size:10px;padding:2px 7px;margin:2px 3px 2px 0;display:inline-block;color:{MUTED}">{rr}</span>' for rr in r.get("reasons",[])])

    # History sparkline
    try:
        from scanner.persistence import get_ticker_history_summary
        hist_sum = get_ticker_history_summary(ticker, history, days=7)
        sparkline = " → ".join([str(h["score"]) for h in hist_sum]) if hist_sum else ""
    except:
        sparkline = ""

    ema_c = "#00c853" if ema_count == 3 else "#ffd600" if ema_count >= 2 else "#ff1744"

    return f"""
<div style="background:{SURFACE};border:1px solid {BORDER};margin-bottom:14px;padding:16px 18px">
  {warnings}
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;flex-wrap:wrap;gap:8px">
    <div>
      <a href="{_tv_url(ticker)}" style="font-family:monospace;font-size:16px;font-weight:600;color:#e6edf3;text-decoration:none">{ticker}</a>
      <a href="{_tv_url(ticker)}" style="font-size:10px;color:{ACCENT};text-decoration:none;margin-left:8px;border:1px solid {BORDER};padding:1px 6px">📊 Chart</a>
      <a href="{_sc_url(ticker)}" style="font-size:10px;color:{ACCENT};text-decoration:none;margin-left:4px;border:1px solid {BORDER};padding:1px 6px">🔍 Screener</a>
      <span style="font-family:monospace;font-size:11px;color:{MUTED};margin-left:10px">₹{r.get("current_price",0):,.2f} <span style="color:{dc}">{r.get("day_change_pct",0):+.1f}%</span> &nbsp;|&nbsp; RSI {r.get("rsi",0):.0f} &nbsp;|&nbsp; ADX {r.get("adx",0):.0f} &nbsp;|&nbsp; Vol {r.get("volume_ratio",1):.1f}x{" &nbsp;|&nbsp; 🏭 " + r.get("sector","") if r.get("sector") else ""}</span>
    </div>
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      {"<span style='font-size:11px;color:" + sent_c + "'>" + sent_label + "</span>" if sent_label else ""}
      <span style="background:{r.get("rec_color","#8b949e")}22;color:{r.get("rec_color","#8b949e")};border:1px solid {r.get("rec_color","#8b949e")}55;font-family:monospace;font-size:10px;font-weight:700;padding:4px 10px">{r.get("recommendation","?")} &nbsp; {r.get("score",0)}/{r.get("max_score",28)}</span>
    </div>
  </div>
  {narr_html}
  <table width="100%" cellpadding="0" cellspacing="8" style="margin-bottom:12px">
    <tr>
      <td style="width:33%;vertical-align:top">
        <div style="font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">Stop / Target</div>
        <div style="font-family:monospace;font-size:12px;color:#e6edf3"><span style="color:#ff1744">SL ₹{r.get("suggested_stop",0):,.0f}</span> &nbsp; <span style="color:#00c853">T ₹{r.get("suggested_target",0):,.0f}</span></div>
        <div style="font-size:10px;color:{MUTED};margin-top:2px">R:R {r.get("risk_reward",0):.1f} | {r.get("entry_context","").title()}</div>
        <div style="font-size:9px;color:#5a6472;margin-top:2px">{r.get("position_guide","")[:70]}</div>
      </td>
      <td style="width:33%;vertical-align:top">
        <div style="font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">52W Range</div>
        <div style="font-family:monospace;font-size:12px;color:#e6edf3">₹{r.get("low_52w",0):,.0f} — ₹{r.get("high_52w",0):,.0f}</div>
        <div style="font-size:10px;color:{MUTED};margin-top:2px">{r.get("pct_from_52h",0):.1f}% from high</div>
      </td>
      <td style="width:33%;vertical-align:top">
        <div style="font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">EMA Stack</div>
        <div style="font-family:monospace;font-size:11px;color:{ema_c}">{ema_label}</div>
        <div style="font-size:10px;color:{MUTED};margin-top:2px">{"🌟 Golden Cross" if r.get("golden_cross") else "💀 Death Cross" if r.get("death_cross") else "—"}{"&nbsp;|&nbsp; 🎯 BB Squeeze" if r.get("bb_squeeze_alert") else ""}</div>
        {"<div style='font-family:monospace;font-size:10px;color:#58a6ff;margin-top:3px'>Score: " + sparkline + "</div>" if sparkline else ""}
      </td>
    </tr>
  </table>
  {news_sum_html}
  {trader_html}
  {chart_html}
  {"<div style='margin-bottom:10px'>" + tweets_html + "</div>" if tweets_html else ""}
  <div style="margin-bottom:10px"><div style="font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">Technical Signals</div>{reasons_html}</div>
  <div><div style="font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">Sources</div>{news_html}</div>
</div>"""


def build_report(
    analysis_results, news_map, history=None, market_env=None,
    macro_context=None, correlations=None, anomalies=None,
    weekly_debrief=None, ai_cost=None, run_date=None,
    list_id="1506463545642217474", member_count=0,
    tweet_count=0, rt_dedupe_count=0, market_status=None,
    market_data=None, gate_status=None, short_candidates=None,
    removed_illiquid=None, fii_dii=None, global_macro=None,
    upcoming_events=None,
):
    run_date = run_date or datetime.now(IST).strftime("%d %B %Y")
    market_data      = market_data or {}
    gate_status      = gate_status or {}
    short_candidates = short_candidates or []
    removed_illiquid = removed_illiquid or []
    fii_dii          = fii_dii or {}
    global_macro     = global_macro or {}
    upcoming_events  = upcoming_events or []
    run_time = datetime.now(IST).strftime("%H:%M IST")
    history  = history or {}; market_env = market_env or {}
    macro_context = macro_context or {}; correlations = correlations or {}
    anomalies = anomalies or {}

    total=len(analysis_results)
    s_buys=sum(1 for r in analysis_results if r.get("recommendation")=="STRONG BUY")
    buys  =sum(1 for r in analysis_results if r.get("recommendation")=="BUY")
    watches=sum(1 for r in analysis_results if r.get("recommendation")=="WATCH")
    cauts =sum(1 for r in analysis_results if r.get("recommendation")=="CAUTION")
    avoids=sum(1 for r in analysis_results if r.get("recommendation")=="AVOID")
    env_color=market_env.get("color",MUTED); env_label=market_env.get("label","Unknown")

    market_banner=""
    if market_status and not market_status.get("is_trading_day"):
        market_banner=f'<div style="background:#2d1b00;border:1px solid #f5a623;padding:10px 16px;margin-bottom:16px;font-size:12px;color:#f5a623">⚠ NSE closed — {market_status.get("status_message","")}</div>'

    ai_line = f"AI cost: ${ai_cost['total_cost_usd']:.3f} (₹{ai_cost['total_cost_inr']:.2f}) | {ai_cost['calls']} calls | {ai_cost.get('input_tokens',0)+ai_cost.get('output_tokens',0):,} tokens" if ai_cost else ""

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FinTwit — {run_date}</title></head>
<body style="margin:0;padding:0;background:{BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:{TEXT}">
<div style="max-width:980px;margin:0 auto;padding:24px 16px">
<div style="border-bottom:1px solid {BORDER};padding-bottom:18px;margin-bottom:18px">
  <p style="font-family:monospace;font-size:10px;color:{MUTED};letter-spacing:0.15em;text-transform:uppercase;margin:0 0 6px">FinTwit Intelligence / Indian Markets / AI-Powered</p>
  <h1 style="font-size:22px;font-weight:600;color:#e6edf3;margin:0 0 6px">Daily Stock Scan — {run_date}</h1>
  <p style="font-size:11px;color:{MUTED};margin:0;font-family:monospace;line-height:1.8">{run_time} &nbsp;|&nbsp; {member_count} accounts &nbsp;|&nbsp; {tweet_count} tweets &nbsp;|&nbsp; {rt_dedupe_count} RTs deduped &nbsp;|&nbsp; Market: <span style="color:{env_color}">{env_label}</span>{" &nbsp;|&nbsp; " + ai_line if ai_line else ""}</p>
</div>
{market_banner}
<table width="100%" cellpadding="0" cellspacing="1" style="background:{BORDER};margin-bottom:20px">
  <tr>
    <td style="background:{SURFACE};padding:12px 14px;text-align:center"><div style="font-family:monospace;font-size:20px;font-weight:600;color:#e6edf3">{total}</div><div style="font-size:10px;color:{MUTED}">Analysed</div></td>
    <td style="background:{SURFACE};padding:12px 14px;text-align:center"><div style="font-family:monospace;font-size:20px;font-weight:600;color:#00c853">{s_buys+buys}</div><div style="font-size:10px;color:{MUTED}">Buy ({s_buys} Strong)</div></td>
    <td style="background:{SURFACE};padding:12px 14px;text-align:center"><div style="font-family:monospace;font-size:20px;font-weight:600;color:#ffd600">{watches}</div><div style="font-size:10px;color:{MUTED}">Watch</div></td>
    <td style="background:{SURFACE};padding:12px 14px;text-align:center"><div style="font-family:monospace;font-size:20px;font-weight:600;color:#ff6d00">{cauts}</div><div style="font-size:10px;color:{MUTED}">Caution</div></td>
    <td style="background:{SURFACE};padding:12px 14px;text-align:center"><div style="font-family:monospace;font-size:20px;font-weight:600;color:#ff1744">{avoids}</div><div style="font-size:10px;color:{MUTED}">Avoid</div></td>
  </tr>
</table>"""

    html += _market_env_panel(market_env, macro_context)

    if weekly_debrief:
        gc = {"A":"#00c853","B":"#69f0ae","C":"#ffd600","D":"#ff1744"}.get(weekly_debrief.get("overall_grade","C"),"#8b949e")
        obs_html = "".join([f'<div style="font-size:11px;color:{MUTED};margin-bottom:3px">→ {o}</div>' for o in weekly_debrief.get("pattern_observations",[])])
        adj_div = ("<div style='font-size:11px;color:#f5a623;margin-top:8px'>💡 " + weekly_debrief.get("model_adjustment_suggestion","") + "</div>") if weekly_debrief.get("model_adjustment_suggestion") else ""
        debrief_content = f'<div style="background:{SURFACE};border:1px solid {BORDER};padding:16px 18px"><div style="display:flex;justify-content:space-between;margin-bottom:10px"><span style="font-size:13px;color:#e6edf3">{weekly_debrief.get("week_summary","")}</span><span style="font-family:monospace;font-size:18px;font-weight:600;color:{gc}">Grade: {weekly_debrief.get("overall_grade","?")}</span></div>{obs_html}{adj_div}</div>'
        html += _section("📊 Weekly Performance Debrief", debrief_content)

    html += _anomaly_panel(anomalies)
    html += _correlation_panel(correlations)

    # Summary table
    html += f'<div style="font-family:monospace;font-size:10px;color:{MUTED};letter-spacing:0.12em;text-transform:uppercase;border-bottom:1px solid {BORDER};padding-bottom:6px;margin-bottom:14px">Stock Analysis ({total} stocks)</div>'
    html += f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-bottom:28px"><thead><tr style="background:{SURFACE};border-bottom:2px solid {BORDER}"><th style="padding:8px 10px;text-align:left;font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase;letter-spacing:0.08em">Ticker</th><th style="padding:8px 10px;text-align:right;font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase">Price</th><th style="padding:8px 10px;text-align:right;font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase">1D%</th><th style="padding:8px 10px;text-align:center;font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase">RSI</th><th style="padding:8px 10px;text-align:center;font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase">Sentiment</th><th style="padding:8px 10px;text-align:center;font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase">Score</th><th style="padding:8px 10px;text-align:center;font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase">History</th><th style="padding:8px 10px;text-align:center;font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase">Signal</th><th style="padding:8px 10px;text-align:left;font-family:monospace;font-size:9px;color:{MUTED};text-transform:uppercase">AI Verdict</th></tr></thead><tbody>'

    for i, r in enumerate(analysis_results):
        row_bg = SURFACE if i%2==0 else BG
        ticker = r["ticker"]; ts=r.get("tweet_signal",{}); narr=r.get("narration",{})
        dc=_pct_color(r.get("day_change_pct",0)); rsi_c="#ff1744" if r.get("rsi",50)>70 else "#00c853" if r.get("rsi",50)<35 else TEXT
        sent_c=_sent_color(ts.get("sentiment_label",""))
        html += f'<tr style="background:{row_bg};border-bottom:1px solid {BORDER}"><td style="padding:8px 10px"><a href="{_tv_url(ticker)}" style="font-family:monospace;font-size:12px;font-weight:600;color:#58a6ff;text-decoration:none">{ticker}</a><div style="font-size:9px;color:{MUTED}">{r.get("sector","")}</div></td><td style="padding:8px 10px;text-align:right;font-family:monospace;font-size:11px">₹{r.get("current_price",0):,.0f}</td><td style="padding:8px 10px;text-align:right;font-family:monospace;font-size:11px;color:{dc}">{r.get("day_change_pct",0):+.1f}%</td><td style="padding:8px 10px;text-align:center;font-family:monospace;font-size:11px;color:{rsi_c}">{r.get("rsi",0):.0f}</td><td style="padding:8px 10px;text-align:center;font-size:10px;color:{sent_c}">{ts.get("sentiment_label","—")}</td><td style="padding:8px 10px;text-align:center;font-family:monospace;font-size:11px;color:{MUTED}">{r.get("score",0)}/{r.get("max_score",28)}</td><td style="padding:8px 10px;font-size:10px;color:{MUTED}">{r.get("history_badge","")}</td><td style="padding:8px 10px;text-align:center"><span style="background:{r.get("rec_color","#8b949e")}22;color:{r.get("rec_color","#8b949e")};border:1px solid {r.get("rec_color","#8b949e")}55;font-family:monospace;font-size:9px;font-weight:700;padding:2px 6px">{r.get("recommendation","?")}</span></td><td style="padding:8px 10px;font-size:10px;color:{MUTED};max-width:180px">{narr.get("one_line_verdict","")[:80]}</td></tr>'

    html += "</tbody></table>\n"

    for r in analysis_results:
        html += _stock_card(r, news_map, history)

    html += f'<div style="margin-top:32px;padding-top:16px;border-top:1px solid {BORDER};font-size:10px;color:{MUTED};font-family:monospace;line-height:1.8"><p>Source: Twitter list {list_id} · {member_count} accounts · OHLCV: Yahoo Finance (NSE) · News: Google News RSS</p>{"<p>" + ai_line + "</p>" if ai_line else ""}<p>⚠ Not investment advice. Verify independently before trading.</p></div></div></body></html>'

    return html
