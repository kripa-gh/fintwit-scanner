"""
technical_analysis.py v3
Integrates base indicators (v2) + advanced factors + market environment.

SCORING MODEL (max 28 points):
  Base indicators (max 12):
    EMA alignment         0–3
    Golden cross          0–1
    RSI healthy           0–1
    RSI turning up        0–1
    MACD above zero       0–1
    MACD bullish cross    0–2
    Volume expanding      0–1
    ADX strong + bullish  0–1
    Uptrend structure     0–1
    Penalties:            RSI overbought, near resistance, 100%+ from low,
                          death cross, stale data (-1 to -4)

  Advanced factors (max 16):
    RSI divergence        -2 to +2
    OBV                   -1 to +1
    A/D + CMF             -2 to +2
    VWAP position         -1 to +1
    Weekly confirmation   -2 to +1
    Relative strength     -2 to +2
    Earnings proximity    -2 to  0
    Patterns (VCP/Flag)    0 to +3

  Market environment adjustment (-2 to +2):
    Applied as a market tailwind/headwind modifier

RECOMMENDATION THRESHOLDS:
  Strong Buy  ≥ 18   (65%+ of max)
  Buy         ≥ 14
  Watch       ≥  9
  Caution     ≥  4
  Avoid        < 4
"""

import logging
import time
from datetime import date
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

from scanner.market_calendar import get_data_freshness
from scanner.advanced_ta import compute_all as compute_advanced
from scanner.market_environment import get_ticker_sector

logger = logging.getLogger(__name__)

# ── Params ────────────────────────────────────────────────────────────────────
EMA_SHORT   = 20
EMA_MED     = 50
EMA_LONG    = 200
RSI_PERIOD  = 14
MACD_FAST   = 12
MACD_SLOW   = 26
MACD_SIG    = 9
ATR_PERIOD  = 14
BB_PERIOD   = 20
BB_STD      = 2
VOL_MA      = 20
ADX_PERIOD  = 14
ATR_STOP    = {">25": 2.0, ">18": 1.5, "else": 1.0}   # ADX → stop mult
ATR_TARGET  = 3.0


def _fetch_ohlcv(symbol: str) -> Dict[str, Optional[pd.DataFrame]]:
    """
    Fetch daily (1y) and weekly (2y) OHLCV in two calls.
    Returns {"daily": df, "weekly": df} — either may be None.
    """
    result = {"daily": None, "weekly": None}
    try:
        daily = yf.download(symbol, period="1y", interval="1d",
                            progress=False, auto_adjust=True)
        if isinstance(daily.columns, pd.MultiIndex):
            daily.columns = [c[0] for c in daily.columns]
        if len(daily) >= 50:
            result["daily"] = daily.dropna().sort_index()
    except Exception as e:
        logger.warning(f"Daily fetch failed {symbol}: {e}")

    try:
        weekly = yf.download(symbol, period="2y", interval="1wk",
                             progress=False, auto_adjust=True)
        if isinstance(weekly.columns, pd.MultiIndex):
            weekly.columns = [c[0] for c in weekly.columns]
        if len(weekly) >= 30:
            result["weekly"] = weekly.dropna().sort_index()
    except Exception as e:
        logger.debug(f"Weekly fetch failed {symbol}: {e}")

    return result


def _detect_corporate_actions(close: pd.Series):
    drops = close.pct_change()[close.pct_change() < -0.30]
    if drops.empty:
        return False, ""
    dates = drops.index.strftime("%Y-%m-%d").tolist()
    return True, f"⚠ Possible split/bonus on {', '.join(dates[-2:])} — check adjusted prices"


