"""
market_environment.py
Assesses the overall Indian market environment before individual stock analysis.

Computes:
  1. Nifty 50 trend     — Bull / Bear / Sideways + strength
  2. India VIX level    — Fear / Neutral / Greed (inverted — high VIX = fear)
  3. Market breadth     — Advance/decline approximated via Nifty vs SmallCap spread
  4. Sector rotation    — Which sectors are outperforming vs underperforming

Market environment affects individual stock scores:
  - Bull market, low VIX, good breadth → stocks get +1 tailwind bonus
  - Bear market, high VIX, poor breadth → stocks get -1 headwind penalty
  - Sideways / neutral → no adjustment

All fetched in a single batch yfinance call to minimise latency.
"""

import logging
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# ── Index symbols (Yahoo Finance format) ─────────────────────────────────────
NIFTY50_SYM   = "^NSEI"
VIX_SYM       = "^INDIAVIX"
SMALLCAP_SYM  = "^CNXSC"        # Nifty Smallcap 100
MIDCAP_SYM    = "^NSEMDCP50"    # Nifty Midcap 50

# Sector indices → sector name mapping
SECTOR_INDICES = {
    "^CNXIT":     "IT",
    "^CNXAUTO":   "Auto",
    "^CNXPHARMA": "Pharma",
    "^CNXMETAL":  "Metal",
    "^CNXFMCG":   "FMCG",
    "^CNXENERGY": "Energy",
    "^CNXINFRA":  "Infra",
    "^CNXREALTY": "Realty",
    "^NSEBANK":   "Banking",
}

# NSE sector → stocks that belong to it (for matching our tickers)
SECTOR_MAP = {
    "IT":      {"TCS","INFY","WIPRO","HCLTECH","TECHM","LTIM","LTTS","MPHASIS",
                "COFORGE","PERSISTENT","KPITTECH"},
    "Auto":    {"TATAMOTORS","MARUTI","M&M","BAJAJ-AUTO","HEROMOTOCO","EICHERMOT",
                "MOTHERSON","ENDURANCE","SUPRAJIT","MINDA","CRAFTSMAN"},
    "Pharma":  {"SUNPHARMA","CIPLA","DRREDDY","DIVISLAB","LUPIN","AUROPHARMA",
                "TORNTPHARM","ALKEM","IPCALAB","GRANULES","LAURUSLABS"},
    "Metal":   {"TATASTEEL","JSWSTEEL","HINDALCO","VEDL","SAIL","NMDC","JINDALSTEL"},
    "FMCG":    {"HINDUNILVR","ITC","NESTLEIND","BRITANNIA","DABUR","MARICO",
                "COLPAL","EMAMILTD","TATACONSUM","VBL","RADICO"},
    "Energy":  {"RELIANCE","ONGC","BPCL","IOC","ADANIGREEN","TATAPOWER",
                "ADANITRANS","TORNTPOWER","NHPC","SJVN","CLEANMAX","WABAG"},
    "Infra":   {"LT","BHEL","NBCC","RVNL","RAILTEL","IRFC","CONCOR","GRSE",
                "HAL","BEL","BEML","MAZAGON"},
    "Realty":  {"DLF","GODREJPROP","OBEROIRLTY","PRESTIGE","SOBHA","PHOENIXLTD",
                "BRIGADERE","RAYMOND","RAYMONDREL"},
    "Banking": {"HDFCBANK","ICICIBANK","SBIN","KOTAKBANK","AXISBANK","INDUSINDBK",
                "BANKBARODA","PNB","CHOLAFIN","BAJFINANCE","BAJAJFINSV"},
    "Defence": {"HAL","BEL","BEML","COCHINSHIP","GRSE","MAZAGON","DATAPATTNS",
                "MTARTECH","AZAD","AEQUS","DYNAMATECH","SOLARINDS","IDEAFORGE"},
    "Water":   {"WABAG","IONEXCHANG","ENVIROINFRA","EMS"},
}


def get_ticker_sector(ticker: str) -> Optional[str]:
    """Return the sector for a given ticker, or None if not mapped."""
    for sector, tickers in SECTOR_MAP.items():
        if ticker.upper() in tickers:
            return sector
    return None


