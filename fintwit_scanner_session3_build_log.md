# FinTwit Daily Scanner — Session 3 Build Log

**Date:** 7 July 2026
**Starting point:** v3 (Telegram integration + STEP 4b NLP ticker promotion live)
**Theme:** Close the learning loop before adding more inputs

---

## What Was Built

| Upgrade | Status |
|---|---|
| A5 — Next-open anchoring for forward returns (bias fix) | ✅ Done + auto-migration |
| A6 — Empirical per-source credibility (earned weights) | ✅ Done |
| A7 — Style templates: Minervini Trend Template + Weinstein Stages | ✅ Done, displayed in report |

Deliberately NOT built this session: new Telegram channels, Chartink/Trendlyne
sources, BSE filings. Rationale: every new input feeds a scorer whose accuracy
numbers were biased (A5) and whose credibility weights were guesses (A6). Fix
the measurement first; new sources enter afterwards on probation.

---

## A5 — Next-Open Anchoring (`trade_journal.py`)

**The bug:** the 4pm scan runs after the 3:30pm close, so the earliest real fill
is the NEXT session's open. But `_fill_forward_returns()` measured returns from
the signal-day price — crediting the scanner an overnight gap no user could ever
capture. Every scoreboard number was inflated.

**The fix:**
- Anchor = first trading bar strictly AFTER the signal date
- Entry = that bar's **Open** (stored as `anchor_price` on each log entry)
- Exit = Close at `anchor + h − 1` (hold h sessions)
- `entry_price` (4pm capture) kept for reference only

**Auto-migration:** on the next run, `score_pending_calls()` detects any entry
scored under the old method (no `anchor_price`), wipes its returns, and
re-scores it under the new methodology. All 152 existing calls will be
re-measured consistently. One-time cost: one yfinance download per distinct
ticker in the backlog.

Synthetic test result (exaggerated 5%-gap series): old method claimed **+2.21%**
where the capturable return was **−2.66%** — a 4.87pp phantom edge, removed.

---

## A6 — Empirical Source Credibility (`trade_journal.py` + `main.py`)

Hand-set channel weights (0.60 / 0.65 / 0.75) are now priors, not verdicts.

**New in `trade_journal.py`:**
- `log_recommendation(..., sources=[...])` — every call now records WHICH
  accounts/channels flagged the ticker
- `compute_source_credibility(journal)` — per-source n / hit rate / avg 20d
  return / empirical weight (0.20–1.30 band, same scale the pipeline uses)
- `blend_credibility(prior, emp)` — shrinkage: k = n/20 capped at 1.
  * < 5 scored calls → prior untouched (small-n hit rates are coin flips)
  * 20+ scored calls → the record fully replaces the prior
- `compute_call_scoreboard()` gains `by_source` — best-to-worst source table

**In `main.py`:** both credibility loops (STEP 4 and STEP 4b) collapsed into a
single `_credibility(user)` helper that blends prior with track record. The run
log now prints top-3 / bottom-2 sources by 20d average.

**Effect:** a tipster channel that keeps losing demotes itself automatically —
no manual pruning, no arguments about which channel "feels" good. Symmetrically,
an account that keeps being right earns weight above 1.0.

---

## A7 — Style Templates (`scanner/style_templates.py`, new module)

Codified rule systems of two world-class traders, computed from the daily/weekly
frames `analyse_ticker()` already fetches (zero extra downloads):

**Minervini Trend Template (8 criteria):** price above 150/200 SMA; 150>200;
200 SMA rising ≥1 month; 50>150>200; price above 50 SMA; ≥30% above 52w low;
within 25% of 52w high; RS criterion **adapted** to "63d return beats Nifty"
(no full-universe RS ranking maintained — flagged as proxy in output).
Pass = 7/8.

**Weinstein Stage Analysis:** weekly close vs 30-week SMA + slope →
Stage 1 (basing) / 2 (advancing) / 3 (topping) / 4 (declining), with a
"fresh Stage 2" flag when the cross above the 30wk MA happened within 8 weeks.

**Design decision — templates are DISPLAYED, not scored.** They appear as a
badge on each stock card ("🏛 Minervini 7/8 ✅ | Weinstein Stage 2 ✅ (fresh)")
but add zero points to the 32-point score. They enter the score only after the
A4 scoreboard shows template-passing calls outperform template-failing ones.
Wiring them in earlier would add another uncalibrated factor — the exact
disease A5/A6 treat.

**Why not "learn" the traders directly:** their actual trades aren't public
data. What's public — and codeable — is their rule systems. Imitating their
tweets would teach the model their marketing, not their P&L.

---

## Files Changed

| File | Change |
|---|---|
| `scanner/style_templates.py` | **NEW** — Minervini + Weinstein evaluators |
| `scanner/trade_journal.py` | Next-open anchoring, auto-migration, `sources` on log entries, `compute_source_credibility`, `blend_credibility`, `by_source` scoreboard |
| `scanner/technical_analysis.py` | Calls `evaluate_styles()`, attaches `style_checks` to results |
| `scanner/report_builder.py` | Style badge line on each stock card (3.11-safe f-strings) |
| `main.py` | `_credibility()` blended helper replaces both hand-set lookups; `sources` passed to journal; source track-record logging |
| `test_upgrades.py` | **NEW** — offline test suite (yfinance stubbed), all 3 upgrades verified |

No new dependencies. No schema-breaking changes — old journal entries migrate
automatically on the next run.

---

## Verification (offline, yfinance stubbed)

```
TEST 1 PASS — style templates: Minervini 8/8 ✅ | Weinstein Stage 2 ✅
              (synthetic uptrend passes, downtrend fails 2/8 + Stage 4)
TEST 2 PASS — anchored ret_5d=-2.66% vs old biased +2.21%
              (overnight gap of +4.87pp removed)
TEST 3 PASS — good_channel w=0.824, bad_channel w=0.32 after 20 scored calls;
              thin evidence (n=3) leaves prior untouched; by_source ranked
migration OK — pre-anchor scored entry re-scored under new methodology
All 5 touched files compile clean
```

Not verified offline (needs live run): yfinance Open-column availability for
every NSE symbol (fallback to Close is in place), report badge rendering in
Gmail, and the one-time migration cost on the real 152-call backlog.

---

## Housekeeping Flagged

- **Node 20 deprecation warning** on run #34: bump `actions/checkout` and
  `actions/setup-python` in `daily_scan.yml` before GitHub force-migrates.

---

## What the Next 4–6 Weeks Should Produce (no code needed)

The loop is now closed. Let it run. By ~30 scored calls per major source:
1. `by_source` tells you which of the 8 Telegram channels earn their weight —
   prune the losers, promote the winners (it happens automatically, but read it)
2. Compare hit rates of Minervini-passing vs failing calls → decide whether
   templates enter the score, and at what weight
3. `by_score` buckets under the unbiased methodology → recalibrate the
   Strong Buy / Buy / Watch thresholds against reality

## Next Session Agenda (unchanged priorities 4–5)

1. BSE Announcements API — quarterly results as catalyst signal
2. MF portfolio disclosures (AMFI) — smart-money delta
3. CANSLIM template (unlocked once fundamentals from #1 exist)
4. Optional: lightweight backtest harness to calibrate factors on history
   instead of waiting for forward data

---

*Session 3 completed 7 July 2026. Measurement fixed; learning loop live.*