def _atr_stop_target(current, atr, adx, recent_low_20d):
    mult = 2.0 if adx > 25 else 1.5 if adx > 18 else 1.0
    label= "Wide (strong trend)" if adx > 25 else "Standard" if adx > 18 else "Tight (weak trend)"
    atr_stop  = round(current - mult * atr, 2)
    floor     = round(recent_low_20d * 0.99, 2)
    stop      = max(atr_stop, floor)
    risk      = current - stop
    target    = round(current + ATR_TARGET * risk, 2)
    rr        = round((target - current) / max(risk, 0.01), 2)
    guide     = f"Risk/share ₹{risk:.0f} | {round(100000/current)} shares per ₹1L → ₹{round(round(100000/current)*risk)} at risk"
    logic     = f"{label} | {mult}x ATR=₹{mult*atr:.0f}, swing floor=₹{floor:.0f}"
    return stop, target, rr, guide, logic


def _compute_rsi_series(close: pd.Series) -> pd.Series:
    delta = close.diff()
    gain  = delta.where(delta > 0, 0.0).ewm(alpha=1/RSI_PERIOD, adjust=False).mean()
    loss  = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/RSI_PERIOD, adjust=False).mean()
    return 100 - (100 / (1 + gain / loss.replace(0, np.nan)))


