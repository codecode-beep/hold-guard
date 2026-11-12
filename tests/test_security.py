import base64

import pytest

from app import config, security


def basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


@pytest.fixture()
def locked(monkeypatch):
    monkeypatch.setattr(config, "APP_USERNAME", "judge")
    monkeypatch.setattr(config, "APP_PASSWORD", "s3cret-pass")


def test_off_by_default_on_your_mac(monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    assert security.allowed("/", None)
    assert security.allowed("/chat", None)


def test_pages_need_the_password(locked):
    for path in ("/", "/chat", "/disputes", "/sales/1", "/demo/seed", "/sales/1/evidence.pdf"):
        assert not security.allowed(path, None), path
        assert not security.allowed(path, basic("judge", "wrong")), path
        assert not security.allowed(path, basic("someone", "s3cret-pass")), path
        assert security.allowed(path, basic("judge", "s3cret-pass")), path


def test_paypal_webhook_and_health_stay_open(locked):
    assert security.allowed("/webhooks/paypal", None)
    assert security.allowed("/health", None)
    assert security.allowed("/static/style.css", None)
    assert security.allowed("/favicon.ico", None)
    assert not security.allowed("/webhooks/paypal/extra", None)


def test_bad_headers_are_rejected_not_crashing(locked):
    for header in ("", "Bearer abc", "Basic !!!notbase64", "Basic " + base64.b64encode(b"nocolon").decode(),
                   "Basic " + base64.b64encode(b"\xff\xfe:\xff").decode()):
        assert not security.allowed("/", header), header


def test_password_with_colon_works(monkeypatch):
    monkeypatch.setattr(config, "APP_USERNAME", "judge")
    monkeypatch.setattr(config, "APP_PASSWORD", "a:b:c")
    assert security.allowed("/", basic("judge", "a:b:c"))
