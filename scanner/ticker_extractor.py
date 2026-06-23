"""
ticker_extractor.py — Gap 3 + Gap 4 Fix
Extracts Indian NSE tickers with:
  1. Validation against real NSE symbol list (no fake tickers)
  2. Retweet amplification scoring (RT'd by 10 people ≠ 10 independent mentions)
  3. Weighted mention score = unique_mentions + (amplification * 0.3)
"""

import re
import logging
from collections import Counter, defaultdict
from typing import Dict, List, Set

logger = logging.getLogger(__name__)

# Generic noise — words that appear as hashtags but are never tickers
# Kept minimal since NSE whitelist handles the heavy lifting now
NOISE = {
    "NIFTY", "NIFTY50", "SENSEX", "BANKNIFTY", "FINNIFTY", "GIFTNIFTY",
    "STOCKS", "STOCKMARKET", "STOCKMARKETINDIA", "MARKET", "MARKETS",
    "SWING", "SWINGTRADING", "INTRADAY", "MOMENTUM", "BREAKOUT",
    "TECHNICAL", "CHART", "CHARTS", "TRADE", "TRADING", "INVEST",
    "INVESTING", "INVESTMENT", "LONGTERM", "MULTIBAGGER", "SMALLCAP",
    "MIDCAP", "LARGECAP", "BLUECHIP", "IPO", "FII", "DII",
    "OPTIONBUYING", "OPTIONTRADING", "FUTURES", "OPTIONS", "FNO",
    "ATH", "ATL", "RSI", "MACD", "EMA", "SMA", "VCP", "DARVAS",
    "DARVASBOX", "TRADINGVIEW", "CHARTGYM", "NEWPOSITION",
    "GOODMORNING", "GOLD", "SILVER", "CRUDE", "CHINA", "INDIA",
    "GLOBAL", "ECONOMY", "BUDGET", "NEET", "TELEGRAM", "FOOTBALL",
    "YOGA", "DATA", "SECTOR", "MIDCAPS", "TRANSFORMER", "TRANSFORMERS",
    "A", "B", "I", "IT", "AT", "MY", "AM", "PM", "TV", "US", "OK",
    "UP", "GO", "DO", "OR", "BE", "NO", "ON", "IN", "SO", "TO",
    "RE", "IF", "OF", "BY", "AS", "AN", "IS", "HE", "WE", "ME",
}


def extract_tickers(
    tweets: List[Dict],
    retweet_counts: Dict[str, int] = None,
    valid_symbols: Set[str] = None,
) -> List[Dict]:
    """
    Extract and rank NSE tickers from tweets.

    Scoring:
      - Each unique account mentioning a ticker = 1 point
      - Retweet amplification = +0.3 per retweet (signal of interest,
        but not as strong as an independent mention)
      - Final list sorted by weighted_score descending

    Args:
      tweets:          list of tweet dicts from twitter_scraper
      retweet_counts:  {tweet_id: amplification_count} from twitter_scraper
      valid_symbols:   set of valid NSE symbols (from nse_symbols.get_valid_symbols())
                       if None, validation is skipped (not recommended)

    Returns:
      [
        {
          ticker, mentions, weighted_score, amplification,
          users, tweets (top 3)
        },
        ...
      ]
    """
    retweet_counts = retweet_counts or {}

    ticker_users       = defaultdict(set)
    ticker_mentions    = Counter()   # unique account mentions
    ticker_amplif      = Counter()   # retweet amplification score
    ticker_tweets      = defaultdict(list)

    for t in tweets:
        username    = t.get("username", "")
        text        = t.get("text", "")
        date        = t.get("date", "")
        url         = t.get("url", "")
        tweet_id    = t.get("id", "")
        amplif      = t.get("retweet_amplification", 0)

        # Extract all candidate tickers from this tweet
        found = _extract_candidates(t)

        for ticker in found:
            # NSE whitelist validation — the key Gap 3 fix
            if valid_symbols and ticker not in valid_symbols:
                continue
            if ticker in NOISE:
                continue

            # Count unique user mentions only (Gap 4 fix)
            if username not in ticker_users[ticker]:
                ticker_mentions[ticker] += 1
                ticker_users[ticker].add(username)

            # Add amplification score
            if amplif > 0:
                ticker_amplif[ticker] += amplif

            # Store sample tweets (max 3)
            if len(ticker_tweets[ticker]) < 3:
                ticker_tweets[ticker].append({
                    "username":      username,
                    "text":          text[:200],
                    "date":          date,
                    "url":           url,
                    "amplification": amplif,
                })

    # Build ranked output
    all_tickers = set(ticker_mentions.keys()) | set(ticker_amplif.keys())
    ranked = []

    for ticker in all_tickers:
        mentions    = ticker_mentions[ticker]
        amplif_score= ticker_amplif[ticker]
        # Weighted: unique mentions worth 1.0 each, RT amplification worth 0.3
        weighted    = round(mentions + (amplif_score * 0.3), 2)

        ranked.append({
            "ticker":         ticker,
            "mentions":       mentions,         # unique accounts
            "amplification":  amplif_score,     # total retweets within list
            "weighted_score": weighted,
            "users":          sorted(ticker_users[ticker]),
            "tweets":         ticker_tweets[ticker],
        })

    ranked.sort(key=lambda x: (x["weighted_score"], x["mentions"]), reverse=True)
    logger.info(
        f"Extracted {len(ranked)} validated tickers "
        f"({'with' if valid_symbols else 'WITHOUT'} NSE whitelist)"
    )
    return ranked


