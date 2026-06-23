"""
gate_filters.py
Smart filtering and ranking for swing/positional traders.

Gap fixes:
  Gap 1:  Entry timing signal (is this stock at a valid entry NOW?)
  Gap 2:  Nifty gate — suppress buys in downtrend
  Gap 5:  Liquidity filter — minimum turnover gate
  Gap 6:  Nifty gate integration into recommendation
  Gap 7:  Relative strength as primary ranking criterion
  Gap 8:  Volatility-adjusted position sizing with account context
  Gap 11: Short signals when market is bearish
"""

import logging
from datetime import date
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. NIFTY GATE
# ═══════════════════════════════════════════════════════════════════════════════

def apply_nifty_gate(
    analysis_results: List[Dict],
    market_env: Dict,
    nifty_close: Optional[pd.Series] = None,
) -> Tuple[List[Dict], Dict]:
    """
    Gap 2 + Gap 6: Suppress BUY signals when Nifty is below 200 DMA.

    Rules:
    - Nifty below 200 DMA: no new BUY or STRONG BUY signals
    - Nifty below 50 DMA but above 200 DMA: reduce BUY to WATCH
    - Nifty in downtrend structure: flag all buys with warning
    - Nifty in uptrend: normal signals

    Returns (filtered_results, gate_status_dict)
    """
    gate_status = {
        "applied": False,
        "reason":  "",
        "nifty_vs_200dma": None,
        "nifty_vs_50dma":  None,
        "mode":    "normal",   # normal / cautious / bearish
    }

    nifty = market_env.get("nifty", {})
    above_200 = nifty.get("above_ema200")
    trend     = nifty.get("trend", "Unknown")

    if nifty_close is not None and len(nifty_close) >= 200:
        current_nifty = float(nifty_close.iloc[-1])
        ema200_nifty  = float(nifty_close.ewm(span=200, adjust=False).mean().iloc[-1])
        ema50_nifty   = float(nifty_close.ewm(span=50,  adjust=False).mean().iloc[-1])

        gate_status["nifty_vs_200dma"] = round(((current_nifty - ema200_nifty) / ema200_nifty) * 100, 2)
        gate_status["nifty_vs_50dma"]  = round(((current_nifty - ema50_nifty)  / ema50_nifty)  * 100, 2)
        above_200 = current_nifty > ema200_nifty
        above_50  = current_nifty > ema50_nifty
    else:
        above_200 = nifty.get("above_ema200", True)
        above_50  = True

    if not above_200:
        # Bear market gate — demote all BUY/STRONG BUY signals
        gate_status["applied"] = True
        gate_status["mode"]    = "bearish"
        gate_status["reason"]  = (
            f"Nifty below 200 DMA ({gate_status.get('nifty_vs_200dma', 0):.1f}%) — "
            f"new long positions suppressed. Only WATCH/CAUTION signals active."
        )
        for r in analysis_results:
            if r.get("recommendation") in ("STRONG BUY", "BUY"):
                r["recommendation"] = "WATCH"
                r["rec_color"]       = "#ffd600"
                r["gate_warning"]    = gate_status["reason"]
                r["score"]           = min(r.get("score", 0), 8)   # cap score
                r["reasons"].append(f"⚠ [NIFTY GATE] Below 200 DMA — BUY downgraded to WATCH")

    elif not above_50:
        # Cautious — reduce conviction
        gate_status["applied"] = True
        gate_status["mode"]    = "cautious"
        gate_status["reason"]  = (
            f"Nifty below 50 DMA — be cautious with new entries. Reduce position size."
        )
        for r in analysis_results:
            if r.get("recommendation") == "STRONG BUY":
                r["recommendation"] = "BUY"
                r["rec_color"]       = "#69f0ae"
                r["gate_warning"]    = gate_status["reason"]
                r["reasons"].append("⚠ [NIFTY GATE] Below 50 DMA — STRONG BUY reduced to BUY")

    logger.info(f"Nifty gate: {gate_status['mode']} | {gate_status.get('reason','All clear')}")
    return analysis_results, gate_status


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LIQUIDITY FILTER
# ═══════════════════════════════════════════════════════════════════════════════

