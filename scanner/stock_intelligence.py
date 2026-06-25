"""
stock_intelligence.py — Tier 1 + Tier 2
Claude-powered stock analysis modules.

Covers:
  1. News deep summarisation + risk extraction
  2. Earnings transcript summarisation
  3. Setup narration per stock (plain English)
  4. Inter-stock correlation detection
  5. Macro context injection
  6. Pre-market brief generation
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from scanner.claude_client import call, call_json

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. NEWS DEEP SUMMARISATION + RISK EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

NEWS_SUMMARY_SYSTEM = """You are an expert Indian equity analyst summarising market news.
Be precise, specific, and focused on what matters for a trader's decision.
Respond only with JSON."""

NEWS_SUMMARY_PROMPT = """Analyse these news items and exchange filings for {ticker} and return a concise analysis.

News:
{news_items}

Exchange Filings:
{filings}

Return JSON:
{{
  "catalyst_type": "Order Win" | "Earnings Beat" | "Earnings Miss" | "Analyst Upgrade" | "Analyst Downgrade" | "Partnership" | "Fundraise" | "Capacity Expansion" | "Corporate Action" | "Regulatory Risk" | "Negative Event" | "Sector Tailwind" | "No Catalyst",
  "catalyst_summary": "one crisp sentence describing the key catalyst",
  "catalyst_magnitude": "transformational" | "significant" | "moderate" | "minor",
  "bull_case": "2 sentences — why this stock could move up",
  "bear_case": "2 sentences — key risks and what could go wrong",
  "key_risks": ["specific risk 1", "specific risk 2", "specific risk 3"],
  "price_sensitive": true | false,
  "time_horizon_relevance": "near_term" | "medium_term" | "long_term",
  "news_freshness": "today" | "this_week" | "older"
}}"""


def summarise_news(ticker: str, news_items: List[Dict], filings: List[Dict]) -> Dict:
    """Deep summarise news and extract risks for a stock."""
    if not news_items and not filings:
        return {
            "catalyst_type":    "No Catalyst",
            "catalyst_summary": "No recent news found",
            "bull_case":        "",
            "bear_case":        "",
            "key_risks":        [],
            "catalyst_magnitude": "minor",
        }

    news_text = "\n".join([
        f"[{n.get('date','')}] {n.get('source','')}: {n.get('title','')}. {n.get('snippet','')[:200]}"
        for n in news_items[:5]
    ])
    filings_text = "\n".join([
        f"[{f.get('date','')}] NSE Filing: {f.get('title','')}"
        for f in filings[:4]
    ])

    result = call_json(
        prompt=NEWS_SUMMARY_PROMPT.format(
            ticker=ticker,
            news_items=news_text or "None",
            filings=filings_text or "None",
        ),
        system=NEWS_SUMMARY_SYSTEM,
        max_tokens=600,
        fallback={
            "catalyst_type":    "No Catalyst",
            "catalyst_summary": "News analysis unavailable",
            "bull_case":        "",
            "bear_case":        "",
            "key_risks":        [],
            "catalyst_magnitude": "minor",
        }
    )
    return result or {}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SETUP NARRATION
# ═══════════════════════════════════════════════════════════════════════════════

NARRATION_SYSTEM = """You are a senior Indian equity trader explaining a stock setup to a colleague.
Write in plain English — no jargon overload. Be specific about levels.
3 sentences maximum per section. Respond only with JSON."""

NARRATION_PROMPT = """Write a plain-English trade setup narration for {ticker}.

Technical data:
- Current price: ₹{price}
- Recommendation: {recommendation} (score {score}/{max_score})
- RSI: {rsi} ({rsi_signal})
- MACD: {macd_status}
- EMA alignment: {ema_status}
- ADX: {adx} ({trend_dir})
- Volume: {vol_ratio}x average
- Pattern: {patterns}
- Suggested stop: ₹{stop}
- Suggested target: ₹{target}
- Risk:Reward: {rr}
- 52W: Low ₹{low52} — High ₹{high52}
- From 52W high: {pct_52h:.1f}%