def analyse_ticker(
    ticker: str,
    mention_count: int,
    users: List[str],
    market_env: Optional[Dict] = None,
    nifty_close: Optional[pd.Series] = None,
) -> Optional[Dict]:
    """Full technical analysis for one NSE ticker."""

    nse_symbol = f"{ticker}.NS"
    data = _fetch_ohlcv(nse_symbol)
    df   = data["daily"]
    wdf  = data["weekly"]

    if df is None:
        logger.warning(f"No data for {ticker}")
        return None

    # Gap 5: corporate action detection
    corp_action, corp_msg = _detect_corporate_actions(df["Close"])

    close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]
    current   = float(close.iloc[-1])
    prev      = float(close.iloc[-2])
    last_date = df.index[-1].date()
    freshness, is_stale = get_data_freshness(last_date)

    # ── Price returns ─────────────────────────────────────────────────────────
    def ret(n): return ((current - float(close.iloc[-n])) / float(close.iloc[-n])) * 100
    day_pct   = ret(2)
    week_pct  = ret(6)  if len(close) >= 6  else 0
    month_pct = ret(22) if len(close) >= 22 else 0
    high_52w  = float(high.rolling(252).max().iloc[-1])
    low_52w   = float(low.rolling(252).min().iloc[-1])
    pct_52h   = ((current - high_52w) / high_52w) * 100
    pct_52l   = ((current - low_52w)  / low_52w)  * 100

    # ── EMAs ─────────────────────────────────────────────────────────────────
    ema20  = close.ewm(span=EMA_SHORT, adjust=False).mean()
    ema50  = close.ewm(span=EMA_MED,   adjust=False).mean()
    ema200 = close.ewm(span=EMA_LONG,  adjust=False).mean()
    e20, e50, e200 = float(ema20.iloc[-1]), float(ema50.iloc[-1]), float(ema200.iloc[-1])
    above_e20 = current > e20
    above_e50 = current > e50
    above_e200= current > e200
    golden    = e50 > e200
    death     = (e50 < e200 and
                 float(ema50.iloc[-20]) > float(ema200.iloc[-20]))

    # ── RSI ───────────────────────────────────────────────────────────────────
    rsi_series = _compute_rsi_series(close)
    rsi = float(rsi_series.iloc[-1])
    rsi_prev = float(rsi_series.iloc[-2])

    # ── MACD ──────────────────────────────────────────────────────────────────
    ef  = close.ewm(span=MACD_FAST, adjust=False).mean()
    es  = close.ewm(span=MACD_SLOW, adjust=False).mean()
    ml  = ef - es
    sl  = ml.ewm(span=MACD_SIG, adjust=False).mean()
    hist= ml - sl
    macd_val  = float(ml.iloc[-1])
    sig_val   = float(sl.iloc[-1])
    hist_val  = float(hist.iloc[-1])
    hist_prev = float(hist.iloc[-2])
    macd_bull_x = macd_val > sig_val and float(ml.iloc[-2]) <= float(sl.iloc[-2])
    macd_bear_x = macd_val < sig_val and float(ml.iloc[-2]) >= float(sl.iloc[-2])
    macd_pos    = macd_val > 0

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    bb_m  = close.rolling(BB_PERIOD).mean()
    bb_s  = close.rolling(BB_PERIOD).std()
    bb_u  = bb_m + BB_STD * bb_s
    bb_l  = bb_m - BB_STD * bb_s
    bb_w  = (float(bb_u.iloc[-1]) - float(bb_l.iloc[-1])) / float(bb_m.iloc[-1])
    bb_pct= (current - float(bb_l.iloc[-1])) / max(float(bb_u.iloc[-1]) - float(bb_l.iloc[-1]), 0.01)
    squeeze = bb_w < 0.06

    # ── Volume ────────────────────────────────────────────────────────────────
    vol_ma  = float(volume.rolling(VOL_MA).mean().iloc[-1])
    vol_now = float(volume.iloc[-1])
    vol_r   = vol_now / max(vol_ma, 1)

    # ── ATR ───────────────────────────────────────────────────────────────────
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr     = float(tr.ewm(span=ATR_PERIOD, adjust=False).mean().iloc[-1])
    atr_pct = (atr / current) * 100

    # ── ADX ───────────────────────────────────────────────────────────────────
    pdm  = high.diff().clip(lower=0)
    mdm  = (-low.diff()).clip(lower=0)
    pdm[pdm < mdm] = 0
    mdm[mdm < pdm] = 0
    atr_s  = tr.ewm(span=ADX_PERIOD, adjust=False).mean()
    pdi    = 100 * (pdm.ewm(span=ADX_PERIOD, adjust=False).mean() / atr_s)
    mdi    = 100 * (mdm.ewm(span=ADX_PERIOD, adjust=False).mean() / atr_s)
    dx     = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx    = float(dx.ewm(span=ADX_PERIOD, adjust=False).mean().iloc[-1])
    pdi_v  = float(pdi.iloc[-1])
    mdi_v  = float(mdi.iloc[-1])
    trend_dir    = "Bullish" if pdi_v > mdi_v else "Bearish"
    trend_str    = "Strong" if adx > 25 else "Weak"

    # ── Support/Resistance ────────────────────────────────────────────────────
    r20d = float(high.rolling(20).max().iloc[-1])
    s20d = float(low.rolling(20).min().iloc[-1])
    d_res= ((r20d - current) / current) * 100
    d_sup= ((current - s20d) / current) * 100

    # ── Trend structure ───────────────────────────────────────────────────────
    h10  = high.rolling(10).max()
    l10  = low.rolling(10).min()
    uptrend   = float(h10.iloc[-1]) > float(h10.iloc[-11]) and float(l10.iloc[-1]) > float(l10.iloc[-11])
    downtrend = float(h10.iloc[-1]) < float(h10.iloc[-11]) and float(l10.iloc[-1]) < float(l10.iloc[-11])

    # ── Context-aware stop / target ───────────────────────────────────────────
    stop, target, rr, pos_guide, stop_logic = _atr_stop_target(current, atr, adx, s20d)
    entry_ctx = "breakout" if d_res < 3 else "pullback"

    # ── BASE SCORING ──────────────────────────────────────────────────────────
    base_score = 0
    reasons    = []

    if above_e200:  base_score += 1; reasons.append("Price above 200 EMA")
    if above_e50:   base_score += 1; reasons.append("Price above 50 EMA")
    if above_e20:   base_score += 1; reasons.append("Price above 20 EMA")
    if golden:      base_score += 1; reasons.append("Golden cross (50 > 200 EMA)")
    if 40 < rsi < 70: base_score += 1; reasons.append(f"RSI healthy ({rsi:.0f})")
    if rsi > rsi_prev and rsi < 70: base_score += 1; reasons.append("RSI turning up")
    if macd_pos:    base_score += 1; reasons.append("MACD above zero")
    if macd_bull_x: base_score += 2; reasons.append("MACD bullish crossover ⚡")
    if vol_r > 1.2: base_score += 1; reasons.append(f"Volume {vol_r:.1f}x average")
    if adx > 25 and trend_dir == "Bullish": base_score += 1; reasons.append(f"Strong trend (ADX {adx:.0f})")
    if uptrend:     base_score += 1; reasons.append("Higher highs & higher lows")

    # Base penalties
    if rsi > 75:          base_score -= 1; reasons.append(f"⚠ RSI overbought ({rsi:.0f})")
    if d_res < 2:         base_score -= 1; reasons.append("⚠ Near 20D resistance")
    if pct_52l > 100:     base_score -= 1; reasons.append("⚠ 100%+ from 52W low")
    if death:             base_score -= 2; reasons.append("⚠ Death cross")
    if is_stale:          base_score -= 1; reasons.append(f"⚠ {freshness}")
    if corp_action:       reasons.append(corp_msg)

    # ── ADVANCED SCORING ──────────────────────────────────────────────────────
    adv = compute_advanced(
        daily_df   = df,
        weekly_df  = wdf,
        nifty_close= nifty_close,
        rsi_series = rsi_series,
        atr        = atr,
        ticker_sym = nse_symbol,
    )
    adv_score = adv["advanced_score"]

    # Add advanced reasons
    for key in ["divergence","obv","ad_cmf","vwap","weekly","rs","earnings"]:
        factor = adv.get(key, {})
        sig    = factor.get("signal","")
        score  = factor.get("score", 0)
        if score != 0 or key in ["earnings"]:
            prefix = "✅" if score > 0 else "⚠" if score < 0 else "→"
            reasons.append(f"{prefix} [{key.upper()}] {sig}")

    for p in adv["patterns"]["patterns"]:
        reasons.append(f"✅ [PATTERN] {p}")

    # ── MARKET ENVIRONMENT ADJUSTMENT ─────────────────────────────────────────
    env_score = 0
    env_label = "Unknown"
    if market_env:
        env_score = market_env.get("score", 0)
        env_label = market_env.get("label", "Unknown")
        if env_score > 0:
            reasons.append(f"✅ [MARKET] {env_label} — tailwind")
        elif env_score < 0:
            reasons.append(f"⚠ [MARKET] {env_label} — headwind")

    # ── TOTAL SCORE ───────────────────────────────────────────────────────────
    total_score = base_score + adv_score + env_score
    max_score   = 28   # 12 base + 16 advanced (env not counted in max, it's a modifier)

    # ── RECOMMENDATION ────────────────────────────────────────────────────────
    if total_score >= 18:  rec, col = "STRONG BUY", "#00c853"
    elif total_score >= 14: rec, col = "BUY",        "#69f0ae"
    elif total_score >= 9:  rec, col = "WATCH",      "#ffd600"
    elif total_score >= 4:  rec, col = "CAUTION",    "#ff6d00"
    else:                   rec, col = "AVOID",      "#ff1744"

    sector = get_ticker_sector(ticker)
    sector_trend = None
    if market_env and sector:
        all_sectors = market_env.get("sectors", {}).get("all", {})
        if sector in all_sectors:
            sector_trend = all_sectors[sector]

    return {
        # Identity
        "ticker":             ticker,
        "nse_symbol":         nse_symbol,
        "sector":             sector,
        "sector_trend":       sector_trend,
        "mentions":           mention_count,
        "mentioned_by":       users,
        "as_of_date":         str(last_date),
        "data_freshness":     freshness,
        "is_stale_data":      is_stale,
        "corp_action_warning":corp_msg,

        # Price
        "current_price":      round(current, 2),
        "day_change_pct":     round(day_pct, 2),
        "week_change_pct":    round(week_pct, 2),
        "month_change_pct":   round(month_pct, 2),
        "high_52w":           round(high_52w, 2),
        "low_52w":            round(low_52w, 2),
        "pct_from_52h":       round(pct_52h, 2),
        "pct_from_52l":       round(pct_52l, 2),

        # EMAs
        "ema20": round(e20,2), "ema50": round(e50,2), "ema200": round(e200,2),
        "above_ema20": above_e20, "above_ema50": above_e50, "above_ema200": above_e200,
        "golden_cross": golden, "death_cross": death,

        # RSI
        "rsi": round(rsi, 1), "rsi_signal": ("Overbought" if rsi>70 else "Oversold" if rsi<30 else "Neutral"),

        # MACD
        "macd": round(macd_val,3), "macd_signal_val": round(sig_val,3),
        "macd_histogram": round(hist_val,3), "histogram_expanding": abs(hist_val)>abs(hist_prev),
        "macd_bullish_cross": macd_bull_x, "macd_bearish_cross": macd_bear_x,
        "macd_above_zero": macd_pos,

        # Bollinger Bands
        "bb_upper": round(float(bb_u.iloc[-1]),2), "bb_mid": round(float(bb_m.iloc[-1]),2),
        "bb_lower": round(float(bb_l.iloc[-1]),2), "bb_squeeze": squeeze,
        "bb_signal": ("Near Upper" if bb_pct>0.85 else "Near Lower" if bb_pct<0.15 else "Mid Band"),
        "bb_squeeze_alert": squeeze,

        # Volume
        "volume_today": int(vol_now), "volume_ma20": int(vol_ma),
        "volume_ratio": round(vol_r,2),
        "volume_signal": ("High" if vol_r>1.5 else "Low" if vol_r<0.7 else "Average"),

        # ATR / Stops
        "atr": round(atr,2), "atr_pct": round(atr_pct,2),
        "suggested_stop": stop, "suggested_target": target,
        "risk_reward": rr, "stop_logic": stop_logic,
        "position_guide": pos_guide, "entry_context": entry_ctx,

        # ADX
        "adx": round(adx,1), "trend_strength": trend_str, "trend_direction": trend_dir,

        # Support/Resistance
        "recent_high_20d": round(r20d,2), "recent_low_20d": round(s20d,2),
        "dist_to_resistance_pct": round(d_res,2), "dist_to_support_pct": round(d_sup,2),
        "uptrend_structure": uptrend, "downtrend_structure": downtrend,

        # Advanced factors (full dict for report)
        "advanced":       adv,
        "adv_score":      adv_score,

        # Market environment
        "env_score":      env_score,
        "env_label":      env_label,

        # Composite
        "base_score":     base_score,
        "score":          total_score,
        "max_score":      max_score,
        "recommendation": rec,
        "rec_color":      col,
        "reasons":        reasons,
    }


def analyse_all(
    tickers: List[Dict],
    min_mentions: int = 1,
    market_env: Optional[Dict] = None,
    nifty_close: Optional[pd.Series] = None,
) -> List[Dict]:
    """Run analysis on all tickers."""
    results  = []
    filtered = [t for t in tickers if t["mentions"] >= min_mentions]
    logger.info(f"Analysing {len(filtered)} tickers with full TA suite")

    for i, t in enumerate(filtered):
        logger.info(f"[{i+1}/{len(filtered)}] {t['ticker']}...")
        result = analyse_ticker(
            t["ticker"], t["mentions"], t["users"],
            market_env=market_env, nifty_close=nifty_close
        )
        if result:
            result["tweets"]         = t.get("tweets", [])
            result["amplification"]  = t.get("amplification", 0)
            result["weighted_score"] = t.get("weighted_score", t["mentions"])
            results.append(result)
        time.sleep(1.2)   # slightly longer — two yfinance calls per ticker now

    results.sort(key=lambda x: (x["score"], x["weighted_score"]), reverse=True)
    logger.info(f"Analysis complete: {len(results)} stocks")
    return results
