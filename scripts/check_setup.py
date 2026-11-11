"""Day 1 done-check: confirms your PayPal sandbox keys and Claude key work.

Run from the project folder:  python -m scripts.check_setup
"""
from app import config, paypal


def main():
    print("PayPal sandbox:", end=" ")
    try:
        paypal.check_auth()
        print("OK, got an access token")
    except paypal.PayPalError as e:
        print("FAILED ->", e)

    print("Claude:", end=" ")
    if not config.ANTHROPIC_API_KEY:
        print("FAILED -> ANTHROPIC_API_KEY is not set in .env")
        return
    try:
        from app import ai
        print("OK ->", ai.classify_item("iPhone 15 Pro 256GB"))
    except Exception as e:
        print("FAILED ->", e)


if __name__ == "__main__":
    main()
