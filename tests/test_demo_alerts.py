import sys
import types

import pytest

sys.modules.setdefault("httpx", types.ModuleType("httpx"))  # no network needed for these tests

from app import alerts, config, db, demo, services  # noqa: E402


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    db.init()  # second run must not fail (column migration is safe to repeat)


def add_live(order_id, name, amount, category):
    sid = db.insert_sale(order_id=order_id, capture_id=order_id.upper(), item_name=name, item_type="physical",
                         amount=amount, currency="USD", created_at=db.now(), source="live", category=category)
    services.rescore(sid)
    return db.get_sale(sid)


def test_demo_history_gives_seller_a_normal(fresh_db):
    assert demo.seed_history() == 30
    assert demo.seed_history() == 30          # running again replaces, doesn't duplicate
    assert db.count_demo_history() == 30
    assert db.list_sales() == []              # demo rows never show as live sales

    normal = add_live("o-1", "Wool scarf", 35, "general")
    assert normal["risk_level"] == "low"

    phone = add_live("o-2", "iPhone 15 Pro 256GB", 1200, "electronics")
    codes = {r["code"] for r in phone["risk_reasons"]}
    assert phone["risk_level"] == "high"
    assert {"price_jump", "high_risk_item"} <= codes and "new_seller" not in codes


def test_without_history_seller_is_new(fresh_db):
    sale = add_live("o-3", "Wool scarf", 35, "general")
    assert "new_seller" in {r["code"] for r in sale["risk_reasons"]}


def test_sample_hold_and_release(fresh_db):
    sale = add_live("o-4", "Wool scarf", 35, "general")
    demo.simulate_hold(sale)
    held = db.get_sale(sale["id"])
    assert held["hold_status"] == "held" and held["hold_note"].startswith("SAMPLE")
    demo.simulate_release(held)
    assert db.get_sale(sale["id"])["hold_status"] == "released"


def test_alert_email_content(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", "me@example.com")
    monkeypatch.setattr(config, "ALERT_EMAIL", "me@example.com")
    demo.seed_history()
    phone = add_live("o-5", "iPhone 15 Pro 256GB", 1200, "electronics")
    msg = alerts.build_alert(phone)
    body = msg.get_content()
    assert "1,200.00" in msg["Subject"] and "high risk" in msg["Subject"]
    assert "What to do:" in body and f"/sales/{phone['id']}" in body


def test_alert_failure_never_breaks_sale(fresh_db, monkeypatch):
    for k, v in {"SMTP_HOST": "smtp.invalid", "SMTP_USER": "a", "SMTP_PASSWORD": "b", "ALERT_EMAIL": "c"}.items():
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(alerts, "send_alert", lambda s: (_ for _ in ()).throw(OSError("no route")))
    demo.seed_history()
    sale = add_live("o-6", "Concert tickets x2", 900, "event_tickets")
    assert sale["risk_level"] == "high"
    services.maybe_alert(sale["id"])
    after = db.get_sale(sale["id"])
    assert after["alert_sent_at"] is None and "no route" in after["alert_error"]
