"""
market_data.py v2
Multi-source Indian market intelligence with fallback chains.

Source priority per data type:
  FII/DII:      NSE → Trendlyne → MoneyControl → yfinance India ETF proxy
  Bulk Deals:   NSE → BSE → Screener.in → yfinance institutional_holders
  Options:      NSE → yfinance options chain
  Insider:      NSE → yfinance insider_transactions
  Global Macro: yfinance (primary) → investing.com RSS
  Sector:       NSE → yfinance sector ETFs
  Liquidity:    yfinance (primary, works reliably)
  Events:       Hardcoded + Google Calendar RSS

Each fetch function tries sources in order, returns data from whichever works.
"""

import json
import logging
import re
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

# ── Shared headers ─────────────────────────────────────────────────────────────
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
}

BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0",
    "Accept": "application/json",
    "Referer": "https://www.bseindia.com/",
}

GENERIC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml,application/json,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── NSE session (shared, initialised once) ────────────────────────────────────
_nse_opener = None
_nse_ok     = False
_nse_failed = False   # circuit breaker: once NSE blocks this runner, stop retrying


def _init_nse() -> bool:
    global _nse_opener, _nse_ok, _nse_failed
    if _nse_ok:
        return True
    if _nse_failed:
        return False   # already failed this run — don't hammer NSE ~20x (datacenter IPs stay blocked)
    try:
        import http.cookiejar
        jar    = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        req    = urllib.request.Request("https://www.nseindia.com", headers=NSE_HEADERS)
        with opener.open(req, timeout=12):
            pass
        time.sleep(1.0)
        _nse_opener = opener
        _nse_ok     = True
        logger.debug("NSE session established")
        return True
    except Exception as e:
        _nse_failed = True
        logger.warning(f"NSE unreachable — disabling NSE sources for this run ({e})")
        return False


def _nse_fetch(url: str, timeout: int = 12) -> Optional[Dict]:
    if not _init_nse():
        return None
    try:
        req = urllib.request.Request(url, headers=NSE_HEADERS)
        with _nse_opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.debug(f"NSE fetch failed {url}: {e}")
        return None