def apply_liquidity_filter(
    analysis_results: List[Dict],
    liquidity_data: Dict[str, Dict],
) -> Tuple[List[Dict], List[str]]:
    """
    Gap 5: Remove illiquid stocks from analysis results.
    Returns (filtered_results, removed_tickers).
    """
    filtered  = []
    removed   = []

    for r in analysis_results:
        ticker = r["ticker"]
        liq    = liquidity_data.get(ticker, {})

        if liq.get("liquid", True):
            # Add liquidity info to result
            r["avg_turnover_cr"] = liq.get("avg_turnover_cr", 0)
            r["avg_volume"]      = liq.get("avg_volume", 0)
            filtered.append(r)
        else:
            reason = liq.get("reason", "Insufficient liquidity")
            logger.info(f"Filtered {ticker}: {reason}")
            removed.append(ticker)

    if removed:
        logger.info(f"Liquidity filter removed {len(removed)} stocks: {', '.join(removed)}")

    return filtered, removed


# ═══════════════════════════════════════════════════════════════════════════════
# 3. RELATIVE STRENGTH RANKING (primary sort)
# ═══════════════════════════════════════════════════════════════════════════════

def rank_by_relative_strength(
    analysis_results: List[Dict],
) -> List[Dict]:
    """
    Gap 7: Re-rank stocks by 10-day relative strength vs Nifty.
    RS is the single best predictor of near-term swing outperformance.

    Ranking formula:
      final_rank_score = (rs_10d * 0.4) + (ta_score_normalised * 0.3) + (sentiment_score * 0.3)

    Where:
      rs_10d = 10-day return vs Nifty (from advanced TA rs module)
      ta_score = current TA score (0-28)
      sentiment_score = Claude tweet sentiment (-2 to +2)
    """
    for r in analysis_results:
        adv = r.get("advanced", {})
        rs  = adv.get("rs", {})

        rs_10d        = rs.get("rs_1m", 0) or 0   # using 1M as proxy for ~10D
        ta_score      = r.get("score", 0)
        ta_normalised = (ta_score / max(r.get("max_score", 28), 1)) * 10   # 0-10

        ts = r.get("tweet_signal", {})
        sentiment = ts.get("sentiment_score", 0) or 0

        # Conviction weight from tweet analysis
        conviction = ts.get("avg_conviction", 3) or 3

        rank_score = (
            (rs_10d * 0.4) +
            (ta_normalised * 0.3) +
            (sentiment * conviction / 5 * 0.3)
        )

        r["rs_10d"]          = round(rs_10d, 2)
        r["rank_score"]      = round(rank_score, 2)
        r["rs_signal"]       = rs.get("signal", "Unknown")

    # Sort by rank_score descending
    analysis_results.sort(key=lambda x: x.get("rank_score", 0), reverse=True)
    logger.info(f"RS ranking applied — top stock: {analysis_results[0]['ticker']} (RS={analysis_results[0].get('rs_10d',0):+.1f}%)" if analysis_results else "No results to rank")

    return analysis_results


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ENTRY TIMING SIGNAL
# ═══════════════════════════════════════════════════════════════════════════════