Sentiment from traders:
- {sentiment_label} ({bullish} bullish, {bearish} bearish)
- Avg conviction: {conviction}/5
- Dominant signal: {signal_type}
- Trader entry zone: ₹{trader_entry}
- Trader SL: ₹{trader_sl}

News catalyst: {catalyst}

Return JSON:
{{
  "setup_title": "5-word catchy title for this setup",
  "setup_description": "2-3 sentences: what is happening technically and why it matters",
  "entry_rationale": "2 sentences: why this is a good entry point specifically",
  "risk_statement": "1-2 sentences: what would invalidate this setup",
  "key_levels": {{
    "entry_zone": "₹X–₹Y",
    "stop_loss": "₹X",
    "target_1": "₹X",
    "target_2": "₹X or null"
  }},
  "trader_consensus": "1 sentence summarising what traders in the list are saying",
  "one_line_verdict": "single decisive sentence: buy/watch/avoid and why"
}}"""


def narrate_setup(
    ticker: str,
    ta: Dict,
    tweet_signal: Optional[Dict],
    news_summary: Optional[Dict],
) -> Dict:
    """Generate plain-English setup narration for a stock."""

    adv    = ta.get("advanced", {})
    patterns_text = adv.get("patterns", {}).get("description", "None detected")
    ema_status = f"{sum([ta.get('above_ema20',False), ta.get('above_ema50',False), ta.get('above_ema200',False)])}/3 EMAs above price"

    ts = tweet_signal or {}
    ns = news_summary or {}

    result = call_json(
        prompt=NARRATION_PROMPT.format(
            ticker         = ticker,
            price          = ta.get("current_price", 0),
            recommendation = ta.get("recommendation", "WATCH"),
            score          = ta.get("score", 0),
            max_score      = ta.get("max_score", 28),
            rsi            = ta.get("rsi", 0),
            rsi_signal     = ta.get("rsi_signal", "Neutral"),
            macd_status    = "Above zero" if ta.get("macd_above_zero") else "Below zero",
            ema_status     = ema_status,
            adx            = ta.get("adx", 0),
            trend_dir      = ta.get("trend_direction", "Unknown"),
            vol_ratio      = ta.get("volume_ratio", 1.0),
            patterns       = patterns_text,
            stop           = ta.get("suggested_stop", 0),
            target         = ta.get("suggested_target", 0),
            rr             = ta.get("risk_reward", 0),
            low52          = ta.get("low_52w", 0),
            high52         = ta.get("high_52w", 0),
            pct_52h        = ta.get("pct_from_52h", 0),
            sentiment_label= ts.get("sentiment_label", "Unknown"),
            bullish        = ts.get("bullish_count", 0),
            bearish        = ts.get("bearish_count", 0),
            conviction     = ts.get("avg_conviction", 0),
            signal_type    = ts.get("dominant_signal_type", "unknown"),
            trader_entry   = ts.get("trader_entry") or "Not specified",
            trader_sl      = ts.get("trader_sl") or "Not specified",
            catalyst       = ns.get("catalyst_summary", "No recent catalyst"),
        ),
        system=NARRATION_SYSTEM,
        max_tokens=700,
        fallback={
            "setup_title":       f"{ticker} — {ta.get('recommendation', 'WATCH')}",
            "setup_description": "Setup analysis unavailable",
            "entry_rationale":   "",
            "risk_statement":    "",
            "key_levels":        {},
            "trader_consensus":  "",
            "one_line_verdict":  "",
        }
    )
    return result or {}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. INTER-STOCK CORRELATION DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

CORRELATION_SYSTEM = """You are an Indian equity portfolio analyst identifying stock correlations and sector overlaps.
Be specific. Respond only with JSON."""

CORRELATION_PROMPT = """Analyse these stocks and identify correlations, sector overlaps, and concentration risks.

Stocks on today's watchlist:
{stocks}

