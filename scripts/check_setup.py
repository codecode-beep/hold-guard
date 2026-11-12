"""Done-check: confirms your PayPal sandbox keys and your Gemini key work.

Run from the project folder:  python -m scripts.check_setup
"""
from app import ai, config, paypal


def main():
    print("PayPal sandbox:", end=" ")
    try:
        paypal.check_auth()
        print("OK, got an access token")
    except paypal.PayPalError as e:
        print("FAILED ->", e)

    print(f"AI: {ai.provider_name()}")
    if not config.GEMINI_API_KEY:
        print("AI: FAILED -> GEMINI_API_KEY is not set in .env")
        return
    try:
        # 1) Plain answer, 2) the JSON item reader (must come back from the AI, not the keyword fallback)
        answer = ai._ask("In one short sentence: what is one reason PayPal may hold a payment?", max_tokens=120)
        print("AI chat: OK ->", answer[:120])
        item = ai.classify_item("iPhone 15 Pro 256GB")
        if item["by"] == "ai" and item["category"] == "electronics":
            print("AI item reader: OK ->", item)
        else:
            print("AI item reader: PROBLEM ->", item)
    except Exception as e:
        print("AI: FAILED ->", e)


if __name__ == "__main__":
    main()
