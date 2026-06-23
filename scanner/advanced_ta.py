"""
advanced_ta.py
New technical factors — all computed on existing OHLCV data with no extra API calls.

Modules:
  1. RSI Divergence         — bullish/bearish divergence detection
  2. OBV + Trend            — On-Balance Volume direction
  3. A/D Line + CMF         — Accumulation/Distribution + Chaikin Money Flow
  4. VWAP                   — Volume-Weighted Average Price (daily approximation)
  5. Multi-timeframe        — Weekly EMA/RSI/MACD confirmation
  6. Relative Strength      — Stock vs Nifty over 1M/3M/6M
  7. Earnings proximity     — Days to next earnings (penalise if < 5 days)
  8. Pattern recognition    — VCP, Cup & Handle, Flag, Squeeze breakout

Each function returns a dict with: signal, score, description
Scores feed into the master composite in technical_analysis.py
"""

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. RSI DIVERGENCE
# ═══════════════════════════════════════════════════════════════════════════════

def _find_pivots(series: pd.Series, window: int = 5) -> Tuple[List[int], List[int]]:
    """
    Find local highs and lows in a series using a rolling window.
    Returns (high_indices, low_indices).
    """
    highs, lows = [], []
    arr = series.values
    for i in range(window, len(arr) - window):
        if arr[i] == max(arr[i-window:i+window+1]):
            highs.append(i)
        if arr[i] == min(arr[i-window:i+window+1]):
            lows.append(i)
    return highs, lows


