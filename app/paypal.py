"""Small PayPal REST client (sandbox). Every call uses official PayPal endpoints."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import config

_token = {"value": None, "expires": 0.0}


class PayPalError(Exception):
    def __init__(self, status: int, body: str, where: str):
        super().__init__(f"PayPal {where} failed ({status}): {body[:500]}")
        self.status, self.body, self.where = status, body, where


def _access_token() -> str:
    """OAuth 2.0 client-credentials: trade Client ID + Secret for a short-lived token."""
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    if not (config.PAYPAL_CLIENT_ID and config.PAYPAL_CLIENT_SECRET):
        raise PayPalError(0, "PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set", "auth")
    r = httpx.post(
        f"{config.PAYPAL_BASE}/v1/oauth2/token",
        auth=(config.PAYPAL_CLIENT_ID, config.PAYPAL_CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    if r.status_code != 200:
        raise PayPalError(r.status_code, r.text, "auth")
    data = r.json()
    _token["value"] = data["access_token"]
    _token["expires"] = time.time() + data.get("expires_in", 3000)
    return _token["value"]


def _call(method: str, path: str, where: str, **kw) -> dict:
    headers = kw.pop("headers", {})
    headers["Authorization"] = f"Bearer {_access_token()}"
    r = httpx.request(method, f"{config.PAYPAL_BASE}{path}", headers=headers, timeout=60, **kw)
    if r.status_code >= 300:
        raise PayPalError(r.status_code, r.text, where)
    return r.json() if r.content else {}


def check_auth() -> bool:
    return bool(_access_token())


# ---------- Orders v2 ----------

def create_order(item_name: str, amount: float, item_type: str) -> dict:
    """Create a sandbox order. The buyer approves it at the returned 'payer-action' link."""
    category = "DIGITAL_GOODS" if item_type == "service" else "PHYSICAL_GOODS"
    body = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "description": item_name[:127],
            "amount": {
                "currency_code": "USD",
                "value": f"{amount:.2f}",
                "breakdown": {"item_total": {"currency_code": "USD", "value": f"{amount:.2f}"}},
            },
            "items": [{
                "name": item_name[:127],
                "quantity": "1",
                "category": category,
                "unit_amount": {"currency_code": "USD", "value": f"{amount:.2f}"},
            }],
        }],
        "payment_source": {"paypal": {"experience_context": {
            "brand_name": config.SELLER_NAME[:127],
            "shipping_preference": "NO_SHIPPING" if item_type == "service" else "GET_FROM_FILE",
            "user_action": "PAY_NOW",
            "return_url": f"{config.APP_BASE_URL}/paypal/return",
            "cancel_url": f"{config.APP_BASE_URL}/",
        }}},
    }
    return _call("POST", "/v2/checkout/orders", "create order", json=body,
                 headers={"Prefer": "return=representation"})


def approve_link(order: dict) -> str:
    for link in order.get("links", []):
        if link.get("rel") in ("payer-action", "approve"):
            return link["href"]
    raise PayPalError(0, json.dumps(order), "find approve link")


def capture_order(order_id: str) -> dict:
    return _call("POST", f"/v2/checkout/orders/{order_id}/capture", "capture order", json={},
                 headers={"Prefer": "return=representation"})


def get_order(order_id: str) -> dict:
    return _call("GET", f"/v2/checkout/orders/{order_id}", "get order")


def add_tracking(order_id: str, capture_id: str, carrier: str, tracking_number: str) -> dict:
    """Orders v2 tracking: PayPal's recommended way to share tracking details."""
    body = {
        "capture_id": capture_id,
        "tracking_number": tracking_number,
        "carrier": carrier,
        "notify_payer": True,
    }
    if carrier == "OTHER":
        body["carrier_name_other"] = "Other"
    return _call("POST", f"/v2/checkout/orders/{order_id}/track", "add tracking", json=body)


def mark_service_completed(capture_id: str) -> dict:
    """Services / digital items have no tracking number. We use the Add Tracking API's
    'no tracking number' option to tell PayPal the order is fulfilled.
    (PayPal's seller dashboard calls this updating the order status to Completed.)"""
    # PayPal's documented "without tracking numbers" request: only the transaction ID and a status.
    # Adding a carrier makes PayPal require a tracking number (TRACKING_NUMBER_IS_MISSING).
    body = {"trackers": [{
        "transaction_id": capture_id,
        "status": "SHIPPED",
    }]}
    return _call("POST", "/v1/shipping/trackers-batch", "mark completed", json=body)


# ---------- Transaction Search (sales history) ----------

def search_transactions(days: int = 31) -> list[dict]:
    """PayPal allows up to 31 days per request; data can take a few hours to appear."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=min(days, 31))
    out, page = [], 1
    while True:
        data = _call("GET", "/v1/reporting/transactions", "transaction search", params={
            "start_date": start.strftime("%Y-%m-%dT%H:%M:%S-0000"),
            "end_date": end.strftime("%Y-%m-%dT%H:%M:%S-0000"),
            "fields": "transaction_info,payer_info,cart_info",
            "page_size": 100, "page": page,
        })
        out.extend(data.get("transaction_details", []))
        if page >= data.get("total_pages", 1):
            return out
        page += 1


# ---------- Disputes ----------

def list_disputes() -> list[dict]:
    return _call("GET", "/v1/customer/disputes", "list disputes", params={"page_size": 50}).get("items", [])


def get_dispute(dispute_id: str) -> dict:
    return _call("GET", f"/v1/customer/disputes/{dispute_id}", "get dispute")


def provide_evidence(dispute_id: str, notes: str, carrier: str | None, tracking: str | None,
                     pdf_bytes: bytes | None) -> dict:
    evidence: dict = {"evidence_type": "PROOF_OF_FULFILLMENT", "notes": notes[:2000]}
    if carrier and tracking:
        evidence["evidence_info"] = {"tracking_info": [{"carrier_name": carrier, "tracking_number": tracking}]}
    files = {"input": (None, json.dumps({"evidences": [evidence]}), "application/json")}
    if pdf_bytes:
        files["file1"] = ("evidence-pack.pdf", pdf_bytes, "application/pdf")
    return _call("POST", f"/v1/customer/disputes/{dispute_id}/provide-evidence", "provide evidence",
                 files=files)


# ---------- Webhooks ----------

def verify_webhook(headers: dict, event: dict) -> bool:
    """Ask PayPal to confirm the webhook really came from PayPal (not a fake caller)."""
    if not config.PAYPAL_WEBHOOK_ID:
        return False
    h = {k.lower(): v for k, v in headers.items()}
    body = {
        "auth_algo": h.get("paypal-auth-algo"),
        "cert_url": h.get("paypal-cert-url"),
        "transmission_id": h.get("paypal-transmission-id"),
        "transmission_sig": h.get("paypal-transmission-sig"),
        "transmission_time": h.get("paypal-transmission-time"),
        "webhook_id": config.PAYPAL_WEBHOOK_ID,
        "webhook_event": event,
    }
    data = _call("POST", "/v1/notifications/verify-webhook-signature", "verify webhook", json=body)
    return data.get("verification_status") == "SUCCESS"
