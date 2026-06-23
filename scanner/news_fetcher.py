"""
news_fetcher.py — Gap 7 Fix
Google News RSS (primary) + NSE exchange filings (secondary).

Gap 7 fix: NSE filing fetch is now session-aware with proper retry logic,
timeout handling, and graceful degradation. If NSE blocks (common on cloud IPs),
Google News RSS alone is sufficient — no silent failures.
"""

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple

try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False
    logging.getLogger(__name__).warning(
        "feedparser not installed — Google News RSS unavailable. "
        "Run: pip install feedparser"
    )

logger = logging.getLogger(__name__)

TIMEOUT = 12
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

# NSE session (reused across all tickers in a run)
_nse_session: Optional[urllib.request.OpenerDirector] = None
_nse_ok: bool = False
_nse_failed_permanently: bool = False   # Gap 7: stop retrying after hard failure


# ── Catalyst rules ────────────────────────────────────────────────────────────
CATALYST_RULES = [
    ("Order Win",          ["order win", "contract win", "wins contract", "award of order",
                            "secured order", "bagged order", "letter of award", "loa received",
                            "epc contract", "dbo contract", "wins mega", "wins large", "secures deal"]),
    ("Strong Earnings",    ["profit", "pat surge", "net profit", "earnings beat", "revenue up",
                            "record revenue", "q4 results", "q3 results", "q2 results", "q1 results",
                            "fy26", "fy25", "quarterly results", "ebitda"]),
    ("Analyst Upgrade",    ["upgrade", "target price raised", "overweight", "outperform",
                            "buy rating", "strong buy", "price target hiked", "initiates coverage"]),
    ("Analyst Downgrade",  ["downgrade", "underperform", "reduce rating",
                            "target cut", "price target lowered", "rating cut"]),
    ("Risk / Negative",    ["crash", "fell", "plunged", "slumped", "tanks", "tumbles",
                            "concern", "warning", "net loss", "slump", "withdrew", "cancelled",
                            "delays project", "setback", "headwinds"]),
    ("Partnership / Deal", ["partnership", "joint venture", " jv ", "collaboration", " mou ",
                            "memorandum of understanding", "tie-up", "power purchase agreement",
                            "ppa signed", "strategic alliance"]),
    ("Fundraise / QIP",    ["qip", "fundraise", "fund raise", "equity raise", "rights issue",
                            "fpo", "private placement", "ecb facility", "ncd", "raised funds"]),
    ("Capacity Expansion", ["expansion", "capex", "new plant", "capacity addition",
                            "greenfield", "brownfield", "commissioning", "inaugurated"]),
    ("IPO / Listing",      ["ipo", "listed today", "allotment", "debut", "pre-ipo", "listing gains"]),
    ("Corporate Action",   ["dividend", "bonus shares", "stock split", "record date",
                            "buyback", "annual general meeting", "agm", "board meeting"]),
    ("Regulatory / Legal", ["sebi order", "gst demand notice", "tax demand", "penalty notice",
                            "regulatory action", "compliance notice", "probe launched", "show cause"]),
]


def _classify_catalyst(text: str) -> str:
    t = text.lower()
    for label, keywords in CATALYST_RULES:
        if any(kw in t for kw in keywords):
            return label
    return "News Activity"


def _parse_date(raw: str) -> str:
    try:
        return parsedate_to_datetime(raw).strftime("%Y-%m-%d")
    except Exception:
        return raw[:10] if raw else ""


# ── Google News RSS ───────────────────────────────────────────────────────────