def compute_rsi_divergence(close: pd.Series, rsi: pd.Series, lookback: int = 60) -> Dict:
    """
    Detect RSI divergence over the last `lookback` bars.

    Bullish divergence:  price makes lower low, RSI makes higher low
    Bearish divergence:  price makes higher high, RSI makes lower high

    Returns signal, score, description.
    """
    if len(close) < lookback + 10:
        return {"signal": "Insufficient data", "score": 0, "description": ""}

    c = close.iloc[-lookback:].reset_index(drop=True)
    r = rsi.iloc[-lookback:].reset_index(drop=True)

    price_highs, price_lows = _find_pivots(c, window=5)
    rsi_highs,   rsi_lows   = _find_pivots(r, window=5)

    # ── Bullish divergence: last two price lows ───────────────────────────────
    bull_div = False
    if len(price_lows) >= 2 and len(rsi_lows) >= 2:
        p1, p2 = price_lows[-2], price_lows[-1]
        # Find nearest RSI low to each price low
        r1 = min(rsi_lows, key=lambda x: abs(x - p1))
        r2 = min(rsi_lows, key=lambda x: abs(x - p2))
        if abs(p1 - r1) <= 4 and abs(p2 - r2) <= 4:  # within 4 bars
            price_lower_low = float(c.iloc[p2]) < float(c.iloc[p1])
            rsi_higher_low  = float(r.iloc[r2]) > float(r.iloc[r1])
            if price_lower_low and rsi_higher_low:
                bull_div = True

    # ── Bearish divergence: last two price highs ──────────────────────────────
    bear_div = False
    if len(price_highs) >= 2 and len(rsi_highs) >= 2:
        p1, p2 = price_highs[-2], price_highs[-1]
        r1 = min(rsi_highs, key=lambda x: abs(x - p1))
        r2 = min(rsi_highs, key=lambda x: abs(x - p2))
        if abs(p1 - r1) <= 4 and abs(p2 - r2) <= 4:
            price_higher_high = float(c.iloc[p2]) > float(c.iloc[p1])
            rsi_lower_high    = float(r.iloc[r2]) < float(r.iloc[r1])
            if price_higher_high and rsi_lower_high:
                bear_div = True

    if bull_div:
        return {"signal": "Bullish Divergence", "score": 2,
                "description": "Price making lower lows while RSI makes higher lows — momentum building ahead of price"}
    if bear_div:
        return {"signal": "Bearish Divergence", "score": -2,
                "description": "Price making higher highs while RSI makes lower highs — hidden weakness"}
    return {"signal": "No Divergence", "score": 0, "description": "RSI tracking price normally"}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. OBV (ON-BALANCE VOLUME)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_obv(close: pd.Series, volume: pd.Series) -> Dict:
    """
    On-Balance Volume: cumulative volume directional indicator.
    OBV trending up while price trends up = confirmation.
    OBV diverging from price = warning.
    """
    direction = np.sign(close.diff().fillna(0))
    obv = (direction * volume).cumsum()

    obv_ema20 = obv.ewm(span=20, adjust=False).mean()
    obv_now   = float(obv.iloc[-1])
    obv_ma    = float(obv_ema20.iloc[-1])
    obv_prev  = float(obv.iloc[-22]) if len(obv) >= 22 else float(obv.iloc[0])

    # OBV trend
    obv_rising  = obv_now > obv_ma
    obv_1m_gain = ((obv_now - obv_prev) / max(abs(obv_prev), 1)) * 100

    # Compare OBV trend to price trend
    price_1m_pct = ((float(close.iloc[-1]) - float(close.iloc[-22])) / float(close.iloc[-22])) * 100

    price_up = price_1m_pct > 0
    obv_up   = obv_1m_gain > 0

    if price_up and obv_up:
        signal = "Accumulation Confirmed"
        score  = 1
        desc   = f"Price +{price_1m_pct:.1f}% and OBV rising — volume confirming the move"
    elif price_up and not obv_up:
        signal = "Distribution Warning"
        score  = -1
        desc   = f"Price +{price_1m_pct:.1f}% but OBV falling — smart money may be distributing"
    elif not price_up and obv_up:
        signal = "Stealth Accumulation"
        score  = 1
        desc   = "Price flat/down but OBV rising — accumulation under the surface"
    else:
        signal = "Distribution"
        score  = -1
        desc   = "Price and OBV both declining — confirmed distribution"

    return {
        "signal":      signal,
        "score":       score,
        "description": desc,
        "obv_rising":  obv_rising,
        "obv_1m_pct":  round(obv_1m_gain, 1),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. A/D LINE + CHAIKIN MONEY FLOW (CMF)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_accumulation_distribution(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    cmf_period: int = 20,
) -> Dict:
    """
    Accumulation/Distribution Line + Chaikin Money Flow.

    Money Flow Multiplier (MFM) = ((Close - Low) - (High - Close)) / (High - Low)
    Range between -1 and +1:
      +1 = closed at high = full accumulation
      -1 = closed at low  = full distribution

    CMF = sum(MFM * Volume, N) / sum(Volume, N)
    CMF > 0.1  = strong accumulation
    CMF < -0.1 = strong distribution
    """
    hl_range = (high - low).replace(0, np.nan)
    mfm      = ((close - low) - (high - close)) / hl_range
    mfm      = mfm.fillna(0).clip(-1, 1)
    mfv      = mfm * volume   # Money Flow Volume
    ad_line  = mfv.cumsum()

    # CMF
    cmf = mfv.rolling(cmf_period).sum() / volume.rolling(cmf_period).sum().replace(0, np.nan)
    cmf_now  = float(cmf.iloc[-1])
    cmf_prev = float(cmf.iloc[-5]) if len(cmf) >= 5 else cmf_now

    # A/D line trend
    ad_now   = float(ad_line.iloc[-1])
    ad_22    = float(ad_line.iloc[-22]) if len(ad_line) >= 22 else float(ad_line.iloc[0])
    ad_rising= ad_now > ad_22

    # CMF signal
    if cmf_now > 0.15:
        signal = "Strong Accumulation"
        score  = 2
        desc   = f"CMF {cmf_now:.2f} — sustained buying pressure over {cmf_period} days"
    elif cmf_now > 0.05:
        signal = "Mild Accumulation"
        score  = 1
        desc   = f"CMF {cmf_now:.2f} — money flowing in"
    elif cmf_now < -0.15:
        signal = "Strong Distribution"
        score  = -2
        desc   = f"CMF {cmf_now:.2f} — sustained selling pressure"
    elif cmf_now < -0.05:
        signal = "Mild Distribution"
        score  = -1
        desc   = f"CMF {cmf_now:.2f} — money flowing out"
    else:
        signal = "Neutral"
        score  = 0
        desc   = f"CMF {cmf_now:.2f} — balanced buying and selling"

    # Bonus: CMF improving
    if cmf_now > cmf_prev and cmf_now > 0:
        score = min(score + 1, 2)

    return {
        "signal":      signal,
        "score":       score,
        "description": desc,
        "cmf":         round(cmf_now, 3),
        "cmf_prev":    round(cmf_prev, 3),
        "ad_rising":   ad_rising,
        "cmf_improving": cmf_now > cmf_prev,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 4. VWAP (VOLUME-WEIGHTED AVERAGE PRICE)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int = 20,
) -> Dict:
    """
    Rolling VWAP over `period` days (daily bar approximation).
    Typical price = (H + L + C) / 3
    VWAP = sum(TP * Vol, N) / sum(Vol, N)

    Price above VWAP = buyers in control
    Price below VWAP = sellers in control
    Distance from VWAP indicates how extended the move is.
    """
    typical = (high + low + close) / 3
    vwap    = (typical * volume).rolling(period).sum() / volume.rolling(period).sum()

    vwap_now = float(vwap.iloc[-1])
    current  = float(close.iloc[-1])

    if np.isnan(vwap_now):
        return {"signal": "Insufficient data", "score": 0, "description": "", "vwap": 0, "pct_from_vwap": 0}

    pct_from_vwap = ((current - vwap_now) / vwap_now) * 100
    above_vwap    = current > vwap_now

    if above_vwap and pct_from_vwap < 3:
        signal = "Above VWAP"
        score  = 1
        desc   = f"Price {pct_from_vwap:.1f}% above {period}D VWAP — buyers in control"
    elif above_vwap and pct_from_vwap >= 3:
        signal = "Extended Above VWAP"
        score  = 0   # too extended — mean reversion risk
        desc   = f"Price {pct_from_vwap:.1f}% above VWAP — extended, mean-reversion risk"
    elif not above_vwap and pct_from_vwap > -3:
        signal = "Near VWAP"
        score  = 0
        desc   = f"Price {pct_from_vwap:.1f}% below {period}D VWAP — support zone"
    else:
        signal = "Below VWAP"
        score  = -1
        desc   = f"Price {pct_from_vwap:.1f}% below VWAP — sellers in control"

    return {
        "signal":        signal,
        "score":         score,
        "description":   desc,
        "vwap":          round(vwap_now, 2),
        "pct_from_vwap": round(pct_from_vwap, 2),
        "above_vwap":    above_vwap,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 5. MULTI-TIMEFRAME ANALYSIS (WEEKLY)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_weekly_confirmation(weekly_df: pd.DataFrame) -> Dict:
    """
    Compute EMA + RSI + MACD on weekly bars.
    Weekly confirmation = all timeframes aligned.
    Weekly contradiction = daily signal may be noise within a weekly downtrend.
    """
    if weekly_df is None or len(weekly_df) < 30:
        return {"signal": "Insufficient weekly data", "score": 0, "description": ""}

    close = weekly_df["Close"]
    w_ema13 = close.ewm(span=13, adjust=False).mean()
    w_ema26 = close.ewm(span=26, adjust=False).mean()

    # Weekly RSI
    delta = close.diff()
    gain  = delta.where(delta > 0, 0.0).ewm(alpha=1/14, adjust=False).mean()
    loss  = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/14, adjust=False).mean()
    w_rsi = float((100 - 100 / (1 + gain / loss.replace(0, np.nan))).iloc[-1])

    # Weekly MACD
    w_macd = float((w_ema13 - w_ema26).iloc[-1])
    w_sig  = float((w_ema13 - w_ema26).ewm(span=9, adjust=False).mean().iloc[-1])

    current    = float(close.iloc[-1])
    above_13w  = current > float(w_ema13.iloc[-1])
    above_26w  = current > float(w_ema26.iloc[-1])
    w_macd_pos = w_macd > w_sig

    bull_count = sum([above_13w, above_26w, w_macd_pos, 40 < w_rsi < 70])

    if bull_count >= 4:
        signal = "Weekly Bullish"
        score  = 1
        desc   = f"Weekly: above EMAs, MACD positive, RSI {w_rsi:.0f} — strong confirmation"
    elif bull_count >= 3:
        signal = "Weekly Mildly Bullish"
        score  = 0
        desc   = f"Weekly: mostly bullish, RSI {w_rsi:.0f}"
    elif bull_count <= 1:
        signal = "Weekly Bearish"
        score  = -2
        desc   = f"Weekly: bearish — daily buy signal may be a dead-cat bounce, RSI {w_rsi:.0f}"
    else:
        signal = "Weekly Mixed"
        score  = -1
        desc   = f"Weekly: mixed signals — proceed cautiously, RSI {w_rsi:.0f}"

    return {
        "signal":       signal,
        "score":        score,
        "description":  desc,
        "weekly_rsi":   round(w_rsi, 1),
        "weekly_macd_bullish": w_macd_pos,
        "above_13w_ema":      above_13w,
        "above_26w_ema":      above_26w,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 6. RELATIVE STRENGTH VS NIFTY
# ═══════════════════════════════════════════════════════════════════════════════

def compute_relative_strength(
    stock_close: pd.Series,
    nifty_close: pd.Series,
) -> Dict:
    """
    Compare stock return vs Nifty return over 1M, 3M, 6M.
    Positive RS = stock outperforming → institutional interest.
    Negative RS = stock underperforming → avoid / reduce.
    """
    if nifty_close is None or len(nifty_close) < 22:
        return {"signal": "Nifty data unavailable", "score": 0, "description": ""}

    # Align on common dates
    combined    = pd.concat([stock_close, nifty_close], axis=1, join="inner")
    combined.columns = ["stock", "nifty"]

    def rs(n):
        if len(combined) < n:
            return None
        s = ((combined["stock"].iloc[-1] - combined["stock"].iloc[-n]) / combined["stock"].iloc[-n]) * 100
        ni= ((combined["nifty"].iloc[-1] - combined["nifty"].iloc[-n]) / combined["nifty"].iloc[-n]) * 100
        return round(s - ni, 2)

    rs_1m = rs(22)
    rs_3m = rs(66)
    rs_6m = rs(132)

    # Score based on 1M RS (most actionable) with 3M as context
    if rs_1m is None:
        return {"signal": "Insufficient data", "score": 0, "rs_1m": None, "rs_3m": None, "rs_6m": None, "description": ""}

    if rs_1m > 5 and (rs_3m is None or rs_3m > 0):
        signal = "Strong Outperformer"
        score  = 2
        desc   = f"Stock +{rs_1m:.1f}% vs Nifty (1M) — institutional momentum"
    elif rs_1m > 2:
        signal = "Outperformer"
        score  = 1
        desc   = f"Stock +{rs_1m:.1f}% vs Nifty (1M) — mild outperformance"
    elif rs_1m > -2:
        signal = "In-line"
        score  = 0
        desc   = f"Stock {rs_1m:+.1f}% vs Nifty (1M) — tracking market"
    elif rs_1m > -5:
        signal = "Underperformer"
        score  = -1
        desc   = f"Stock {rs_1m:.1f}% vs Nifty (1M) — lagging the market"
    else:
        signal = "Strong Underperformer"
        score  = -2
        desc   = f"Stock {rs_1m:.1f}% vs Nifty (1M) — significant underperformance"

    return {
        "signal":      signal,
        "score":       score,
        "description": desc,
        "rs_1m":       rs_1m,
        "rs_3m":       rs_3m,
        "rs_6m":       rs_6m,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 7. EARNINGS PROXIMITY
# ═══════════════════════════════════════════════════════════════════════════════

def compute_earnings_proximity(ticker_sym: str) -> Dict:
    """
    Fetch next earnings date via yfinance and compute days to earnings.
    Penalise if earnings within 5 days (gap risk).
    Penalise mildly if within 15 days (elevated uncertainty).
    """
    try:
        t = yf.Ticker(ticker_sym)
        cal = t.calendar
        if cal is None or cal.empty:
            return {"days_to_earnings": None, "score": 0, "signal": "Unknown",
                    "description": "Earnings date not available"}

        # calendar returns a DataFrame with dates as columns
        if "Earnings Date" in cal.index:
            earn_date = cal.loc["Earnings Date"].iloc[0]
        elif hasattr(cal, "columns") and len(cal.columns) > 0:
            earn_date = cal.iloc[0, 0]
        else:
            return {"days_to_earnings": None, "score": 0, "signal": "Unknown",
                    "description": "Earnings date not parseable"}

        import datetime
        if hasattr(earn_date, "date"):
            earn_date = earn_date.date()
        today  = datetime.date.today()
        days   = (earn_date - today).days

        if days < 0:
            # Past earnings
            return {"days_to_earnings": abs(days), "score": 0, "signal": "Recent Results",
                    "description": f"Results {abs(days)} days ago — post-earnings drift possible"}
        elif days <= 5:
            return {"days_to_earnings": days, "score": -2, "signal": "Earnings Imminent",
                    "description": f"⚠ Earnings in {days} days — gap risk high. Avoid new positions."}
        elif days <= 15:
            return {"days_to_earnings": days, "score": -1, "signal": "Earnings Nearby",
                    "description": f"Earnings in {days} days — elevated uncertainty. Reduce size."}
        else:
            return {"days_to_earnings": days, "score": 0, "signal": "Earnings Distant",
                    "description": f"Earnings in {days} days — no near-term gap risk"}

    except Exception as e:
        logger.debug(f"Earnings date fetch failed for {ticker_sym}: {e}")
        return {"days_to_earnings": None, "score": 0, "signal": "Unknown",
                "description": "Earnings date unavailable"}


# ═══════════════════════════════════════════════════════════════════════════════
# 8. PATTERN RECOGNITION
# ═══════════════════════════════════════════════════════════════════════════════

def compute_patterns(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    atr: float,
) -> Dict:
    """
    Detect chart patterns algorithmically:
      - VCP (Volatility Contraction Pattern)
      - Cup & Handle
      - Bull Flag / Pennant
      - Base / Consolidation breakout
    """
    patterns_found = []
    score = 0

    # ── VCP (Volatility Contraction Pattern) ─────────────────────────────────
    # Conditions: 3+ contractions in price range, each < previous, volume declining
    vcp = _detect_vcp(high, low, close, volume, atr)
    if vcp["detected"]:
        patterns_found.append(f"VCP: {vcp['description']}")
        score += vcp["score"]

    # ── Bull Flag ─────────────────────────────────────────────────────────────
    flag = _detect_bull_flag(high, low, close, volume)
    if flag["detected"]:
        patterns_found.append(f"Bull Flag: {flag['description']}")
        score += flag["score"]

    # ── Cup & Handle ──────────────────────────────────────────────────────────
    cup = _detect_cup_handle(high, low, close, volume)
    if cup["detected"]:
        patterns_found.append(f"Cup & Handle: {cup['description']}")
        score += cup["score"]

    # ── Base breakout ─────────────────────────────────────────────────────────
    base = _detect_base_breakout(high, low, close, volume)
    if base["detected"]:
        patterns_found.append(f"Base Breakout: {base['description']}")
        score += base["score"]

    return {
        "patterns":    patterns_found,
        "score":       min(score, 3),   # cap pattern bonus at 3
        "description": " | ".join(patterns_found) if patterns_found else "No classic pattern detected",
        "vcp":         vcp,
        "flag":        flag,
        "cup":         cup,
        "base":        base,
    }


def _detect_vcp(high, low, close, volume, atr) -> Dict:
    """
    VCP (Mark Minervini): tightening price ranges with shrinking volume.
    Look at last 3 x 10-day windows. Each should have smaller range.
    Volume should be declining across windows.
    """
    if len(close) < 40:
        return {"detected": False, "score": 0, "description": ""}

    # Compute range and avg volume in last 3 x 10-day buckets
    ranges = []
    vols   = []
    for i in [30, 20, 10]:
        window_h = high.iloc[-i:-i+10] if i > 10 else high.iloc[-10:]
        window_l = low.iloc[-i:-i+10]  if i > 10 else low.iloc[-10:]
        window_v = volume.iloc[-i:-i+10] if i > 10 else volume.iloc[-10:]
        if len(window_h) >= 5:
            ranges.append(float(window_h.max() - window_l.min()))
            vols.append(float(window_v.mean()))

    if len(ranges) < 3:
        return {"detected": False, "score": 0, "description": ""}

    range_contracting = ranges[0] > ranges[1] > ranges[2]
    vol_declining     = vols[0] > vols[1]

    # Also check current ATR is less than 20-day ATR (tightening)
    current_tight = ranges[2] < 2.5 * atr

    if range_contracting and vol_declining and current_tight:
        tightness = round((1 - ranges[2] / ranges[0]) * 100, 1)
        return {
            "detected":    True,
            "score":       2,
            "description": f"Price range tightened {tightness}% over 30 days with declining volume — classic VCP",
            "tightness":   tightness,
        }
    return {"detected": False, "score": 0, "description": ""}


def _detect_bull_flag(high, low, close, volume) -> Dict:
    """
    Bull flag: sharp move up (pole), followed by tight downward/sideways drift.
    Pole: >8% move in 5-10 days
    Flag: <4% drift down over next 5-15 days with declining volume
    """
    if len(close) < 30:
        return {"detected": False, "score": 0, "description": ""}

    # Look for pole: last big move
    for pole_end in range(len(close)-15, len(close)-5):
        pole_start = pole_end - 8
        if pole_start < 0:
            continue
        pole_move = ((float(close.iloc[pole_end]) - float(close.iloc[pole_start])) /
                     float(close.iloc[pole_start])) * 100
        if pole_move < 8:
            continue

        # Flag: drift after pole
        flag_high = float(high.iloc[pole_end:].max())
        flag_low  = float(low.iloc[pole_end:].min())
        flag_drift= ((flag_low - flag_high) / flag_high) * 100
        flag_vol  = float(volume.iloc[pole_end:].mean())
        pole_vol  = float(volume.iloc[pole_start:pole_end].mean())
        vol_declining = flag_vol < pole_vol * 0.8

        if -5 < flag_drift < 0 and vol_declining:
            return {
                "detected":    True,
                "score":       2,
                "description": f"Pole +{pole_move:.1f}%, flag {flag_drift:.1f}% with declining volume",
            }
    return {"detected": False, "score": 0, "description": ""}


def _detect_cup_handle(high, low, close, volume) -> Dict:
    """
    Cup & Handle: U-shaped base (60+ days), handle (10-20 day pullback < 15%),
    breakout above cup rim with volume.
    """
    if len(close) < 80:
        return {"detected": False, "score": 0, "description": ""}

    # Cup: find prior high, deep drop, recovery to near prior high
    cup_window = close.iloc[-80:-20]
    cup_high   = float(cup_window.max())
    cup_low    = float(cup_window.min())
    cup_depth  = ((cup_low - cup_high) / cup_high) * 100

    # Recovery: last 20 days should be near the cup high
    recent_high = float(high.iloc[-20:].max())
    recovery    = ((recent_high - cup_high) / cup_high) * 100

    # Handle: last 10 days, tight pullback
    handle_low  = float(low.iloc[-10:].min())
    handle_drop = ((handle_low - recent_high) / recent_high) * 100
    current     = float(close.iloc[-1])

    if (-35 < cup_depth < -12 and
        -5 < recovery < 3 and
        -15 < handle_drop < -2 and
        current > handle_low):
        return {
            "detected":    True,
            "score":       2,
            "description": f"Cup depth {cup_depth:.1f}%, handle {handle_drop:.1f}% — watch for breakout above ₹{cup_high:.0f}",
        }
    return {"detected": False, "score": 0, "description": ""}


def _detect_base_breakout(high, low, close, volume) -> Dict:
    """
    Base breakout: stock consolidated in tight range (< 15% width) for 15+ days,
    now breaking out with above-average volume.
    """
    if len(close) < 25:
        return {"detected": False, "score": 0, "description": ""}

    base_high = float(high.iloc[-20:-1].max())
    base_low  = float(low.iloc[-20:-1].min())
    base_width= ((base_high - base_low) / base_low) * 100
    current   = float(close.iloc[-1])
    vol_today = float(volume.iloc[-1])
    vol_ma20  = float(volume.iloc[-21:-1].mean())
    vol_surge = vol_today > vol_ma20 * 1.5

    if base_width < 12 and current > base_high * 1.005 and vol_surge:
        return {
            "detected":    True,
            "score":       2,
            "description": f"Breaking out of {base_width:.1f}% base on {vol_today/vol_ma20:.1f}x volume",
        }
    return {"detected": False, "score": 0, "description": ""}


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER FUNCTION — compute all advanced factors at once
# ═══════════════════════════════════════════════════════════════════════════════

def compute_all(
    daily_df: pd.DataFrame,
    weekly_df: Optional[pd.DataFrame],
    nifty_close: Optional[pd.Series],
    rsi_series: pd.Series,
    atr: float,
    ticker_sym: str,
) -> Dict:
    """
    Compute all advanced TA factors and return combined dict.
    Called from technical_analysis.py after base indicators are computed.
    """
    close  = daily_df["Close"]
    high   = daily_df["High"]
    low    = daily_df["Low"]
    volume = daily_df["Volume"]

    divergence = compute_rsi_divergence(close, rsi_series)
    obv        = compute_obv(close, volume)
    ad_cmf     = compute_accumulation_distribution(high, low, close, volume)
    vwap       = compute_vwap(high, low, close, volume)
    weekly     = compute_weekly_confirmation(weekly_df)
    rs         = compute_relative_strength(close, nifty_close)
    earnings   = compute_earnings_proximity(ticker_sym)
    patterns   = compute_patterns(high, low, close, volume, atr)

    # Composite advanced score
    adv_score = (
        divergence["score"] +
        obv["score"] +
        ad_cmf["score"] +
        vwap["score"] +
        weekly["score"] +
        rs["score"] +
        earnings["score"] +
        patterns["score"]
    )

    return {
        "divergence":  divergence,
        "obv":         obv,
        "ad_cmf":      ad_cmf,
        "vwap":        vwap,
        "weekly":      weekly,
        "rs":          rs,
        "earnings":    earnings,
        "patterns":    patterns,
        "advanced_score": adv_score,
        "advanced_max":   16,   # theoretical max: 2+1+2+1+1+2+0+3
    }


if __name__ == "__main__":
    import pandas as pd
    import numpy as np
    logging.basicConfig(level=logging.WARNING)

    # Generate synthetic data for testing
    np.random.seed(42)
    dates  = pd.date_range("2025-06-22", periods=252, freq="B")
    prices = 1000 * np.cumprod(1 + np.random.normal(0.0008, 0.018, 252))
    volume = np.random.randint(200000, 800000, 252).astype(float)

    # Make it look like accumulation: volume increases on up days
    daily_returns = np.diff(prices, prepend=prices[0])
    volume = np.where(daily_returns > 0, volume * 1.4, volume * 0.7)

    # Add VCP: last 30 days tighten
    prices[-30:] = prices[-30] + np.random.normal(0, 15, 30).cumsum()
    volume[-30:] *= np.linspace(1, 0.4, 30)

    df = pd.DataFrame({
        "Close":  prices,
        "High":   prices * (1 + np.abs(np.random.normal(0, 0.005, 252))),
        "Low":    prices * (1 - np.abs(np.random.normal(0, 0.005, 252))),
        "Open":   prices * 0.999,
        "Volume": volume,
    }, index=dates)

    # Weekly resample
    weekly = df.resample("W").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()

    # RSI
    delta = df["Close"].diff()
    gain  = delta.where(delta > 0, 0.0).ewm(alpha=1/14, adjust=False).mean()
    loss  = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/14, adjust=False).mean()
    rsi   = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

    atr_val = float(pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"] - df["Close"].shift()).abs()
    ], axis=1).max(axis=1).ewm(span=14, adjust=False).mean().iloc[-1])

    result = compute_all(df, weekly, None, rsi, atr_val, "TEST.NS")

    print("=== ADVANCED TA RESULTS (synthetic data) ===\n")
    for key, val in result.items():
        if key == "advanced_score":
            print(f"  TOTAL ADVANCED SCORE: {val}/{result['advanced_max']}")
        elif isinstance(val, dict):
            sig   = val.get("signal", "?")
            score = val.get("score", 0)
            desc  = val.get("description", "")[:70]
            print(f"  {key:12} | {sig:25} | score={score:+d} | {desc}")

    print("\n  Pattern details:")
    for p in result["patterns"]["patterns"]:
        print(f"    → {p}")
