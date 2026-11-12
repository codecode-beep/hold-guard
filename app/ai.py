"""AI features powered by Google Gemini. Each function does one job a fixed rule cannot do."""
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
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


EXTRACTOR = (
    "You are a precise data extraction tool inside a seller app. Reply with a single JSON object "
    "and nothing else: no greeting, no explanation, no markdown. Never invent values you cannot see."
)


# ---------- Talking to Gemini (Google AI Studio REST API) ----------

def _gemini_parts(content) -> list[dict]:
    """`content` is a string, or a list of blocks: {"type": "text", "text": ...} or
    {"type": "image", "mime": ..., "data": <base64>}. Turns it into Gemini 'parts'."""
    if isinstance(content, str):
        return [{"text": content}]
    parts = []
    for block in content:
        if block.get("type") == "image":
            parts.append({"inline_data": {"mime_type": block["mime"], "data": block["data"]}})
        else:
            parts.append({"text": block.get("text", "")})
    return parts


def _gemini_request(body: dict) -> dict:
    import httpx
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent",
        headers={"x-goog-api-key": config.GEMINI_API_KEY},
        json=body, timeout=60,
    )
    if r.status_code != 200:
        raise AIError(f"Gemini error {r.status_code}: {r.text[:300]}", status=r.status_code)
    return r.json()


def friendly_error(e: Exception) -> str:
    """Plain-language message for an AI problem, shown to the seller."""
    status = getattr(e, "status", 0)
    if "GEMINI_API_KEY is not set" in str(e):
        return "The AI isn't set up yet: add GEMINI_API_KEY to .env and restart the app."
    if status == 429:
        return ("The AI's free usage limit has been reached for now. Please try again in a minute; "
                "the daily limit resets at midnight Pacific time.")
    if status in (400, 401, 403) and "API key" in str(e):
        return "Google didn't accept the Gemini API key. Check GEMINI_API_KEY in .env."
    if status >= 500:
        return "The AI service is busy right now. Please try again in a moment."
    return "The AI couldn't answer this time. Please try again."


def _ask(content, max_tokens: int = 800, system: str = SYSTEM, json_mode: bool = False) -> str:
    """Send one request to Gemini and return its text answer.
    json_mode=True: the answer must be a JSON object. Uses the extractor instructions, and
    Gemini is told to return JSON (application/json), which it then enforces."""
    if json_mode:
        system = EXTRACTOR
    if not config.GEMINI_API_KEY:
        raise AIError("GEMINI_API_KEY is not set")
    gen = {"maxOutputTokens": max_tokens}
    if json_mode:
        gen["responseMimeType"] = "application/json"  # Gemini then only answers with valid JSON
    if config.GEMINI_MODEL.startswith("gemini-2.5-flash"):
        gen["thinkingConfig"] = {"thinkingBudget": 0}  # short answers; don't spend tokens on hidden thinking
    else:
        gen["maxOutputTokens"] = max_tokens + 2048  # room for the model's own thinking
    data = _gemini_request({
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": _gemini_parts(content)}],
        "generationConfig": gen,
    })
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no answer")
        raise AIError(f"Gemini returned no answer ({reason})")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts).strip()


def provider_name() -> str:
    return f"Google Gemini ({config.GEMINI_MODEL})"


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
            max_tokens=150, json_mode=True))
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
        content.append({"type": "image", "mime": mime, "data": base64.b64encode(image).decode()})
    content.append({"type": "text", "text": (
        "Find the shipping carrier and tracking number in this shipping receipt or message. "
        "Only report a number you can actually see; do not guess or make one up.\n"
        f"Seller's message: {text!r}\n"
        'Reply with JSON only: {"found": true|false, "carrier": "USPS|UPS|FEDEX|DHL|OTHER", '
        '"tracking_number": "...", "order_hint": "<buyer name, item or amount if visible, else empty>", '
        '"confidence": "high|medium|low"}')})
    out = _json(_ask(content, max_tokens=250, json_mode=True))
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
