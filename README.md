# Hold Guard

**An AI assistant that helps small sellers prevent and release PayPal payment holds by sending PayPal true proof of every sale, from a simple chat or a photo.**

Built for the PayPal AI Hackathon. Runs entirely on the PayPal **sandbox** (fake money).

## The problem

PayPal sometimes holds a seller's money to protect buyers. PayPal publishes why (new or inactive seller, complaints, unusual selling patterns, higher-risk items like electronics and tickets) and how to release it (add tracking: released about 24 hours after delivery; services marked completed: released about 7 days later). Small sellers on Instagram, WhatsApp, Facebook Marketplace or PayPal invoices often don't know these steps or do them too late, so their money stays stuck.

## What Hold Guard does

| Stage | Feature | PayPal API | AI (Google Gemini) |
|---|---|---|---|
| Before | **Email alert** the moment a high-risk sale arrives, with the exact steps to take | Webhooks | — |
| Before | **Hold Risk Check** scores each sale on PayPal's published hold reasons, compared with the seller's own history | Webhooks, Orders v2, Transaction Search | Reads free-text item names ("iPhone 15 Pro") to spot higher-risk categories |
| During | **Chat**: send a receipt photo or "shipped USPS 9400…"; seller taps Confirm; tracking goes to PayPal | Orders v2 `/track` | Reads the receipt photo, finds carrier + tracking number |
| During | **Services**: mark the order completed | Add Tracking (no tracking number) | — |
| After | **Hold Explainer**, per-sale checklist, **Evidence Pack PDF**, drafted message to PayPal support | — | Writes plain-language explanation and support message |
| After | **Dispute helper**: AI drafts the reply, seller edits and approves, evidence + PDF submitted | Disputes API `provide-evidence` | Drafts the response from real sale facts only |

**Trust rules built in:** every action sent to PayPal needs a seller click; the AI refuses requests to split payments, fake tracking, or dodge PayPal's checks (a filter runs before the AI, and the AI's instructions repeat the rule). Hold Guard only adds *true* information, which helps honest sellers and gives scammers nothing.

## Run it locally

Needs Python 3.11+.

```bash
git clone https://github.com/<you>/hold-guard.git
cd hold-guard
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env        # then fill in your keys
python -m scripts.check_setup
python -m uvicorn app.main:app --reload
```

Open http://localhost:8000

### Keys you need
1. **PayPal sandbox app**: developer.paypal.com → Apps & Credentials → Sandbox → Create App. Copy Client ID and Secret.
2. **Sandbox accounts**: developer.paypal.com → Testing Tools → Sandbox Accounts. Use the default *business* account as the seller (it owns the app) and the *personal* account as the buyer.
3. **Google Gemini key**: aistudio.google.com → Get API key (free tier). Put it in `GEMINI_API_KEY`.

## Deploy on Render

1. New → Blueprint → pick this repo (it uses `render.yaml`).
2. Fill in the secret env vars. Set `APP_BASE_URL` to your Render URL.
3. In your PayPal sandbox app → Webhooks → Add webhook: `https://<your-app>.onrender.com/webhooks/paypal`, events `Payment capture completed` and all `Customer dispute` events. Copy the Webhook ID into `PAYPAL_WEBHOOK_ID`.

Note: Render's free plan resets the SQLite file on each deploy. Use **Sync now** to reload from PayPal.

## Try the demo

1. Click **Add demo history** (about 2 months of small, labeled demo sales so the seller has a "normal"; or run `python -m scripts.seed_demo`).
2. **Create a sandbox test sale** for a normal item ("Wool scarf", $35) → **Low** risk.
3. Create a big electronics sale ("iPhone 15 Pro 256GB", $1,200) → flagged **High**, 34x the usual sale, electronics; an email alert arrives if SMTP is set.
4. On that sale, click **Simulate PayPal hold (sample data)** to show what a held payment looks like.
5. Go to **Chat**, upload a photo of a shipping receipt (or type `shipped USPS 9400111899223817345678`) → Confirm → tracking appears on the order in the sandbox.
6. Log in at sandbox.paypal.com as the buyer, open that payment, report "item not received" → **Disputes** → Load → Draft with AI → Approve and submit.
7. Open the sale → **Download Evidence Pack**.

