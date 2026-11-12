"""Simple password protection for the hosted demo (HTTP Basic Auth).

Turned on only when APP_PASSWORD is set (on Render). On your own Mac, leave it empty and nothing changes.
PayPal's webhook stays open because PayPal can't type a password; it is protected by PayPal's own
signature check instead (see /webhooks/paypal in main.py).
"""
from __future__ import annotations

import base64
import binascii
import secrets

from . import config

# Paths that never ask for the password
OPEN_PATHS = ("/webhooks/paypal", "/health")
OPEN_PREFIXES = ("/static/",)


def is_enabled() -> bool:
    return bool(config.APP_PASSWORD)


def is_open_path(path: str) -> bool:
    return path in OPEN_PATHS or path == "/favicon.ico" or path.startswith(OPEN_PREFIXES)


def credentials_ok(auth_header: str | None) -> bool:
    """Check an 'Authorization: Basic <base64(user:password)>' header."""
    if not auth_header or not auth_header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    user, sep, password = decoded.partition(":")
    if not sep:
        return False
    # compare_digest takes the same time whether the guess is close or not (harder to guess by timing)
    user_ok = secrets.compare_digest(user.encode(), config.APP_USERNAME.encode())
    pass_ok = secrets.compare_digest(password.encode(), config.APP_PASSWORD.encode())
    return user_ok and pass_ok


def allowed(path: str, auth_header: str | None) -> bool:
    return not is_enabled() or is_open_path(path) or credentials_ok(auth_header)