def assess_entry_timing(r: Dict) -> Dict:
    """
    Gap 1: Is this stock at a valid entry point RIGHT NOW?

    Checks:
    - Distance from ideal entry zone (pullback to key MA or breakout level)
    - Volume pattern today (is volume confirming or warning?)
    - Day's candle relative to range (closing near high vs near low)
    - Whether stock is extended from last base

    Returns entry_timing dict with verdict and score.
    """
    current   = r.get("current_price", 0)
    ema20     = r.get("ema20", 0)
    ema50     = r.get("ema50", 0)
    vol_ratio = r.get("volume_ratio", 1.0)
    adx       = r.get("adx", 0)
    bb_pct    = (current - r.get("bb_lower", current)) / max(r.get("bb_upper", current) - r.get("bb_lower", current), 1)
    pct_52l   = r.get("pct_from_52l", 0)
    d_res     = r.get("dist_to_resistance_pct", 5)
    entry_ctx = r.get("entry_context", "pullback")

    timing_score = 0
    notes        = []

    # 1. Pullback to EMA (ideal entry for trending stocks)
    pct_above_e20 = ((current - ema20) / max(ema20, 1)) * 100
    pct_above_e50 = ((current - ema50) / max(ema50, 1)) * 100

    if entry_ctx == "pullback":
        if 0 < pct_above_e20 < 3:
            timing_score += 2
            notes.append("✅ Pulled back to 20 EMA — ideal entry zone")
        elif 0 < pct_above_e20 < 7:
            timing_score += 1
            notes.append("✅ Near 20 EMA — acceptable entry")
        elif pct_above_e20 > 15:
            timing_score -= 2
            notes.append(f"⚠ Extended {pct_above_e20:.1f}% above 20 EMA — wait for pullback")
        elif pct_above_e20 < 0:
            timing_score -= 1
            notes.append("⚠ Below 20 EMA — pullback may continue")

    elif entry_ctx == "breakout":
        if d_res < 1:
            timing_score += 2
            notes.append("✅ At breakout level — valid entry on close above resistance")
        elif d_res < 3:
            timing_score += 1
            notes.append("✅ Approaching resistance — watch for breakout")
        else:
            timing_score -= 1
            notes.append(f"⚠ {d_res:.1f}% from resistance — not yet at breakout level")

    # 2. Volume today
    if vol_ratio > 1.5 and r.get("day_change_pct", 0) > 0:
        timing_score += 1
        notes.append(f"✅ High volume {vol_ratio:.1f}x on up day — strong participation")
    elif vol_ratio < 0.7 and entry_ctx == "breakout":
        timing_score -= 1
        notes.append("⚠ Low volume breakout — weak signal, wait for volume confirmation")

    # 3. Extended from 52W low (too extended = mean reversion risk)
    if pct_52l > 150:
        timing_score -= 2
        notes.append(f"⚠ {pct_52l:.0f}% above 52W low — highly extended, risk of pullback")
    elif pct_52l > 80:
        timing_score -= 1
        notes.append(f"⚠ {pct_52l:.0f}% above 52W low — extended")

    # 4. Bollinger Band position
    if entry_ctx == "pullback" and bb_pct < 0.3:
        timing_score += 1
        notes.append("✅ Near lower BB — oversold within trend")
    elif bb_pct > 0.9:
        timing_score -= 1
        notes.append("⚠ Near upper BB — overbought short-term")

    # Verdict
    if timing_score >= 3:
        verdict = "Strong Entry"
        verdict_color = "#00c853"
    elif timing_score >= 1:
        verdict = "Valid Entry"
        verdict_color = "#69f0ae"
    elif timing_score == 0:
        verdict = "Neutral — Wait"
        verdict_color = "#ffd600"
    elif timing_score >= -2:
        verdict = "Suboptimal — Be Patient"
        verdict_color = "#ff6d00"
    else:
        verdict = "Poor Timing — Avoid"
        verdict_color = "#ff1744"

    return {
        "timing_score":  timing_score,
        "verdict":       verdict,
        "verdict_color": verdict_color,
        "notes":         notes,
        "pct_above_e20": round(pct_above_e20, 1),
        "pct_above_e50": round(pct_above_e50, 1),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 5. VOLATILITY-ADJUSTED POSITION SIZING
# ═══════════════════════════════════════════════════════════════════════════════

def compute_position_size(
    r: Dict,
    account_size: int,
    risk_per_trade_pct: float,
    open_positions: List[Dict],
    market_env: Dict,
    liquidity_data: Dict,
    fii_dii: Dict,
    event_score: int,
) -> Dict:
    """
    Gap 8: Full volatility-adjusted position sizing.

    Factors:
    - ATR-based risk (how far stop is from entry)
    - Account size and risk per trade %
    - Market environment (reduce in Cautious/Bear)
    - FII/DII flows (reduce when FIIs selling)
    - Number of open positions (limit concentration)
    - Sector concentration (open positions in same sector)
    - Liquidity (can we get out?)
    - Event proximity (reduce before events)
    """
    ticker  = r["ticker"]
    current = r.get("current_price", 0)
    stop    = r.get("suggested_stop", 0)
    sector  = r.get("sector", "Unknown")

    if current <= 0 or stop <= 0 or current <= stop:
        return {"shares": 0, "value": 0, "risk_amount": 0, "notes": ["Invalid price/stop"]}

    risk_per_share = current - stop
    risk_amount    = account_size * (risk_per_trade_pct / 100)

    # Base shares from risk
    base_shares = int(risk_amount / risk_per_share)
    base_value  = base_shares * current

    # Adjustment multipliers
    multiplier = 1.0
    notes      = []

    # Market environment adjustment
    env_score = market_env.get("score", 0)
    if env_score <= -2:
        multiplier *= 0.5
        notes.append("50% size: Cautious/Bear market")
    elif env_score == -1:
        multiplier *= 0.75
        notes.append("75% size: Weak market")
    elif env_score >= 2:
        multiplier *= 1.0   # full size in strong bull
        notes.append("Full size: Bull market")

    # FII/DII adjustment
    fii_score = fii_dii.get("fii_score", 0)
    fii_streak = fii_dii.get("fii_streak", 0)
    if fii_streak <= -3:
        multiplier *= 0.75
        notes.append("75% size: FII selling 3+ consecutive days")
    elif fii_score == -2:
        multiplier *= 0.75
        notes.append("75% size: Heavy FII selling today")

    # Event proximity
    if event_score == -1:
        multiplier *= 0.75
        notes.append("75% size: Major event in <3 days")

    # Open position count (max 6 simultaneous)
    open_count = len(open_positions)
    if open_count >= 6:
        multiplier = 0
        notes.append("0 size: Maximum 6 positions reached — no new entries")
    elif open_count >= 4:
        multiplier *= 0.75
        notes.append("75% size: Already 4+ open positions")

    # Sector concentration (max 2 in same sector)
    same_sector = sum(1 for p in open_positions if p.get("sector") == sector)
    if same_sector >= 2:
        multiplier *= 0.5
        notes.append(f"50% size: Already 2 positions in {sector}")

    # Liquidity cap (don't exceed 2% of avg daily volume)
    liq = liquidity_data.get(ticker, {})
    avg_vol = liq.get("avg_volume", 0)
    if avg_vol > 0:
        max_shares_from_liquidity = int(avg_vol * 0.02)
        if base_shares > max_shares_from_liquidity:
            multiplier *= 0.5
            notes.append(f"Size capped at 2% of avg daily volume ({avg_vol:,})")

    # Apply multiplier
    final_shares = max(0, int(base_shares * multiplier))
    final_value  = final_shares * current
    final_risk   = final_shares * risk_per_share

    # Position as % of account
    pct_of_account = round((final_value / max(account_size, 1)) * 100, 1)

    # Cap at 20% of account per position
    if pct_of_account > 20:
        final_shares   = int(account_size * 0.20 / current)
        final_value    = final_shares * current
        final_risk     = final_shares * risk_per_share
        pct_of_account = 20.0
        notes.append("Capped at 20% of account per position")

    return {
        "shares":          final_shares,
        "value":           round(final_value),
        "risk_amount":     round(final_risk),
        "risk_pct":        round((final_risk / max(account_size, 1)) * 100, 2),
        "pct_of_account":  pct_of_account,
        "multiplier":      round(multiplier, 2),
        "notes":           notes,
        "sizing_summary": (
            f"{final_shares} shares @ ₹{current:.0f} = ₹{final_value:,.0f} "
            f"({pct_of_account}% of account) | Risk ₹{final_risk:,.0f} ({round((final_risk/max(account_size,1))*100,2)}%)"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 6. SHORT SIGNALS (Gap 11)
# ═══════════════════════════════════════════════════════════════════════════════

def identify_short_candidates(
    analysis_results: List[Dict],
    gate_status: Dict,
    market_env: Dict,
) -> List[Dict]:
    """
    Gap 11: Identify stocks suitable for short selling.
    Only activates in bearish market environment.
    Returns list of short candidates with rationale.
    """
    if gate_status.get("mode") not in ("bearish", "cautious"):
        return []

    shorts = []
    for r in analysis_results:
        score    = r.get("score", 0)
        rsi      = r.get("rsi", 50)
        trend    = r.get("trend_direction", "")
        above_50 = r.get("above_ema50", True)
        adx      = r.get("adx", 0)
        ret_1m   = r.get("month_change_pct", 0)
        rs       = r.get("rs_10d", 0)

        short_score = 0
        reasons     = []

        # Bearish indicators
        if trend == "Bearish":        short_score += 2; reasons.append("Bearish trend direction")
        if not above_50:              short_score += 1; reasons.append("Below 50 EMA")
        if rsi > 65:                  short_score += 1; reasons.append(f"RSI overbought {rsi:.0f}")
        if ret_1m > 20:               short_score += 1; reasons.append(f"Extended +{ret_1m:.0f}% in 1M")
        if rs < -3:                   short_score += 1; reasons.append(f"RS {rs:.1f}% vs Nifty")
        if r.get("death_cross"):      short_score += 2; reasons.append("Death cross")
        if r.get("macd_bearish_cross"): short_score += 1; reasons.append("MACD bearish cross")

        # Bullish indicators that reduce short conviction
        if r.get("above_ema200"):     short_score -= 1
        if r.get("golden_cross"):     short_score -= 2

        if short_score >= 4:
            shorts.append({
                "ticker":      r["ticker"],
                "short_score": short_score,
                "reasons":     reasons,
                "entry_short": r.get("current_price"),
                "cover_target":r.get("suggested_stop"),   # stop becomes target for shorts
                "stop_short":  r.get("suggested_target"), # target becomes stop for shorts
                "rec_color":   "#ff1744",
            })

    shorts.sort(key=lambda x: x["short_score"], reverse=True)
    if shorts:
        logger.info(f"Short candidates: {[s['ticker'] for s in shorts[:3]]}")
    return shorts[:5]


# ═══════════════════════════════════════════════════════════════════════════════
# APPLY ALL FILTERS IN SEQUENCE
# ═══════════════════════════════════════════════════════════════════════════════

def apply_all_filters(
    analysis_results: List[Dict],
    market_env: Dict,
    nifty_close,
    liquidity_data: Dict,
    market_data: Dict,
    journal_context: Dict,
    account_size: int = 500000,
    risk_per_trade_pct: float = 1.0,
) -> Dict:
    """
    Apply all filters and ranking in correct order.
    Returns comprehensive filtered + ranked + sized results.
    """
    logger.info(f"Applying filters to {len(analysis_results)} stocks...")

    # 1. Liquidity filter (remove illiquid stocks)
    analysis_results, removed_illiquid = apply_liquidity_filter(
        analysis_results, liquidity_data
    )
    logger.info(f"  After liquidity filter: {len(analysis_results)} stocks ({len(removed_illiquid)} removed)")

    # 2. Nifty gate (suppress buys in downtrend)
    analysis_results, gate_status = apply_nifty_gate(
        analysis_results, market_env, nifty_close
    )

    # 3. Entry timing assessment (per stock)
    for r in analysis_results:
        r["entry_timing"] = assess_entry_timing(r)

    # 4. Position sizing (per stock)
    open_positions = journal_context.get("open_positions_detail", [])
    fii_dii        = market_data.get("fii_dii", {})
    event_score    = market_data.get("event_score", 0)

    for r in analysis_results:
        r["position_sizing"] = compute_position_size(
            r, account_size, risk_per_trade_pct,
            open_positions, market_env, liquidity_data,
            fii_dii, event_score,
        )

    # 5. Relative strength ranking (primary sort)
    analysis_results = rank_by_relative_strength(analysis_results)

    # 6. Short candidates (only in bearish market)
    short_candidates = identify_short_candidates(analysis_results, gate_status, market_env)

    logger.info(f"Filters complete: {len(analysis_results)} long candidates, {len(short_candidates)} short candidates")

    return {
        "results":            analysis_results,
        "gate_status":        gate_status,
        "removed_illiquid":   removed_illiquid,
        "short_candidates":   short_candidates,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test with mock data
    mock_results = [
        {"ticker": "WABAG", "score": 18, "max_score": 28, "recommendation": "STRONG BUY",
         "rec_color": "#00c853", "current_price": 1882, "ema20": 1820, "ema50": 1650,
         "ema200": 1200, "above_ema20": True, "above_ema50": True, "above_ema200": True,
         "golden_cross": True, "death_cross": False, "adx": 32, "trend_direction": "Bullish",
         "volume_ratio": 1.5, "day_change_pct": 4.2, "month_change_pct": 37,
         "suggested_stop": 1750, "suggested_target": 2100, "dist_to_resistance_pct": 2,
         "bb_lower": 1600, "bb_upper": 1950, "pct_from_52l": 98, "rsi": 68,
         "entry_context": "breakout", "sector": "Energy",
         "advanced": {"rs": {"rs_1m": 12.5, "signal": "Strong Outperformer"}},
         "tweet_signal": {"sentiment_score": 1.5, "avg_conviction": 4.0},
         "macd_above_zero": True, "macd_bullish_cross": False, "macd_bearish_cross": False,
         "reasons": ["Price above 200 EMA"]},
        {"ticker": "SYRMA", "score": 8, "max_score": 28, "recommendation": "WATCH",
         "rec_color": "#ffd600", "current_price": 450, "ema20": 440, "ema50": 420,
         "ema200": 380, "above_ema20": True, "above_ema50": True, "above_ema200": True,
         "golden_cross": True, "death_cross": False, "adx": 18, "trend_direction": "Bullish",
         "volume_ratio": 0.8, "day_change_pct": 0.5, "month_change_pct": 8,
         "suggested_stop": 415, "suggested_target": 520, "dist_to_resistance_pct": 5,
         "bb_lower": 410, "bb_upper": 480, "pct_from_52l": 45, "rsi": 55,
         "entry_context": "pullback", "sector": "Electronics",
         "advanced": {"rs": {"rs_1m": -2.1, "signal": "Underperformer"}},
         "tweet_signal": {"sentiment_score": 0.5, "avg_conviction": 2.5},
         "macd_above_zero": True, "macd_bullish_cross": False, "macd_bearish_cross": False,
         "reasons": ["Price above 200 EMA"]},
    ]

    mock_market_env = {"score": 1, "label": "Mildly Bullish", "color": "#69f0ae",
                       "nifty": {"trend": "Bull", "above_ema200": True}}
    mock_liquidity  = {"WABAG": {"liquid": True, "avg_turnover_cr": 25, "avg_volume": 300000},
                       "SYRMA": {"liquid": False, "avg_turnover_cr": 1.2, "avg_volume": 50000,
                                 "reason": "Low turnover ₹1.2Cr/day"}}
    mock_market_data= {"fii_dii": {"fii_score": 1, "fii_streak": 2},
                       "event_score": 0}
    mock_journal    = {"open_positions_detail": []}

    result = apply_all_filters(
        mock_results, mock_market_env, None,
        mock_liquidity, mock_market_data, mock_journal,
        account_size=500000, risk_per_trade_pct=1.0
    )

    print("=== FILTERED RESULTS ===")
    for r in result["results"]:
        ps = r.get("position_sizing", {})
        et = r.get("entry_timing", {})
        print(f"\n{r['ticker']}: {r['recommendation']} | RS={r.get('rs_10d',0):+.1f}% | rank={r.get('rank_score',0):.1f}")
        print(f"  Entry timing: {et.get('verdict','?')} (score={et.get('timing_score',0)})")
        print(f"  Position size: {ps.get('sizing_summary','?')}")
        for note in et.get("notes", []):
            print(f"  {note}")

    print(f"\nGate status: {result['gate_status']['mode']}")
    print(f"Removed (illiquid): {result['removed_illiquid']}")
    print(f"Short candidates: {[s['ticker'] for s in result['short_candidates']]}")
