import json

from jinja2 import Environment, FileSystemLoader

from app import ai
from app.paypal import PayPalError

DISPUTE_STATE_BODY = json.dumps({
    "name": "UNPROCESSABLE_ENTITY",
    "message": "The requested action could not be performed, semantically incorrect, or failed business validation.",
    "debug_id": "f607024fed625",
    "details": [{"issue": "ACTION_NOT_ALLOWED_IN_CURRENT_DISPUTE_STATE"}],
})


def test_dispute_state_error_is_plain_language():
    msg = PayPalError(422, DISPUTE_STATE_BODY, "provide evidence").friendly()
    assert msg.startswith("PayPal isn't accepting evidence for this case right now.")
    assert "UNPROCESSABLE_ENTITY" not in msg and "{" not in msg
    assert msg.endswith("(PayPal: ACTION_NOT_ALLOWED_IN_CURRENT_DISPUTE_STATE, reference f607024fed625)")


def test_tracking_missing_and_auth_errors():
    body = json.dumps({"name": "INVALID_REQUEST", "debug_id": "abc", "details": [{"issue": "TRACKING_NUMBER_IS_MISSING"}]})
    assert "needs a tracking number" in PayPalError(400, body, "mark completed").friendly()
    auth = PayPalError(401, '{"error":"invalid_client","error_description":"Client Authentication failed"}', "auth")
    assert "PAYPAL_CLIENT_ID" in auth.friendly()


def test_unknown_and_non_json_errors_never_crash():
    assert "try again in a few minutes" in PayPalError(503, "<html>oops</html>", "capture order").friendly()
    assert PayPalError(418, "", "x").friendly() == "PayPal couldn't complete this (x)."
    assert "not set" in PayPalError(0, "PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set", "auth").friendly()


def test_ai_errors_are_plain_language():
    assert "free usage limit" in ai.friendly_error(ai.AIError("Gemini error 429: quota", status=429))
    assert "GEMINI_API_KEY" in ai.friendly_error(ai.AIError("GEMINI_API_KEY is not set"))
    assert ai.friendly_error(ValueError("bad json")) == "The AI couldn't answer this time. Please try again."


def _render(status):
    env = Environment(loader=FileSystemLoader("app/templates"))
    d = {"id": "PP-R-1", "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", "amount": 1000, "status": status,
         "sale": {"id": 3, "item_name": "iphone", "tracking_number": "X1", "carrier": "USPS"},
         "buyer_note": "", "ai_draft": "Hello", "submitted_at": None, "submit_result": None}
    return env.get_template("disputes.html").render(disputes=[d], seller="D", flash=None, error=None)


def test_dispute_under_review_disables_submit():
    html = _render("UNDER_REVIEW")
    assert "Under PayPal review" in html and "no longer accepts evidence" in html
    assert "<button class=\"btn\" disabled>Approve and submit" in html


def test_dispute_waiting_for_seller_allows_submit():
    html = _render("WAITING_FOR_SELLER_RESPONSE")
    assert "Waiting for your response" in html and "no longer accepts" not in html
    assert "<button class=\"btn\" >Approve and submit" in html