def _generic_fetch(url: str, headers: Dict = None, timeout: int = 10) -> Optional[bytes]:
    """Generic HTTP fetch returning raw bytes."""
    try:
        req = urllib.request.Request(url, headers=headers or GENERIC_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        logger.debug(f"Fetch failed {url}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 1. FII / DII FLOWS
# Sources: NSE → Trendlyne scrape → MoneyControl RSS → yfinance ETF proxy
# ═══════════════════════════════════════════════════════════════════════════════

def _fii_dii_from_nse() -> Optional[Dict]:
    """NSE official FII/DII data."""
    data = _nse_fetch("https://www.nseindia.com/api/fiidiiTradeReact")
    if not data:
        return None
    try:
        entries = data if isinstance(data, list) else data.get("data", [])
        if not entries:
            return None
        today = entries[0]
        fii_net = float(str(today.get("fiiNetActivity", 0)).replace(",", "") or 0)
        dii_net = float(str(today.get("diiNetActivity", 0)).replace(",", "") or 0)
        history = []
        for e in entries[:5]:
            fn = float(str(e.get("fiiNetActivity", 0)).replace(",", "") or 0)
            dn = float(str(e.get("diiNetActivity", 0)).replace(",", "") or 0)
            history.append({"date": e.get("date", ""), "fii": fn, "dii": dn})
        return {"fii_net_cr": round(fii_net, 1), "dii_net_cr": round(dii_net, 1),
                "history": history, "source": "NSE"}
    except Exception as e:
        logger.debug(f"NSE FII parse failed: {e}")
        return None


def _fii_dii_from_trendlyne() -> Optional[Dict]:
    """Scrape FII/DII from Trendlyne public page."""
    try:
        raw = _generic_fetch(
            "https://trendlyne.com/data-bank/fii-dii-data/",
            headers={**GENERIC_HEADERS, "Referer": "https://trendlyne.com/"},
            timeout=12,
        )
        if not raw:
            return None
        text = raw.decode("utf-8", errors="ignore")
        # Look for FII net data patterns in the HTML
        fii_match = re.search(r'FII.*?Net[^0-9-]*([+-]?\d[\d,\.]+)', text, re.IGNORECASE | re.DOTALL)
        dii_match = re.search(r'DII.*?Net[^0-9-]*([+-]?\d[\d,\.]+)', text, re.IGNORECASE | re.DOTALL)
        if not fii_match:
            return None
        fii_net = float(fii_match.group(1).replace(",", ""))
        dii_net = float(dii_match.group(1).replace(",", "")) if dii_match else 0
        return {"fii_net_cr": round(fii_net, 1), "dii_net_cr": round(dii_net, 1),
                "history": [], "source": "Trendlyne"}
    except Exception as e:
        logger.debug(f"Trendlyne FII failed: {e}")
        return None


def _fii_dii_from_moneycontrol() -> Optional[Dict]:
    """Scrape FII/DII from MoneyControl."""
    try:
        raw = _generic_fetch(
            "https://www.moneycontrol.com/stocks/marketinfo/fii_dii_activity.php",
            headers={**GENERIC_HEADERS, "Referer": "https://www.moneycontrol.com/"},
            timeout=12,
        )
        if not raw:
            return None
        text = raw.decode("utf-8", errors="ignore")
        # Extract net figures from table
        nets = re.findall(r'>\s*([+-]?\d[\d,]+\.\d+)\s*<', text)
        if len(nets) >= 2:
            fii_net = float(nets[0].replace(",", ""))
            dii_net = float(nets[1].replace(",", ""))
            return {"fii_net_cr": round(fii_net / 10, 1),  # MC shows in lakhs
                    "dii_net_cr": round(dii_net / 10, 1),
                    "history": [], "source": "MoneyControl"}
        return None
    except Exception as e:
        logger.debug(f"MoneyControl FII failed: {e}")
        return None


def _fii_dii_from_yfinance_proxy() -> Optional[Dict]:
    """
    Proxy FII/DII signal from India ETF flows.
    INDA (iShares India ETF) - tracks FII sentiment on India.
    Rising INDA on strong volume = FII inflows; falling = outflows.
    """
    try:
        inda = yf.download("INDA", period="5d", interval="1d",
                           progress=False, auto_adjust=True)
        if inda is None or len(inda) < 2:
            return None
        if isinstance(inda.columns, pd.MultiIndex):
            inda.columns = [c[0] for c in inda.columns]
        current = float(inda["Close"].iloc[-1])
        prev    = float(inda["Close"].iloc[-2])
        vol     = float(inda["Volume"].iloc[-1])
        vol_avg = float(inda["Volume"].mean())
        chg_pct = ((current - prev) / prev) * 100
        vol_ratio = vol / max(vol_avg, 1)

        # Estimate FII net from ETF move (rough proxy)
        # 1% INDA move ≈ ₹500Cr FII activity (calibrated estimate)
        fii_est = round(chg_pct * 500, 0)
        if vol_ratio < 0.5:
            fii_est *= 0.5   # low volume = less conviction

        return {
            "fii_net_cr":  fii_est,
            "dii_net_cr":  0,  # DII not estimable from ETF
            "estimated":     True,    # this is a directional guess from ETF flow, NOT real institutional data
            "dii_available": False,   # DII genuinely unknown from an ETF proxy — do not present ₹0 as fact
            "history":     [],
            "source":      "yfinance-INDA-proxy",
            "note":        f"INDA proxy: {chg_pct:+.2f}% on {vol_ratio:.1f}x volume (estimated ≈₹{fii_est:.0f}Cr)",
        }
    except Exception as e:
        logger.debug(f"yfinance FII proxy failed: {e}")
        return None


def fetch_fii_dii() -> Dict:
    """Fetch FII/DII with source fallback chain."""
    for fn in [_fii_dii_from_nse, _fii_dii_from_trendlyne,
               _fii_dii_from_moneycontrol, _fii_dii_from_yfinance_proxy]:
        try:
            result = fn()
            if result:
                fii_net = result["fii_net_cr"]
                dii_net = result["dii_net_cr"]
                source  = result.get("source", "?")

                # Compute streak from history
                history = result.get("history", [])
                streak  = 0
                for h in history:
                    if fii_net < 0 and h["fii"] < 0:
                        streak -= 1
                    elif fii_net > 0 and h["fii"] > 0:
                        streak += 1
                    else:
                        break

                estimated     = bool(result.get("estimated", False))
                dii_available = result.get("dii_available", True)

                # Signal — a directional ETF *guess* must not move the market read.
                if   estimated:        fii_sig, fii_score = "Estimated — low confidence", 0
                elif fii_net > 2000:   fii_sig, fii_score = "Strong Buying", 2
                elif fii_net > 500:  fii_sig, fii_score = "Mild Buying", 1
                elif fii_net < -2000:fii_sig, fii_score = "Heavy Selling", -2
                elif fii_net < -500: fii_sig, fii_score = "Mild Selling", -1
                else:                fii_sig, fii_score = "Neutral", 0

                fii_str = (f"FII ≈₹{abs(fii_net):.0f}Cr (est.)" if estimated
                           else f"FII {'bought' if fii_net>0 else 'sold'} ₹{abs(fii_net):.0f}Cr")
                dii_str = (f"DII {'bought' if dii_net>0 else 'sold'} ₹{abs(dii_net):.0f}Cr"
                           if dii_available else "DII unavailable")

                logger.info(
                    "  FII/DII [{}]: {}".format(
                        source,
                        f"≈₹{fii_net:+.0f}Cr FII (estimated) | DII unavailable" if estimated
                        else f"₹{fii_net:+.0f}Cr FII | ₹{dii_net:+.0f}Cr DII")
                )
                return {
                    "fii_net_cr":    round(fii_net, 1),
                    "dii_net_cr":    round(dii_net, 1),
                    "fii_signal":    fii_sig,
                    "fii_score":     fii_score,
                    "fii_streak":    streak,
                    "estimated":     estimated,
                    "dii_available": dii_available,
                    "history":       history,
                    "source":        source,
                    "note":          result.get("note", ""),
                    "summary":       f"{fii_str} | {dii_str} [{source}]",
                }
        except Exception as e:
            logger.debug(f"FII source failed: {e}")
            continue

    return _fii_dii_unavailable()


def _fii_dii_unavailable() -> Dict:
    return {"fii_net_cr": 0, "dii_net_cr": 0, "fii_signal": "Data unavailable",
            "fii_score": 0, "fii_streak": 0, "history": [], "source": "none",
            "summary": "FII/DII data unavailable from all sources"}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. BULK & BLOCK DEALS
# Sources: NSE → BSE → Screener.in → yfinance institutional_holders
# ═══════════════════════════════════════════════════════════════════════════════

def _bulk_deals_from_nse() -> Optional[List[Dict]]:
    data = _nse_fetch("https://www.nseindia.com/api/snapshot-capital-market-largeDeals")
    if not data:
        return None
    try:
        deals = data if isinstance(data, list) else data.get("data", [])
        return [{"symbol": d.get("symbol","").upper(), "date": d.get("date",""),
                 "client": d.get("clientName",""), "buy_sell": d.get("buySell",""),
                 "qty": int(float(str(d.get("quantity",0)).replace(",","") or 0)),
                 "price": float(str(d.get("price",0)).replace(",","") or 0),
                 "source": "NSE"} for d in deals if d.get("symbol")]
    except Exception as e:
        logger.debug(f"NSE bulk deals parse: {e}")
        return None


def _bulk_deals_from_bse() -> Optional[List[Dict]]:
    """BSE bulk deals from public API."""
    today = date.today().strftime("%Y%m%d")
    raw = _generic_fetch(
        f"https://api.bseindia.com/BseIndiaAPI/api/BulkDealData/w?quotetype=EQ&fromdate={today}&todate={today}",
        headers=BSE_HEADERS,
    )
    if not raw:
        return None
    try:
        data = json.loads(raw)
        deals = data if isinstance(data, list) else data.get("Table", [])
        return [{"symbol": d.get("SCRIP_CODE","") or d.get("ScrCode",""),
                 "date": d.get("TRADE_DATE",""), "client": d.get("CLIENT_NAME",""),
                 "buy_sell": d.get("BUY_SELL",""), "qty": int(d.get("QUANTITY",0) or 0),
                 "price": float(d.get("TRADE_PRICE",0) or 0),
                 "source": "BSE"} for d in deals if d.get("SCRIP_CODE") or d.get("ScrCode")]
    except Exception as e:
        logger.debug(f"BSE bulk deals parse: {e}")
        return None


def _bulk_deals_from_screener(ticker: str) -> Optional[List[Dict]]:
    """Screener.in bulk deals for a specific ticker."""
    raw = _generic_fetch(
        f"https://www.screener.in/api/company/{ticker}/",
        headers={**GENERIC_HEADERS, "Referer": "https://www.screener.in/"},
    )
    if not raw:
        return None
    try:
        data = json.loads(raw)
        bd = data.get("bulk_deals", [])
        return [{"symbol": ticker, "date": d.get("date",""),
                 "client": d.get("name",""), "buy_sell": d.get("type",""),
                 "qty": d.get("quantity", 0), "price": d.get("price", 0),
                 "source": "Screener"} for d in bd]
    except Exception as e:
        logger.debug(f"Screener bulk deals parse: {e}")
        return None


def _institutional_from_yfinance(ticker: str) -> Optional[Dict]:
    """
    yfinance institutional/major holders as a proxy for bulk deal signal.
    Not real-time bulk deals, but shows institutional ownership changes.
    """
    try:
        t = yf.Ticker(f"{ticker}.NS")
        inst = t.institutional_holders
        if inst is None or inst.empty:
            return None
        # Recent changes in institutional holding
        holders = inst.head(10).to_dict("records")
        total_pct = sum(float(h.get("% Out", 0)) for h in holders
                        if h.get("% Out") is not None)
        return {
            "symbol":     ticker,
            "source":     "yfinance-institutional",
            "total_inst_pct": round(total_pct, 1),
            "top_holders": holders[:5],
            "signal":     "High Institutional" if total_pct > 50 else "Moderate Institutional",
        }
    except Exception as e:
        logger.debug(f"yfinance institutional for {ticker}: {e}")
        return None


def fetch_bulk_deals(tickers: List[str]) -> Dict[str, List[Dict]]:
    """Fetch bulk deals with fallback chain."""
    result = {t: [] for t in tickers}
    ticker_set = {t.upper() for t in tickers}

    # Try NSE first (all tickers at once)
    nse_deals = _bulk_deals_from_nse()
    if nse_deals:
        for d in nse_deals:
            sym = d["symbol"].replace(".NS", "").replace("-EQ", "")
            if sym in ticker_set:
                result[sym].append(d)
        logger.debug(f"NSE bulk deals: {len(nse_deals)} total")
        return result

    # Try BSE
    bse_deals = _bulk_deals_from_bse()
    if bse_deals:
        for d in bse_deals:
            sym = str(d["symbol"]).upper()
            if sym in ticker_set:
                result[sym].append(d)
        logger.debug(f"BSE bulk deals: {len(bse_deals)} total")
        return result

    # Per-ticker Screener fallback
    for ticker in list(ticker_set)[:5]:
        deals = _bulk_deals_from_screener(ticker)
        if deals:
            result[ticker] = deals
        time.sleep(0.3)

    return result


def fetch_block_deals(tickers: List[str]) -> Dict[str, List[Dict]]:
    """Fetch block deals (NSE → BSE → empty)."""
    result = {t: [] for t in tickers}
    ticker_set = {t.upper() for t in tickers}

    data = _nse_fetch("https://www.nseindia.com/api/snapshot-capital-market-blockDeals")
    if data:
        deals = data if isinstance(data, list) else data.get("data", [])
        for d in deals:
            sym = d.get("symbol","").upper()
            if sym in ticker_set:
                result[sym].append({
                    "symbol": sym, "date": d.get("date",""),
                    "client": d.get("clientName",""), "buy_sell": d.get("buySell",""),
                    "qty": int(float(str(d.get("quantity",0)).replace(",","") or 0)),
                    "price": float(str(d.get("price",0)).replace(",","") or 0),
                    "source": "NSE",
                })
    return result


def get_deal_signal(ticker: str, bulk: List[Dict], block: List[Dict]) -> Dict:
    all_deals = bulk + block
    if not all_deals:
        return {"signal": "No deals", "score": 0, "description": ""}

    INSTITUTIONAL_KEYWORDS = ["MUTUAL FUND","MF","FII","FPI","INSURANCE","BANK",
                               "LIC","FUND","SECURITIES","CAPITAL","ASSET","HDFC","ICICI","AXIS"]
    inst_buys  = [d for d in all_deals if d.get("buy_sell","").upper() in ("B","BUY")
                  and any(k in d.get("client","").upper() for k in INSTITUTIONAL_KEYWORDS)]
    inst_sells = [d for d in all_deals if d.get("buy_sell","").upper() in ("S","SELL")
                  and any(k in d.get("client","").upper() for k in INSTITUTIONAL_KEYWORDS)]
    total_buy  = sum(d.get("qty",0) * d.get("price",0) / 1e7 for d in all_deals
                     if d.get("buy_sell","").upper() in ("B","BUY"))
    total_sell = sum(d.get("qty",0) * d.get("price",0) / 1e7 for d in all_deals
                     if d.get("buy_sell","").upper() in ("S","SELL"))

    if inst_buys and not inst_sells:
        return {"signal": "Institutional Accumulation", "score": 2,
                "description": f"₹{total_buy:.0f}Cr institutional buying in bulk/block deals"}
    elif inst_sells and not inst_buys:
        return {"signal": "Institutional Distribution", "score": -2,
                "description": f"₹{total_sell:.0f}Cr institutional selling"}
    elif total_buy > total_sell * 1.5:
        return {"signal": "Net Buying", "score": 1,
                "description": f"Net ₹{total_buy - total_sell:.0f}Cr buying"}
    elif total_sell > total_buy * 1.5:
        return {"signal": "Net Selling", "score": -1,
                "description": f"Net ₹{total_sell - total_buy:.0f}Cr selling"}
    return {"signal": "Mixed Activity", "score": 0,
            "description": "Mixed bulk/block deal activity"}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. INSIDER / PROMOTER TRADES
# Sources: NSE SAST → yfinance insider_transactions → Screener
# ═══════════════════════════════════════════════════════════════════════════════

def _insider_from_nse(ticker: str) -> Optional[List[Dict]]:
    url = (f"https://www.nseindia.com/api/corporate-announcements"
           f"?index=equities&symbol={ticker}&subject=Insider%20Trading%20Disclosures")
    data = _nse_fetch(url)
    if not data:
        return None
    try:
        items = data if isinstance(data, list) else data.get("data", [])
        trades = []
        for item in items[:10]:
            desc      = item.get("desc","") or item.get("attchmntText","")
            desc_low  = desc.lower()
            date_str  = item.get("an_dt","")[:10]
            if any(w in desc_low for w in ["acquired","purchase","bought","increase"]):
                action, score = "BUY", 2
            elif any(w in desc_low for w in ["sold","disposal","decrease","pledged"]):
                action, score = "SELL", -2
            else:
                action, score = "DISCLOSURE", 0
            trades.append({"date": date_str, "description": desc[:200],
                           "action": action, "score": score, "source": "NSE"})
        return trades[:5] if trades else None
    except Exception as e:
        logger.debug(f"NSE insider parse {ticker}: {e}")
        return None


def _insider_from_yfinance(ticker: str) -> Optional[List[Dict]]:
    """yfinance insider transactions as fallback."""
    try:
        t = yf.Ticker(f"{ticker}.NS")
        ins = t.insider_transactions
        if ins is None or ins.empty:
            return None
        trades = []
        for _, row in ins.head(5).iterrows():
            shares = row.get("Shares", 0) or 0
            action = "BUY" if shares > 0 else "SELL"
            score  = 2 if action == "BUY" else -2
            trades.append({
                "date":        str(row.get("Start Date", ""))[:10],
                "description": f"{row.get('Insider','')} {action} {abs(int(shares)):,} shares",
                "action":      action,
                "score":       score,
                "source":      "yfinance",
            })
        return trades if trades else None
    except Exception as e:
        logger.debug(f"yfinance insider {ticker}: {e}")
        return None


def _insider_from_screener(ticker: str) -> Optional[List[Dict]]:
    """Scrape insider trades from Screener.in."""
    try:
        raw = _generic_fetch(
            f"https://www.screener.in/company/{ticker}/",
            headers={**GENERIC_HEADERS, "Referer": "https://www.screener.in/"},
        )
        if not raw:
            return None
        text = raw.decode("utf-8", errors="ignore")
        # Look for insider trading section
        if "insider" not in text.lower() and "promoter" not in text.lower():
            return None
        # Extract promoter holding change
        promo_match = re.search(r'Promoter.*?(\d+\.?\d*)\s*%', text, re.IGNORECASE)
        if promo_match:
            pct = float(promo_match.group(1))
            return [{"date": date.today().isoformat(),
                     "description": f"Promoter holding: {pct:.1f}%",
                     "action": "DISCLOSURE", "score": 0, "source": "Screener"}]
        return None
    except Exception as e:
        logger.debug(f"Screener insider {ticker}: {e}")
        return None


def fetch_insider_trades(ticker: str) -> List[Dict]:
    """Fetch insider trades with fallback chain."""
    for fn in [lambda: _insider_from_nse(ticker),
               lambda: _insider_from_yfinance(ticker),
               lambda: _insider_from_screener(ticker)]:
        try:
            result = fn()
            if result:
                return result
        except Exception:
            continue
    return []


def get_insider_signal(trades: List[Dict]) -> Dict:
    if not trades:
        return {"signal": "No recent filings", "score": 0}
    recent = trades[:3]
    buys   = sum(1 for t in recent if t["action"] == "BUY")
    sells  = sum(1 for t in recent if t["action"] == "SELL")
    source = recent[0].get("source", "?") if recent else "?"
    if buys >= 2:
        return {"signal": "Promoter Accumulation", "score": 2, "source": source,
                "description": f"{buys} recent insider buy filings [{source}]"}
    elif sells >= 2:
        return {"signal": "Promoter Distribution", "score": -2, "source": source,
                "description": f"{sells} recent insider sell filings [{source}]"}
    elif buys > sells:
        return {"signal": "Mild Insider Buying", "score": 1, "source": source,
                "description": "Recent insider purchase disclosures"}
    return {"signal": "Neutral", "score": 0, "source": source}


# ═══════════════════════════════════════════════════════════════════════════════
# 4. OPTIONS DATA
# Sources: NSE options chain → yfinance options
# ═══════════════════════════════════════════════════════════════════════════════

def _options_from_nse(ticker: str) -> Optional[Dict]:
    data = _nse_fetch(f"https://www.nseindia.com/api/option-chain-equities?symbol={ticker}")
    if not data:
        return None
    try:
        records  = data.get("records", {})
        data_list= records.get("data", [])
        underlying = records.get("underlyingValue", 0)
        call_oi = put_oi = 0
        strikes = {}
        for item in data_list:
            strike = item.get("strikePrice", 0)
            ce = item.get("CE", {}) or {}
            pe = item.get("PE", {}) or {}
            coi = ce.get("openInterest", 0)
            poi = pe.get("openInterest", 0)
            call_oi += coi; put_oi += poi
            strikes[strike] = {"call_oi": strikes.get(strike,{}).get("call_oi",0) + coi,
                               "put_oi": strikes.get(strike,{}).get("put_oi",0) + poi}
        pcr = round(put_oi / max(call_oi, 1), 2)
        # Max pain
        max_pain = None
        min_pain = float("inf")
        for s, oi in strikes.items():
            pain = sum(max(0,(ss-s))*v["call_oi"] + max(0,(s-ss))*v["put_oi"]
                       for ss,v in strikes.items())
            if pain < min_pain:
                min_pain = pain; max_pain = s
        call_res = max(strikes, key=lambda x: strikes[x]["call_oi"]) if strikes else None
        put_sup  = max(strikes, key=lambda x: strikes[x]["put_oi"])  if strikes else None
        return {"pcr": pcr, "max_pain": max_pain, "call_resistance": call_res,
                "put_support": put_sup, "underlying": underlying, "source": "NSE"}
    except Exception as e:
        logger.debug(f"NSE options parse {ticker}: {e}")
        return None


def _options_from_yfinance(ticker: str) -> Optional[Dict]:
    """yfinance options chain as fallback."""
    try:
        t = yf.Ticker(f"{ticker}.NS")
        exps = t.options
        if not exps:
            return None
        # Use nearest expiry
        chain = t.option_chain(exps[0])
        calls = chain.calls
        puts  = chain.puts
        if calls.empty or puts.empty:
            return None
        call_oi_total = int(calls["openInterest"].sum())
        put_oi_total  = int(puts["openInterest"].sum())
        pcr = round(put_oi_total / max(call_oi_total, 1), 2)
        # Max call OI strike = resistance
        call_res = float(calls.loc[calls["openInterest"].idxmax(), "strike"]) if not calls.empty else None
        put_sup  = float(puts.loc[puts["openInterest"].idxmax(),  "strike"]) if not puts.empty else None
        return {"pcr": pcr, "max_pain": None, "call_resistance": call_res,
                "put_support": put_sup, "underlying": 0, "source": "yfinance"}
    except Exception as e:
        logger.debug(f"yfinance options {ticker}: {e}")
        return None


def fetch_options_data(ticker: str) -> Dict:
    """Fetch options data with fallback."""
    for fn in [lambda: _options_from_nse(ticker),
               lambda: _options_from_yfinance(ticker)]:
        try:
            result = fn()
            if result:
                pcr = result.get("pcr", 0)
                if pcr and pcr > 1.2:
                    pcr_signal, pcr_score = "Bearish Positioning", 0
                elif pcr and pcr < 0.8:
                    pcr_signal, pcr_score = "Bullish Positioning", 0
                else:
                    pcr_signal, pcr_score = "Neutral", 0
                result.update({
                    "pcr_signal": pcr_signal,
                    "pcr_score":  pcr_score,
                    "description": (
                        f"PCR {pcr} ({pcr_signal}) | "
                        f"Resistance ₹{result.get('call_resistance','?')} | "
                        f"Support ₹{result.get('put_support','?')}"
                        f" [{result.get('source','?')}]"
                    ),
                })
                return result
        except Exception:
            continue
    return {"pcr": None, "pcr_signal": "Data unavailable", "pcr_score": 0,
            "max_pain": None, "call_resistance": None, "put_support": None,
            "description": "Options data unavailable"}


# ═══════════════════════════════════════════════════════════════════════════════
# 5. GLOBAL MACRO
# Sources: yfinance (primary) → investing.com RSS → hardcoded fallback
# ═══════════════════════════════════════════════════════════════════════════════

MACRO_SYMBOLS = {
    "sp500":     "^GSPC",
    "nasdaq":    "^IXIC",
    "dxy":       "DX-Y.NYB",
    "crude_wti": "CL=F",
    "gold":      "GC=F",
    "us10y":     "^TNX",
    "nifty":     "^NSEI",
    "vix_us":    "^VIX",
    "sgx_nifty": "^NSEI",
}

# Investing.com RSS feeds as backup for macro data
INVESTING_RSS = {
    "sp500":     "https://www.investing.com/rss/news_25.rss",
    "crude_wti": "https://www.investing.com/rss/news_8.rss",
}


def _macro_from_yfinance() -> Dict:
    """Primary macro data source."""
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
            macro[name] = {
                "value":   round(current, 2),
                "chg_pct": round(((current - prev) / prev) * 100, 2),
                "source":  "yfinance",
            }
            time.sleep(0.15)
        except Exception as e:
            logger.debug(f"Macro {name}: {e}")
    return macro


def _macro_from_investing_rss() -> Dict:
    """Backup: parse investing.com RSS for market context."""
    macro = {}
    try:
        raw = _generic_fetch("https://www.investing.com/rss/news_25.rss")
        if raw:
            text = raw.decode("utf-8", errors="ignore")
            # Extract S&P 500 mentions from headlines
            sp_match = re.search(r'S&P 500.*?(\d[\d,]+\.?\d*)', text)
            if sp_match:
                macro["sp500_context"] = sp_match.group(0)[:100]
    except Exception as e:
        logger.debug(f"Investing RSS: {e}")
    return macro


def fetch_global_macro() -> Dict:
    """Fetch global macro with fallback."""
    macro = _macro_from_yfinance()

    # Enrich with RSS if yfinance missed any key metrics
    if not macro.get("sp500"):
        rss_data = _macro_from_investing_rss()
        macro.update(rss_data)

    return _interpret_macro(macro)


def _interpret_macro(macro: Dict) -> Dict:
    risk_score = 0
    signals    = []

    sp500  = macro.get("sp500",  {})
    dxy    = macro.get("dxy",    {})
    crude  = macro.get("crude_wti", {})
    us10y  = macro.get("us10y",  {})
    vix_us = macro.get("vix_us", {})

    sp_chg    = sp500.get("chg_pct", 0) or 0
    dxy_val   = dxy.get("value", 100) or 100
    dxy_chg   = dxy.get("chg_pct", 0) or 0
    crude_val = crude.get("value", 80) or 80
    yield_val = us10y.get("value", 4.0) or 4.0
    vix_val   = vix_us.get("value", 15) or 15

    if sp_chg < -1.5:   risk_score -= 2; signals.append(f"S&P 500 down {sp_chg:.1f}% — expect gap-down open")
    elif sp_chg < -0.5: risk_score -= 1; signals.append(f"S&P 500 weak ({sp_chg:.1f}%)")
    elif sp_chg > 1.0:  risk_score += 1; signals.append(f"S&P 500 strong +{sp_chg:.1f}%")

    if dxy_val > 105:   risk_score -= 1; signals.append(f"DXY {dxy_val:.1f} — strong dollar, FII headwind")
    elif dxy_val < 100: risk_score += 1; signals.append(f"DXY {dxy_val:.1f} — weak dollar, EM tailwind")
    if dxy_chg > 0.5:   risk_score -= 1; signals.append(f"Dollar strengthening {dxy_chg:+.1f}%")

    if crude_val > 90:  risk_score -= 1; signals.append(f"Crude ${crude_val:.0f} — elevated, India CAD pressure")
    elif crude_val < 70:risk_score += 1; signals.append(f"Crude ${crude_val:.0f} — low, India macro positive")

    if yield_val > 4.5: risk_score -= 1; signals.append(f"US 10Y {yield_val:.2f}% — high, EM risk-off")
    if vix_val > 25:    risk_score -= 2; signals.append(f"US VIX {vix_val:.0f} — fear, risk-off globally")
    elif vix_val > 20:  risk_score -= 1; signals.append(f"US VIX {vix_val:.0f} — moderate anxiety")

    env_map = {2: ("Risk-On","#00c853"), 1: ("Mildly Bullish","#69f0ae"),
               0: ("Neutral","#ffd600"), -1: ("Cautious","#ff6d00")}
    env, env_color = env_map.get(max(-2, min(2, risk_score)),
                                  ("Risk-Off","#ff1744") if risk_score < -1 else ("Neutral","#ffd600"))

    return {
        "raw":         macro,
        "risk_score":  risk_score,
        "environment": env,
        "env_color":   env_color,
        "signals":     signals,
        "sp500":       sp500,
        "dxy":         dxy,
        "crude":       crude,
        "us10y":       us10y,
        "vix_us":      vix_us,
        "sgx_nifty":   macro.get("sgx_nifty", {}),
        "summary": (
            f"Global: {env} | S&P {sp_chg:+.1f}% | "
            f"DXY {dxy_val:.1f} | Crude ${crude_val:.0f} | US10Y {yield_val:.2f}%"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 6. LIQUIDITY CHECK (yfinance — works reliably)
# ═══════════════════════════════════════════════════════════════════════════════

LIQUIDITY_CACHE = CACHE_DIR / "liquidity_cache.json"
LIQUIDITY_TTL   = 24  # hours


def _load_liq_cache() -> Dict:
    if not LIQUIDITY_CACHE.exists():
        return {}
    try:
        with open(LIQUIDITY_CACHE) as f:
            d = json.load(f)
        cached_at = datetime.fromisoformat(d.get("cached_at","2000-01-01"))
        if (datetime.now() - cached_at).total_seconds() / 3600 < LIQUIDITY_TTL:
            return d.get("stocks", {})
    except Exception:
        pass
    return {}


def _save_liq_cache(stocks: Dict) -> None:
    LIQUIDITY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(LIQUIDITY_CACHE, "w") as f:
            json.dump({"cached_at": datetime.now().isoformat(), "stocks": stocks}, f)
    except Exception:
        pass


def check_liquidity(tickers: List[str], min_turnover_cr: float = 5.0,
                    min_volume: int = 100000) -> Dict[str, Dict]:
    cache    = _load_liq_cache()
    result   = {}
    to_fetch = [t for t in tickers if t not in cache]

    for ticker in to_fetch:
        try:
            df = yf.download(f"{ticker}.NS", period="30d", interval="1d",
                             progress=False, auto_adjust=True)
            if df is None or len(df) < 5:
                result[ticker] = {"liquid": False, "avg_turnover_cr": 0,
                                  "avg_volume": 0, "reason": "No data"}
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]
            avg_vol  = float(df["Volume"].mean())
            avg_close= float(df["Close"].mean())
            avg_turn = (avg_vol * avg_close) / 1e7
            is_liq   = avg_turn >= min_turnover_cr and avg_vol >= min_volume
            reason   = "" if is_liq else (
                f"Low turnover ₹{avg_turn:.1f}Cr/day" if avg_turn < min_turnover_cr
                else f"Low volume {avg_vol:.0f}/day"
            )
            liq_data = {"liquid": is_liq, "avg_turnover_cr": round(avg_turn, 1),
                        "avg_volume": int(avg_vol), "reason": reason}
            result[ticker] = cache[ticker] = liq_data
            time.sleep(0.4)
        except Exception as e:
            result[ticker] = {"liquid": True, "avg_turnover_cr": 0,
                              "avg_volume": 0, "reason": f"Check failed: {e}"}

    # Merge cached
    for t in tickers:
        if t not in result and t in cache:
            result[t] = cache[t]

    _save_liq_cache(cache)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 7. EVENT CALENDAR (hardcoded + NSE corporate actions)
# ═══════════════════════════════════════════════════════════════════════════════

KNOWN_EVENTS = {
    "2026-07-28": "US FOMC Meeting",
    "2026-08-05": "RBI MPC Decision",
    "2026-09-16": "US FOMC Meeting",
    "2026-10-08": "RBI MPC Decision",
    "2026-11-04": "US FOMC Meeting",
    "2026-12-04": "RBI MPC Decision",
    "2026-12-15": "US FOMC Meeting",
    "2027-02-01": "Union Budget",
}


def _corp_actions_from_nse(ticker: str) -> List[Dict]:
    """NSE corporate actions for earnings dates."""
    today = date.today()
    from_dt = today.strftime("%d-%m-%Y")
    to_dt   = (today + timedelta(days=60)).strftime("%d-%m-%Y")
    data = _nse_fetch(
        f"https://www.nseindia.com/api/corporateActions?index=equities"
        f"&from_date={from_dt}&to_date={to_dt}&csv=false"
    )
    if not data:
        return []
    try:
        items = data if isinstance(data, list) else data.get("data", [])
        return [{"date": i.get("recDate","") or i.get("bcStDt",""),
                 "event": i.get("subject",""),
                 "ticker": i.get("symbol","")} for i in items
                if ticker.upper() in i.get("symbol","").upper()]
    except Exception:
        return []


def get_upcoming_events(days_ahead: int = 14) -> List[Dict]:
    today    = date.today()
    upcoming = []
    for ds, ev in KNOWN_EVENTS.items():
        try:
            ed = date.fromisoformat(ds)
            days = (ed - today).days
            if 0 <= days <= days_ahead:
                upcoming.append({"date": ds, "event": ev, "days_away": days,
                                  "urgent": days <= 3})
        except Exception:
            pass
    return sorted(upcoming, key=lambda x: x["days_away"])


def event_risk_score(events: List[Dict]) -> Tuple[int, str]:
    if not events:
        return 0, "No major events in next 14 days"
    urgent = [e for e in events if e["days_away"] <= 3]
    soon   = [e for e in events if 4 <= e["days_away"] <= 7]
    if urgent:
        return -1, f"⚠ {urgent[0]['event']} in {urgent[0]['days_away']} days — reduce size"
    if soon:
        return 0, f"📅 {soon[0]['event']} in {soon[0]['days_away']} days"
    return 0, f"Next: {events[0]['event']} in {events[0]['days_away']} days"


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER FETCH
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_all_market_data(tickers: List[str]) -> Dict:
    logger.info("Fetching market intelligence (multi-source)...")
    _init_nse()

    logger.info("  → Global macro (yfinance)...")
    global_macro = fetch_global_macro()
    logger.info(f"     {global_macro['summary']}")

    logger.info("  → FII/DII (NSE → Trendlyne → MC → yfinance proxy)...")
    fii_dii = fetch_fii_dii()
    logger.info(f"     {fii_dii['summary']}")

    logger.info("  → Bulk/block deals (NSE → BSE → Screener)...")
    bulk_deals  = fetch_bulk_deals(tickers)
    block_deals = fetch_block_deals(tickers)

    logger.info("  → Event calendar...")
    upcoming_events = get_upcoming_events()
    event_score, event_desc = event_risk_score(upcoming_events)

    logger.info("  → Liquidity check (yfinance)...")
    liquidity = check_liquidity(tickers)
    illiquid  = sum(1 for v in liquidity.values() if not v.get("liquid"))
    logger.info(f"     {illiquid} illiquid stocks will be filtered")

    logger.info("  → Options data (NSE → yfinance)...")
    options = {}
    for ticker in tickers[:8]:
        options[ticker] = fetch_options_data(ticker)
        time.sleep(0.2)

    logger.info("✅ Market intelligence complete")

    return {
        "global_macro":    global_macro,
        "fii_dii":         fii_dii,
        "bulk_deals":      bulk_deals,
        "block_deals":     block_deals,
        "liquidity":       liquidity,
        "options":         options,
        "upcoming_events": upcoming_events,
        "event_score":     event_score,
        "event_desc":      event_desc,
        "overall_risk_score": (
            global_macro["risk_score"] + fii_dii["fii_score"] + event_score
        ),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=== GLOBAL MACRO ===")
    macro = fetch_global_macro()
    print(f"Environment: {macro['environment']} (score={macro['risk_score']})")
    print(f"Summary: {macro['summary']}")
    for s in macro['signals']:
        print(f"  → {s}")

    print("\n=== UPCOMING EVENTS ===")
    for e in get_upcoming_events(days_ahead=365):
        print(f"  {e['date']} ({e['days_away']}d): {e['event']}")

    print("\n=== LIQUIDITY (mock test) ===")
    # Can't test yfinance in this sandbox — will work on GitHub Actions
    print("  (yfinance blocked in sandbox, works on GitHub Actions)")