def _fetch_index_data(symbols: List[str], period: str = "1y") -> Dict[str, pd.DataFrame]:
    """Batch fetch OHLCV for multiple index symbols."""
    result = {}
    for sym in symbols:
        try:
            df = yf.download(sym, period=period, interval="1d",
                             progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]
            if len(df) >= 20:
                result[sym] = df.dropna().sort_index()
        except Exception as e:
            logger.debug(f"Index fetch failed {sym}: {e}")
        time.sleep(0.3)
    return result


def _nifty_trend(df: pd.DataFrame) -> Dict:
    """
    Classify Nifty 50 trend using EMAs and price structure.
    Returns trend classification and numeric score.
    """
    close  = df["Close"]
    ema20  = close.ewm(span=20, adjust=False).mean()
    ema50  = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()

    current   = float(close.iloc[-1])
    e20, e50, e200 = float(ema20.iloc[-1]), float(ema50.iloc[-1]), float(ema200.iloc[-1])

    above_200  = current > e200
    above_50   = current > e50
    above_20   = current > e20
    bullish_ma = e20 > e50 > e200

    # 1-month and 3-month returns
    ret_1m = ((current - float(close.iloc[-22])) / float(close.iloc[-22])) * 100
    ret_3m = ((current - float(close.iloc[-66])) / float(close.iloc[-66])) * 100 if len(close) >= 66 else 0

    # Trend classification
    bull_count = sum([above_200, above_50, above_20, bullish_ma])
    if bull_count >= 4:
        trend = "Strong Bull"
        score = 2
    elif bull_count >= 3:
        trend = "Bull"
        score = 1
    elif bull_count <= 1:
        trend = "Bear"
        score = -2
    elif not above_200:
        trend = "Weak / Bear"
        score = -1
    else:
        trend = "Sideways"
        score = 0

    pct_from_ath = ((current - float(df["High"].max())) / float(df["High"].max())) * 100

    return {
        "trend":         trend,
        "score":         score,
        "current":       round(current, 0),
        "ema20":         round(e20, 0),
        "ema50":         round(e50, 0),
        "ema200":        round(e200, 0),
        "above_ema200":  above_200,
        "ret_1m_pct":    round(ret_1m, 2),
        "ret_3m_pct":    round(ret_3m, 2),
        "pct_from_ath":  round(pct_from_ath, 2),
        "bullish_ma_stack": bullish_ma,
    }


def _vix_signal(df: pd.DataFrame) -> Dict:
    """
    Classify India VIX.
    VIX < 13: Complacency (can be bearish reversal risk)
    VIX 13-18: Normal range
    VIX 18-22: Elevated anxiety
    VIX > 22: Fear (often a buying opportunity if trend is up)
    """
    close  = df["Close"]
    vix    = float(close.iloc[-1])
    vix_ma = float(close.rolling(20).mean().iloc[-1])
    vix_rising = vix > vix_ma

    if vix < 13:
        signal = "Complacency"
        score  = -1  # market too comfortable — risk of reversal
    elif vix < 18:
        signal = "Calm"
        score  = 1
    elif vix < 22:
        signal = "Anxiety"
        score  = 0
    elif vix < 28:
        signal = "Fear"
        score  = -1
    else:
        signal = "Panic"
        score  = -2

    return {
        "vix":       round(vix, 2),
        "vix_ma20":  round(vix_ma, 2),
        "vix_rising":vix_rising,
        "signal":    signal,
        "score":     score,
        "interpretation": (
            f"VIX {vix:.1f} — {signal}. "
            f"{'Rising (increasing uncertainty)' if vix_rising else 'Falling (settling down)'}."
        ),
    }


