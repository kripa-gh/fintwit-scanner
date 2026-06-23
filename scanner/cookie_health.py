"""
cookie_health.py — Gap 1 Fix
Twitter cookie health check and alerting.

Validates auth_token + ct0 format and confirms twscrape
can add the account successfully. Does NOT make a live
API call (avoids XClIdGen issues in some twscrape versions).
"""

import asyncio
import logging
import os
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Tuple

logger = logging.getLogger(__name__)

AUTH_TOKEN = os.getenv("TWITTER_AUTH_TOKEN", "")
CT0_TOKEN  = os.getenv("TWITTER_CT0", "")
GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_PASS = os.getenv("GMAIL_APP_PASSWORD", "")
RECIPIENT  = os.getenv("REPORT_RECIPIENT", "")


def _send_alert(subject: str, body: str) -> None:
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


async def _validate_cookies() -> Tuple[bool, str]:
    """
    Validate cookie format and confirm twscrape accepts them.
    Does NOT make a live Twitter API call to avoid XClIdGen issues.
    """
    if not AUTH_TOKEN or not CT0_TOKEN:
        return False, "TWITTER_AUTH_TOKEN or TWITTER_CT0 secret is empty"

    # Format checks
    if len(AUTH_TOKEN) < 20:
        return False, f"auth_token too short ({len(AUTH_TOKEN)} chars) — likely not set correctly"
    if len(CT0_TOKEN) < 50:
        return False, f"ct0 too short ({len(CT0_TOKEN)} chars) — likely not set correctly"
    if not re.match(r'^[a-f0-9]+$', AUTH_TOKEN):
        return False, "auth_token contains unexpected characters — copy it again from browser"

    # Try adding to twscrape pool — confirms library accepts the format
    try:
        from twscrape import API
        api        = API()
        cookie_str = f"auth_token={AUTH_TOKEN}; ct0={CT0_TOKEN}"
        await api.pool.add_account_cookies("health_check_account", cookie_str)
        accounts   = await api.pool.get_all()
        if not accounts:
            return False, "twscrape could not register the account"
        return True, f"Cookies valid format — account registered in twscrape pool"
    except Exception as e:
        return False, f"twscrape error: {e}"


def check_and_alert() -> bool:
    """
    Run cookie health check. Returns True if cookies look valid.
    Sends alert email and aborts pipeline on failure.
    """
    logger.info("Checking Twitter cookie health...")
    valid, message = asyncio.run(_validate_cookies())

    if valid:
        logger.info(f"✅ Cookie health check passed: {message}")
        return True

    logger.error(f"❌ Cookie health check FAILED: {message}")

    alert_body = f"""FinTwit Daily Scanner — Cookie Alert
======================================
Date:   {datetime.now().strftime('%d %B %Y %H:%M IST')}
Status: FAILED
Reason: {message}

Action Required:
1. Open x.com in your browser
2. Press F12 → Application tab → Cookies → x.com
3. Copy fresh values of:
   - auth_token
   - ct0
4. Go to: github.com/kripa-gh/fintwit-scanner
   Settings → Secrets and variables → Actions
5. Update TWITTER_AUTH_TOKEN and TWITTER_CT0
6. Re-run the workflow manually

No stock report will be delivered today.
"""
    _send_alert("⚠️ FinTwit Scanner — Twitter Cookies Issue", alert_body)
    return False


def send_run_summary(ticker_count, member_count, tweet_count, run_duration_s, errors=None):
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
