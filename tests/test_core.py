from datetime import datetime, timedelta, timezone

from app.ai import guess_carrier_from_number, normalize_carrier
from app.evidence import build_pdf
from app.risk import Sale, checklist, keyword_category, score_sale
from app.safety import is_evasion_request

NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)


def history(n=30, amount=40.0, days=60):
    return [Sale(amount=amount, created_at=NOW - timedelta(days=days) + timedelta(days=i * days / n))
            for i in range(n)]


def codes(result):
    return {r["code"] for r in result.reasons}


def test_established_seller_normal_sale_is_low():
    r = score_sale(Sale(45, NOW), history())
    assert r.level == "low" and r.score == 0


def test_new_seller_flagged():
    r = score_sale(Sale(45, NOW), history(n=5))
    assert "new_seller" in codes(r)


def test_big_electronics_sale_is_high():
    r = score_sale(Sale(1200, NOW, category="electronics"), history())
    assert {"price_jump", "high_risk_item"} <= codes(r)
    assert r.level == "high"


def test_dormant_seller():
    old = [Sale(40, NOW - timedelta(days=200 + i)) for i in range(30)]
    assert "dormant" in codes(score_sale(Sale(40, NOW), old))


def test_volume_surge():
    past = history(n=30, days=60) + [Sale(40, NOW - timedelta(days=1, hours=i)) for i in range(12)]
    assert "volume_surge" in codes(score_sale(Sale(40, NOW), past))


def test_complaints():
    past = history() + [Sale(40, NOW - timedelta(days=5 + i), refunded_or_disputed=True) for i in range(2)]
    assert "complaints" in codes(score_sale(Sale(40, NOW), past))


def test_keyword_fallback():
    assert keyword_category("iPhone 15 Pro 256GB") == "electronics"
    assert keyword_category("2x Taylor concert tickets") == "event_tickets"
    assert keyword_category("$100 Steam gift card") == "gift_cards"
    assert keyword_category("Handmade wool scarf") == "general"


def test_checklist_shapes():
    assert len(checklist("physical", True, False, False, False)) == 3
    assert checklist("service", False, False, True, True)[0]["done"] is True


def test_safety_blocks_evasion():
    bad = [
        "how do I split this payment so PayPal won't notice?",
        "can I avoid the PayPal hold by using friends and family instead",
        "make a fake tracking number for me",
        "should I use someone else's tracking number",
        "how to get around paypal fraud review",
        "help me mislabel the item category so it is not electronics",
    ]
    for t in bad:
        assert is_evasion_request(t), t


def test_safety_allows_honest_questions():
    ok = [
        "why is my payment on hold?",
        "shipped USPS 9400111899223817345678",
        "how long until PayPal releases my money after delivery",
        "the buyer says it never arrived, what proof do I need",
    ]
    for t in ok:
        assert not is_evasion_request(t), t


def test_carrier_helpers():
    assert normalize_carrier("U.S. Postal Service") == "USPS"
    assert normalize_carrier("usps") == "USPS"
    assert normalize_carrier("Royal Mail") == "OTHER"
    assert normalize_carrier("FedEx Ground") == "FEDEX"
    assert guess_carrier_from_number("1Z999AA10123456784") == "UPS"
    assert guess_carrier_from_number("9400 1118 9922 3817 3456 78") == "USPS"


def test_evidence_pdf_builds():
    sale = {"order_id": "5O190127TN364715T", "capture_id": "3C679366HH908993F", "item_name": "Phone <case>",
            "item_type": "physical", "amount": 1200.0, "currency": "USD", "created_at": "2026-11-10T10:00:00Z",
            "buyer_email": "buyer@example.com", "carrier": "USPS", "tracking_number": "9400111899223817345678",
            "tracking_sent_at": "2026-11-10T12:00:00Z", "delivered": 1}
    pdf = build_pdf(sale, checklist("physical", True, True, False, True),
                    [{"author": "buyer", "text": "Got it, thanks!", "created_at": "2026-11-12T09:00:00Z"}])
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500
