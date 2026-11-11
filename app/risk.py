"""Hold Risk Check.

Scores a sale against the reasons PayPal publishes for holding payments
(PayPal Help: "Why is my payment on hold or unavailable?"):
  - new seller, or a seller who hasn't sold in a while
  - multiple complaints or refund requests
  - unusual selling pattern: surge in sales, change in average selling price
  - higher-risk items: event tickets, consumer electronics, travel, gift cards

Pure Python, no network calls, so it is easy to test.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

HIGH_RISK_CATEGORIES = {"electronics", "event_tickets", "travel", "gift_cards"}

# Used only when the AI is unavailable; the AI item reader is the main path.
_KEYWORDS = {
    "electronics": r"iphone|ipad|macbook|laptop|airpods|playstation|ps5|xbox|nintendo|switch 2|gpu|graphics card|"
                   r"rtx|camera|drone|galaxy s\d|pixel \d|smartwatch|apple watch|tv\b|monitor|console",
    "event_tickets": r"ticket|concert|admission|festival pass|seat\b|vip pass",
    "travel": r"flight|hotel|airbnb|vacation|cruise|travel package",
    "gift_cards": r"gift ?card|voucher|steam card|itunes card|prepaid card",
}


def keyword_category(item_name: str) -> str:
    text = (item_name or "").lower()
    for cat, pattern in _KEYWORDS.items():
        if re.search(pattern, text):
            return cat
    return "general"


@dataclass
class Sale:
    amount: float
    created_at: datetime
    category: str = "general"
    refunded_or_disputed: bool = False


@dataclass
class RiskResult:
    score: int
    level: str  # low / medium / high
    reasons: list[dict] = field(default_factory=list)  # {code, text, action}


def score_sale(sale: Sale, history: list[Sale]) -> RiskResult:
    """history = this seller's earlier sales (not including `sale`)."""
    reasons: list[dict] = []
    score = 0
    past = [s for s in history if s.created_at < sale.created_at]
    total = sum(s.amount for s in past)

    # 1. New seller
    if len(past) < 25 or total < 250:
        score += 25
        reasons.append({
            "code": "new_seller",
            "text": f"You have {len(past)} earlier sales (${total:,.0f} total). PayPal often holds payments for newer sellers.",
            "action": "Add tracking as soon as you ship. PayPal releases holds about 24 hours after delivery is confirmed.",
        })

    # 2. Dormant seller
    if past:
        last = max(s.created_at for s in past)
        gap = (sale.created_at - last).days
        if gap >= 90:
            score += 20
            reasons.append({
                "code": "dormant",
                "text": f"Your last sale was {gap} days ago. PayPal may hold money after a long break.",
                "action": "Ship quickly with tracking and keep your messages with the buyer.",
            })

    # 3. Price jump vs. the seller's normal sale
    if past:
        avg = total / len(past)
        if avg > 0 and sale.amount >= 3 * avg:
            ratio = sale.amount / avg
            score += 40 if ratio >= 5 else 25
            reasons.append({
                "code": "price_jump",
                "text": f"This ${sale.amount:,.0f} sale is {ratio:.1f}x your usual sale (${avg:,.0f}). A change in average price is an unusual selling pattern.",
                "action": "Use a tracked carrier with signature confirmation for this order.",
            })

    # 4. Sudden surge in number of sales (needs about a month of history to judge)
    if past:
        first = min(s.created_at for s in past)
        weeks = max((sale.created_at - first).days / 7, 0)
        if weeks >= 4:
            weekly_avg = len(past) / weeks
            last7 = sum(1 for s in past if s.created_at >= sale.created_at - timedelta(days=7)) + 1
            if weekly_avg > 0 and last7 >= 3 * weekly_avg and last7 >= 5:
                score += 15
                reasons.append({
                    "code": "volume_surge",
                    "text": f"{last7} sales in the last 7 days vs. about {weekly_avg:.1f} per week normally.",
                    "action": "Add tracking to every order the same day you ship.",
                })

    # 5. Higher-risk item
    if sale.category in HIGH_RISK_CATEGORIES:
        score += 20
        label = sale.category.replace("_", " ")
        reasons.append({
            "code": "high_risk_item",
            "text": f"This item looks like {label}, which PayPal lists as a higher-risk category.",
            "action": "Keep photos of the item and packaging, and the shipping receipt.",
        })

    # 6. Recent complaints or refunds
    recent_problems = sum(
        1 for s in past
        if s.refunded_or_disputed and s.created_at >= sale.created_at - timedelta(days=90)
    )
    if recent_problems >= 2:
        score += 20
        reasons.append({
            "code": "complaints",
            "text": f"{recent_problems} of your sales in the last 90 days had a refund or dispute.",
            "action": "Reply to buyers fast and solve problems before they open a dispute.",
        })

    score = min(score, 100)
    level = "high" if score >= 60 else "medium" if score >= 30 else "low"
    return RiskResult(score=score, level=level, reasons=reasons)


def checklist(item_type: str, tracking_sent: bool, delivered: bool, completed: bool,
              has_messages: bool) -> list[dict]:
    """What is done / missing for a sale, based on PayPal's published release steps."""
    if item_type == "service":
        items = [
            ("Order marked completed (PayPal releases about 7 days later)", completed),
        ]
    else:
        items = [
            ("Tracking number sent to PayPal", tracking_sent),
            ("Courier confirmed delivery (PayPal releases about 24 hours later)", delivered),
        ]
    items.append(("Buyer messages saved as proof", has_messages))
    return [{"label": label, "done": done} for label, done in items]
