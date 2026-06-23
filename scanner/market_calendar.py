"""
market_calendar.py — Gap 2 Fix
NSE market holiday awareness.

Fetches NSE's official holiday list and determines:
  - Is today a trading day?
  - What was the last trading day?
  - Is the data we're fetching fresh or stale?

Source: NSE holiday master API (public, no auth)
Fallback: hardcoded FY2026-27 holiday list
"""

import json
import logging
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

CACHE_PATH  = Path(__file__).parent.parent / "data" / "nse_holidays.json"
CACHE_TTL_D = 30  # refresh monthly

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# ── Hardcoded FY2025-26 + FY2026-27 NSE holidays ─────────────────────────────
# Source: NSE India official holiday calendar
HARDCODED_HOLIDAYS: List[str] = [
    # FY2025-26
    "2026-01-26",  # Republic Day
    "2026-02-26",  # Mahashivratri
    "2026-03-02",  # Holi
    "2026-03-30",  # Ram Navami
    "2026-04-02",  # Shri Mahavir Jayanti / Good Friday
    "2026-04-14",  # Dr. Ambedkar Jayanti / Baisakhi
    "2026-05-01",  # Maharashtra Day / Labour Day
    "2026-06-29",  # Id ul-Adha (Bakri Id) — approx
    "2026-08-15",  # Independence Day
    "2026-08-27",  # Ganesh Chaturthi
    "2026-10-02",  # Mahatma Gandhi Jayanti
    "2026-10-20",  # Diwali Laxmi Pujan (approx)
    "2026-10-21",  # Diwali Balipratipada (approx)
    "2026-11-05",  # Guru Nanak Jayanti (approx)
    "2026-11-20",  # Chhatrapati Shivaji Maharaj Jayanti
    "2026-12-25",  # Christmas
    # FY2026-27
    "2027-01-26",  # Republic Day
    "2027-03-23",  # Holi (approx)
]


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH) as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
        age_days   = (datetime.now() - cached_at).days
        if age_days < CACHE_TTL_D:
            return data
    except Exception:
        pass
    return {}


def _save_cache(holidays: List[str]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump({
                "cached_at": datetime.now().isoformat(),
                "holidays":  sorted(holidays),
            }, f, indent=2)
    except Exception as e:
        logger.warning(f"Holiday cache write failed: {e}")


def _fetch_nse_holidays() -> List[str]:
    """Fetch holiday list from NSE API. Returns list of 'YYYY-MM-DD' strings."""
    url = "https://www.nseindia.com/api/holiday-master?type=trading"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        holidays = []
        # NSE returns { "CM": [ {"tradingDate": "19-Jan-2026", ...}, ... ] }
        cm_list = data.get("CM", [])
        for item in cm_list:
            raw = item.get("tradingDate", "")
            try:
                dt = datetime.strptime(raw, "%d-%b-%Y")
                holidays.append(dt.strftime("%Y-%m-%d"))
            except Exception:
                pass

        logger.info(f"Fetched {len(holidays)} NSE holidays from API")
        return holidays

    except Exception as e:
        logger.warning(f"NSE holiday fetch failed: {e}")
        return []


def get_holidays() -> List[str]:
    """Return list of NSE holiday dates as 'YYYY-MM-DD' strings."""
    cached = _load_cache()
    if cached:
        return cached.get("holidays", HARDCODED_HOLIDAYS)

    remote = _fetch_nse_holidays()
    if remote:
        _save_cache(remote)
        return remote

    logger.warning("Using hardcoded holiday list")
    return HARDCODED_HOLIDAYS


def is_trading_day(check_date: date = None) -> bool:
    """Return True if given date (default: today) is an NSE trading day."""
    if check_date is None:
        check_date = date.today()

    # Weekend check
    if check_date.weekday() >= 5:  # Saturday=5, Sunday=6
        return False

    # Holiday check
    holidays = get_holidays()
    return check_date.isoformat() not in holidays


def get_last_trading_day(from_date: date = None) -> date:
    """Return the most recent NSE trading day on or before from_date."""
    if from_date is None:
        from_date = date.today()

    holidays = get_holidays()
    check = from_date
    for _ in range(14):  # look back up to 14 days
        if check.weekday() < 5 and check.isoformat() not in holidays:
            return check
        check -= timedelta(days=1)

    # Fallback — shouldn't happen
    return from_date - timedelta(days=1)


def get_data_freshness(data_date: date) -> Tuple[str, bool]:
    """
    Assess how fresh the price data is.
    Returns (message, is_stale) where is_stale=True means data is > 1 trading day old.
    """
    last_trading = get_last_trading_day()
    delta_days   = (last_trading - data_date).days

    if delta_days == 0:
        return "Data is current (latest trading session)", False
    elif delta_days == 1:
        return f"Data from {data_date} — 1 trading day old", False
    elif delta_days <= 3:
        return f"⚠ Data from {data_date} — {delta_days} trading days old (holiday period?)", True
    else:
        return f"⚠ STALE: Data from {data_date} — {delta_days} days old", True


def market_status_message() -> dict:
    """
    Return a dict describing today's market status.
    Used by main.py to decide whether to run or skip.
    """
    today   = date.today()
    trading = is_trading_day(today)
    last_td = get_last_trading_day(today)
    holidays= get_holidays()

    # Find next trading day
    nxt = today + timedelta(days=1)
    for _ in range(7):
        if nxt.weekday() < 5 and nxt.isoformat() not in holidays:
            break
        nxt += timedelta(days=1)

    return {
        "today":            today.isoformat(),
        "is_trading_day":   trading,
        "last_trading_day": last_td.isoformat(),
        "next_trading_day": nxt.isoformat(),
        "day_of_week":      today.strftime("%A"),
        "status_message":   (
            "NSE is OPEN today" if trading else
            f"NSE CLOSED today ({today.strftime('%A')}). Last trading day: {last_td}"
        ),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    status = market_status_message()
    print(json.dumps(status, indent=2))

    # Test a few dates
    test_dates = [
        date(2026, 1, 26),   # Republic Day — holiday
        date(2026, 6, 22),   # Today (Monday) — should be trading
        date(2026, 6, 20),   # Saturday — closed
        date(2026, 12, 25),  # Christmas — holiday
    ]
    print("\nDate checks:")
    for d in test_dates:
        print(f"  {d} ({d.strftime('%A'):9}): {'✅ Trading' if is_trading_day(d) else '❌ Closed'}")
