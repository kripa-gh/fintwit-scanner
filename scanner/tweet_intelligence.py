"""
tweet_intelligence.py — Tier 1
Claude-powered tweet analysis replacing regex-based extraction.

Covers:
  1. Sentiment classification per ticker per tweet (bullish/bearish/neutral)
  2. Conviction scoring (1-5)
  3. Entry/exit/SL/target price extraction
  4. Chart image analysis (reads screenshots traders post)
  5. Aggregated ticker signal combining all of the above
"""

import base64
import logging
import time
from collections import defaultdict
from typing import Dict, List, Optional
from urllib.request import urlopen
from urllib.error import URLError

from scanner.claude_client import call_json, call

logger = logging.getLogger(__name__)

BATCH_SIZE = 20   # tweets per Claude call (keeps prompt manageable)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. BATCH TWEET SENTIMENT + CONVICTION + PRICE EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

TWEET_ANALYSIS_SYSTEM = """You are an expert Indian stock market analyst reading tweets from FinTwit traders.
For each tweet, extract trading signals with precision.
Respond ONLY with valid JSON — no explanation, no markdown.
NSE stocks are referenced as hashtags (#WABAG) or cashtags ($WABAG) or plain text.
Indian market context: prices in INR (₹), NSE/BSE listed stocks only."""

TWEET_ANALYSIS_PROMPT = """Analyse these tweets and return a JSON array. One object per tweet.

Tweets:
{tweets}

For each tweet return:
{{
  "tweet_id": "the id field",
  "username": "username",
  "tickers": [
    {{
      "symbol": "NSE_SYMBOL_UPPERCASE",
      "sentiment": "bullish" | "bearish" | "neutral" | "exit",
      "conviction": 1-5,
      "entry_price": number or null,
      "stop_loss": number or null,
      "target_price": number or null,
      "timeframe": "intraday" | "swing" | "positional" | "long_term" | "unknown",
      "signal_type": "breakout" | "pullback" | "reversal" | "momentum" | "value" | "avoid" | "informational",
      "key_reason": "one line reason the trader is bullish/bearish"
    }}
  ],
  "has_chart_image": true | false,
  "chart_image_url": "url or null",
  "tweet_quality": "high" | "medium" | "low"
}}

Rules:
- Only include tickers clearly referenced in the tweet
- "exit" sentiment = trader exiting/booking profits
- conviction 5 = "highest conviction", "adding aggressively"; 1 = "just watching"
- tweet_quality "high" = specific prices/levels/reasons; "low" = vague/noise
- If no stocks mentioned, return empty tickers array
- Return ONLY the JSON array, nothing else"""


def analyse_tweets_batch(tweets: List[Dict]) -> List[Dict]:
    """
    Analyse a batch of tweets with Claude.
    Returns list of tweet analysis dicts.
    """
    if not tweets:
        return []

    # Format tweets for the prompt
    tweet_text = "\n\n".join([
        f"ID: {t['id']}\n@{t['username']}: {t['text'][:300]}"
        for t in tweets
    ])

    result = call_json(
        prompt=TWEET_ANALYSIS_PROMPT.format(tweets=tweet_text),
        system=TWEET_ANALYSIS_SYSTEM,
        max_tokens=2000,
        fallback=[],
    )

    if not isinstance(result, list):
        logger.warning(f"Tweet batch returned non-list: {type(result)}")
        return []

    return result


def analyse_all_tweets(tweets: List[Dict]) -> Dict[str, Dict]:
    """
    Analyse all tweets in batches.
    Returns {ticker: aggregated_signal_dict}.
    """
    logger.info(f"Analysing {len(tweets)} tweets with Claude...")

    all_analyses = []
    for i in range(0, len(tweets), BATCH_SIZE):
        batch = tweets[i:i + BATCH_SIZE]
        logger.info(f"  Batch {i//BATCH_SIZE + 1}/{(len(tweets)-1)//BATCH_SIZE + 1}...")
        batch_result = analyse_tweets_batch(batch)
        all_analyses.extend(batch_result)
        if i + BATCH_SIZE < len(tweets):
            time.sleep(0.5)   # rate limit

    # Aggregate by ticker
    ticker_signals = _aggregate_by_ticker(all_analyses, tweets)
    logger.info(f"  → {len(ticker_signals)} tickers with Claude sentiment")
    return ticker_signals


