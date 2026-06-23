"""mailer.py — Gmail SMTP sender (unchanged from v1)."""

import os
import smtplib
import logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

GMAIL_USER     = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASSWORD", "")
RECIPIENT      = os.getenv("REPORT_RECIPIENT", "")


def send_report(html: str, subject: str = None) -> bool:
    if not all([GMAIL_USER, GMAIL_APP_PASS, RECIPIENT]):
        logger.error("Missing GMAIL_USER, GMAIL_APP_PASSWORD, or REPORT_RECIPIENT")
        return False

    subject = subject or f"📊 FinTwit Daily Scan — {datetime.now().strftime('%d %b %Y')}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = RECIPIENT
    msg.attach(MIMEText("Open in an HTML-capable email client to view this report.", "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASS)
            server.sendmail(GMAIL_USER, RECIPIENT, msg.as_string())
        logger.info(f"Report sent to {RECIPIENT}")
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error("Gmail auth failed — use an App Password, not your account password")
        return False
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False