Return JSON:
{{
  "correlated_groups": [
    {{
      "stocks": ["TICKER1", "TICKER2"],
      "reason": "why they are correlated",
      "risk": "what happens if the thesis is wrong for both"
    }}
  ],
  "sector_concentration": [
    {{
      "sector": "sector name",
      "stocks": ["TICKER1", "TICKER2"],
      "exposure_warning": "warning if too concentrated"
    }}
  ],
  "diversification_score": 1-10,
  "portfolio_note": "1-2 sentences on overall watchlist composition"
}}"""


def detect_correlations(analysis_results: List[Dict]) -> Dict:
    """Detect correlations and concentration risks across watchlist."""
    if len(analysis_results) < 2:
        return {}

    stocks_text = "\n".join([
        f"- {r['ticker']}: {r.get('recommendation','?')} | sector={r.get('sector','?')} | "
        f"catalyst={r.get('advanced',{}).get('rs',{}).get('signal','?')}"
        for r in analysis_results[:20]
    ])

    result = call_json(
        prompt=CORRELATION_PROMPT.format(stocks=stocks_text),
        system=CORRELATION_SYSTEM,
        max_tokens=2500,   # 1500 still truncated on 3-stock OMC group (char 5635)
        fallback={"portfolio_note": "Correlation analysis unavailable"},
    )
    return result or {}


# ═══════════════════════════════════════════════════════════════════════════════
# 4. MACRO CONTEXT
# ═══════════════════════════════════════════════════════════════════════════════

MACRO_SYSTEM = """You are an Indian equity macro analyst. Write concisely about macro factors.
Focus on what matters TODAY for Indian stock traders. Respond only with JSON."""

MACRO_PROMPT = """Write a macro context brief for Indian stock traders today.

Market environment data:
- Nifty 50: {nifty_trend} | 1M return: {nifty_1m}% | From ATH: {nifty_ath}%
- India VIX: {vix} ({vix_signal})
- Market breadth: {breadth}
- Sector leaders: {leaders}
- Sector laggards: {laggards}

Stocks on watchlist: {tickers}

Return JSON:
{{
  "macro_headline": "one powerful sentence about today's market",
  "nifty_context": "2 sentences on Nifty structure and what it means",
  "vix_context": "1 sentence on volatility environment",
  "sector_rotation": "2 sentences on which sectors are in/out of favour",
  "watchlist_impact": "2 sentences on how today's macro specifically affects the watchlist stocks",
  "key_risk_today": "biggest macro risk for Indian markets today",
  "opportunity_note": "if any, what macro tailwind exists"
}}"""


def generate_macro_context(market_env: Dict, analysis_results: List[Dict]) -> Dict:
    """Generate macro context commentary."""
    if not market_env or not market_env.get("data_available"):
        return {"macro_headline": "Market data unavailable"}

    nifty   = market_env.get("nifty", {})
    vix     = market_env.get("vix", {})
    breadth = market_env.get("breadth", {})
    sectors = market_env.get("sectors", {})

    leaders  = ", ".join([f"{s}(+{d['vs_nifty']:.1f}%)" for s, d in (sectors.get("leaders") or [])[:3]])
    laggards = ", ".join([f"{s}({d['vs_nifty']:.1f}%)"  for s, d in (sectors.get("laggards") or [])[:3]])
    tickers  = ", ".join([r["ticker"] for r in analysis_results[:15]])

    result = call_json(
        prompt=MACRO_PROMPT.format(
            nifty_trend = nifty.get("trend", "Unknown"),
            nifty_1m    = nifty.get("ret_1m_pct", 0),
            nifty_ath   = nifty.get("pct_from_ath", 0),
            vix         = vix.get("vix", 0),
            vix_signal  = vix.get("signal", "Unknown"),
            breadth     = breadth.get("breadth", "Unknown"),
            leaders     = leaders or "Data unavailable",
            laggards    = laggards or "Data unavailable",
            tickers     = tickers,
        ),
        system=MACRO_SYSTEM,
        max_tokens=600,
        fallback={"macro_headline": "Macro analysis unavailable"},
    )
    return result or {}


# ═══════════════════════════════════════════════════════════════════════════════
# 5. PRE-MARKET BRIEF (separate 8:30am workflow)
# ═══════════════════════════════════════════════════════════════════════════════

PREMARKET_SYSTEM = """You are writing a pre-market brief for an Indian equity trader.
Be crisp. 5 bullets maximum. Respond only with JSON."""

PREMARKET_PROMPT = """Write a pre-market brief for Indian markets today.

