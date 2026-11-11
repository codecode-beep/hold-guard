"""Claude-powered features. Each function does one job a fixed rule cannot do."""
from __future__ import annotations

import base64
import json
import re

from . import config
from .risk import HIGH_RISK_CATEGORIES, keyword_category
from .safety import REFUSAL, is_evasion_request

CARRIERS = {"USPS", "UPS", "FEDEX", "DHL", "DHL_API", "ONTRAC", "LASERSHIP", "AMAZON", "OTHER"}

SYSTEM = (
    "You are Hold Guard, an assistant for small online sellers who get paid with PayPal. "
    "Your job: help honest sellers give PayPal true proof of their sales (tracking, delivery, "
    "buyer messages) so held payments are released faster, and explain PayPal holds in plain, "
    "simple words.\n"
    "Facts you can rely on (from PayPal's help pages): PayPal may hold payments for new sellers, "
    "sellers who have not sold in a while, sellers with complaints or refunds, unusual selling "
    "patterns (sudden surge, change in average price or item type), and higher-risk items "
    "(event tickets, consumer electronics, travel packages, gift cards). Adding tracking from an "
    "approved carrier releases the hold about 24 hours after the courier confirms delivery. For "
    "services or digital items, marking the order Completed releases it about 7 days later. In "
    "rare cases PayPal may still hold up to 21 days.\n"
    "STRICT RULES: Never advise splitting payments, using Friends and Family to avoid fees or "
    "protection, opening new accounts, mislabeling items, editing or inventing tracking or "
    "receipts, or any way to avoid PayPal's checks. If asked, refuse briefly and offer the honest "
    "path. Never claim you can force PayPal to release money. Never invent facts about an order; "
    "use only the details given. Keep answers short and plain."
)


class AIError(Exception):
    pass


def _client():
    if not config.ANTHROPIC_API_KEY:
        raise AIError("ANTHROPIC_API_KEY is not set")
    import anthropic  # imported here so the rest of the app works without it during tests
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _ask(content, max_tokens: int = 800, system: str = SYSTEM) -> str:
    msg = _client().messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise AIError(f"AI did not return JSON: {text[:200]}")
    return json.loads(m.group(0))


def normalize_carrier(name: str | None) -> str:
    n = (name or "").upper().replace(" ", "").replace("-", "")
    if "USPS" in n or "POSTAL" in n:
        return "USPS"
    if "FEDEX" in n or "FEDERALEXPRESS" in n:
        return "FEDEX"
    if n.startswith("UPS") or n == "UNITEDPARCELSERVICE":
        return "UPS"
    if "DHL" in n:
        return "DHL"
    if "ONTRAC" in n:
        return "ONTRAC"
    return "OTHER"


def guess_carrier_from_number(number: str) -> str:
    """Common US tracking number shapes, used to double-check the AI."""
    n = re.sub(r"\s", "", number or "").upper()
    if re.fullmatch(r"1Z[0-9A-Z]{16}", n):
        return "UPS"
    if re.fullmatch(r"(94|93|92|95)\d{20}", n) or re.fullmatch(r"[A-Z]{2}\d{9}US", n):
        return "USPS"
    if re.fullmatch(r"\d{12}|\d{15}", n):
        return "FEDEX"
    if re.fullmatch(r"\d{10}", n):
        return "DHL"
    return "OTHER"


# ---------- 1. Item reader ----------

def classify_item(item_name: str) -> dict:
    """Reads a free-text item name and decides PayPal's risk category."""
    try:
        out = _json(_ask(
            "Classify this item a seller is selling into exactly one category: "
            "electronics, event_tickets, travel, gift_cards, general.\n"
            f"Item: {item_name!r}\n"
            'Reply with JSON only: {"category": "...", "reason": "<one short sentence>"}',
            max_tokens=150))
        cat = out.get("category", "general")
        if cat not in HIGH_RISK_CATEGORIES | {"general"}:
            cat = "general"
        return {"category": cat, "reason": out.get("reason", ""), "by": "ai"}
    except Exception as e:  # keep the app working if the AI is down
        cat = keyword_category(item_name)
        return {"category": cat, "reason": f"Keyword match (AI unavailable: {e.__class__.__name__})", "by": "keywords"}


# ---------- 2. Receipt reader ----------