def _extract_candidates(tweet: Dict) -> Set[str]:
    """Extract all ticker candidates from a single tweet."""
    found = set()
    text = tweet.get("text", "")

    # 1. Explicit cashtags parsed by twscrape
    for ct in tweet.get("cashtags", []):
        if isinstance(ct, str):
            found.add(ct.upper().strip())

    # 2. Hashtags parsed by twscrape
    for ht in tweet.get("hashtags", []):
        if isinstance(ht, str) and 2 <= len(ht) <= 15:
            found.add(ht.upper().strip())

    # 3. $TICKER pattern in raw text
    for m in re.findall(r'\$([A-Z]{2,10})\b', text):
        found.add(m.upper())

    # 4. #TICKER pattern — only ALL-CAPS (likely deliberate stock reference)
    for m in re.findall(r'#([A-Z][A-Z0-9]{1,14})\b', text):
        found.add(m.upper())

    return found


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    # Import here to allow standalone testing
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from scanner.nse_symbols import get_valid_symbols

    valid = get_valid_symbols()

    # Simulate: 15 accounts RT the same VCPSwing tweet about MTARTECH
    sample_tweets = [
        # Original post
        {"id": "001", "username": "VCPSwing", "date": "2026-06-22", "url": "#",
         "text": "#MTARTECH - 130%+ Move, thesis intact. #WABAG Kuwait order",
         "hashtags": ["MTARTECH", "WABAG"], "cashtags": [], "retweet_amplification": 15, "retweeters": []},
        # Someone else mentions the same tickers independently
        {"id": "002", "username": "price_action_NS", "date": "2026-06-22", "url": "#",
         "text": "Defence picks: #MTARTECH #GRSE #PREMEXPLN for next 2 weeks",
         "hashtags": ["MTARTECH", "GRSE", "PREMEXPLN"], "cashtags": [], "retweet_amplification": 0, "retweeters": []},
        # Another independent mention
        {"id": "003", "username": "Fintech00", "date": "2026-06-22", "url": "#",
         "text": "#WABAG Gone mad! Mega order Kuwait. Also #CLEANMAX",
         "hashtags": ["WABAG", "CLEANMAX"], "cashtags": [], "retweet_amplification": 3, "retweeters": []},
        # A noise tweet that should be filtered
        {"id": "004", "username": "random_account", "date": "2026-06-22", "url": "#",
         "text": "#NIFTY at resistance 24100. #SENSEX down. #STOCKS looking weak",
         "hashtags": ["NIFTY", "SENSEX", "STOCKS"], "cashtags": [], "retweet_amplification": 0, "retweeters": []},
        # A fake ticker that should be caught by whitelist
        {"id": "005", "username": "trader5", "date": "2026-06-22", "url": "#",
         "text": "Watching #WATERTECH and #CLEANMAX for breakout",
         "hashtags": ["WATERTECH", "CLEANMAX"], "cashtags": [], "retweet_amplification": 0, "retweeters": []},
    ]

    results = extract_tickers(sample_tweets, valid_symbols=valid)

    print("=== TICKER EXTRACTION WITH WHITELIST + DEDUPLICATION ===\n")
    for r in results:
        print(f"${r['ticker']:15} | mentions={r['mentions']} | amplif={r['amplification']} | score={r['weighted_score']} | by: {r['users']}")

    print("\nExpected:")
    print("  MTARTECH: 2 unique mentions + 15 amplification = score 6.5")
    print("  WABAG:    2 unique mentions + 3 amplification  = score 2.9")
    print("  CLEANMAX: 2 unique mentions + 3 amplification  = score 2.9")
    print("  NIFTY / SENSEX / STOCKS / WATERTECH: filtered")