def _market_breadth(nifty_df: pd.DataFrame, smallcap_df: Optional[pd.DataFrame]) -> Dict:
    """
    Approximate market breadth using Nifty 50 vs Nifty Smallcap ratio.
    When smallcaps outperform → broad participation → healthy bull market.
    When smallcaps underperform → narrow rally (large caps only) → weaker market.
    """
    if smallcap_df is None or len(smallcap_df) < 22:
        return {
            "breadth": "Unknown",
            "score": 0,
            "interpretation": "Breadth data unavailable",
            "nifty_1m": 0,
            "smallcap_1m": 0,
        }

    n_ret  = ((float(nifty_df["Close"].iloc[-1])    - float(nifty_df["Close"].iloc[-22]))    / float(nifty_df["Close"].iloc[-22]))    * 100
    sc_ret = ((float(smallcap_df["Close"].iloc[-1]) - float(smallcap_df["Close"].iloc[-22])) / float(smallcap_df["Close"].iloc[-22])) * 100

    spread = sc_ret - n_ret  # positive = smallcaps outperforming

    if spread > 3:
        breadth = "Strong"
        score   = 1
        interp  = f"Smallcaps +{spread:.1f}% vs Nifty — broad participation, healthy rally"
    elif spread > -2:
        breadth = "Moderate"
        score   = 0
        interp  = f"Smallcaps {spread:+.1f}% vs Nifty — mixed breadth"
    else:
        breadth = "Weak"
        score   = -1
        interp  = f"Smallcaps {spread:.1f}% vs Nifty — narrow rally, mostly large cap driven"

    return {
        "breadth":       breadth,
        "score":         score,
        "nifty_1m":      round(n_ret, 2),
        "smallcap_1m":   round(sc_ret, 2),
        "spread":        round(spread, 2),
        "interpretation": interp,
    }


def _sector_performance(sector_data: Dict[str, pd.DataFrame], nifty_df: pd.DataFrame) -> Dict:
    """
    Rank sectors by 1-month relative performance vs Nifty.
    Returns top 3 outperformers and bottom 3 underperformers.
    """
    nifty_1m = ((float(nifty_df["Close"].iloc[-1]) - float(nifty_df["Close"].iloc[-22])) / float(nifty_df["Close"].iloc[-22])) * 100
    sector_perf = {}

    for sym, df in sector_data.items():
        sector_name = SECTOR_INDICES.get(sym, sym)
        if len(df) < 22:
            continue
        ret_1m = ((float(df["Close"].iloc[-1]) - float(df["Close"].iloc[-22])) / float(df["Close"].iloc[-22])) * 100
        rel    = round(ret_1m - nifty_1m, 2)
        sector_perf[sector_name] = {
            "return_1m": round(ret_1m, 2),
            "vs_nifty":  rel,
        }

    if not sector_perf:
        return {"leaders": [], "laggards": [], "all": {}}

    sorted_sectors = sorted(sector_perf.items(), key=lambda x: x[1]["vs_nifty"], reverse=True)
    leaders  = [(s, d) for s, d in sorted_sectors[:3]]
    laggards = [(s, d) for s, d in sorted_sectors[-3:]]

    return {
        "leaders":  leaders,
        "laggards": laggards,
        "all":      dict(sorted_sectors),
    }


def _composite_environment_score(nifty: Dict, vix: Dict, breadth: Dict) -> Tuple[int, str, str]:
    """
    Combine sub-scores into overall market environment.
    Returns (score, label, description).
    """
    total = nifty["score"] + vix["score"] + breadth["score"]

    if total >= 3:
        label = "Risk-On"
        desc  = "Strong bull market, low volatility, broad participation — ideal for long positions"
        color = "#00c853"
    elif total >= 1:
        label = "Mildly Bullish"
        desc  = "Generally positive — be selective, favour high-quality setups"
        color = "#69f0ae"
    elif total == 0:
        label = "Neutral"
        desc  = "Mixed signals — position size conservatively, favour tight stops"
        color = "#ffd600"
    elif total >= -2:
        label = "Cautious"
        desc  = "Headwinds present — reduce exposure, avoid chasing breakouts"
        color = "#ff6d00"
    else:
        label = "Risk-Off"
        desc  = "Bear market conditions — cash is a position, only short setups or avoid"
        color = "#ff1744"

    return total, label, desc, color


