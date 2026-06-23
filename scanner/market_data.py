"""
market_data.py
Real-time Indian market intelligence beyond price data.

Fetches:
  1. FII/DII provisional flows (NSE daily)
  2. Bulk & block deals (NSE)
  3. Promoter buying/selling (NSE insider activity)
  4. Options data — PCR, max pain, OI buildup by strike
  5. Global macro — US futures, DXY, crude, SGX Nifty, US 10Y yield
  6. Liquidity data — average daily turnover per stock

All sources are public NSE/BSE/Yahoo Finance endpoints.
"""

import json
import logging
import time
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent.parent / "data"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
}

# NSE session for authenticated endpoints
_nse_opener = None
_nse_ok     = False


def _init_nse():
    global _nse_opener, _nse_ok
    if _nse_ok:
        return True
    try:
        import http.cookiejar
        jar    = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        req    = urllib.request.Request("https://www.nseindia.com", headers=HEADERS)
        with opener.open(req, timeout=12):
            pass
        time.sleep(1.0)
        _nse_opener = opener
        _nse_ok     = True
        return True
    except Exception as e:
        logger.warning(f"NSE session failed: {e}")
        return False


def _nse_fetch(url: str) -> Optional[Dict]:
    if not _init_nse():
        return None
    try:
        req = urllib.request.Request(url, headers={
            **HEADERS,
            "X-Requested-With": "XMLHttpRequest",
        })
        with _nse_opener.open(req, timeout=12) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.debug(f"NSE fetch failed {url}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 1. FII / DII FLOWS
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_fii_dii() -> Dict:
    """
    Fetch FII/DII provisional net investment data from NSE.
    Returns today's net buy/sell and 5-day trend.
    """
    data = _nse_fetch("https://www.nseindia.com/api/fiidiiTradeReact")
    if not data:
        return _fii_dii_fallback()

    try:
        entries = data if isinstance(data, list) else data.get("data", [])
        today   = {}
        history = []

        for entry in entries[:10]:
            date_str = entry.get("date", "")
            fii_net  = float(str(entry.get("fiiNetActivity", 0)).replace(",", "") or 0)
            dii_net  = float(str(entry.get("diiNetActivity", 0)).replace(",", "") or 0)
            history.append({"date": date_str, "fii": fii_net, "dii": dii_net})

        if history:
            today = history[0]

        # Consecutive selling/buying streak
        fii_streak = 0
        for h in history:
            if today.get("fii", 0) < 0 and h["fii"] < 0:
                fii_streak -= 1
            elif today.get("fii", 0) > 0 and h["fii"] > 0:
                fii_streak += 1
            else:
                break

        fii_net = today.get("fii", 0)
        dii_net = today.get("dii", 0)

        # Signal
        if fii_net > 2000:
            fii_signal = "Strong Buying"
            fii_score  = 2
        elif fii_net > 500:
            fii_signal = "Mild Buying"
            fii_score  = 1
        elif fii_net < -2000:
            fii_signal = "Heavy Selling"
            fii_score  = -2
        elif fii_net < -500:
            fii_signal = "Mild Selling"
            fii_score  = -1
        else:
            fii_signal = "Neutral"
            fii_score  = 0

        return {
            "fii_net_cr":    round(fii_net, 1),
            "dii_net_cr":    round(dii_net, 1),
            "fii_signal":    fii_signal,
            "fii_score":     fii_score,
            "fii_streak":    fii_streak,
            "history":       history[:5],
            "summary":       (
                f"FII {'bought' if fii_net > 0 else 'sold'} ₹{abs(fii_net):.0f}Cr | "
                f"DII {'bought' if dii_net > 0 else 'sold'} ₹{abs(dii_net):.0f}Cr | "
                f"FII streak: {fii_streak} days"
            ),
        }
    except Exception as e:
        logger.warning(f"FII/DII parse failed: {e}")
        return _fii_dii_fallback()


def _fii_dii_fallback() -> Dict:
    return {
        "fii_net_cr": 0, "dii_net_cr": 0,
        "fii_signal": "Data unavailable", "fii_score": 0,
        "fii_streak": 0, "history": [],
        "summary": "FII/DII data unavailable",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. BULK & BLOCK DEALS
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_bulk_deals(tickers: List[str]) -> Dict[str, List[Dict]]:
    """
    Fetch bulk deals for a list of tickers from NSE.
    Returns {ticker: [deal_dict, ...]}
    Bulk deals > 0.5% of listed shares = significant institutional activity.
    """
    data = _nse_fetch("https://www.nseindia.com/api/snapshot-capital-market-largeDeals")
    result = {t: [] for t in tickers}

    if not data:
        return result

    try:
        deals = data if isinstance(data, list) else data.get("data", [])
        ticker_set = {t.upper() for t in tickers}

        for deal in deals:
            sym = deal.get("symbol", "").upper()
            if sym not in ticker_set:
                continue

            qty    = float(str(deal.get("quantity", 0)).replace(",", "") or 0)
            price  = float(str(deal.get("price", 0)).replace(",", "") or 0)
            value  = qty * price / 1e7   # in Crores
            client = deal.get("clientName", "")
            buy_sell = deal.get("buySell", "")

            result[sym].append({
                "date":       deal.get("date", ""),
                "client":     client,
                "buy_sell":   buy_sell,
                "quantity":   int(qty),
                "price":      round(price, 2),
                "value_cr":   round(value, 2),
                "is_institutional": any(word in client.upper() for word in
                    ["MUTUAL FUND", "MF", "FII", "FPI", "INSURANCE", "BANK",
                     "LIC", "FUND", "SECURITIES", "CAPITAL", "ASSET"]),
            })

    except Exception as e:
        logger.debug(f"Bulk deals parse failed: {e}")

    return result


def fetch_block_deals(tickers: List[str]) -> Dict[str, List[Dict]]:
    """Fetch block deals (large negotiated trades) for tickers."""
    data = _nse_fetch("https://www.nseindia.com/api/snapshot-capital-market-blockDeals")
    result = {t: [] for t in tickers}

    if not data:
        return result

    try:
        deals = data if isinstance(data, list) else data.get("data", [])
        ticker_set = {t.upper() for t in tickers}

        for deal in deals:
            sym = deal.get("symbol", "").upper()
            if sym not in ticker_set:
                continue

            qty   = float(str(deal.get("quantity", 0)).replace(",", "") or 0)
            price = float(str(deal.get("price", 0)).replace(",", "") or 0)

            result[sym].append({
                "date":      deal.get("date", ""),
                "client":    deal.get("clientName", ""),
                "buy_sell":  deal.get("buySell", ""),
                "quantity":  int(qty),
                "price":     round(price, 2),
                "value_cr":  round(qty * price / 1e7, 2),
            })

    except Exception as e:
        logger.debug(f"Block deals parse failed: {e}")

    return result


def get_deal_signal(ticker: str, bulk: List[Dict], block: List[Dict]) -> Dict:
    """Summarise deal activity for a ticker."""
    all_deals = bulk + block
    if not all_deals:
        return {"signal": "No deals", "score": 0, "description": ""}

    institutional_buys  = [d for d in all_deals if d.get("buy_sell","").upper() == "B" and d.get("is_institutional")]
    institutional_sells = [d for d in all_deals if d.get("buy_sell","").upper() == "S" and d.get("is_institutional")]
    total_buy_value  = sum(d["value_cr"] for d in all_deals if d.get("buy_sell","").upper() == "B")
    total_sell_value = sum(d["value_cr"] for d in all_deals if d.get("buy_sell","").upper() == "S")

    if institutional_buys and not institutional_sells:
        return {"signal": "Institutional Accumulation", "score": 2,
                "description": f"₹{total_buy_value:.0f}Cr institutional buying in bulk/block deals"}
    elif institutional_sells and not institutional_buys:
        return {"signal": "Institutional Distribution", "score": -2,
                "description": f"₹{total_sell_value:.0f}Cr institutional selling"}
    elif total_buy_value > total_sell_value * 1.5:
        return {"signal": "Net Buying", "score": 1,
                "description": f"Net ₹{total_buy_value - total_sell_value:.0f}Cr buying"}
    elif total_sell_value > total_buy_value * 1.5:
        return {"signal": "Net Selling", "score": -1,
                "description": f"Net ₹{total_sell_value - total_buy_value:.0f}Cr selling"}
    else:
        return {"signal": "Mixed Activity", "score": 0,
                "description": "Mixed bulk/block deal activity"}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PROMOTER BUYING / INSIDER ACTIVITY
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_insider_trades(ticker: str) -> List[Dict]:
    """
    Fetch recent insider/promoter trades from NSE SAST filings.
    Promoter buying is one of the strongest bullish signals available.
    """
    url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={ticker}&subject=Insider%20Trading%20Disclosures"
    data = _nse_fetch(url)
    if not data:
        return []

    trades = []
    try:
        items = data if isinstance(data, list) else data.get("data", [])
        for item in items[:10]:
            desc = item.get("desc", "") or item.get("attchmntText", "")
            date_str = item.get("an_dt", "")[:10]

            # Classify as buy/sell from description text
            desc_lower = desc.lower()
            if any(w in desc_lower for w in ["acquired", "purchase", "bought", "increase"]):
                action = "BUY"
                score  = 2
            elif any(w in desc_lower for w in ["sold", "disposal", "decrease", "pledged"]):
                action = "SELL"
                score  = -2
            else:
                action = "DISCLOSURE"
                score  = 0

            trades.append({
                "date":        date_str,
                "description": desc[:200],
                "action":      action,
                "score":       score,
            })
    except Exception as e:
        logger.debug(f"Insider trade parse failed for {ticker}: {e}")

    return trades[:5]


def get_insider_signal(trades: List[Dict]) -> Dict:
    if not trades:
        return {"signal": "No recent filings", "score": 0}

    recent = trades[:3]
    buy_count  = sum(1 for t in recent if t["action"] == "BUY")
    sell_count = sum(1 for t in recent if t["action"] == "SELL")

    if buy_count >= 2:
        return {"signal": "Promoter Accumulation", "score": 2,
                "description": f"{buy_count} recent insider buy filings"}
    elif sell_count >= 2:
        return {"signal": "Promoter Distribution", "score": -2,
                "description": f"{sell_count} recent insider sell filings"}
    elif buy_count > sell_count:
        return {"signal": "Mild Insider Buying", "score": 1,
                "description": "Recent insider purchase disclosures"}
    else:
        return {"signal": "Neutral", "score": 0,
                "description": "No clear insider directional activity"}


# ═══════════════════════════════════════════════════════════════════════════════
# 4. OPTIONS DATA — PCR, MAX PAIN, OI BUILDUP
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_options_data(ticker: str) -> Dict:
    """
    Fetch options chain data from NSE.
    Computes: PCR, max pain level, significant OI strike buildup.
    """
    url = f"https://www.nseindia.com/api/option-chain-equities?symbol={ticker}"
    data = _nse_fetch(url)

    if not data:
        return _options_fallback()

    try:
        records = data.get("records", {})
        data_list = records.get("data", [])
        underlying = records.get("underlyingValue", 0)

        call_oi_total = 0
        put_oi_total  = 0
        strikes_data  = {}

        for item in data_list:
            strike = item.get("strikePrice", 0)
            ce = item.get("CE", {})
            pe = item.get("PE", {})

            ce_oi = ce.get("openInterest", 0) if ce else 0
            pe_oi = pe.get("openInterest", 0) if pe else 0

            call_oi_total += ce_oi
            put_oi_total  += pe_oi

            if strike not in strikes_data:
                strikes_data[strike] = {"call_oi": 0, "put_oi": 0}
            strikes_data[strike]["call_oi"] += ce_oi
            strikes_data[strike]["put_oi"]  += pe_oi

        # PCR
        pcr = round(put_oi_total / max(call_oi_total, 1), 2)

        # PCR signal
        if pcr > 1.5:
            pcr_signal = "Extremely Bearish Positioning (contrarian bullish)"
            pcr_score  = 1   # extreme fear = contrarian buy
        elif pcr > 1.2:
            pcr_signal = "Bearish Positioning"
            pcr_score  = 0
        elif pcr < 0.5:
            pcr_signal = "Extremely Bullish Positioning (contrarian bearish)"
            pcr_score  = -1  # extreme greed = contrarian sell
        elif pcr < 0.8:
            pcr_signal = "Bullish Positioning"
            pcr_score  = 0
        else:
            pcr_signal = "Neutral"
            pcr_score  = 0

        # Max pain — strike where most options expire worthless
        # (minimises total payout to buyers)
        max_pain_strike = None
        min_pain_value  = float("inf")
        for strike, oi in strikes_data.items():
            pain = sum(
                max(0, (s - strike)) * v["call_oi"] +
                max(0, (strike - s)) * v["put_oi"]
                for s, v in strikes_data.items()
            )
            if pain < min_pain_value:
                min_pain_value  = pain
                max_pain_strike = strike

        # Key resistance (highest call OI) and support (highest put OI)
        if strikes_data:
            call_resistance = max(strikes_data, key=lambda s: strikes_data[s]["call_oi"])
            put_support     = max(strikes_data, key=lambda s: strikes_data[s]["put_oi"])
        else:
            call_resistance = put_support = None

        return {
            "pcr":              pcr,
            "pcr_signal":       pcr_signal,
            "pcr_score":        pcr_score,
            "max_pain":         max_pain_strike,
            "call_resistance":  call_resistance,
            "put_support":      put_support,
            "underlying":       underlying,
            "call_oi_total":    call_oi_total,
            "put_oi_total":     put_oi_total,
            "description": (
                f"PCR {pcr} ({pcr_signal}) | "
                f"Max pain ₹{max_pain_strike} | "
                f"Resistance ₹{call_resistance} | Support ₹{put_support}"
            ),
        }

    except Exception as e:
        logger.debug(f"Options data parse failed for {ticker}: {e}")
        return _options_fallback()


def _options_fallback() -> Dict:
    return {
        "pcr": None, "pcr_signal": "Data unavailable",
        "pcr_score": 0, "max_pain": None,
        "call_resistance": None, "put_support": None,
        "description": "Options data unavailable (not all stocks have F&O)",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 5. GLOBAL MACRO
# ═══════════════════════════════════════════════════════════════════════════════

MACRO_SYMBOLS = {
    "sp500":      "^GSPC",      # S&P 500
    "nasdaq":     "^IXIC",      # Nasdaq
    "dxy":        "DX-Y.NYB",   # US Dollar Index
    "crude_wti":  "CL=F",       # Crude Oil WTI
    "gold":       "GC=F",       # Gold
    "us10y":      "^TNX",       # US 10Y Treasury Yield
    "sgx_nifty":  "^NSEI",      # Use Nifty as proxy (SGX not on yfinance)
    "vix_us":     "^VIX",       # US VIX
}


def fetch_global_macro() -> Dict:
    """
    Fetch global macro indicators relevant to Indian swing traders.
    Uses yfinance for simplicity — all these symbols are available.
    """
    macro = {}

    for name, symbol in MACRO_SYMBOLS.items():
        try:
            df = yf.download(symbol, period="5d", interval="1d",
                             progress=False, auto_adjust=True)
            if df is None or len(df) < 2:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]

            current = float(df["Close"].iloc[-1])
            prev    = float(df["Close"].iloc[-2])
            chg_pct = ((current - prev) / prev) * 100

            macro[name] = {
                "value":   round(current, 2),
                "chg_pct": round(chg_pct, 2),
            }
            time.sleep(0.2)
        except Exception as e:
            logger.debug(f"Macro fetch failed {name}/{symbol}: {e}")

    return _interpret_macro(macro)


def _interpret_macro(macro: Dict) -> Dict:
    """Add signals and risk score to raw macro data."""
    risk_score = 0
    signals    = []

    sp500   = macro.get("sp500", {})
    dxy     = macro.get("dxy", {})
    crude   = macro.get("crude_wti", {})
    us10y   = macro.get("us10y", {})
    vix_us  = macro.get("vix_us", {})

    # S&P 500 direction
    sp_chg = sp500.get("chg_pct", 0)
    if sp_chg < -1.5:
        risk_score -= 2
        signals.append(f"S&P 500 down {sp_chg:.1f}% — expect gap-down open")
    elif sp_chg < -0.5:
        risk_score -= 1
        signals.append(f"S&P 500 weak ({sp_chg:.1f}%)")
    elif sp_chg > 1.0:
        risk_score += 1
        signals.append(f"S&P 500 strong +{sp_chg:.1f}% — positive for FII flows")

    # DXY (strong dollar = FII outflows from India)
    dxy_val = dxy.get("value", 100)
    dxy_chg = dxy.get("chg_pct", 0)
    if dxy_val > 105:
        risk_score -= 1
        signals.append(f"DXY at {dxy_val:.1f} — strong dollar pressures FII inflows")
    elif dxy_val < 100:
        risk_score += 1
        signals.append(f"DXY at {dxy_val:.1f} — weak dollar supports emerging markets")
    if dxy_chg > 0.5:
        risk_score -= 1
        signals.append(f"Dollar strengthening ({dxy_chg:+.1f}%) — near-term headwind")

    # Crude (India is a net importer — high crude = bad for CAD)
    crude_val = crude.get("value", 80)
    if crude_val > 90:
        risk_score -= 1
        signals.append(f"Crude at ${crude_val:.0f} — elevated, pressures India CAD")
    elif crude_val < 70:
        risk_score += 1
        signals.append(f"Crude at ${crude_val:.0f} — low, positive for India macro")

    # US 10Y yield (high yield = risk-off for EMs)
    yield_val = us10y.get("value", 4.0)
    if yield_val > 4.5:
        risk_score -= 1
        signals.append(f"US 10Y at {yield_val:.2f}% — high, competes with EM equities")

    # US VIX
    vix_val = vix_us.get("value", 15)
    if vix_val > 25:
        risk_score -= 2
        signals.append(f"US VIX at {vix_val:.0f} — elevated fear, risk-off globally")
    elif vix_val > 20:
        risk_score -= 1
        signals.append(f"US VIX at {vix_val:.0f} — moderate anxiety")

    # Overall risk environment
    if risk_score >= 2:
        env = "Risk-On"
        env_color = "#00c853"
    elif risk_score >= 0:
        env = "Neutral"
        env_color = "#ffd600"
    elif risk_score >= -2:
        env = "Cautious"
        env_color = "#ff6d00"
    else:
        env = "Risk-Off"
        env_color = "#ff1744"

    return {
        "raw":        macro,
        "risk_score": risk_score,
        "environment":env,
        "env_color":  env_color,
        "signals":    signals,
        "sp500":      sp500,
        "dxy":        dxy,
        "crude":      crude,
        "us10y":      us10y,
        "vix_us":     vix_us,
        "sgx_nifty":  macro.get("sgx_nifty", {}),
        "summary": (
            f"Global: {env} | "
            f"S&P {sp_chg:+.1f}% | "
            f"DXY {dxy_val:.1f} | "
            f"Crude ${crude_val:.0f} | "
            f"US10Y {yield_val:.2f}%"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 6. LIQUIDITY FILTER
# ═══════════════════════════════════════════════════════════════════════════════

LIQUIDITY_CACHE_PATH = CACHE_DIR / "liquidity_cache.json"
LIQUIDITY_CACHE_TTL  = 24   # hours


def _load_liquidity_cache() -> Dict:
    if not LIQUIDITY_CACHE_PATH.exists():
        return {}
    try:
        with open(LIQUIDITY_CACHE_PATH) as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
        if (datetime.now() - cached_at).total_seconds() / 3600 < LIQUIDITY_CACHE_TTL:
            return data.get("stocks", {})
    except Exception:
        pass
    return {}


def _save_liquidity_cache(stocks: Dict) -> None:
    LIQUIDITY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(LIQUIDITY_CACHE_PATH, "w") as f:
            json.dump({"cached_at": datetime.now().isoformat(), "stocks": stocks}, f)
    except Exception as e:
        logger.warning(f"Liquidity cache save failed: {e}")


def check_liquidity(
    tickers: List[str],
    min_avg_turnover_cr: float = 5.0,   # ₹5Cr avg daily turnover
    min_avg_volume: int = 100000,        # 1 lakh shares/day
) -> Dict[str, Dict]:
    """
    Check liquidity for each ticker.
    Returns {ticker: {liquid: bool, avg_turnover_cr, avg_volume, reason}}
    Uses cached data where available.
    """
    cache    = _load_liquidity_cache()
    result   = {}
    to_fetch = []

    for ticker in tickers:
        if ticker in cache:
            result[ticker] = cache[ticker]
        else:
            to_fetch.append(ticker)

    # Fetch missing tickers
    for ticker in to_fetch:
        try:
            df = yf.download(f"{ticker}.NS", period="30d", interval="1d",
                             progress=False, auto_adjust=True)
            if df is None or len(df) < 5:
                result[ticker] = {"liquid": False, "avg_turnover_cr": 0,
                                  "avg_volume": 0, "reason": "No price data"}
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]

            avg_vol   = float(df["Volume"].mean())
            avg_close = float(df["Close"].mean())
            avg_turn  = (avg_vol * avg_close) / 1e7   # in Crores

            is_liquid = avg_turn >= min_avg_turnover_cr and avg_vol >= min_avg_volume
            reason    = ""
            if not is_liquid:
                if avg_turn < min_avg_turnover_cr:
                    reason = f"Low turnover ₹{avg_turn:.1f}Cr/day (min ₹{min_avg_turnover_cr}Cr)"
                else:
                    reason = f"Low volume {avg_vol:.0f}/day (min {min_avg_volume:,})"

            liq_data = {
                "liquid":          is_liquid,
                "avg_turnover_cr": round(avg_turn, 1),
                "avg_volume":      int(avg_vol),
                "reason":          reason,
            }
            result[ticker]  = liq_data
            cache[ticker]   = liq_data
            time.sleep(0.5)

        except Exception as e:
            logger.debug(f"Liquidity check failed {ticker}: {e}")
            result[ticker] = {"liquid": True, "avg_turnover_cr": 0,
                              "avg_volume": 0, "reason": "Check failed — assuming liquid"}

    _save_liquidity_cache(cache)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 7. EVENT CALENDAR
# ═══════════════════════════════════════════════════════════════════════════════

# Key global events that affect Indian markets
# Format: {YYYY-MM-DD: "Event description"}
KNOWN_GLOBAL_EVENTS = {
    # RBI MPC meetings (approx dates — update quarterly)
    "2026-08-05": "RBI MPC Decision",
    "2026-10-08": "RBI MPC Decision",
    "2026-12-04": "RBI MPC Decision",
    # US Fed meetings
    "2026-07-28": "US FOMC Meeting",
    "2026-09-16": "US FOMC Meeting",
    "2026-11-04": "US FOMC Meeting",
    "2026-12-15": "US FOMC Meeting",
    # Indian budget (Feb 1 typically)
    "2027-02-01": "Union Budget",
}


def get_upcoming_events(days_ahead: int = 14) -> List[Dict]:
    """Return known market events in the next N days."""
    today    = date.today()
    upcoming = []

    for date_str, event in KNOWN_GLOBAL_EVENTS.items():
        try:
            event_date = date.fromisoformat(date_str)
            days_away  = (event_date - today).days
            if 0 <= days_away <= days_ahead:
                upcoming.append({
                    "date":      date_str,
                    "event":     event,
                    "days_away": days_away,
                    "urgent":    days_away <= 3,
                })
        except Exception:
            pass

    return sorted(upcoming, key=lambda x: x["days_away"])


def event_risk_score(upcoming_events: List[Dict]) -> Tuple[int, str]:
    """
    Score event risk for the next 14 days.
    Returns (score_adjustment, description).
    """
    if not upcoming_events:
        return 0, "No major events in next 14 days"

    urgent = [e for e in upcoming_events if e["days_away"] <= 3]
    soon   = [e for e in upcoming_events if 4 <= e["days_away"] <= 7]

    if urgent:
        return -1, f"⚠ {urgent[0]['event']} in {urgent[0]['days_away']} days — reduce position size"
    elif soon:
        return 0, f"📅 {soon[0]['event']} in {soon[0]['days_away']} days — be cautious with size"
    else:
        return 0, f"Next event: {upcoming_events[0]['event']} in {upcoming_events[0]['days_away']} days"


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER FETCH — call once per run
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_all_market_data(tickers: List[str]) -> Dict:
    """
    Fetch all market intelligence in one call.
    Returns comprehensive market data dict consumed by main pipeline.
    """
    logger.info("Fetching market intelligence data...")
    _init_nse()

    # Global macro
    logger.info("  → Global macro...")
    global_macro = fetch_global_macro()
    logger.info(f"     {global_macro['summary']}")

    # FII/DII
    logger.info("  → FII/DII flows...")
    fii_dii = fetch_fii_dii()
    logger.info(f"     {fii_dii['summary']}")

    # Bulk/block deals
    logger.info("  → Bulk/block deals...")
    bulk_deals  = fetch_bulk_deals(tickers)
    block_deals = fetch_block_deals(tickers)

    # Event calendar
    upcoming_events = get_upcoming_events()
    event_score, event_desc = event_risk_score(upcoming_events)

    # Liquidity check
    logger.info("  → Liquidity check...")
    liquidity = check_liquidity(tickers)
    liquid_count   = sum(1 for v in liquidity.values() if v.get("liquid"))
    illiquid_count = len(liquidity) - liquid_count
    if illiquid_count > 0:
        logger.info(f"     {illiquid_count} illiquid stocks will be filtered")

    # Per-ticker options data (only for F&O stocks, skip others gracefully)
    logger.info("  → Options chain data...")
    options = {}
    for ticker in tickers[:10]:   # limit to avoid timeout
        options[ticker] = fetch_options_data(ticker)
        time.sleep(0.3)

    logger.info("✅ Market intelligence fetch complete")

    return {
        "global_macro":     global_macro,
        "fii_dii":          fii_dii,
        "bulk_deals":       bulk_deals,
        "block_deals":      block_deals,
        "liquidity":        liquidity,
        "options":          options,
        "upcoming_events":  upcoming_events,
        "event_score":      event_score,
        "event_desc":       event_desc,
        "overall_risk_score": global_macro["risk_score"] + fii_dii["fii_score"] + event_score,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import json

    # Test global macro (works without NSE session)
    print("=== GLOBAL MACRO ===")
    macro = fetch_global_macro()
    print(f"Environment: {macro['environment']} (score={macro['risk_score']})")
    print(f"Summary: {macro['summary']}")
    for sig in macro['signals']:
        print(f"  → {sig}")

    # Test event calendar
    print("\n=== UPCOMING EVENTS ===")
    events = get_upcoming_events()
    for e in events:
        print(f"  {e['date']} ({e['days_away']}d): {e['event']} {'⚠ URGENT' if e['urgent'] else ''}")

    # Test liquidity (needs yfinance)
    print("\n=== LIQUIDITY CHECK ===")
    test_tickers = ["WABAG", "CLEANMAX", "RELIANCE", "TATAMOTORS"]
    liq = check_liquidity(test_tickers, min_avg_turnover_cr=5.0)
    for ticker, data in liq.items():
        status = "✅ LIQUID" if data["liquid"] else "❌ ILLIQUID"
        print(f"  {ticker:15} {status} | ₹{data['avg_turnover_cr']:.1f}Cr/day | {data.get('reason','')}")
