"""
style_templates.py — Rule systems of world-class traders, applied as evidence.

Two codified playbooks:

  1. Minervini Trend Template (8 criteria, from "Trade Like a Stock Market Wizard")
     Criterion 8 (RS rank >= 70 vs universe) is ADAPTED: we don't maintain a full
     NSE universe ranking, so it's replaced with "63-day return beats Nifty" —
     a weaker but directionally equivalent proxy. Marked as adapted in output.

  2. Weinstein Stage Analysis (from "Secrets for Profiting in Bull and Bear Markets")
     Weekly close vs 30-week SMA and the SMA's slope classify the stock into
     Stage 1 (basing) / 2 (advancing) / 3 (topping) / 4 (declining).

DESIGN DECISION: these checks are DISPLAYED as evidence, not added to the
32-point score. They stay out of the score until the A4 scoreboard shows they
have predictive value (compare hit rates of template-passing vs failing calls).
Wiring them straight into the score before that would be adding another
uncalibrated factor — the exact problem being fixed elsewhere.
"""

import logging
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Slope is "rising"/"falling" only if it moved more than this fraction of price
# over the lookback — filters out flat-but-noisy MAs.
_SLOPE_EPS = 0.004   # 0.4%


def _series(obj) -> Optional[pd.Series]:
    """Coerce a possibly 2-D / dirty yfinance column into a clean float Series."""
    if obj is None:
        return None
    s = obj.iloc[:, 0] if isinstance(obj, pd.DataFrame) else obj
    s = pd.to_numeric(s, errors="coerce").dropna()
    return s if len(s) else None


# ═══════════════════════════════════════════════════════════════════════════════
# 1. MINERVINI TREND TEMPLATE
# ═══════════════════════════════════════════════════════════════════════════════

def minervini_trend_template(
    daily_df: pd.DataFrame,
    nifty_close: Optional[pd.Series] = None,
) -> Dict:
    """Evaluate the 8-point Trend Template on ~1y of daily data.

    Returns {"passed": int, "total": 8, "pass": bool, "criteria": {...}, "note": str}.
    "pass" requires 7/8 — Minervini treats the template as near-absolute, but the
    RS criterion here is a proxy, so one miss is tolerated.
    """
    out = {"passed": 0, "total": 8, "pass": False, "criteria": {}, "note": ""}
    close = _series(daily_df.get("Close")) if daily_df is not None else None
    if close is None or len(close) < 210:
        out["note"] = "insufficient history (<210 daily bars)"
        return out

    current = float(close.iloc[-1])
    sma50   = close.rolling(50).mean()
    sma150  = close.rolling(150).mean()
    sma200  = close.rolling(200).mean()
    s50, s150, s200 = float(sma50.iloc[-1]), float(sma150.iloc[-1]), float(sma200.iloc[-1])

    low_52w  = float(close.iloc[-252:].min())
    high_52w = float(close.iloc[-252:].max())

    # 200 SMA trending up for at least ~1 month (22 trading days)
    sma200_rising = (
        len(sma200.dropna()) >= 23
        and float(sma200.iloc[-1]) > float(sma200.iloc[-23])
    )

    # RS proxy: 63-day return vs Nifty 63-day return
    rs_ok, rs_detail = None, "no Nifty series — criterion skipped"
    if nifty_close is not None:
        n = _series(nifty_close)
        if n is not None and len(n) >= 64 and len(close) >= 64:
            stock_63 = current / float(close.iloc[-64]) - 1
            nifty_63 = float(n.iloc[-1]) / float(n.iloc[-64]) - 1
            rs_ok = stock_63 > nifty_63
            rs_detail = f"63d {stock_63*100:+.1f}% vs Nifty {nifty_63*100:+.1f}% (adapted proxy)"

    criteria = {
        "1_price_above_150_200sma":  current > s150 and current > s200,
        "2_150sma_above_200sma":     s150 > s200,
        "3_200sma_rising_1mo":       sma200_rising,
        "4_50sma_above_150_200sma":  s50 > s150 and s50 > s200,
        "5_price_above_50sma":       current > s50,
        "6_price_30pct_above_52wlo": current >= low_52w * 1.30,
        "7_within_25pct_of_52whi":   current >= high_52w * 0.75,
        "8_rs_vs_nifty_63d":         bool(rs_ok) if rs_ok is not None else False,
    }
    out["criteria"] = criteria
    out["passed"]   = sum(1 for v in criteria.values() if v)
    out["pass"]     = out["passed"] >= 7
    out["note"]     = rs_detail
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# 2. WEINSTEIN STAGE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def weinstein_stage(weekly_df: pd.DataFrame) -> Dict:
    """Classify into Weinstein stages using weekly close vs 30-week SMA + slope.

    Returns {"stage": 1|2|3|4|0, "label": str, "detail": str,
             "fresh_stage2": bool}  (0 = insufficient data)
    """
    out = {"stage": 0, "label": "unknown", "detail": "", "fresh_stage2": False}
    close = _series(weekly_df.get("Close")) if weekly_df is not None else None
    if close is None or len(close) < 35:
        out["detail"] = "insufficient history (<35 weekly bars)"
        return out

    sma30 = close.rolling(30).mean().dropna()
    if len(sma30) < 6:
        out["detail"] = "insufficient SMA history"
        return out

    price   = float(close.iloc[-1])
    ma_now  = float(sma30.iloc[-1])
    ma_prev = float(sma30.iloc[-6])          # slope over ~5 weeks
    slope   = (ma_now - ma_prev) / ma_now
    above   = price > ma_now

    if above and slope > _SLOPE_EPS:
        stage, label = 2, "Stage 2 — advancing"
    elif (not above) and slope < -_SLOPE_EPS:
        stage, label = 4, "Stage 4 — declining"
    elif above:
        stage, label = 3, "Stage 3 — topping (above flat/rolling 30wk MA)"
    else:
        stage, label = 1, "Stage 1 — basing (below flat/turning 30wk MA)"

    # Fresh Stage 2: crossed above the 30wk MA within the last 8 weeks
    fresh = False
    if stage == 2:
        tail_close = close.iloc[-9:]
        tail_ma    = sma30.iloc[-9:] if len(sma30) >= 9 else sma30
        k = min(len(tail_close), len(tail_ma))
        rel = (tail_close.iloc[-k:].values > tail_ma.iloc[-k:].values)
        fresh = (not rel.all()) and rel[-1]

    out.update({
        "stage": stage,
        "label": label,
        "fresh_stage2": bool(fresh),
        "detail": f"price {'above' if above else 'below'} 30wk MA "
                  f"(₹{ma_now:,.0f}), slope {slope*100:+.2f}%/5wk"
                  + (" — fresh breakout" if fresh else ""),
    })
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# 3. DONCHIAN / TURTLE CHANNEL BREAKOUT
# ═══════════════════════════════════════════════════════════════════════════════

