"""Hold Guard web app (FastAPI)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import ai, alerts, config, db, demo, evidence, paypal, security, services
from .safety import REFUSAL, is_evasion_request

log = logging.getLogger("holdguard")
BASE = Path(__file__).parent
app = FastAPI(title="Hold Guard")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


@app.on_event("startup")
def startup():
    db.init()
    if security.is_enabled():
        log.info("Password protection is ON for user %r", config.APP_USERNAME)


@app.middleware("http")
async def require_password(request: Request, call_next):
    """Runs before every page. Asks the browser for the demo password when APP_PASSWORD is set."""
    if security.allowed(request.url.path, request.headers.get("authorization")):
        return await call_next(request)
    return Response("Password required for this Hold Guard demo.", status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Hold Guard demo", charset="UTF-8"'})


def page(request: Request, name: str, **ctx):
    ctx.setdefault("flash", request.query_params.get("msg"))
    ctx.setdefault("error", request.query_params.get("err"))
    return templates.TemplateResponse(request, name, {"seller": config.SELLER_NAME, **ctx})


def back(url: str, msg: str = "", err: str = ""):
    from urllib.parse import urlencode
    q = urlencode({k: v for k, v in (("msg", msg), ("err", err)) if v})
    return RedirectResponse(f"{url}{'?' + q if q else ''}", status_code=303)


# ---------- Dashboard ----------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    sales = db.list_sales()
    for s in sales:
        s["checklist"] = services.sale_checklist(s)
        s["next_step"] = next((c["label"] for c in s["checklist"] if not c["done"]), "All proof provided")
    counts = {lvl: sum(1 for s in sales if s["risk_level"] == lvl) for lvl in ("high", "medium", "low")}
    return page(request, "dashboard.html", sales=sales, counts=counts,
                disputes=[d for d in db.list_disputes() if d["status"] not in ("RESOLVED",)],
                demo_count=db.count_demo_history(), alerts_on=alerts.is_configured())


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return RedirectResponse("/static/favicon.svg", status_code=301)


@app.get("/health")
def health():
    return {"ok": True}


# ---------- Creating sandbox test sales ----------

@app.post("/sales/new")
def new_sale(item_name: str = Form(...), amount: float = Form(...), item_type: str = Form("physical")):
    try:
        order = paypal.create_order(item_name, amount, item_type)
        return RedirectResponse(paypal.approve_link(order), status_code=303)
    except paypal.PayPalError as e:
        return back("/", err=e.friendly())


@app.get("/paypal/return")
def paypal_return(token: str):
    """The sandbox buyer approved the order; capture it and record the sale."""
    try:
        existing = db.get_sale_by_order(token)
        if existing:
            return back(f"/sales/{existing['id']}")
        order = paypal.capture_order(token)
        sale_id = services.record_order(order)
        return back(f"/sales/{sale_id}", msg="Payment captured in the PayPal sandbox.")
    except paypal.PayPalError as e:
        return back("/", err=e.friendly())


@app.post("/sync")
def sync():
    try:
        n = services.sync_history()
        d = services.sync_disputes()
        return back("/", msg=f"Imported {n} past sales and {d} disputes from PayPal.")
    except paypal.PayPalError as e:
        return back("/", err=e.friendly())


# ---------- Demo helpers (always labeled as demo in the UI) ----------

@app.post("/demo/seed")
def demo_seed():
    n = demo.seed_history()
    for s in db.list_sales():
        services.rescore(s["id"])
    return back("/", msg=f"Added {n} demo past sales (last 60 days) so the risk check has a normal to compare with.")


@app.post("/demo/clear")
def demo_clear():
    db.delete_demo_history()
    for s in db.list_sales():
        services.rescore(s["id"])
    return back("/", msg="Demo history removed.")


@app.post("/sales/{sale_id}/hold")
def sale_hold(sale_id: int):
    demo.simulate_hold(db.get_sale(sale_id))
    return back(f"/sales/{sale_id}")


@app.post("/sales/{sale_id}/release")
def sale_release(sale_id: int):
    demo.simulate_release(db.get_sale(sale_id))
    return back(f"/sales/{sale_id}", msg="Sample hold released.")


@app.post("/sales/{sale_id}/alert")
def sale_alert(sale_id: int):
    if not alerts.is_configured():
        return back(f"/sales/{sale_id}", err="Email alerts aren't set up. Add the SMTP_ settings to .env.")
    try:
        alerts.send_alert(db.get_sale(sale_id))
        db.update_sale(sale_id, alert_sent_at=db.now(), alert_error=None)
        return back(f"/sales/{sale_id}", msg=f"Alert emailed to {config.ALERT_EMAIL}.")
    except Exception as e:
        db.update_sale(sale_id, alert_error=str(e)[:300])
        return back(f"/sales/{sale_id}", err=f"Email failed: {e}")


# ---------- Sale detail and actions (each one needs a seller click) ----------

@app.get("/sales/{sale_id}", response_class=HTMLResponse)
def sale_detail(request: Request, sale_id: int):
    sale = db.get_sale(sale_id)
    if not sale:
        return back("/", err="Sale not found")
    return page(request, "sale.html", sale=sale, checklist=services.sale_checklist(sale),
                messages=db.messages_for(sale_id), carriers=sorted(ai.CARRIERS),
                alerts_on=alerts.is_configured())


@app.post("/sales/{sale_id}/tracking")
def send_tracking(sale_id: int, carrier: str = Form(...), tracking_number: str = Form(...),
                  chat_id: int | None = Form(None)):
    sale = db.get_sale(sale_id)
    tracking_number = tracking_number.strip().replace(" ", "").upper()
    try:
        paypal.add_tracking(sale["order_id"], sale["capture_id"], carrier, tracking_number)
    except paypal.PayPalError as e:
        if chat_id:
            db.clear_proposal(chat_id, f"Could not send: {e.friendly()}")
            return back("/chat", err=e.friendly())
        return back(f"/sales/{sale_id}", err=e.friendly())
    db.update_sale(sale_id, carrier=carrier, tracking_number=tracking_number, tracking_sent_at=db.now())
    if chat_id:
        db.clear_proposal(chat_id, f"Sent to PayPal: {carrier} {tracking_number} for sale #{sale_id}.")
        return back("/chat", msg="Tracking sent to PayPal.")
    return back(f"/sales/{sale_id}", msg="Tracking sent to PayPal.")


@app.post("/sales/{sale_id}/complete")
def complete_service(sale_id: int):
    sale = db.get_sale(sale_id)
    try:
        paypal.mark_service_completed(sale["capture_id"])
    except paypal.PayPalError as e:
        return back(f"/sales/{sale_id}", err=e.friendly())
    db.update_sale(sale_id, completed_at=db.now())
    return back(f"/sales/{sale_id}", msg="Order marked completed with PayPal.")


@app.post("/sales/{sale_id}/delivered")
def mark_delivered(sale_id: int):
    # The sandbox has no real courier, so delivery confirmation is simulated for the demo.
    db.update_sale(sale_id, delivered=1)
    return back(f"/sales/{sale_id}", msg="Delivery marked as confirmed (sandbox simulation).")


@app.post("/sales/{sale_id}/messages")
def add_message(sale_id: int, author: str = Form("buyer"), text: str = Form(...)):
    if text.strip():
        db.add_message(sale_id, author, text.strip())
    return back(f"/sales/{sale_id}", msg="Message saved to the proof locker.")


@app.post("/sales/{sale_id}/explain")
def explain(sale_id: int):
    sale = db.get_sale(sale_id)
    try:
        db.update_sale(sale_id, explanation=ai.explain_hold(sale, services.sale_checklist(sale)))
    except Exception as e:
        return back(f"/sales/{sale_id}", err=ai.friendly_error(e))
    return back(f"/sales/{sale_id}")


@app.post("/sales/{sale_id}/support-message")
def support_message(sale_id: int):
    sale = db.get_sale(sale_id)
    try:
        db.update_sale(sale_id, support_message=ai.draft_support_message(sale, services.sale_checklist(sale)))
    except Exception as e:
        return back(f"/sales/{sale_id}", err=ai.friendly_error(e))
    return back(f"/sales/{sale_id}")


@app.get("/sales/{sale_id}/evidence.pdf")
def evidence_pdf(sale_id: int):
    sale = db.get_sale(sale_id)
    pdf = evidence.build_pdf(sale, services.sale_checklist(sale), db.messages_for(sale_id))
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="evidence-{sale["order_id"]}.pdf"'})


# ---------- Chat (phone-friendly) ----------

@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    return page(request, "chat.html", history=db.chat_history())


@app.post("/chat")
async def chat_send(text: str = Form(""), photo: UploadFile | None = File(None)):
    text = text.strip()
    image = await photo.read() if photo and photo.filename else None
    if not text and not image:
        return back("/chat")
    db.add_chat("seller", text or "(sent a photo)")
    if is_evasion_request(text):
        db.add_chat("assistant", REFUSAL)
        return back("/chat")
    try:
        looks_like_receipt = image is not None or any(ch.isdigit() for ch in text) and len(text) > 8
        if looks_like_receipt:
            found = ai.read_receipt(text, image, (photo.content_type if image else None) or "image/jpeg")
            if found.get("found"):
                sale = services.find_sale_for_receipt(found.get("order_hint", "") + " " + text)
                if sale:
                    warn = "" if found["shape_matches"] else " The number's format doesn't look like this carrier, so please double-check it."
                    db.add_chat("assistant",
                                f"I found {found['carrier']} tracking {found['tracking_number']} "
                                f"(confidence: {found['confidence']}). It looks like it's for sale "
                                f"#{sale['id']}: {sale['item_name']} (${sale['amount']:.2f}).{warn} "
                                "Tap Confirm to send it to PayPal, or fix it first.",
                                proposal={"type": "tracking", "sale_id": sale["id"],
                                          "carrier": found["carrier"], "tracking_number": found["tracking_number"]})
                else:
                    db.add_chat("assistant", f"I found {found['carrier']} {found['tracking_number']}, but every "
                                "physical sale already has tracking. Open a sale to add it by hand.")
                return back("/chat")
        db.add_chat("assistant", ai.chat_reply(text, services.sales_summary()))
    except Exception as e:
        log.exception("chat failed")
        msg = e.friendly() if isinstance(e, paypal.PayPalError) else ai.friendly_error(e)
        db.add_chat("assistant", f"Sorry, I couldn't do that. {msg}")
    return back("/chat")


@app.post("/chat/{chat_id}/dismiss")
def chat_dismiss(chat_id: int):
    db.clear_proposal(chat_id, "Cancelled by seller. Nothing was sent.")
    return back("/chat")


# ---------- Disputes ----------

@app.get("/disputes", response_class=HTMLResponse)
def disputes_page(request: Request):
    ds = db.list_disputes()
    for d in ds:
        d["sale"] = db.get_sale(d["sale_id"]) if d["sale_id"] else None
    return page(request, "disputes.html", disputes=ds)


@app.post("/disputes/sync")
def disputes_sync():
    try:
        n = services.sync_disputes()
        return back("/disputes", msg=f"Loaded {n} disputes from PayPal.")
    except paypal.PayPalError as e:
        return back("/disputes", err=e.friendly())


@app.post("/disputes/{dispute_id}/draft")
def dispute_draft(dispute_id: str):
    d = db.get_dispute(dispute_id)
    sale = db.get_sale(d["sale_id"]) if d["sale_id"] else None
    try:
        draft = ai.draft_dispute_reply(d, sale, db.messages_for(sale["id"]) if sale else [])
    except Exception as e:
        return back("/disputes", err=ai.friendly_error(e))
    db.update_dispute(dispute_id, ai_draft=draft)
    return back("/disputes")


@app.post("/disputes/{dispute_id}/submit")
def dispute_submit(dispute_id: str, notes: str = Form(...)):
    """Seller reviewed (and maybe edited) the AI draft and clicked Submit."""
    d = db.get_dispute(dispute_id)
    sale = db.get_sale(d["sale_id"]) if d["sale_id"] else None
    pdf = evidence.build_pdf(sale, services.sale_checklist(sale), db.messages_for(sale["id"])) if sale else None
    try:
        result = paypal.provide_evidence(dispute_id, notes, sale and sale.get("carrier"),
                                         sale and sale.get("tracking_number"), pdf)
    except paypal.PayPalError as e:
        db.update_dispute(dispute_id, ai_draft=notes, submit_result=e.friendly())  # keep the seller's edits
        try:  # refresh the case status, so the page shows why PayPal said no
            services.store_dispute(paypal.get_dispute(dispute_id))
        except paypal.PayPalError:
            pass
        return back("/disputes", err=e.friendly())
    db.update_dispute(dispute_id, ai_draft=notes, submitted_at=db.now(), submit_result=json.dumps(result)[:500])
    return back("/disputes", msg="Evidence submitted to PayPal.")


# ---------- Webhooks ----------

@app.post("/webhooks/paypal")
async def webhook(request: Request):
    raw = await request.body()
    event = json.loads(raw or b"{}")
    try:
        if not paypal.verify_webhook(dict(request.headers), event):
            log.warning("webhook signature not verified; ignored")
            return Response(status_code=400)
    except paypal.PayPalError:
        log.exception("webhook verification call failed")
        return Response(status_code=500)  # PayPal will retry later
    if db.save_event(event.get("id", ""), event.get("event_type", ""), raw.decode()):
        try:
            services.handle_webhook(event)
        except Exception:
            log.exception("webhook handling failed")
    return {"ok": True}
