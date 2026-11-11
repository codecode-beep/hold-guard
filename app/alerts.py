"""Seller alerts: an email the moment a high-risk sale arrives.

Uses plain SMTP from Python's standard library. Works with a Gmail "app password".
If SMTP isn't configured, alerts are skipped and the dashboard still shows the warning.
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from . import config


def is_configured() -> bool:
    return bool(config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASSWORD and config.ALERT_EMAIL)


def build_alert(sale: dict) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = (f"Hold Guard: ${sale['amount']:,.2f} sale may be held by PayPal "
                      f"({sale['risk_level']} risk)")
    msg["From"] = config.SMTP_USER
    msg["To"] = config.ALERT_EMAIL
    lines = [
        f"New sale: {sale['item_name']} for ${sale['amount']:,.2f}.",
        f"Hold risk: {str(sale['risk_level']).upper()} ({sale['risk_score']}/100).",
        "",
        "Why PayPal might hold this payment:",
    ]
    for r in sale["risk_reasons"]:
        lines += [f"- {r['text']}", f"  What to do: {r['action']}"]
    lines += [
        "",
        "Fastest way to get paid: ship with a tracked carrier, then send the receipt photo in "
        "Hold Guard's chat. PayPal releases holds about 24 hours after the courier confirms delivery.",
        "",
        f"Open this sale: {config.APP_BASE_URL}/sales/{sale['id']}",
    ]
    msg.set_content("\n".join(lines))
    return msg


def send_alert(sale: dict) -> None:
    """Raises on failure so the caller can record the error."""
    msg = build_alert(sale)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
        s.starttls()
        s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.send_message(msg)