def fetch_market_environment() -> Dict:
    """
    Fetch and compute full market environment.
    Returns a dict consumed by technical_analysis.py to adjust stock scores
    and by report_builder.py for the environment summary panel.
    """
    logger.info("Fetching market environment data...")

    # Batch fetch all indices
    all_symbols = [NIFTY50_SYM, VIX_SYM, SMALLCAP_SYM] + list(SECTOR_INDICES.keys())
    index_data  = _fetch_index_data(all_symbols)

    nifty_df    = index_data.get(NIFTY50_SYM)
    vix_df      = index_data.get(VIX_SYM)
    smallcap_df = index_data.get(SMALLCAP_SYM)
    sector_data = {sym: df for sym, df in index_data.items()
                   if sym in SECTOR_INDICES and df is not None}

    # Handle missing data gracefully
    if nifty_df is None or len(nifty_df) < 50:
        logger.warning("Nifty data unavailable — using neutral environment")
        return _neutral_environment()

    nifty   = _nifty_trend(nifty_df)
    vix     = _vix_signal(vix_df)      if vix_df is not None and len(vix_df) >= 5  else _neutral_vix()
    breadth = _market_breadth(nifty_df, smallcap_df)
    sectors = _sector_performance(sector_data, nifty_df)

    score, label, desc, color = _composite_environment_score(nifty, vix, breadth)

    env = {
        "label":          label,
        "score":          score,
        "description":    desc,
        "color":          color,
        "nifty":          nifty,
        "vix":            vix,
        "breadth":        breadth,
        "sectors":        sectors,
        "data_available": True,
    }

    logger.info(f"Market environment: {label} (score={score}) | Nifty={nifty['trend']} | VIX={vix.get('signal','?')}")
    return env


def _neutral_environment() -> Dict:
    return {
        "label": "Unknown", "score": 0,
        "description": "Market environment data unavailable — applying neutral score",
        "color": "#8b949e",
        "nifty":   {"trend": "Unknown", "score": 0, "current": 0, "ret_1m_pct": 0, "ret_3m_pct": 0, "pct_from_ath": 0, "above_ema200": None, "bullish_ma_stack": None},
        "vix":     _neutral_vix(),
        "breadth": {"breadth": "Unknown", "score": 0, "interpretation": "Data unavailable", "nifty_1m": 0, "smallcap_1m": 0},
        "sectors": {"leaders": [], "laggards": [], "all": {}},
        "data_available": False,
    }


def _neutral_vix() -> Dict:
    return {"vix": 0, "vix_ma20": 0, "vix_rising": False, "signal": "Unknown", "score": 0,
            "interpretation": "VIX data unavailable"}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test with synthetic data since sandbox blocks Yahoo Finance
    import numpy as np

    dates = pd.date_range("2025-06-22", periods=252, freq="B")
    np.random.seed(42)

    # Simulate Nifty in uptrend
    nifty_prices = 22000 * np.cumprod(1 + np.random.normal(0.0005, 0.01, 252))
    nifty_df = pd.DataFrame({
        "Close": nifty_prices, "High": nifty_prices * 1.005,
        "Low": nifty_prices * 0.995, "Open": nifty_prices * 0.999,
        "Volume": np.random.randint(1e8, 5e8, 252).astype(float)
    }, index=dates)

    # Simulate VIX at 15 (calm)
    vix_prices = np.random.normal(15, 2, 252).clip(10, 35)
    vix_df = pd.DataFrame({"Close": vix_prices, "High": vix_prices+1, "Low": vix_prices-1,
                            "Open": vix_prices, "Volume": np.ones(252)}, index=dates)

    # Simulate SmallCap slightly outperforming
    sc_prices = 10000 * np.cumprod(1 + np.random.normal(0.0007, 0.015, 252))
    sc_df = pd.DataFrame({"Close": sc_prices, "High": sc_prices*1.01, "Low": sc_prices*0.99,
                           "Open": sc_prices, "Volume": np.ones(252)}, index=dates)

    nifty   = _nifty_trend(nifty_df)
    vix_res = _vix_signal(vix_df)
    breadth = _market_breadth(nifty_df, sc_df)
    score, label, desc, color = _composite_environment_score(nifty, vix_res, breadth)

    print("=== MARKET ENVIRONMENT (synthetic data) ===")
    print(f"  Nifty trend:  {nifty['trend']} (score={nifty['score']})")
    print(f"  VIX signal:   {vix_res['signal']} {vix_res['vix']:.1f} (score={vix_res['score']})")
    print(f"  Breadth:      {breadth['breadth']} (score={breadth['score']})")
    print(f"  Environment:  {label} (total score={score})")
    print(f"  Description:  {desc}")

    # Test sector mapping
    print("\n=== SECTOR MAPPING ===")
    for ticker in ["WABAG", "CLEANMAX", "MTARTECH", "AZAD", "RELIANCE", "FAKEXYZ"]:
        sector = get_ticker_sector(ticker)
        print(f"  {ticker:15} → {sector or 'Unmapped'}")