Demo history rows and sample holds are labeled in the app and are never sent to PayPal.

## Security
- **Demo password:** when `APP_PASSWORD` is set (as on the hosted demo), the browser asks for a username and password before showing any page. Login details for judges are in the Devpost testing instructions. On your own computer, leave it empty.
- **PayPal webhook** stays open (PayPal can't type a password) and is protected instead by verifying PayPal's signature on every message.
- **Secrets** (PayPal, Gemini, email) live only in environment variables, never in the code or pages.
- **Every action sent to PayPal needs a seller click,** and an anti-fraud filter blocks requests to get around PayPal's checks.
- **One seller per deployment:** the demo acts as the sandbox business account that owns the PayPal app keys. A production version would let each seller connect their own PayPal account through PayPal's partner onboarding.

## Honest limits
- The sandbox can't create real payment holds or real courier deliveries. Delivery confirmation is a labeled simulation button; holds in the video use labeled sample data. Every PayPal call (orders, tracking, transaction search, disputes, webhooks) is real sandbox traffic.
- Hold Guard can't force PayPal to release money. It does the steps PayPal says help, sooner.
- The risk score uses PayPal's **public** hold reasons; PayPal's internal systems use more signals.
- PayPal's REST API has no direct "mark order Completed" call like the seller website. For services, Hold Guard uses the Add Tracking API's documented "without tracking number" option, the closest API equivalent. Whether PayPal's hold system treats both identically can only be confirmed on a live account.
- Some sandbox dispute records list only the buyer's transaction ID, so Hold Guard falls back to matching a dispute to the most recent sale with the same amount.
- In the sandbox, an "item not received" dispute can move straight to PayPal review within minutes, which may limit when evidence can be submitted.

## Challenges we hit
- **Services have no tracking number.** Our first request included a carrier, and PayPal answered `TRACKING_NUMBER_IS_MISSING`. PayPal's docs show the no-tracking-number request must send only the transaction ID and a status.
- **Two copies of uvicorn.** On machines with pyenv, `uvicorn` can start the system Python instead of the project's. Running `python -m uvicorn` always uses the project's environment.
- **Disputes missing the seller's transaction ID**, solved with the amount-based fallback above.
- **Disputes closing to the seller.** A sandbox "item not received" case moved to PayPal's own review within minutes, and PayPal answered `ACTION_NOT_ALLOWED_IN_CURRENT_DISPUTE_STATE`. Hold Guard now shows each case's status in plain words, disables Submit once PayPal no longer accepts evidence, and turns every PayPal error into a plain-language message with the PayPal code kept as a small reference.
- **AI answering in sentences instead of data.** The item and receipt readers need JSON, but the chatty assistant instructions made Gemini reply conversationally. We gave those two tasks their own short "data extractor" instructions and set Gemini's `responseMimeType` to `application/json`, which makes it return valid JSON only.

## AI tools used
**Google Gemini** (`gemini-2.5-flash` through the Google AI Studio API; model set by `GEMINI_MODEL`).

Gemini does six jobs:
1. **Item reader:** classifies free-text item names into PayPal's risk categories (strict JSON output)
2. **Receipt reader:** reads a shipping receipt photo or message and extracts carrier + tracking number (strict JSON output)
3. **Hold explainer:** explains in plain words why a sale may be held and what to do
4. **Support message:** drafts a message to PayPal support listing the proof provided
5. **Dispute reply:** drafts the seller's response from real sale facts only
6. **Chat:** answers seller questions about their sales

The anti-fraud filter runs before Gemini is called, and Gemini's instructions repeat the rule.

Note: Google's free tier may use requests to improve its products. Only sandbox test data is used in this demo; a production version would use a paid tier.

## Tests
```bash
python -m pytest -q
```

## License
MIT