def _aggregate_by_ticker(analyses: List[Dict], raw_tweets: List[Dict]) -> Dict[str, Dict]:
    """
    Aggregate individual tweet analyses into per-ticker signals.
    Weights: bullish=+1, bearish=-1, exit=-0.5, neutral=0
    Conviction multiplies the weight.
    """
    ticker_data = defaultdict(lambda: {
        "mentions":       0,
        "bullish":        0,
        "bearish":        0,
        "neutral":        0,
        "exit":           0,
        "total_conviction": 0,
        "sentiment_score":  0.0,
        "entry_prices":   [],
        "stop_losses":    [],
        "targets":        [],
        "timeframes":     [],
        "signal_types":   [],
        "key_reasons":    [],
        "traders":        [],
        "high_quality_mentions": 0,
        "chart_images":   [],
    })

    # Map tweet_id → username for enrichment
    id_to_tweet = {t["id"]: t for t in raw_tweets}

    for analysis in analyses:
        if not isinstance(analysis, dict):
            continue
        tweet_id  = str(analysis.get("tweet_id", ""))
        username  = analysis.get("username", "")
        quality   = analysis.get("tweet_quality", "low")
        has_chart = analysis.get("has_chart_image", False)
        chart_url = analysis.get("chart_image_url")

        for ticker_info in analysis.get("tickers", []):
            if not isinstance(ticker_info, dict):
                continue
            sym = ticker_info.get("symbol", "").upper().strip()
            if not sym or len(sym) < 2:
                continue

            sentiment  = ticker_info.get("sentiment", "neutral")
            conviction = int(ticker_info.get("conviction", 3))
            entry      = ticker_info.get("entry_price")
            sl         = ticker_info.get("stop_loss")
            tgt        = ticker_info.get("target_price")
            tf         = ticker_info.get("timeframe", "unknown")
            sig_type   = ticker_info.get("signal_type", "informational")
            reason     = ticker_info.get("key_reason", "")

            d = ticker_data[sym]
            d["mentions"]         += 1
            d["total_conviction"] += conviction
            d[sentiment]          += 1
            d["traders"].append(username)

            # Sentiment score: bullish=+1, exit=-0.5, bearish=-1, neutral=0
            weight = {"bullish": 1.0, "bearish": -1.0, "exit": -0.5, "neutral": 0.0}.get(sentiment, 0)
            d["sentiment_score"] += weight * (conviction / 3)   # normalise conviction

            if entry:  d["entry_prices"].append(float(entry))
            if sl:     d["stop_losses"].append(float(sl))
            if tgt:    d["targets"].append(float(tgt))
            if tf != "unknown": d["timeframes"].append(tf)
            if sig_type != "informational": d["signal_types"].append(sig_type)
            if reason:  d["key_reasons"].append(reason)
            if quality == "high": d["high_quality_mentions"] += 1
            if has_chart and chart_url: d["chart_images"].append(chart_url)

    # Finalise
    result = {}
    for sym, d in ticker_data.items():
        mentions = d["mentions"]
        if mentions == 0:
            continue

        # Net sentiment label
        score = d["sentiment_score"]
        if score >= 1.5:   sentiment_label = "Strong Bullish"
        elif score >= 0.5: sentiment_label = "Bullish"
        elif score <= -1.5:sentiment_label = "Strong Bearish"
        elif score <= -0.5:sentiment_label = "Bearish"
        else:              sentiment_label = "Mixed / Neutral"

        # Average prices from trader posts
        avg_entry = round(sum(d["entry_prices"]) / len(d["entry_prices"]), 2) if d["entry_prices"] else None
        avg_sl    = round(sum(d["stop_losses"])  / len(d["stop_losses"]),  2) if d["stop_losses"]  else None
        avg_tgt   = round(sum(d["targets"])      / len(d["targets"]),      2) if d["targets"]      else None

        # Most common timeframe and signal type
        from collections import Counter
        tf_common  = Counter(d["timeframes"]).most_common(1)[0][0]  if d["timeframes"]   else "unknown"
        sig_common = Counter(d["signal_types"]).most_common(1)[0][0] if d["signal_types"] else "informational"

        result[sym] = {
            "ticker":                sym,
            "mentions":              mentions,
            "sentiment_label":       sentiment_label,
            "sentiment_score":       round(score, 2),
            "bullish_count":         d["bullish"],
            "bearish_count":         d["bearish"],
            "exit_count":            d["exit"],
            "avg_conviction":        round(d["total_conviction"] / mentions, 1),
            "high_quality_mentions": d["high_quality_mentions"],
            "traders":               list(set(d["traders"])),
            "trader_entry":          avg_entry,
            "trader_sl":             avg_sl,
            "trader_target":         avg_tgt,
            "dominant_timeframe":    tf_common,
            "dominant_signal_type":  sig_common,
            "key_reasons":           d["key_reasons"][:5],
            "chart_image_urls":      d["chart_images"][:3],
        }

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CHART IMAGE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

