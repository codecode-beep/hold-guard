import pytest

from app import ai, config


@pytest.fixture()
def gemini(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-2.5-flash")
    sent = {}

    def fake_request(body):
        sent["body"] = body
        return {"candidates": [{"content": {"parts": [{"text": sent.get("reply", "")}]}}]}

    monkeypatch.setattr(ai, "_gemini_request", fake_request)
    return sent


def test_receipt_photo_goes_to_gemini_as_image(gemini):
    gemini["reply"] = '{"found": true, "carrier": "UPS", "tracking_number": "1Z999AA10123456784", "confidence": "high"}'
    out = ai.read_receipt("shipped today", image=b"\x89PNGfake", mime="image/png")
    parts = gemini["body"]["contents"][0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "image/png"
    assert "text" in parts[1]
    assert gemini["body"]["system_instruction"]["parts"][0]["text"].startswith("You are a precise data extraction")
    assert gemini["body"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert gemini["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert out["found"] and out["carrier"] == "UPS" and out["tracking_number"] == "1Z999AA10123456784"


def test_item_classifier_uses_gemini(gemini):
    gemini["reply"] = '```json\n{"category": "electronics", "reason": "A phone."}\n```'
    out = ai.classify_item("iPhone 15 Pro 256GB")
    assert out == {"category": "electronics", "reason": "A phone.", "by": "ai"}


def test_missing_key_falls_back_to_keywords(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    out = ai.classify_item("Steam gift card $50")
    assert out["category"] == "gift_cards" and out["by"] == "keywords"


def test_chat_answers_are_not_forced_into_json(gemini):
    gemini["reply"] = "PayPal may hold it because it's your biggest sale."
    ai.chat_reply("why might my iphone sale be held?", "")
    assert "responseMimeType" not in gemini["body"]["generationConfig"]
    assert gemini["body"]["system_instruction"]["parts"][0]["text"].startswith("You are Hold Guard")


def test_safety_filter_runs_before_any_ai(gemini):
    reply = ai.chat_reply("how do I split this payment so PayPal won't notice?", "")
    assert "can't help" in reply and "body" not in gemini   # Gemini was never called


def test_empty_answer_is_an_error(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(ai, "_gemini_request", lambda b: {"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(ai.AIError, match="SAFETY"):
        ai._ask("hi")