def read_receipt(text: str = "", image: bytes | None = None, mime: str = "image/jpeg") -> dict:
    """Pulls carrier + tracking number from a receipt photo or a typed message."""
    content: list = []
    if image:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": mime, "data": base64.b64encode(image).decode()}})
    content.append({"type": "text", "text": (
        "Find the shipping carrier and tracking number in this shipping receipt or message. "
        "Only report a number you can actually see; do not guess or make one up.\n"
        f"Seller's message: {text!r}\n"
        'Reply with JSON only: {"found": true|false, "carrier": "USPS|UPS|FEDEX|DHL|OTHER", '
        '"tracking_number": "...", "order_hint": "<buyer name, item or amount if visible, else empty>", '
        '"confidence": "high|medium|low"}')})
    out = _json(_ask(content, max_tokens=250))
    if not out.get("found") or not out.get("tracking_number"):
        return {"found": False}
    number = re.sub(r"\s", "", str(out["tracking_number"])).upper()
    carrier = normalize_carrier(out.get("carrier"))
    shape = guess_carrier_from_number(number)
    if carrier == "OTHER" and shape != "OTHER":
        carrier = shape
    return {"found": True, "carrier": carrier, "tracking_number": number,
            "order_hint": out.get("order_hint", ""), "confidence": out.get("confidence", "medium"),
            "shape_matches": shape in (carrier, "OTHER")}


# ---------- 3. Hold explainer ----------

def explain_hold(sale: dict, checklist: list[dict]) -> str:
    reasons = "\n".join(f"- {r['text']}" for r in sale["risk_reasons"]) or "- No risk signals found."
    todo = "\n".join(f"- [{'x' if c['done'] else ' '}] {c['label']}" for c in checklist)
    return _ask(
        "Explain to this seller, in 4 to 6 short plain sentences, why PayPal might hold this "
        "payment and exactly what to do next. Use only the facts below.\n"
        f"Item: {sale['item_name']} ({sale['item_type']}), ${sale['amount']:.2f}\n"
        f"Risk signals:\n{reasons}\nChecklist:\n{todo}", max_tokens=400)


# ---------- 4. Dispute reply ----------

def draft_dispute_reply(dispute: dict, sale: dict | None, messages: list[dict]) -> str:
    facts = "No matching sale found in Hold Guard."
    if sale:
        facts = (f"Item: {sale['item_name']}, ${sale['amount']:.2f}, sold {sale['created_at'][:10]}.\n"
                 f"Carrier: {sale.get('carrier') or 'none'}, tracking: {sale.get('tracking_number') or 'none'}, "
                 f"tracking sent to PayPal: {sale.get('tracking_sent_at') or 'no'}, "
                 f"delivered: {'yes' if sale.get('delivered') else 'not confirmed'}.")
    msgs = "\n".join(f"{m['author']}: {m['text']}" for m in messages[-10:]) or "none saved"
    return _ask(
        "Write the seller's response to this PayPal dispute. Be polite, factual and short "
        "(under 150 words). Only state facts listed below; if proof is missing, do not pretend "
        "it exists. End with what the attached evidence pack contains.\n"
        f"Dispute reason: {dispute.get('reason')}\nBuyer said: {dispute.get('buyer_note') or 'not given'}\n"
        f"Sale facts: {facts}\nBuyer messages:\n{msgs}", max_tokens=400)


# ---------- 5. Message to PayPal support ----------

def draft_support_message(sale: dict, checklist: list[dict]) -> str:
    done = ", ".join(c["label"] for c in checklist if c["done"]) or "nothing yet"
    return _ask(
        "Write a short, polite message (under 120 words) from this seller to PayPal support "
        "asking for a review of a held payment. List the proof already provided. Use only these facts.\n"
        f"Item: {sale['item_name']}, ${sale['amount']:.2f}, PayPal capture ID {sale.get('capture_id')}.\n"
        f"Carrier/tracking: {sale.get('carrier') or 'n/a'} {sale.get('tracking_number') or ''}\n"
        f"Proof already provided: {done}", max_tokens=300)


# ---------- 6. Chat ----------

def chat_reply(message: str, sales_summary: str) -> str:
    if is_evasion_request(message):
        return REFUSAL
    return _ask(
        f"Seller's recent sales:\n{sales_summary}\n\nSeller says: {message}\n"
        "Answer helpfully in plain words (under 120 words).", max_tokens=400)
