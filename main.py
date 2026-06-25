"""
main.py v5 — Full swing/positional trader pipeline
All 12 gaps addressed.
"""

import logging, os, sys
from datetime import datetime, date

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("main")

# ── Pre-market brief mode ─────────────────────────────────────────────────────
PREMARKET_MODE = os.getenv("PREMARKET_MODE", "0") == "1"


def main():
    start    = datetime.now()
    run_date = date.today().isoformat()

    # Pre-market brief (separate 8:30am run)
    if PREMARKET_MODE:
        logger.info("Running in PRE-MARKET BRIEF mode")
        from scanner.premarket_brief import run_premarket_brief
        run_premarket_brief()
        return

    logger.info("=" * 60)
    logger.info("FinTwit Daily Scanner v5 — Swing/Positional Trader")
    logger.info("=" * 60)

    from scanner.claude_client import reset_run_stats, get_run_stats
    reset_run_stats()

    # ── Account config (from secrets or defaults) ─────────────────────────────
    ACCOUNT_SIZE       = int(os.getenv("ACCOUNT_SIZE", "500000"))
    RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
    logger.info(f"Account: ₹{ACCOUNT_SIZE:,} | Risk per trade: {RISK_PER_TRADE_PCT}%")

    # ── Pre-flight checks ─────────────────────────────────────────────────────
    from scanner.cookie_health import check_and_alert
    if not check_and_alert(): sys.exit(1)

    from scanner.market_calendar import market_status_message
    market_status = market_status_message()
    logger.info(f"Market: {market_status['status_message']}")

    from scanner.nse_symbols import get_valid_symbols
    valid_symbols = get_valid_symbols()

    from scanner.persistence import load_history, update_history, save_history
    history = load_history()

    from scanner.trader_intelligence import load_trader_db, save_trader_db
    trader_db = load_trader_db()

    from scanner.trade_journal import load_journal, save_journal, get_open_positions, \
        update_position_outcomes, log_recommendation, get_performance_context
    journal = load_journal()

    # ── STEP 0: Market environment + intelligence ─────────────────────────────
    logger.info("STEP 0: Market environment + intelligence...")
    from scanner.market_environment import fetch_market_environment
    import yfinance as yf
    market_env  = fetch_market_environment()
    nifty_close = None
    try:
        ndf = yf.download("^NSEI", period="1y", interval="1d", progress=False, auto_adjust=True)
        if len(ndf) > 0:
            if hasattr(ndf.columns,"levels"): ndf.columns = [c[0] for c in ndf.columns]
            nifty_close = ndf["Close"].dropna()
    except Exception as e:
        logger.warning(f"Nifty fetch failed: {e}")

    logger.info(f"  → Market: {market_env['label']} | Nifty: {market_env['nifty']['trend']}")

    # ── STEP 1: Twitter scrape ─────────────────────────────────────────────────
    logger.info("STEP 1/8 — Twitter scrape...")
    from scanner.twitter_scraper import run as scrape_twitter
    try:
        tw = scrape_twitter()
        members, tweets = tw["members"], tw["tweets"]
        retweet_counts  = tw["retweet_counts"]
        stats           = tw["stats"]
        logger.info(f"  → {stats['member_count']} members | {stats['tweet_count']} tweets | {stats['rt_dedupe_count']} RTs deduped")
    except Exception as e:
        logger.error(f"Twitter failed: {e}"); sys.exit(1)

    if not tweets:
        from scanner.mailer import send_report
        send_report(f"<p>No tweets ({run_date}). {market_status['status_message']}</p>",
                    subject=f"⚠ FinTwit — No Data ({run_date})")
        sys.exit(0)

    # ── STEP 1b: Telegram scrape ──────────────────────────────────────────────
    logger.info("STEP 1b — Telegram scrape...")
    from scanner.telegram_scraper import (
        fetch_telegram_messages,
        telegram_available,
        normalise_for_pipeline,
    )

    telegram_tweets = []
    if telegram_available():
        try:
            raw_tg = fetch_telegram_messages(lookback_hours=48)
            telegram_tweets = normalise_for_pipeline(raw_tg)
            tg_channels = len(set(m["channel"] for m in raw_tg))
            logger.info(
                f"  → {len(telegram_tweets)} Telegram messages "
                f"from {tg_channels} channels"
            )
        except Exception as e:
            logger.warning(f"  → Telegram failed, continuing without it: {e}")
    else:
        logger.info("  → Telegram secrets not configured, skipping")

    # Merge Twitter + Telegram for STEP 2 and STEP 4.
    # `tweets` (Twitter only) is kept unchanged for STEP 3 — classify_all_accounts
    # expects Twitter account profiles, not Telegram channels.
    all_messages = tweets + telegram_tweets
    logger.info(
        f"  → Combined pipeline: {len(all_messages)} messages "
        f"({len(tweets)} Twitter + {len(telegram_tweets)} Telegram)"
    )

    # Channel credibility map — used in STEP 4 weighting block.
    # Keys are channel names (same value written to `username` after normalisation).
    channel_cred_map = {
        m["channel"]: m.get("channel_credibility", 0.5)
        for m in telegram_tweets
    }

    # ── STEP 2: Claude tweet intelligence ─────────────────────────────────────
    logger.info("STEP 2/8 — Claude tweet intelligence...")
    from scanner.tweet_intelligence import analyse_all_tweets, analyse_charts_for_tickers
    tweet_signals = analyse_all_tweets(all_messages)
    tweet_signals = analyse_charts_for_tickers(tweet_signals)
    logger.info(f"  → {len(tweet_signals)} tickers with Claude sentiment")

    # ── STEP 3: Trader intelligence ───────────────────────────────────────────
    logger.info("STEP 3/8 — Trader intelligence...")
    from scanner.trader_intelligence import classify_all_accounts
    trader_db = classify_all_accounts(members, tweets, trader_db)

    # ── STEP 4: Ticker extraction ──────────────────────────────────────────────
    logger.info("STEP 4/8 — Ticker extraction...")
    from scanner.ticker_extractor import extract_tickers
    tickers = extract_tickers(all_messages, retweet_counts, valid_symbols)

    # Apply credibility weighting
    for t in tickers:
        cred = []
        for u in t.get("users", []):
            if u in channel_cred_map:
                # Telegram channel: use credibility from telegram_channels.py (0–1 scale)
                cred.append(channel_cred_map[u])
            else:
                # Twitter account: use trader_db recommended_weight as before
                cred.append(
                    trader_db.get("accounts", {})
                              .get(u, {})
                              .get("recommended_weight", 1.0)
                )
        avg_cred = sum(cred) / max(len(cred), 1)
        sig = tweet_signals.get(t["ticker"], {})
        t["weighted_score"] = round(
            t["weighted_score"] * avg_cred
            + max(0, sig.get("sentiment_score", 0) * 0.5),
            2,
        )
    tickers.sort(key=lambda x: x["weighted_score"], reverse=True)
    logger.info(f"  → {len(tickers)} tickers extracted (regex)")

    # ── STEP 4b: Promote actionable Claude-NLP tickers (Telegram plain-text) ──────────────────────
    # Telegram channels post a stock ONCE, in plain text — no #/$ cashtags — so
    # the regex in extract_tickers() catches almost none of them. Claude already
    # identified these tickers semantically in STEP 2 (tweet_signals).
    #
    # Mention-count weighting (the Twitter model) does NOT apply: a single
    # high-conviction Telegram call is signal, not noise. So we promote a
    # Claude-found ticker (not already caught by regex, and a valid NSE symbol)
    # only if Claude judged it ACTIONABLE — not passive chatter. Gate, promote
    # if ANY of:
    #   1. conviction >= 3                      (incl. price-only calls like "ABC 450 sl 430 tgt 510")
    #   2. signal_type is directional           (breakout/pullback/reversal/momentum/value)
    #   3. directional sentiment + conviction>=2 (clear bull/bear, not neutral)
    #   4. actionable keyword in raw text        ("watchlist", "added", "explosive", ...)
    # Pure neutral conviction-1 chatter with no keyword is dropped.
    DIRECTIONAL_SIGNALS = {"breakout", "pullback", "reversal", "momentum", "value"}
    regex_ticker_set = {t["ticker"] for t in tickers}
    nlp_added = 0

    for sym, sig in tweet_signals.items():
        if sym in regex_ticker_set:
            continue  # regex already found it; STEP 4 credibility already applied
        if valid_symbols and sym not in valid_symbols:
            continue  # Claude hallucinated a non-NSE symbol — discard

        conviction   = sig.get("avg_conviction", 0) or 0
        sig_type     = sig.get("dominant_signal_type", "informational")
        sent_score   = sig.get("sentiment_score", 0) or 0
        keyword_hit  = sig.get("keyword_hit", False)

        actionable = (
            conviction >= 3
            or sig_type in DIRECTIONAL_SIGNALS
            or (abs(sent_score) >= 0.5 and conviction >= 2)
            or keyword_hit
        )
        if not actionable:
            continue  # passive chatter — skip

        # Source-aware credibility: Telegram channel cred (0–1) for channel
        # authors, trader_db recommended_weight for any Twitter authors.
        cred = []
        for u in sig.get("traders", []):
            if u in channel_cred_map:
                cred.append(channel_cred_map[u])
            else:
                cred.append(
                    trader_db.get("accounts", {})
                              .get(u, {})
                              .get("recommended_weight", 1.0)
                )
        avg_cred = sum(cred) / max(len(cred), 1)

        # Telegram scoring — credibility × conviction × sentiment clarity.
        # NO mention count: Telegram stocks appear once by design.
        if   abs(sent_score) >= 1.0: sent_mult = 1.5
        elif abs(sent_score) >= 0.5: sent_mult = 1.2
        else:                        sent_mult = 1.0
        weighted = round(avg_cred * (max(conviction, 1) / 3) * sent_mult, 2)

        tickers.append({
            "ticker":         sym,
            "mentions":       sig.get("mentions", 1),
            "amplification":  0,
            "weighted_score": weighted,
            "users":          sig.get("traders", []),
            "tweets":         [],
            "source":         "telegram" if sig.get("from_telegram") else "claude_nlp",
            "promotion_reason": (
                f"conv={conviction} type={sig_type} "
                f"sent={sent_score} kw={keyword_hit}"
            ),
        })
        nlp_added += 1

    if nlp_added:
        tickers.sort(key=lambda x: x["weighted_score"], reverse=True)
        logger.info(
            f"  → STEP 4b: {nlp_added} actionable Claude-NLP tickers promoted "
            f"(Telegram plain-text). Total: {len(tickers)}"
        )

    if not tickers:
        logger.warning("No valid tickers")
        sys.exit(0)

    ticker_list = [t["ticker"] for t in tickers]

    # ── STEP 5: Market data (FII, bulk deals, liquidity, options, macro) ──────
    logger.info("STEP 5/8 — Market intelligence...")
    from scanner.market_data import fetch_all_market_data, fetch_bulk_deals, \
        fetch_block_deals, get_deal_signal, fetch_insider_trades, get_insider_signal
    market_data = fetch_all_market_data(ticker_list)
    logger.info(f"  → FII: {market_data['fii_dii']['fii_signal']} | Global: {market_data['global_macro']['environment']}")
    logger.info(f"  → {sum(1 for v in market_data['liquidity'].values() if not v.get('liquid'))} illiquid stocks flagged")

    # ── STEP 6: Technical analysis ─────────────────────────────────────────────
    logger.info("STEP 6/8 — Technical analysis...")
    from scanner.technical_analysis import analyse_all
    min_mentions = int(os.getenv("MIN_MENTIONS","1"))

    # Build deal/insider signals per ticker
    bulk_deals  = market_data.get("bulk_deals", {})
    block_deals = market_data.get("block_deals", {})

    analysis_results = analyse_all(
        tickers,
        min_mentions   = min_mentions,
        market_env     = market_env,
        nifty_close    = nifty_close,
    )

    # Enrich with deal signals and insider activity
    for r in analysis_results:
        ticker = r["ticker"]
        bulk   = bulk_deals.get(ticker, [])
        block  = block_deals.get(ticker, [])
        deal   = get_deal_signal(ticker, bulk, block)
        r["deal_signal"] = deal

        insider_trades = fetch_insider_trades(ticker)
        insider = get_insider_signal(insider_trades)
        r["insider_signal"]  = insider
        r["insider_trades"]  = insider_trades

        # Add options data
        r["options_data"] = market_data.get("options", {}).get(ticker, {})

    # Update trade journal with current prices for stop/target tracking
    current_prices = {r["ticker"]: r["current_price"] for r in analysis_results}
    journal = update_position_outcomes(journal, current_prices)

    logger.info(f"  → {len(analysis_results)} stocks analysed")
    history = update_history(history, analysis_results, run_date)

    # ── STEP 6b: Gate filters + ranking ───────────────────────────────────────
    logger.info("STEP 6b/8 — Applying gate filters...")
    from scanner.gate_filters import apply_all_filters
    open_positions = get_open_positions(journal)
    journal_context = {
        "open_positions_detail": [
            {**p, "sector": next((r.get("sector") for r in analysis_results if r["ticker"]==p["ticker"]), "Unknown")}
            for p in open_positions
        ]
    }
    perf_context = get_performance_context(journal)

    filter_result = apply_all_filters(
        analysis_results,
        market_env       = market_env,
        nifty_close      = nifty_close,
        liquidity_data   = market_data["liquidity"],
        market_data      = market_data,
        journal_context  = journal_context,
        account_size     = ACCOUNT_SIZE,
        risk_per_trade_pct = RISK_PER_TRADE_PCT,
    )
    analysis_results  = filter_result["results"]
    gate_status       = filter_result["gate_status"]
    removed_illiquid  = filter_result["removed_illiquid"]
    short_candidates  = filter_result["short_candidates"]
    logger.info(f"  → {len(analysis_results)} stocks after filtering | Gate: {gate_status['mode']}")

    # Log recommendations to journal
    for r in analysis_results:
        log_recommendation(journal, r["ticker"], r["recommendation"], r["score"],
                          f"₹{r.get('suggested_stop',0):.0f}–₹{r.get('current_price',0):.0f}",
                          r.get("suggested_stop",0), r.get("suggested_target",0),
                          r.get("entry_context","unknown"), run_date)

    # ── STEP 7: News + Claude stock intelligence ──────────────────────────────
    logger.info("STEP 7/8 — News + AI intelligence...")
    from scanner.news_fetcher import fetch_all_news
    news_map = fetch_all_news(analysis_results)

    from scanner.stock_intelligence import enrich_all_stocks
    analysis_results, correlations, macro_context = enrich_all_stocks(
        analysis_results, news_map, tweet_signals, market_env,
    )

    from scanner.performance_tracker import detect_anomalies, generate_weekly_debrief
    anomalies      = detect_anomalies(tweet_signals, history, trader_db)
    weekly_debrief = generate_weekly_debrief(history, market_env)

    # ── STEP 8: Build report + send ───────────────────────────────────────────
    logger.info("STEP 8/8 — Report + email...")
    ai_cost = get_run_stats()
    logger.info(f"  → AI cost: ${ai_cost['total_cost_usd']:.3f} (₹{ai_cost['total_cost_inr']:.2f}) | {ai_cost['calls']} calls")

    from scanner.report_builder import build_report
    from scanner.mailer import send_report

    html = build_report(
        analysis_results = analysis_results,
        news_map         = news_map,
        history          = history,
        market_env       = market_env,
        macro_context    = macro_context,
        correlations     = correlations,
        anomalies        = anomalies,
        weekly_debrief   = weekly_debrief,
        ai_cost          = ai_cost,
        run_date         = start.strftime("%d %B %Y"),
        list_id          = os.getenv("TWITTER_LIST_ID","1506463545642217474"),
        member_count     = len(members),
        tweet_count      = len(tweets),
        rt_dedupe_count  = stats["rt_dedupe_count"],
        market_status    = market_status,
        market_data      = market_data,
        gate_status      = gate_status,
        short_candidates = short_candidates,
        removed_illiquid = removed_illiquid,
        fii_dii          = market_data.get("fii_dii", {}),
        global_macro     = market_data.get("global_macro", {}),
        upcoming_events  = market_data.get("upcoming_events", []),
    )

    buys  = sum(1 for r in analysis_results if r.get("recommendation") in ("BUY","STRONG BUY"))
    watch = sum(1 for r in analysis_results if r.get("recommendation") == "WATCH")
    hot   = [r["ticker"] for r in analysis_results if r.get("consecutive_days",0) >= 3]
    gate_icon = "🔴" if gate_status.get("mode") == "bearish" else "🟡" if gate_status.get("mode") == "cautious" else "🟢"

    subject = (
        f"📊 FinTwit {start.strftime('%d %b')} {gate_icon} [{market_env['label']}] — "
        f"{buys} Buy · {watch} Watch · {len(analysis_results)} stocks"
        + (f" 🔥 {','.join(hot)}" if hot else "")
        + (f" | {len(short_candidates)} short ideas" if short_candidates else "")
    )

    ok = send_report(html, subject=subject)
    if ok:
        save_history(history)
        save_trader_db(trader_db)
        save_journal(journal)

    elapsed = (datetime.now() - start).seconds
    from scanner.cookie_health import send_run_summary
    send_run_summary(len(analysis_results), len(members), len(tweets), elapsed, [])
    logger.info(f"{'✅' if ok else '❌'} Done in {elapsed}s | AI ₹{ai_cost['total_cost_inr']:.2f}")
    if not ok: sys.exit(1)


if __name__ == "__main__":
    main()
