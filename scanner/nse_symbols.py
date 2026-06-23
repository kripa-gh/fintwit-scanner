"""
nse_symbols.py — Gap 3 Fix
Fetches and caches the NSE equity master symbol list.
All extracted tickers are validated against this before analysis.

Source: NSE equity bhavcopy CSV (updated daily, publicly accessible)
Fallback: bundled seed list of ~200 common NSE symbols
"""

import csv
import io
import json
import logging
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from typing import Set

logger = logging.getLogger(__name__)

# Local cache path — committed to repo so it persists across runs
CACHE_PATH   = Path(__file__).parent.parent / "data" / "nse_symbols.json"
CACHE_TTL_H  = 24   # refresh once per day

# NSE equity master CSV (publicly accessible, no auth needed)
NSE_CM_URL   = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,*/*",
}

# ── Seed list ─────────────────────────────────────────────────────────────────
# ~300 commonly traded NSE symbols as fallback if remote fetch fails
SEED_SYMBOLS: Set[str] = {
    "RELIANCE","TCS","HDFCBANK","INFY","HINDUNILVR","ICICIBANK","KOTAKBANK",
    "SBIN","BHARTIARTL","ITC","AXISBANK","WIPRO","LT","HCLTECH","ASIANPAINT",
    "MARUTI","SUNPHARMA","BAJFINANCE","TITAN","ULTRACEMCO","NESTLEIND","TECHM",
    "POWERGRID","NTPC","ONGC","COALINDIA","BAJAJFINSV","GRASIM","DIVISLAB",
    "CIPLA","DRREDDY","EICHERMOT","INDUSINDBK","ADANIPORTS","HINDALCO",
    "JSWSTEEL","TATASTEEL","BRITANNIA","TATACONSUM","BPCL","HEROMOTOCO",
    "APOLLOHOSP","ADANIENT","VEDL","SIEMENS","ABB","HAVELLS","PIDILITIND",
    "GODREJCP","DABUR","MARICO","COLPAL","EMAMILTD","MUTHOOTFIN","CHOLAFIN",
    "BAJAJ-AUTO","M&M","TATAMOTORS","TATAPOWER","ADANIGREEN","ADANITRANS",
    "ATGL","ADANIPOWER","AWL","ADANIPORTS","NAUKRI","PERSISTENT","COFORGE",
    "MPHASIS","LTIM","LTTS","KPITTECH","ROUTE","TRENT","PAGEIND","VOLTAS",
    "WHIRLPOOL","BLUESTARCO","BATAINDIA","RELAXO","VIPIND","SAFARI","VBL",
    "RADICO","UNITDSPR","GLOBUS","MCDOWELL-N","DIAGEO","SRF","AAPL",
    "ATUL","DEEPAKNI","PIIND","ASTRAL","SUPREMEIND","FINOLEX","APLAPOLLO",
    "HFCL","STERLITE","POLYCAB","HAVELLS","KEI","FINOLEX","CUMMINS","KSB",
    "THERMAX","BHEL","BEL","HAL","BEML","COCHINSHIP","GRSE","MAZAGON",
    "DATAPATTNS","MTAR","MTARTECH","PARAS","IDEAFORGE","SOLARINDS","NEWGEN",
    "TATAELXSI","KFINTECH","CDSL","BSE","MCX","IRCTC","CONCOR","RVNL",
    "IRFC","RECLTD","PFC","HUDCO","NBCC","RAILTEL","NMDC","SAIL","JINDALSTEL",
    "JSWENERGY","CESC","TATAPOWER","TORNTPOWER","ADANIGREEN","NHPC","SJVN",
    "INOXWIND","SUZLON","GREENKO","CLEANMAX","AMrenewpow","RPOWER","JPPOWER",
    "WABAG","IONEXCHANG","DENTA","ENVIROINFRA","FELIX","EMS","ANTONYASTE",
    "MTARTECH","AZAD","AEQUS","DYNAMATECH","SANSERA","ELECON","INOXINDIA",
    "KAYNES","AVALON","CYIENT","MTAR","PARAS","IDEAFORGE","NIBE","PLUTUS",
    "RAYMOND","RAYMONDREL","KALYANKJIL","PCJEWELLER","SENCO","JUBLPHARMA",
    "SUNPHARMA","LUPIN","CIPLA","DRREDDY","AUROPHARMA","TORNTPHARM","ALKEM",
    "IPCALAB","GRANULES","LAURUSLABS","STAR","AJANTPHARM","GLENMARK",
    "BIOCON","DIVIS","SEQUENT","SUVEN","WINDLAS","GLAND","SYNGENE","NACLIND",
    "UFLEX","ESTER","JBMA","JBMAUTO","SUPRAJIT","ENDURANCE","MINDA","MOTHERSON",
    "SAMVARDHANA","CRAFTSMAN","RAMKRISHNA","TATACHEM","DEEPAKNTR","AARTI",
    "VINATIORGA","NEOGEN","ANURAS","PRIVISCL","TARIL","CMRGREEN","HARIOM",
    "CMPDI","UNIVERSALCAB","UNIVERSALCABLES","FCL","AEGISLOG","SCI","GRSE",
    "PREMEXPLN","APOLLODEF","APOLLO","ZENTECH","TEJASNET","TRENT","THELEELA",
    "JITU","AZADIND","SUPRAJIT","DATAPATTERNS","CLEANMAX","WABAG","MTARTECH",
}


def _load_cache() -> dict:
    """Load cached symbol data if fresh enough."""
    if not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH) as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
        age_hours  = (datetime.now() - cached_at).total_seconds() / 3600
        if age_hours < CACHE_TTL_H:
            logger.info(f"NSE symbol cache hit ({len(data.get('symbols', []))} symbols, {age_hours:.1f}h old)")
            return data
    except Exception as e:
        logger.warning(f"Cache read failed: {e}")
    return {}


def _save_cache(symbols: Set[str]) -> None:
    """Persist symbol set to local cache file."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump({
                "cached_at": datetime.now().isoformat(),
                "count":     len(symbols),
                "symbols":   sorted(symbols),
            }, f, indent=2)
        logger.info(f"NSE symbol cache saved: {len(symbols)} symbols")
    except Exception as e:
        logger.warning(f"Cache write failed: {e}")


def _fetch_remote() -> Set[str]:
    """
    Download NSE equity master list from archives.nseindia.com.
    CSV columns: SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, ...
    Returns set of valid NSE symbols.
    """
    try:
        req = urllib.request.Request(NSE_CM_URL, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        symbols = set()
        reader = csv.DictReader(io.StringIO(raw))
        for row in reader:
            sym = row.get("SYMBOL", "").strip().upper()
            if sym and len(sym) >= 2:
                symbols.add(sym)

        logger.info(f"Fetched {len(symbols)} symbols from NSE equity master")
        return symbols

    except urllib.error.HTTPError as e:
        logger.warning(f"NSE equity master HTTP {e.code} — using seed list")
        return set()
    except Exception as e:
        logger.warning(f"NSE equity master fetch failed: {e} — using seed list")
        return set()


def get_valid_symbols() -> Set[str]:
    """
    Return the full set of valid NSE equity symbols.
    Strategy:
      1. Use cache if < 24h old
      2. Fetch fresh from NSE archives
      3. Fallback to seed list if fetch fails
    Always merges seed list in so hand-curated symbols are never lost.
    """
    cached = _load_cache()
    if cached:
        return set(cached["symbols"]) | SEED_SYMBOLS

    remote = _fetch_remote()
    if remote:
        combined = remote | SEED_SYMBOLS
        _save_cache(combined)
        return combined

    # Both failed — use seed only
    logger.warning("Using seed symbol list only — remote fetch and cache both unavailable")
    return SEED_SYMBOLS


def is_valid_nse_symbol(ticker: str, valid_symbols: Set[str] = None) -> bool:
    """Check if a ticker is a real NSE symbol."""
    if valid_symbols is None:
        valid_symbols = get_valid_symbols()
    return ticker.upper() in valid_symbols


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    symbols = get_valid_symbols()
    print(f"Total valid NSE symbols: {len(symbols)}")
    test = ["WABAG", "CLEANMAX", "MTARTECH", "FAKEXYZ", "NIFTY", "STOCKS", "AZAD"]
    for t in test:
        print(f"  {t:15} {'✅ VALID' if t in symbols else '❌ INVALID'}")
