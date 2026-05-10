# Deployment guide

## Prerequisites
- Google Cloud project with billing enabled
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A Google Cloud service account with:
  - Google Sheets API enabled
  - Google Calendar API enabled
  - The service account email invited to the target calendar and spreadsheet
- Anthropic API key

---

## 1. Local setup

```bash
pip install -r requirements.txt
```

### Create the spreadsheet structure
```bash
python setup_sheets.py path/to/service-account.json YOUR_SPREADSHEET_ID
```
Then open the `config` sheet and fill in:

| key | value |
|-----|-------|
| work_start | 09:00 |
| work_end | 17:00 |
| pickup_time | 15:30 |
| dropoff_time | 08:00 |
| husband_email | spouse@example.com |
| telegram_whitelist | 123456789,987654321 |

---

## 2. Environment variables

Set these in `.env` (local) or Cloud Run (production):

```
TELEGRAM_BOT_TOKEN=...
ANTHROPIC_API_KEY=...
GOOGLE_SHEETS_ID=...
GOOGLE_CALENDAR_ID=...
GOOGLE_SERVICE_ACCOUNT_JSON=/secrets/service-account.json
WEBHOOK_URL=https://YOUR_CLOUD_RUN_URL
SCHEDULER_SECRET=some-random-secret   # optional but recommended
```

---

## 3. Deploy to Cloud Run

```bash
PROJECT_ID=your-gcp-project
SERVICE=assistant-bot
REGION=us-central1

# Build and push
gcloud builds submit --tag gcr.io/$PROJECT_ID/$SERVICE

# Deploy
gcloud run deploy $SERVICE \
  --image gcr.io/$PROJECT_ID/$SERVICE \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --set-env-vars TELEGRAM_BOT_TOKEN=...,ANTHROPIC_API_KEY=...,GOOGLE_SHEETS_ID=...,GOOGLE_CALENDAR_ID=...,GOOGLE_SERVICE_ACCOUNT_JSON=/secrets/sa.json,SCHEDULER_SECRET=...,WEBHOOK_URL=https://YOUR_SERVICE_URL
```

### Mount the service account JSON as a secret
```bash
gcloud secrets create sa-json --data-file=path/to/service-account.json
gcloud run services update $SERVICE \
  --update-secrets /secrets/sa.json=sa-json:latest \
  --region $REGION
```

---

## 4. Register the Telegram webhook

The app calls `bot.set_webhook()` on startup automatically when `WEBHOOK_URL` is set.

Or manually:
```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook?url=https://YOUR_SERVICE_URL/webhook"
```

---

## 5. Set up Cloud Scheduler jobs

```bash
# Daily summary — 07:00 every day
gcloud scheduler jobs create http daily-summary \
  --schedule "0 7 * * *" \
  --uri "https://YOUR_SERVICE_URL/daily" \
  --http-method POST \
  --headers "X-Scheduler-Secret=YOUR_SECRET" \
  --time-zone "YOUR_TIMEZONE"

# Weekly summary — Monday 07:00
gcloud scheduler jobs create http weekly-summary \
  --schedule "0 7 * * 1" \
  --uri "https://YOUR_SERVICE_URL/weekly" \
  --http-method POST \
  --headers "X-Scheduler-Secret=YOUR_SECRET" \
  --time-zone "YOUR_TIMEZONE"

# Evening triage — 21:00 every day
gcloud scheduler jobs create http evening-triage \
  --schedule "0 21 * * *" \
  --uri "https://YOUR_SERVICE_URL/triage" \
  --http-method POST \
  --headers "X-Scheduler-Secret=YOUR_SECRET" \
  --time-zone "YOUR_TIMEZONE"

# Reminders — every 15 minutes
gcloud scheduler jobs create http reminders \
  --schedule "*/15 * * * *" \
  --uri "https://YOUR_SERVICE_URL/reminders" \
  --http-method POST \
  --headers "X-Scheduler-Secret=YOUR_SECRET" \
  --time-zone "YOUR_TIMEZONE"
```

---

## 6. Verify

```bash
curl https://YOUR_SERVICE_URL/health
# → {"status": "ok"}
```

Send a message to your bot in Telegram to confirm the full flow works.
