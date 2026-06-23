"""
main.py v4 — Full AI-powered pipeline
All 10 gaps fixed + complete Claude intelligence suite
"""

import logging, os, sys
from datetime import datetime, date

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("main")


def main():
    start    = datetime.now()
    run_date = date.today().isoformat()
    errors   = []

    logger.info("=" * 60)
    logger.info("FinTwit Daily Scanner v4 — AI-Powered")
    logger.info("=" * 60)

    # Reset Claude cost tracker
    from scanner.claude_client import reset_run_stats, get_run_stats
    reset_run_stats()

    # ── Pre-flight ────────────────────────────────────────────────────────────
    from scanner.cookie_health import check_and_alert
    if not check_and_alert(): sys.exit(1)

    from scanner.market_calendar import market_status_message
    market_status = market_status_message()
    logger.info(f"Market: {market_status['status_message']}")

    from scanner.nse_symbols import get_valid_symbols
    valid_symbols = get_valid_symbols()
    logger.info(f"NSE symbols: {len(valid_symbols)}")

    from scanner.persistence import load_history, update_history, save_history
    history = load_history()

    from scanner.trader_intelligence import load_trader_db, save_trader_db
    trader_db = load_trader_db()

    # ── Market environment ────────────────────────────────────────────────────
    logger.info("STEP 0: Market environment...")
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
    logger.info(f"  → {market_env['label']} | Nifty: {market_env['nifty']['trend']}")

    # ── Twitter scrape ────────────────────────────────────────────────────────
    logger.info("STEP 1/7 — Twitter scrape...")
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
        send_report(f"<p>No tweets ({run_date}). {market_status['status_message']}</p>", subject=f"⚠ FinTwit — No Data ({run_date})")
        sys.exit(0)

    # ── Claude tweet intelligence ─────────────────────────────────────────────
    logger.info("STEP 2/7 — Claude tweet intelligence...")
    from scanner.tweet_intelligence import analyse_all_tweets, analyse_charts_for_tickers
    tweet_signals = analyse_all_tweets(tweets)
    tweet_signals = analyse_charts_for_tickers(tweet_signals)
    logger.info(f"  → {len(tweet_signals)} tickers with Claude sentiment")

    # ── Trader classification (weekly, cached) ────────────────────────────────
    logger.info("STEP 3/7 — Trader intelligence...")
    from scanner.trader_intelligence import classify_all_accounts, update_call_outcomes
    trader_db = classify_all_accounts(members, tweets, trader_db)
    logger.info(f"  → {len(trader_db.get('accounts',{}))} accounts classified")

    # ── Ticker extraction (weighted by trader credibility) ────────────────────
    logger.info("STEP 4/7 — Ticker extraction...")
    from scanner.ticker_extractor import extract_tickers
    # Merge tweet_signals into ticker extraction
    tickers = extract_tickers(tweets, retweet_counts, valid_symbols)
    # Boost weighted_score by trader credibility + Claude sentiment
    for t in tickers:
        ticker = t["ticker"]
        # Credibility weight from all mentioning traders
        cred_weights = [trader_db.get("accounts",{}).get(u,{}).get("recommended_weight",1.0) for u in t.get("users",[])]
        avg_cred = sum(cred_weights) / max(len(cred_weights),1)
        # Sentiment boost from Claude
        sig = tweet_signals.get(ticker,{})
        sent_boost = max(0, sig.get("sentiment_score",0) * 0.5)
        t["weighted_score"] = round(t["weighted_score"] * avg_cred + sent_boost, 2)
    tickers.sort(key=lambda x: x["weighted_score"], reverse=True)
    logger.info(f"  → {len(tickers)} valid tickers (credibility-weighted)")

    if not tickers:
        logger.warning("No valid tickers after filtering")
        sys.exit(0)

    # ── Technical analysis ────────────────────────────────────────────────────
    logger.info("STEP 5/7 — Technical analysis...")
    from scanner.technical_analysis import analyse_all
    analysis_results = analyse_all(tickers, min_mentions=int(os.getenv("MIN_MENTIONS","1")),
                                   market_env=market_env, nifty_close=nifty_close)
    logger.info(f"  → {len(analysis_results)} stocks analysed")
    history = update_history(history, analysis_results, run_date)

    # ── News fetch ────────────────────────────────────────────────────────────
    logger.info("STEP 6/7 — News & catalysts...")
    from scanner.news_fetcher import fetch_all_news
    news_map = fetch_all_news(analysis_results)

    # ── Claude stock intelligence ─────────────────────────────────────────────
    logger.info("STEP 6b/7 — Claude stock intelligence...")
    from scanner.stock_intelligence import enrich_all_stocks, generate_premarket_brief
    analysis_results, correlations, macro_context = enrich_all_stocks(
        analysis_results, news_map, tweet_signals, market_env
    )

    # ── Anomaly detection ─────────────────────────────────────────────────────
    from scanner.performance_tracker import detect_anomalies, generate_weekly_debrief
    anomalies     = detect_anomalies(tweet_signals, history, trader_db)
    weekly_debrief= generate_weekly_debrief(history, market_env)

    # Log trader calls for accuracy tracking
    for ticker, sig in tweet_signals.items():
        for trader in sig.get("traders",[])[:3]:
            from scanner.trader_intelligence import log_trader_call
            log_trader_call(trader_db, trader, ticker, sig.get("sentiment_label","neutral"),
                           sig.get("trader_entry"), sig.get("trader_target"), run_date)

    # ── Build & send report ───────────────────────────────────────────────────
    logger.info("STEP 7/7 — Report & email...")
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
    )

    buys  = sum(1 for r in analysis_results if r.get("recommendation") in ("BUY","STRONG BUY"))
    watch = sum(1 for r in analysis_results if r.get("recommendation") == "WATCH")
    hot   = [r["ticker"] for r in analysis_results if r.get("consecutive_days",0) >= 3]
    subject = (
        f"📊 FinTwit {start.strftime('%d %b')} [{market_env['label']}] — "
        f"{buys} Buy · {watch} Watch · {len(analysis_results)} stocks"
        + (f" 🔥 {','.join(hot)}" if hot else "")
    )

    ok = send_report(html, subject=subject)
    if ok:
        save_history(history)
        save_trader_db(trader_db)

    elapsed = (datetime.now() - start).seconds
    from scanner.cookie_health import send_run_summary
    send_run_summary(len(analysis_results), len(members), len(tweets), elapsed, errors)

    logger.info(f"{'✅' if ok else '❌'} Done in {elapsed}s | AI cost ₹{ai_cost['total_cost_inr']:.2f}")
    if not ok: sys.exit(1)


if __name__ == "__main__":
    main()
