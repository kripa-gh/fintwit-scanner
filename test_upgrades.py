"""
Offline verification for the Session-3 upgrades. Run: python3 test_upgrades.py
No network needed — yfinance is stubbed.
"""
import sys, types, json
from datetime import date, timedelta
import numpy as np
import pandas as pd

sys.path.insert(0, ".")

# ── Stub yfinance BEFORE any scanner import ──────────────────────────────────
fake_yf = types.ModuleType("yfinance")

def _make_price_df(days=300, start=100.0, drift=0.15):
    idx = pd.bdate_range(end=date.today(), periods=days)
    closes = start * np.cumprod(1 + np.full(days, drift / days) + 0.0)
    opens = closes * 0.995  # open slightly below prior close path
    return pd.DataFrame({"Open": opens, "Close": closes,
                         "High": closes * 1.01, "Low": closes * 0.99,
                         "Volume": 1_000_000}, index=idx)

_FAKE_DF = _make_price_df()
fake_yf.download = lambda *a, **k: _FAKE_DF.copy()
sys.modules["yfinance"] = fake_yf

# ═══ TEST 1: style templates ═════════════════════════════════════════════════
from scanner.style_templates import minervini_trend_template, weinstein_stage, evaluate_styles

def uptrend_daily(n=300):
    idx = pd.bdate_range(end=date.today(), periods=n)
    base = np.linspace(100, 175, n) + np.random.RandomState(1).normal(0, 0.8, n)
    return pd.DataFrame({"Close": base, "High": base*1.01, "Low": base*0.99}, index=idx)

def downtrend_daily(n=300):
    idx = pd.bdate_range(end=date.today(), periods=n)
    base = np.linspace(180, 100, n) + np.random.RandomState(2).normal(0, 0.8, n)
    return pd.DataFrame({"Close": base, "High": base*1.01, "Low": base*0.99}, index=idx)

def weekly_from_daily(df):
    return df.resample("W").last().dropna()

nifty_flat = pd.Series(np.linspace(100, 104, 300),
                       index=pd.bdate_range(end=date.today(), periods=300))

up = uptrend_daily(); dn = downtrend_daily()
m_up = minervini_trend_template(up, nifty_flat)
m_dn = minervini_trend_template(dn, nifty_flat)
assert m_up["pass"], f"uptrend should pass template: {m_up}"
assert not m_dn["pass"], f"downtrend must fail template: {m_dn['passed']}/8"
assert m_dn["passed"] <= 2, m_dn

w_up = weinstein_stage(weekly_from_daily(up))
w_dn = weinstein_stage(weekly_from_daily(dn))
assert w_up["stage"] == 2, w_up
assert w_dn["stage"] == 4, w_dn

styles = evaluate_styles(up, weekly_from_daily(up), nifty_flat)
assert "Minervini" in styles["summary"] and "Stage 2" in styles["summary"], styles["summary"]
print("TEST 1 PASS — style templates:", styles["summary"])

# ═══ TEST 2: next-open anchoring ════════════════════════════════════════════
from scanner.trade_journal import _fill_forward_returns, SCORE_HORIZONS

# Deterministic series: signal date = 30 bars ago; big overnight gap next open
idx = pd.bdate_range(end=date.today(), periods=60)
close = pd.Series(np.linspace(100, 130, 60), index=idx)
opens = close.shift(1).fillna(close.iloc[0]) * 1.05  # +5% gap every open (exaggerated)
_FAKE_DF_2 = pd.DataFrame({"Open": opens, "Close": close})
fake_yf.download = lambda *a, **k: _FAKE_DF_2.copy()

signal_date = idx[30].date()
entry = {"date": signal_date.isoformat(), "ticker": "TEST", "entry_price": float(close.iloc[30]),
         "recommendation": "BUY", "ret_5d": None, "ret_20d": None, "scored": False}
_fill_forward_returns("TEST", [entry])

anchor_expected = float(opens.iloc[31])            # next session's open
exit5_expected  = float(close.iloc[31 + 5 - 1])    # close 5 sessions from anchor
manual_ret5 = round((exit5_expected - anchor_expected) / anchor_expected * 100, 2)
assert entry["anchor_price"] == round(anchor_expected, 2), (entry["anchor_price"], anchor_expected)
assert entry["ret_5d"] == manual_ret5, (entry["ret_5d"], manual_ret5)
# Old method would have used entry_price (signal close) — confirm they differ (gap excluded)
old_ret5 = round((float(close.iloc[30+5]) - entry["entry_price"]) / entry["entry_price"] * 100, 2)
assert entry["ret_5d"] != old_ret5, "anchoring made no difference — bias fix not active"
assert entry["scored"] and entry["outcome"] in ("win", "loss")
print(f"TEST 2 PASS — anchored ret_5d={entry['ret_5d']}% vs old biased {old_ret5}% "
      f"(gap of {old_ret5 - entry['ret_5d']:+.2f}pp removed)")

# ═══ TEST 3: empirical source credibility + blending ═══════════════════════
from scanner.trade_journal import compute_source_credibility, blend_credibility, \
    log_recommendation, compute_call_scoreboard

journal = {"log": [], "positions": [], "stats": {}}
# Good source: 20 calls, 70% win, +4% avg | Bad source: 20 calls, 30% win, -3% avg
for i in range(20):
    for src, win, r20 in (("good_channel", i < 14, 4.0), ("bad_channel", i < 6, -3.0)):
        journal["log"].append({
            "date": (date.today() - timedelta(days=40+i)).isoformat(),
            "ticker": f"T{i}", "recommendation": "BUY", "score": 15,
            "entry_price": 100.0, "sources": [src],
            "ret_5d": 1.0, "ret_20d": r20 if win else -abs(r20),
            "outcome": "win" if win else "loss", "scored": True, "origin": "telegram_only",
        })

emp = compute_source_credibility(journal)
g, b = emp["good_channel"], emp["bad_channel"]
assert g["empirical_weight"] > 0.75, g
assert b["empirical_weight"] < 0.45, b
# Blending: 20 scored calls → data fully overrides the hand-set 0.60 prior
assert blend_credibility(0.60, g) == g["empirical_weight"]
assert blend_credibility(0.60, b) == b["empirical_weight"]
# Thin evidence: n=3 keeps the prior untouched
assert blend_credibility(0.60, {"n": 3, "empirical_weight": 1.3}) == 0.60
# No record at all: prior untouched
assert blend_credibility(0.75, None) == 0.75

# log_recommendation records sources; scoreboard splits by source
log_recommendation(journal, "ABC", "BUY", 16, "z", 95, 120, "breakout",
                   entry_price=100.0, origin="both", sources=["good_channel", "trader_x"])
assert journal["log"][-1]["sources"] == ["good_channel", "trader_x"]
sb = compute_call_scoreboard(journal)
assert "good_channel" in sb["by_source"] and "bad_channel" in sb["by_source"]
assert list(sb["by_source"])[0] == "good_channel", "by_source should rank best first"
print(f"TEST 3 PASS — good_channel w={g['empirical_weight']} "
      f"bad_channel w={b['empirical_weight']} | scoreboard by_source ranked")

print("\nALL TESTS PASS")