def donchian_turtle(daily_df: pd.DataFrame) -> Dict:
    """Turtle entries: close breaks the prior 20-day (System 1) or 55-day
    (System 2) high channel. A different *kind* of check from Minervini/
    Weinstein — an event trigger, not a trend state — which is why it earns
    a slot despite the momentum overlap."""
    out = {"passed": 0, "total": 2, "pass": False, "criteria": {}, "note": ""}
    close = _series(daily_df.get("Close")) if daily_df is not None else None
    high  = _series(daily_df.get("High"))  if daily_df is not None else None
    if close is None or len(close) < 60:
        out["note"] = "insufficient history (<60 daily bars)"
        return out
    if high is None or len(high) != len(close):
        high = close  # degrade gracefully to close-only channels
    c = float(close.iloc[-1])
    out["criteria"] = {
        "breakout_20d": c > float(high.iloc[-21:-1].max()),
        "breakout_55d": c > float(high.iloc[-56:-1].max()),
    }
    out["passed"] = sum(1 for v in out["criteria"].values() if v)
    out["pass"]   = out["criteria"]["breakout_20d"]   # System 1 entry
    if out["criteria"]["breakout_55d"]:
        out["note"] = "55d channel break (System 2)"
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# 4. QULLAMAGGIE-STYLE FLAG BREAKOUT (adapted)
# ═══════════════════════════════════════════════════════════════════════════════