def _fetch_gnews(ticker: str) -> List[Dict]:
    """
    Fetch articles from Google News RSS.
    Two query attempts: exact ticker first, then broader fallback.
    Returns [] gracefully on any failure.
    """
    if not HAS_FEEDPARSER:
        return []

    queries = [
        f'"{ticker}" NSE India stock',
        f"{ticker} NSE share price India",
    ]

    for query in queries:
        url = (
            "https://news.google.com/rss/search"
            f"?q={urllib.parse.quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
        )
        try:
            req = urllib.request.Request(
                url,
                headers={**HEADERS, "Accept": "application/rss+xml, application/xml"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()

            feed    = feedparser.parse(raw)
            articles= []

            for entry in feed.entries[:6]:
                title = (entry.get("title") or "").strip()
                if not title or "[Removed]" in title:
                    continue

                src = entry.get("source", {})
                source = src.get("title", "Google News") if isinstance(src, dict) else "Google News"
                snippet = re.sub(r"<[^>]+>", "", entry.get("summary", ""))[:200]

                articles.append({
                    "title":   title,
                    "source":  source,
                    "url":     entry.get("link", ""),
                    "date":    _parse_date(entry.get("published", "")),
                    "snippet": snippet,
                })

            if articles:
                logger.info(f"Google News: {len(articles)} articles for {ticker}")
                return articles

        except urllib.error.HTTPError as e:
            logger.debug(f"Google News HTTP {e.code} for query '{query}'")
        except Exception as e:
            logger.debug(f"Google News error for {ticker}: {e}")

    logger.info(f"Google News: 0 articles for {ticker}")
    return []


# ── NSE Exchange Filings ──────────────────────────────────────────────────────

def _init_nse_session() -> bool:
    """
    Gap 7: Initialise NSE session with retry and permanent-failure detection.
    If NSE blocks twice, sets _nse_failed_permanently = True
    and skips all subsequent NSE calls without logging noise.
    """
    global _nse_session, _nse_ok, _nse_failed_permanently

    if _nse_ok:
        return True
    if _nse_failed_permanently:
        return False

    for attempt in range(2):
        try:
            jar     = http.cookiejar.CookieJar()
            opener  = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            req     = urllib.request.Request("https://www.nseindia.com", headers=HEADERS)
            with opener.open(req, timeout=TIMEOUT):
                pass
            time.sleep(1.0)
            _nse_session = opener
            _nse_ok      = True
            logger.info("NSE session established")
            return True
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                logger.warning(f"NSE blocked (HTTP {e.code}) — skipping NSE filings for this run")
                _nse_failed_permanently = True
                return False
            logger.debug(f"NSE session attempt {attempt+1} failed: {e}")
            time.sleep(2)
        except Exception as e:
            logger.debug(f"NSE session attempt {attempt+1} error: {e}")
            time.sleep(2)

    logger.warning("NSE session failed after 2 attempts — skipping NSE filings")
    _nse_failed_permanently = True
    return False


def _fetch_nse_filings(ticker: str) -> List[Dict]:
    """
    Fetch corporate announcements from NSE.
    Returns [] immediately if NSE is known-blocked (_nse_failed_permanently).
    """
    if not _init_nse_session():
        return []

    nse_url = f"https://www.nseindia.com/get-quotes/equity?symbol={ticker}"
    endpoints = [
        f"https://www.nseindia.com/api/corp-info?symbol={ticker}&type=announcements",
        f"https://www.nseindia.com/api/corp-info?symbol={ticker}&type=corporate_actions",
    ]

    filings = []
    for endpoint in endpoints:
        try:
            req = urllib.request.Request(
                endpoint,
                headers={
                    **HEADERS,
                    "Accept":            "application/json",
                    "Referer":           "https://www.nseindia.com/",
                    "X-Requested-With":  "XMLHttpRequest",
                }
            )
            with _nse_session.open(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read())

            items = data if isinstance(data, list) else data.get("data", [])
            for item in items[:4]:
                title = (
                    item.get("subject") or item.get("desc") or
                    item.get("attchmntText") or item.get("purpose") or ""
                ).strip()
                date_raw = (
                    item.get("an_dt") or item.get("bflag") or
                    item.get("exDate") or item.get("date") or ""
                )
                if title:
                    filings.append({
                        "title":   title,
                        "source":  "NSE India (Exchange Filing)",
                        "url":     nse_url,
                        "date":    date_raw[:10],
                        "snippet": "",
                    })
            time.sleep(0.4)

        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                global _nse_failed_permanently
                _nse_failed_permanently = True
                logger.warning(f"NSE filing HTTP {e.code} — disabling NSE calls for this run")
                return filings
            logger.debug(f"NSE filing HTTP {e.code} for {ticker}")
        except Exception as e:
            logger.debug(f"NSE filing error for {ticker}: {e}")

    logger.info(f"NSE filings: {len(filings)} items for {ticker}")
    return filings[:5]


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_news(ticker: str) -> Dict:
    """
    Fetch news + filings for one ticker.
    Filings take priority in catalyst classification (more authoritative).
    Google News is always attempted regardless of NSE status.
    """
    articles = _fetch_gnews(ticker)
    filings  = _fetch_nse_filings(ticker)

    all_items   = filings + articles
    all_titles  = " ".join(i["title"] for i in all_items)
    cat_type    = _classify_catalyst(all_titles)

    # Best headline: prefer news (more descriptive) over terse filing text
    best = articles[0]["title"] if articles else (filings[0]["title"] if filings else "")
    cat_summary = (
        f"[{cat_type}] {best[:110]}"
        if best
        else "No recent news. Verify on screener.in / NSE."
    )

    return {
        "ticker":           ticker,
        "news":             articles[:5],
        "filings":          filings[:4],
        "catalyst_summary": cat_summary,
        "catalyst_type":    cat_type,
    }


def fetch_all_news(analysis_results: List[Dict]) -> Dict[str, Dict]:
    """Fetch news for all tickers. NSE session is shared across calls."""
    _init_nse_session()
    news_map = {}
    total    = len(analysis_results)

    for i, r in enumerate(analysis_results, 1):
        ticker = r["ticker"]
        logger.info(f"[{i}/{total}] News: {ticker}")
        news_map[ticker] = fetch_news(ticker)
        time.sleep(0.8)

    return news_map


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for ticker in ["WABAG", "CLEANMAX", "MTARTECH"]:
        result = fetch_news(ticker)
        print(f"\n{ticker}: {result['catalyst_summary']}")
        for n in result["news"][:2]:
            print(f"  [{n['date']}] {n['title'][:80]} — {n['source']}")