CHART_ANALYSIS_SYSTEM = """You are an expert technical analyst reading stock chart screenshots posted by traders.
Identify the chart pattern, key levels, and what the trader is likely pointing to.
Be concise and specific. Respond only with JSON."""

CHART_ANALYSIS_PROMPT = """Analyse this stock chart image and return JSON:
{{
  "pattern": "pattern name or 'unclear'",
  "trend": "uptrend" | "downtrend" | "sideways",
  "key_support": number or null,
  "key_resistance": number or null,
  "breakout_level": number or null,
  "stop_loss_zone": number or null,
  "timeframe_visible": "daily" | "weekly" | "intraday" | "unknown",
  "setup_quality": "high" | "medium" | "low",
  "trader_likely_pointing_to": "one sentence description",
  "caution_flags": ["list of any red flags visible"]
}}"""


def analyse_chart_image(image_url: str) -> Optional[Dict]:
    """
    Download and analyse a chart image from a tweet.
    Returns chart analysis dict or None.
    """
    try:
        # Download image
        with urlopen(image_url, timeout=10) as resp:
            image_data = resp.read()
            content_type = resp.headers.get("Content-Type", "image/png")
            media_type = content_type.split(";")[0].strip()
            if media_type not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
                media_type = "image/jpeg"

        image_b64 = base64.b64encode(image_data).decode("utf-8")

        result = call_json(
            prompt=CHART_ANALYSIS_PROMPT,
            system=CHART_ANALYSIS_SYSTEM,
            max_tokens=500,
            image_base64=image_b64,
            fallback=None,
        )
        return result

    except URLError as e:
        logger.debug(f"Chart image fetch failed {image_url}: {e}")
        return None
    except Exception as e:
        logger.debug(f"Chart analysis failed: {e}")
        return None


def analyse_charts_for_tickers(ticker_signals: Dict[str, Dict]) -> Dict[str, Dict]:
    """
    For each ticker with chart image URLs, analyse the charts.
    Adds chart_analysis to the ticker signal dict.
    """
    for ticker, signal in ticker_signals.items():
        chart_urls = signal.get("chart_image_urls", [])
        if not chart_urls:
            signal["chart_analysis"] = None
            continue

        # Analyse first chart image
        chart = analyse_chart_image(chart_urls[0])
        signal["chart_analysis"] = chart
        if chart:
            logger.info(f"  Chart for {ticker}: {chart.get('pattern','?')} | {chart.get('trader_likely_pointing_to','')[:60]}")
        time.sleep(0.3)

    return ticker_signals


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import json

    sample_tweets = [
        {"id": "001", "username": "VCPSwing", "text": "#WABAG breakout happening now! Kuwait order catalyst. Buying at 1880, SL 1750, target 2200. High conviction swing trade.", "hashtags": ["WABAG"], "cashtags": [], "retweet_amplification": 0},
        {"id": "002", "username": "Fintech00", "text": "#CLEANMAX and #WABAG both going mad today. Booked profits in CLEANMAX at 1370, still holding WABAG.", "hashtags": ["CLEANMAX","WABAG"], "cashtags": [], "retweet_amplification": 0},
        {"id": "003", "username": "bearish_trader", "text": "#MTARTECH looks toppy here. Bloom Energy risk unresolved. Avoiding.", "hashtags": ["MTARTECH"], "cashtags": [], "retweet_amplification": 0},
        {"id": "004", "username": "noise_account", "text": "Good morning traders! Have a great day. #stockmarket #trading", "hashtags": ["stockmarket","trading"], "cashtags": [], "retweet_amplification": 0},
    ]

    signals = analyse_all_tweets(sample_tweets)
    print("\n=== TICKER SIGNALS ===")
    for ticker, sig in signals.items():
        print(f"\n{ticker}:")
        print(f"  Sentiment: {sig['sentiment_label']} (score={sig['sentiment_score']})")
        print(f"  Conviction: {sig['avg_conviction']}/5")
        print(f"  Trader entry: ₹{sig['trader_entry']} | SL: ₹{sig['trader_sl']} | Target: ₹{sig['trader_target']}")
        print(f"  Signal type: {sig['dominant_signal_type']}")
        for reason in sig['key_reasons']:
            print(f"  → {reason}")
