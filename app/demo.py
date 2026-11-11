"""Demo helpers, always labeled as demo data in the UI.

1. Demo history: a realistic past for the seller (about 2 months of small sales), so the
   risk check has a 'normal' to compare against. Needed because a brand-new sandbox
   account has no history, and Transaction Search can't create past sales.
2. Sample hold: the sandbox can't put a real hold on a payment, so the video shows a
   clearly labeled sample hold on a real sandbox sale.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from . import db

_ITEMS = [
    ("Handmade wool scarf", 28, 42), ("Ceramic coffee mug set", 24, 38), ("Vintage denim jacket", 45, 65),
    ("Scented soy candle (3 pack)", 18, 30), ("Crochet plant hanger", 15, 25), ("Leather card wallet", 22, 35),
    ("Hand-painted tote bag", 25, 40), ("Silver stud earrings", 30, 55), ("Kids knit beanie", 14, 22),
    ("Macrame wall hanging", 35, 60),
]


def seed_history(n: int = 30, days: int = 60, seed: int = 7) -> int:
    """Adds n small sales spread over the past `days` days. Safe to run twice (replaces old demo rows)."""
    db.delete_demo_history()
    rng = random.Random(seed)
    end = datetime.now(timezone.utc) - timedelta(days=1)
    for i in range(n):
        name, lo, hi = rng.choice(_ITEMS)
        when = end - timedelta(days=days * (n - i) / n, hours=rng.randint(0, 12))
        db.insert_sale(
            order_id=f"demo-{i}", capture_id=f"DEMO-{i:04d}", item_name=name, item_type="physical",
            amount=float(rng.randint(lo, hi)), currency="USD", buyer_email=None,
            created_at=when.isoformat(timespec="seconds"), source="demo", category="general",
            tracking_sent_at=when.isoformat(timespec="seconds"), delivered=1,
        )
    return n


def simulate_hold(sale: dict) -> str:
    if sale["item_type"] == "service":
        note = ("SAMPLE HOLD (demo): PayPal is holding this payment. Mark the service completed "
                "to start PayPal's 7-day release clock.")
    else:
        note = ("SAMPLE HOLD (demo): PayPal is holding this payment. Add tracking from an approved "
                "carrier; PayPal releases it about 24 hours after the courier confirms delivery.")
    db.update_sale(sale["id"], hold_status="held", hold_note=note)
    return note


def simulate_release(sale: dict):
    db.update_sale(sale["id"], hold_status="released",
                   hold_note="SAMPLE (demo): payment released after proof was provided.")