Previous day's watchlist:
{watchlist}

Market environment:
- Nifty: {nifty_trend} ({nifty_1m}% 1M)
- VIX: {vix} ({vix_signal})
- Breadth: {breadth}

Return JSON:
{{
  "brief_date": "{date}",
  "market_mood": "Bullish" | "Cautious" | "Bearish" | "Neutral",
  "key_points": [
    "bullet 1 — most important thing for today",
    "bullet 2",
    "bullet 3",
    "bullet 4",
    "bullet 5"
  ],
  "stocks_to_watch": [
    {{
      "ticker": "TICKER",
      "reason": "why to watch today specifically",
      "key_level": "₹X — what happens here"
    }}
  ],
  "avoid_today": ["TICKER — reason"],
  "one_line_brief": "The single most important thing to know before market open"
}}"""


def generate_premarket_brief(
    history: Dict,
    market_env: Dict,
    run_date: str,
) -> Dict:
    """Generate pre-market brief from yesterday's watchlist + current market env."""

    # Get yesterday's top picks from history
    runs = history.get("runs", [])
    if len(runs) < 1:
        return {"one_line_brief": "No prior watchlist data available"}

    last_run = runs[-1]
    tickers = history.get("tickers", {})
    watchlist = []
    for ticker, th in tickers.items():
        apps = th.get("appearances", [])
        if apps and apps[-1]["date"] == last_run:
            watchlist.append(f"{ticker}: {apps[-1]['recommendation']} (score {apps[-1]['score']})")

    nifty   = market_env.get("nifty", {})
    vix     = market_env.get("vix", {})
    breadth = market_env.get("breadth", {})

    result = call_json(
        prompt=PREMARKET_PROMPT.format(
            watchlist    = "\n".join(watchlist[:10]) or "No prior watchlist",
            nifty_trend  = nifty.get("trend", "Unknown"),
            nifty_1m     = nifty.get("ret_1m_pct", 0),
            vix          = vix.get("vix", 0),
            vix_signal   = vix.get("signal", "Unknown"),
            breadth      = breadth.get("breadth", "Unknown"),
            date         = run_date,
        ),
        system=PREMARKET_SYSTEM,
        max_tokens=600,
        fallback={"one_line_brief": "Pre-market brief unavailable"},
    )
    return result or {}


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER: ENRICH ALL STOCKS
# ═══════════════════════════════════════════════════════════════════════════════

