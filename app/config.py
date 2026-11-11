"""Settings read from environment variables (never hard-code secrets)."""
import os

from dotenv import load_dotenv

load_dotenv()

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID", "")  # from the sandbox app's webhook settings
PAYPAL_BASE = os.getenv("PAYPAL_BASE", "https://api-m.sandbox.paypal.com")  # sandbox only

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")

# Public address of this app, e.g. https://hold-guard.onrender.com (used for PayPal return links)
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
DB_PATH = os.getenv("DB_PATH", "holdguard.db")
SELLER_NAME = os.getenv("SELLER_NAME", "Demo Seller")

# Optional email alerts for high-risk sales (Gmail: smtp.gmail.com, port 587, an app password)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "")  # where alerts go (the seller's inbox)
ALERT_LEVELS = set(os.getenv("ALERT_LEVELS", "high").split(","))
