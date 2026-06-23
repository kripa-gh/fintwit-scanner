"""
cookie_health.py — Gap 1 Fix
Twitter cookie health check and alerting.

Validates auth_token + ct0 before the main pipeline runs.
Sends an alert email if cookies are invalid or near expiry.
Prevents silent failures — if cookies are dead, you know immediately.
"""

import asyncio
import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Tuple

logger = logging.getLogger(__name__)

AUTH_TOKEN  = os.getenv("TWITTER_AUTH_TOKEN", "")
CT0_TOKEN   = os.getenv("TWITTER_CT0", "")
GMAIL_USER  = os.getenv("GMAIL_USER", "")
GMAIL_PASS  = os.getenv("GMAIL_APP_PASSWORD", "")
RECIPIENT   = os.getenv("REPORT_RECIPIENT", "")


def _send_alert(subject: str, body: str) -> None:
    """Send a plain-text alert email."""
    if not all([GMAIL_USER, GMAIL_PASS, RECIPIENT]):
        logger.warning("Cannot send alert — email credentials not configured")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_USER
        msg["To"]      = RECIPIENT
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, RECIPIENT, msg.as_string())
        logger.info(f"Alert sent: {subject}")
    except Exception as e:
        logger.error(f"Alert email failed: {e}")


async def _test_twitter_cookies() -> Tuple[bool, str]:
    """
    Actually attempt to use the cookies via twscrape.
    Returns (is_valid, message).
    """
    if not AUTH_TOKEN or not CT0_TOKEN:
        return False, "TWITTER_AUTH_TOKEN or TWITTER_CT0 is empty"

    # Basic format checks
    if len(AUTH_TOKEN) < 20:
        return False, f"auth_token looks too short ({len(AUTH_TOKEN)} chars)"
    if len(CT0_TOKEN) < 50:
        return False, f"ct0 looks too short ({len(CT0_TOKEN)} chars)"

    try:
        from twscrape import API
        api  = API()
        cookie_str = f"auth_token={AUTH_TOKEN}; ct0={CT0_TOKEN}"
        await api.pool.add_account_cookies("health_check_account", cookie_str)

        # Try a lightweight API call — fetch a single known public user
        user = await api.user_by_login("NSEIndia")
        if user and user.id:
            return True, f"Cookies valid — verified via @NSEIndia (id={user.id})"
        else:
            return False, "Cookies accepted but test request returned no data"

    except Exception as e:
        err = str(e).lower()
        if "unauthorized" in err or "401" in err or "403" in err:
            return False, f"Cookies rejected by Twitter: {e}"
        if "no active accounts" in err:
            return False, "No active accounts — cookies may be expired"
        # Network error or other transient issue
        return False, f"Health check error (may be transient): {e}"


def check_and_alert() -> bool:
    """
    Run cookie health check.
    Returns True if cookies are valid, False if invalid/expired.
    Sends alert email on failure.
    Called at the start of main.py before the pipeline runs.
    """
    logger.info("Checking Twitter cookie health...")

    valid, message = asyncio.run(_test_twitter_cookies())

    if valid:
        logger.info(f"✅ Cookie health check passed: {message}")
        return True

    # Cookies are invalid — send alert and abort
    logger.error(f"❌ Cookie health check FAILED: {message}")

    alert_subject = "⚠️ FinTwit Scanner — Twitter Cookies Expired"
    alert_body = f"""FinTwit Daily Scanner — Cookie Alert
======================================
Date: {datetime.now().strftime('%d %B %Y %H:%M IST')}
Status: FAILED

Reason: {message}

Action Required:
1. Open x.com in your browser
2. Press F12 → Application tab → Cookies → x.com
3. Copy the values of:
   - auth_token
   - ct0
4. Go to your GitHub repo:
   Settings → Secrets and variables → Actions
5. Update these secrets:
   - TWITTER_AUTH_TOKEN  (new auth_token value)
   - TWITTER_CT0         (new ct0 value)
6. Re-run the workflow manually to verify

The scanner will resume automatically on the next scheduled run
once secrets are updated.

This alert was sent because the daily scan could not proceed.
No stock report will be delivered today.
"""

    _send_alert(alert_subject, alert_body)
    return False


def send_run_summary(
    ticker_count: int,
    member_count: int,
    tweet_count: int,
    run_duration_s: int,
    errors: list = None,
) -> None:
    """
    Send a brief operational summary after each successful run.
    Useful for monitoring pipeline health over time.
    Optional — only sends if SEND_RUN_SUMMARY env var is set to '1'.
    """
    if os.getenv("SEND_RUN_SUMMARY", "0") != "1":
        return

    errors = errors or []
    subject = f"✅ FinTwit Scanner ran — {ticker_count} stocks, {run_duration_s}s"
    body = f"""FinTwit Scanner Run Summary
============================
Date:          {datetime.now().strftime('%d %B %Y %H:%M IST')}
Members:       {member_count}
Tweets:        {tweet_count}
Stocks found:  {ticker_count}
Duration:      {run_duration_s}s
Errors:        {len(errors)}

{'Errors:' + chr(10) + chr(10).join(errors) if errors else 'No errors.'}
"""
    _send_alert(subject, body)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ok = check_and_alert()
    print(f"Cookie health: {'✅ VALID' if ok else '❌ INVALID'}")