def qullamaggie_breakout(daily_df: pd.DataFrame) -> Dict:
    """Qullamaggie's breakout setup, price-only approximation: a strong
    momentum leg (>=30% run), a contained consolidation (drawdown <=25% from
    the leg peak), tightening daily ranges, elevated ADR%, and a breakout
    through the 10-day closing high.

    ADAPTATIONS, stated honestly: his flag-pivot is drawn by eye — the 10-day
    high is a mechanical stand-in. His episodic-pivot setup (earnings gaps)
    is NOT implemented here; it needs the BSE filings feed. His parabolic
    short is out of scope for a long-biased scanner.
    """
    out = {"passed": 0, "total": 5, "pass": False, "criteria": {}, "note": ""}
    if daily_df is None:
        out["note"] = "no data"
        return out
    close = _series(daily_df.get("Close"))
    high  = _series(daily_df.get("High"))
    low   = _series(daily_df.get("Low"))
    if close is None or len(close) < 100:
        out["note"] = "insufficient history (<100 daily bars)"
        return out
    if high is None or low is None or len(high) != len(close) or len(low) != len(close):
        high, low = close, close

    n = len(close)
    # Momentum leg: peak within the last ~75 bars EXCLUDING today (so a fresh
    # breakout bar doesn't become its own "leg peak"), base = trough of the
    # 40 bars preceding that peak.
    look = close.iloc[-76:-1]
    ip_rel  = int(look.values.argmax())
    ip_abs  = (n - 76) + ip_rel
    peak    = float(close.iloc[ip_abs])
    base_sl = close.iloc[max(0, ip_abs - 60):ip_abs]
    base    = float(base_sl.min()) if len(base_sl) else peak
    leg_ok  = base > 0 and (peak / base - 1) >= 0.30

    # Consolidation containment: since the peak, no close below 75% of it,
    # and at least 5 sessions of digestion.
    cons    = close.iloc[ip_abs:-1]
    cons_ok = len(cons) >= 5 and float(cons.min()) >= peak * 0.75

    # Range tightening: recent daily ranges narrower than the flag's early half
    rng      = ((high - low) / close).iloc[ip_abs:-1]
    tight_ok = (len(rng) >= 10
                and float(rng.iloc[-5:].mean()) < float(rng.iloc[:len(rng)//2].mean()))

    # ADR%: he trades movers — 20-day average daily range >= 3.5%
    adr    = float(((high - low) / close).iloc[-20:].mean() * 100)
    adr_ok = adr >= 3.5

    # Trigger: today's close takes out the prior 10-day closing high
    trig = float(close.iloc[-1]) > float(close.iloc[-11:-1].max())

    out["criteria"] = {
        "leg_30pct_run":       leg_ok,
        "flag_drawdown_le25":  cons_ok,
        "range_tightening":    tight_ok,
        "adr20_ge_3.5pct":     adr_ok,
        "breakout_10d_high":   trig,
    }
    out["passed"] = sum(1 for v in out["criteria"].values() if v)
    out["pass"]   = trig and out["passed"] >= 4     # trigger is mandatory
    out["note"]   = f"ADR20 {adr:.1f}% | leg {((peak/base-1)*100 if base>0 else 0):.0f}%"
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# COMBINED EVALUATION
# ═══════════════════════════════════════════════════════════════════════════════

def evaluate_styles(
    daily_df: pd.DataFrame,
    weekly_df: Optional[pd.DataFrame],
    nifty_close: Optional[pd.Series] = None,
) -> Dict:
    """Run all style templates. Never raises — returns whatever could be computed."""
    result = {"minervini": None, "weinstein": None, "turtle": None,
              "qullamaggie": None, "summary": ""}
    try:
        result["minervini"] = minervini_trend_template(daily_df, nifty_close)
    except Exception as e:
        logger.debug(f"Minervini check failed: {e}")
    try:
        if weekly_df is not None:
            result["weinstein"] = weinstein_stage(weekly_df)
    except Exception as e:
        logger.debug(f"Weinstein check failed: {e}")
    try:
        result["turtle"] = donchian_turtle(daily_df)
    except Exception as e:
        logger.debug(f"Turtle check failed: {e}")
    try:
        result["qullamaggie"] = qullamaggie_breakout(daily_df)
    except Exception as e:
        logger.debug(f"Qullamaggie check failed: {e}")

    parts = []
    m = result["minervini"]
    if m and m["total"]:
        parts.append(f"Minervini {m['passed']}/8" + (" ✅" if m["pass"] else ""))
    w = result["weinstein"]
    if w and w["stage"]:
        parts.append(f"Weinstein Stage {w['stage']}"
                     + (" ✅" if w["stage"] == 2 else "")
                     + (" (fresh)" if w.get("fresh_stage2") else ""))
    t = result["turtle"]
    if t and t["total"]:
        if t["pass"]:
            parts.append("Turtle 20d ✅" + (" +55d" if t["criteria"].get("breakout_55d") else ""))
    q = result["qullamaggie"]
    if q and q["total"]:
        parts.append(f"Qullamaggie {q['passed']}/5" + (" ✅" if q["pass"] else ""))
    result["summary"] = " | ".join(parts)
    return result


def style_flags(style_checks: Optional[Dict]) -> Dict[str, bool]:
    """Compact pass/fail booleans for journal logging — the shape the
    scoreboard's by_style split aggregates on. One bool per playbook."""
    sc = style_checks or {}
    m = sc.get("minervini") or {}
    w = sc.get("weinstein") or {}
    t = sc.get("turtle") or {}
    q = sc.get("qullamaggie") or {}
    return {
        "minervini":    bool(m.get("pass")),
        "weinstein_s2": w.get("stage") == 2,
        "turtle":       bool(t.get("pass")),
        "qullamaggie":  bool(q.get("pass")),
    }
