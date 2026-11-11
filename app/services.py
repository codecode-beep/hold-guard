"""Business logic: turns PayPal data into scored sales in the proof locker."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import ai, alerts, config, db, paypal
from .risk import Sale, checklist, score_sale


def _dt(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def record_order(order: dict) -> int:
    """Save a captured PayPal order as a sale, classify the item with AI, and score its risk."""
    pu = order["purchase_units"][0]
    capture = pu["payments"]["captures"][0]
    items = pu.get("items") or [{}]
    item_name = items[0].get("name") or pu.get("description") or "Item"
    item_type = "service" if items[0].get("category") == "DIGITAL_GOODS" else "physical"
    existing = db.get_sale_by_order(order["id"])
    if existing:
        return existing["id"]
    sale_id = db.insert_sale(
        order_id=order["id"], capture_id=capture["id"], item_name=item_name, item_type=item_type,
        amount=float(capture["amount"]["value"]), currency=capture["amount"]["currency_code"],
        buyer_email=(order.get("payer") or {}).get("email_address"),
        created_at=capture.get("create_time") or db.now(), source="live",
    )
    cat = ai.classify_item(item_name)
    db.update_sale(sale_id, category=cat["category"], category_reason=cat["reason"])
    rescore(sale_id)
    maybe_alert(sale_id)
    return sale_id


def maybe_alert(sale_id: int):
    """Email the seller right away if this sale is likely to be held."""
    sale = db.get_sale(sale_id)
    if sale["risk_level"] not in config.ALERT_LEVELS or sale.get("alert_sent_at") or not alerts.is_configured():
        return
    try:
        alerts.send_alert(sale)
        db.update_sale(sale_id, alert_sent_at=db.now(), alert_error=None)
    except Exception as e:  # an email problem must never break saving the sale
        db.update_sale(sale_id, alert_error=f"{e.__class__.__name__}: {e}"[:300])


def _as_risk_sale(s: dict) -> Sale:
    return Sale(amount=s["amount"], created_at=_dt(s["created_at"]), category=s.get("category") or "general",
                refunded_or_disputed=bool(s.get("refunded_or_disputed")))


def rescore(sale_id: int):
    sale = db.get_sale(sale_id)
    others = [s for s in db.list_sales(live_only=False) if s["id"] != sale_id]
    result = score_sale(_as_risk_sale(sale), [_as_risk_sale(s) for s in others])
    db.update_sale(sale_id, risk_score=result.score, risk_level=result.level, risk_reasons=result.reasons)


def sale_checklist(sale: dict) -> list[dict]:
    return checklist(
        sale["item_type"], tracking_sent=bool(sale.get("tracking_sent_at")),
        delivered=bool(sale.get("delivered")), completed=bool(sale.get("completed_at")),
        has_messages=bool(db.messages_for(sale["id"])),
    )


def sync_history() -> int:
    """Import past sales from PayPal Transaction Search so 'normal' is learned from real data."""
    count = 0
    for t in paypal.search_transactions(days=31):
        info = t.get("transaction_info", {})
        amt = float(info.get("transaction_amount", {}).get("value", 0) or 0)
        tid = info.get("transaction_id")
        if amt <= 0 or not tid or db.get_sale_by_capture(tid):
            continue
        items = (t.get("cart_info") or {}).get("item_details") or [{}]
        name = items[0].get("item_name") or info.get("transaction_subject") or "Past sale"
        db.insert_sale(order_id=f"hist-{tid}", capture_id=tid, item_name=name, item_type="physical",
                       amount=amt, currency=info.get("transaction_amount", {}).get("currency_code", "USD"),
                       buyer_email=(t.get("payer_info") or {}).get("email_address"),
                       created_at=info.get("transaction_initiation_date"), source="history",
                       category=ai.keyword_category(name))
        count += 1
    for s in db.list_sales():
        rescore(s["id"])
    return count


def store_dispute(d: dict):
    txs = d.get("disputed_transactions") or [{}]
    capture_id = txs[0].get("seller_transaction_id")
    sale = db.get_sale_by_capture(capture_id) if capture_id else None
    if not sale:
        # Some dispute records only carry the buyer's transaction ID. Fall back to the
        # most recent live sale with the same amount.
        amount = float((d.get("dispute_amount") or {}).get("value", 0) or 0)
        sale = next((s for s in db.list_sales() if abs(s["amount"] - amount) < 0.01), None)
    buyer_note = ""
    for m in d.get("messages") or []:
        if m.get("posted_by") == "BUYER":
            buyer_note = m.get("content", "")
    db.upsert_dispute(
        id=d["dispute_id"], sale_id=sale["id"] if sale else None, reason=d.get("reason"),
        status=d.get("status"), amount=float((d.get("dispute_amount") or {}).get("value", 0) or 0),
        buyer_note=buyer_note, raw=json.dumps(d),
    )
    if sale:
        db.update_sale(sale["id"], refunded_or_disputed=1)


def sync_disputes() -> int:
    items = paypal.list_disputes()
    for item in items:
        store_dispute(paypal.get_dispute(item["dispute_id"]))
    return len(items)


def handle_webhook(event: dict):
    etype = event.get("event_type", "")
    res = event.get("resource", {})
    if etype == "PAYMENT.CAPTURE.COMPLETED":
        order_id = ((res.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id")
        if order_id and not db.get_sale_by_order(order_id):
            record_order(paypal.get_order(order_id))
    elif etype.startswith("CUSTOMER.DISPUTE."):
        if res.get("dispute_id"):
            store_dispute(paypal.get_dispute(res["dispute_id"]))


def sales_summary(limit: int = 8) -> str:
    rows = db.list_sales()[:limit]
    if not rows:
        return "No sales yet."
    return "\n".join(
        f"#{s['id']} {s['item_name']} ${s['amount']:.2f} risk={s['risk_level']} "
        f"tracking={'sent' if s['tracking_sent_at'] else 'missing'}" for s in rows)


def find_sale_for_receipt(hint: str) -> dict | None:
    """Match a receipt to a sale: most recent physical sale without tracking, preferring a name match."""
    open_sales = [s for s in db.list_sales() if s["item_type"] == "physical" and not s["tracking_sent_at"]]
    if not open_sales:
        return None
    h = (hint or "").lower()
    for s in open_sales:
        words = [w for w in s["item_name"].lower().split() if len(w) > 3]
        if any(w in h for w in words) or (s.get("buyer_email") or "~").split("@")[0].lower() in h:
            return s
    return open_sales[0]
