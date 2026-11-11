"""Anti-fraud guardrail.

Hold Guard only ever ADDS true information for PayPal (tracking, delivery, real messages).
It must never help someone hide information, split payments, fake proof, or dodge PayPal's checks.
This filter runs BEFORE the AI sees a message; the AI's own instructions repeat the rule.
"""
import re

_EVASION = [
    r"split (the |my |this )?(payment|order|sale|transaction)s?",
    r"break (it|the payment|this) (up|into)",
    r"(avoid|dodge|bypass|get around|trick|fool|evade|beat)\b.{0,40}\b(paypal|hold|holds|detection|review|limit|fraud|risk)",
    r"(won'?t|wouldn'?t|doesn'?t|don'?t) (notice|flag|detect)",
    r"(fake|forge|make up|invent|edit|photoshop|alter)\b.{0,30}\b(tracking|receipt|proof|delivery|invoice|label|screenshot)",
    r"(use|send) (a )?(random|someone else'?s|another|old) tracking",
    r"friends? (and|&) family.{0,40}(avoid|instead|so (that )?(it|paypal))",
    r"(new|second|another|multiple) (paypal )?accounts?.{0,40}(avoid|after|because).{0,20}(limit|hold|ban)",
    r"hide\b.{0,40}\b(from paypal|the (item|sale|income))",
    r"(mislabel|lie about|misdescribe)\b.{0,30}\b(item|category|product)",
]
_RX = [re.compile(p, re.I) for p in _EVASION]

REFUSAL = (
    "I can't help with that. Hold Guard only helps by giving PayPal true information faster, "
    "like real tracking and real delivery proof. Trying to get around PayPal's checks can get an "
    "account permanently limited. What I can do: help you add tracking, save buyer messages, "
    "and build an evidence pack so honest sales get released sooner."
)


def is_evasion_request(text: str) -> bool:
    t = text or ""
    return any(rx.search(t) for rx in _RX)
