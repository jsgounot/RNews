"""
Minimal SMTP mailer.

Configure via environment variables (or a .env file):
    RNEWS_SMTP_HOST     default: localhost
    RNEWS_SMTP_PORT     default: 25  (use 587 for STARTTLS, 465 for SSL)
    RNEWS_SMTP_USER     default: (empty — no auth)
    RNEWS_SMTP_PASSWORD default: (empty — no auth)
    RNEWS_SMTP_TLS      default: false  (set "true" for STARTTLS)
    RNEWS_SMTP_SSL      default: false  (set "true" for SMTP_SSL)
    RNEWS_FROM_EMAIL    default: noreply@rnews.local
    RNEWS_SITE_URL      default: http://127.0.0.1:8000
"""

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

log = logging.getLogger(__name__)

SMTP_HOST     = os.environ.get("RNEWS_SMTP_HOST", "localhost")
SMTP_PORT     = int(os.environ.get("RNEWS_SMTP_PORT", "25"))
SMTP_USER     = os.environ.get("RNEWS_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("RNEWS_SMTP_PASSWORD", "")
SMTP_TLS      = os.environ.get("RNEWS_SMTP_TLS", "false").lower() == "true"
SMTP_SSL      = os.environ.get("RNEWS_SMTP_SSL", "false").lower() == "true"
FROM_EMAIL    = os.environ.get("RNEWS_FROM_EMAIL", "noreply@rnews.local")
SITE_URL      = os.environ.get("RNEWS_SITE_URL", "http://127.0.0.1:8000")


def send_email(to: str, subject: str, body_text: str, body_html: str | None = None) -> bool:
    """Send an email. Returns True on success, False on failure."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = FROM_EMAIL
    msg["To"]      = to

    msg.attach(MIMEText(body_text, "plain"))
    if body_html:
        msg.attach(MIMEText(body_html, "html"))

    try:
        if SMTP_SSL:
            conn = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10)
        else:
            conn = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
            if SMTP_TLS:
                conn.starttls()

        if SMTP_USER and SMTP_PASSWORD:
            conn.login(SMTP_USER, SMTP_PASSWORD)

        conn.sendmail(FROM_EMAIL, [to], msg.as_string())
        conn.quit()
        log.info("Email sent to %s: %s", to, subject)
        return True

    except Exception as exc:
        log.error("Failed to send email to %s: %s", to, exc)
        return False


def send_new_password(to: str, username: str, new_password: str) -> bool:
    subject = "RNews — your new password"
    text = (
        f"Hi {username},\n\n"
        f"A new password has been generated for your RNews account:\n\n"
        f"    {new_password}\n\n"
        f"Please log in at {SITE_URL}/login and change it in Settings → Profile.\n\n"
        f"If you did not request this, contact the site administrator.\n"
    )
    html = f"""
<p>Hi <strong>{username}</strong>,</p>
<p>A new password has been generated for your RNews account:</p>
<pre style="background:#f4f4f4;padding:10px 16px;border-radius:4px;font-size:1.1em">{new_password}</pre>
<p>Please <a href="{SITE_URL}/login">log in</a> and change it in
<strong>Settings → Profile</strong>.</p>
<p style="color:#999;font-size:0.85em">If you did not request this, you can ignore this email.</p>
"""
    return send_email(to, subject, text, html)
