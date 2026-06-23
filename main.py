"""
main.py v3
All 10 gaps + new TA factors integrated.
Market environment fetched once and passed to all ticker analysis.
"""

import logging
import os
import sys
from datetime import datetime, date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def main():
    start    = datetime.now()
    run_date = date.today().isoformat()
    errors   = []

    logger.info("=" * 60)
    logger.info("FinTwit Daily Scanner v3 starting...")
    logger.info("=" * 60)

    # ── Cookie health check ───────────────────────────────────────────────────
    from scanner.cookie_health import check_and_alert
    if not check_and_alert():
        sys.exit(1)

    # ── Market calendar ───────────────────────────────────────────────────────
    from scanner.market_calendar import market_status_message
    market_status = market_status_message()
    logger.info(f"Market: {market_status['status_message']}")

    # ── NSE symbol whitelist ──────────────────────────────────────────────────
    from scanner.nse_symbols import get_valid_symbols
    valid_symbols = get_valid_symbols()
    logger.info(f"NSE symbols loaded: {len(valid_symbols)}")

    # ── Run history ───────────────────────────────────────────────────────────
    from scanner.persistence import load_history, update_history, save_history
    history = load_history()

    # ── Market environment (fetched ONCE, shared across all tickers) ──────────
    logger.info("STEP 0: Fetching market environment...")
    from scanner.market_environment import fetch_market_environment
    import yfinance as yf
    market_env = fetch_market_environment()
    logger.info(f"  → Market: {market_env['label']} | Nifty: {market_env['nifty']['trend']}")

    # Fetch Nifty close series for relative strength calculations
    nifty_close = None
    try:
        nifty_df = yf.download("^NSEI", period="1y", interval="1d",
                               progress=False, auto_adjust=True)
        if len(nifty_df) > 0:
            if hasattr(nifty_df.columns, "levels"):
                nifty_df.columns = [c[0] for c in nifty_df.columns]
            nifty_close = nifty_df["Close"].dropna()
            logger.info(f"  → Nifty close series: {len(nifty_close)} days")
    except Exception as e:
        logger.warning(f"Nifty series fetch failed: {e} — RS scores will be 0")

    # ── Twitter scrape ────────────────────────────────────────────────────────
    logger.info("STEP 1/5 — Twitter scrape...")
    from scanner.twitter_scraper import run as scrape_twitter
    try:
        tw = scrape_twitter()
        members, tweets = tw["members"], tw["tweets"]
        retweet_counts  = tw["retweet_counts"]
        stats           = tw["stats"]
        logger.info(f"  → {stats['member_count']} members | {stats['tweet_count']} tweets | {stats['rt_dedupe_count']} RTs deduped")
    except Exception as e:
        logger.error(f"Twitter failed: {e}")
        sys.exit(1)

    if not tweets:
        from scanner.mailer import send_report
        send_report(f"<p>No tweets found ({run_date}). Market: {market_status['status_message']}</p>",
                    subject=f"⚠ FinTwit — No Data ({run_date})")
        sys.exit(0)

    # ── Ticker extraction ─────────────────────────────────────────────────────
    logger.info("STEP 2/5 — Ticker extraction...")
    from scanner.ticker_extractor import extract_tickers
    tickers = extract_tickers(tweets, retweet_counts, valid_symbols)
    logger.info(f"  → {len(tickers)} valid tickers")

    # ── Technical analysis (full suite) ──────────────────────────────────────
    logger.info("STEP 3/5 — Full TA suite (base + advanced + environment)...")
    from scanner.technical_analysis import analyse_all
    min_mentions     = int(os.getenv("MIN_MENTIONS", "1"))
    analysis_results = analyse_all(
        tickers,
        min_mentions = min_mentions,
        market_env   = market_env,
        nifty_close  = nifty_close,
    )
    logger.info(f"  → {len(analysis_results)} stocks analysed")

    for r in analysis_results[:5]:
        logger.info(
            f"  {r['ticker']:12} {r['recommendation']:12} "
            f"score={r['score']}/{r['max_score']} "
            f"base={r['base_score']} adv={r['adv_score']} env={r['env_score']}"
        )

    # ── History update ────────────────────────────────────────────────────────
    history = update_history(history, analysis_results, run_date)

    # ── News ──────────────────────────────────────────────────────────────────
    logger.info("STEP 4/5 — News & catalysts...")
    from scanner.news_fetcher import fetch_all_news
    news_map = fetch_all_news(analysis_results)

    # ── Build & send report ───────────────────────────────────────────────────
    logger.info("STEP 5/5 — Report & email...")
    from scanner.report_builder import build_report
    from scanner.mailer import send_report

    html = build_report(
        analysis_results = analysis_results,
        news_map         = news_map,
        history          = history,
        market_env       = market_env,
        run_date         = start.strftime("%d %B %Y"),
        list_id          = os.getenv("TWITTER_LIST_ID", "1506463545642217474"),
        member_count     = len(members),
        tweet_count      = len(tweets),
        rt_dedupe_count  = stats["rt_dedupe_count"],
        market_status    = market_status,
    )

    buys  = sum(1 for r in analysis_results if r["recommendation"] in ("BUY","STRONG BUY"))
    watch = sum(1 for r in analysis_results if r["recommendation"] == "WATCH")
    hot   = [r["ticker"] for r in analysis_results if r.get("consecutive_days",0) >= 3]
    subject = (
        f"📊 FinTwit {start.strftime('%d %b')} [{market_env['label']}] — "
        f"{buys} Buy · {watch} Watch · {len(analysis_results)} stocks"
        + (f" 🔥 {','.join(hot)}" if hot else "")
    )

    ok = send_report(html, subject=subject)
    if ok:
        save_history(history)

    elapsed = (datetime.now() - start).seconds
    from scanner.cookie_health import send_run_summary
    send_run_summary(len(analysis_results), len(members), len(tweets), elapsed, errors)

    logger.info(f"{'✅' if ok else '❌'} Done in {elapsed}s")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