def enrich_all_stocks(
    analysis_results: List[Dict],
    news_map: Dict[str, Dict],
    tweet_signals: Dict[str, Dict],
    market_env: Dict,
) -> tuple:
    """
    Run all stock intelligence modules on the full watchlist.
    Returns (enriched_results, correlation_data, macro_context).
    """
    total = len(analysis_results)
    logger.info(f"Enriching {total} stocks with Claude intelligence...")

    # Skip full enrichment for stocks with weak signal — saves ~2 Claude calls per stock
    # A stock qualifies for full enrichment if it has 2+ mentions OR meaningful tweet sentiment
    def _has_signal(r: Dict) -> bool:
        mentions = r.get("mention_count", 0) or r.get("mentions", 0)
        ts = tweet_signals.get(r["ticker"], {})
        sentiment_score = abs(ts.get("sentiment_score", 0) or 0)
        ta_score = r.get("score", 0)
        return mentions >= 2 or sentiment_score >= 1.0 or ta_score >= 14

    to_enrich    = [r for r in analysis_results if _has_signal(r)]
    low_signal   = [r for r in analysis_results if not _has_signal(r)]

    if low_signal:
        logger.info(f"  Skipping full enrichment for {len(low_signal)} low-signal stocks: {[r['ticker'] for r in low_signal]}")
        for r in low_signal:
            r["news_summary"] = {}
            r["narration"]    = {}
            r["tweet_signal"] = tweet_signals.get(r["ticker"], {})

    # Enrich each qualifying stock with 2 Claude calls (news summary → narration).
    # The two calls per stock are sequential (narration consumes news_summary),
    # but stocks are independent, so we run them concurrently. This is the runtime
    # bottleneck: serial it was ~19s/stock; parallel it collapses to roughly the
    # slowest few stocks. Worker count is capped to stay well under API rate limits;
    # claude_client already retries 429s with backoff, so no per-call sleep needed.
    def _enrich_one(r: Dict) -> Dict:
        ticker = r["ticker"]
        news   = news_map.get(ticker, {})
        ts     = tweet_signals.get(ticker, {})

        news_summary = summarise_news(
            ticker,
            news.get("news", []),
            news.get("filings", []),
        )
        narration = narrate_setup(ticker, r, ts, news_summary)

        r["news_summary"] = news_summary
        r["narration"]    = narration
        r["tweet_signal"] = ts
        return r

    max_workers = int(os.getenv("ENRICH_WORKERS", "5"))
    enriched = [None] * len(to_enrich)   # preserve input order for stable ranking
    if to_enrich:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_idx = {
                pool.submit(_enrich_one, r): i
                for i, r in enumerate(to_enrich)
            }
            done = 0
            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                done += 1
                ticker = to_enrich[idx]["ticker"]
                try:
                    enriched[idx] = fut.result()
                    logger.info(f"  [{done}/{len(to_enrich)}] Enriched {ticker}")
                except Exception as e:
                    logger.warning(f"  Enrichment failed for {ticker}: {e}")
                    # Fall back to un-enriched record so the stock still appears
                    r = to_enrich[idx]
                    r.setdefault("news_summary", {})
                    r.setdefault("narration", {})
                    r["tweet_signal"] = tweet_signals.get(ticker, {})
                    enriched[idx] = r
    enriched = [r for r in enriched if r is not None]

    # 3. Correlation detection (once for all stocks)
    logger.info("  Running correlation analysis...")
    all_results = enriched + low_signal
    correlations = detect_correlations(all_results)

    # 4. Macro context
    logger.info("  Generating macro context...")
    macro = generate_macro_context(market_env, all_results)

    logger.info(f"✅ Stock intelligence complete: {len(enriched)} enriched, {len(low_signal)} skipped (low signal)")
    return all_results, correlations, macro


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import json

    # Test with mock data
    mock_ta = {
        "ticker": "WABAG", "current_price": 1882.0, "recommendation": "BUY",
        "score": 18, "max_score": 28, "rsi": 68.0, "rsi_signal": "Neutral",
        "macd_above_zero": True, "above_ema20": True, "above_ema50": True, "above_ema200": True,
        "adx": 32.0, "trend_direction": "Bullish", "volume_ratio": 1.5,
        "suggested_stop": 1750.0, "suggested_target": 2100.0, "risk_reward": 2.5,
        "low_52w": 950.0, "high_52w": 1919.0, "pct_from_52h": -1.9,
        "sector": "Energy", "advanced": {"patterns": {"description": "VCP detected — 45% range tightening"}},
    }
    mock_ts = {
        "sentiment_label": "Strong Bullish", "bullish_count": 5, "bearish_count": 0,
        "avg_conviction": 4.2, "dominant_signal_type": "breakout",
        "trader_entry": 1850.0, "trader_sl": 1720.0, "trader_target": 2200.0,
        "key_reasons": ["Kuwait mega order catalyst", "New 52W high", "Water sector tailwind"],
    }
    mock_news = {
        "news": [{"date": "2026-06-19", "source": "Business Standard",
                  "title": "VA Tech Wabag wins Kuwait mega SWRO desalination contract",
                  "snippet": "The contract is worth over $150 million and includes a 5-year O&M component"}],
        "filings": [{"date": "2026-06-19", "title": "Award of Order: Kuwait SWRO DBO Contract"}],
    }

    narration = narrate_setup("WABAG", mock_ta, mock_ts, {})
    print("=== SETUP NARRATION ===")
    print(json.dumps(narration, indent=2))

    news_sum = summarise_news("WABAG", mock_news["news"], mock_news["filings"])
    print("\n=== NEWS SUMMARY ===")
    print(json.dumps(news_sum, indent=2))
