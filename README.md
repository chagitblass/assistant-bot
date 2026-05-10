# Personal Assistant Telegram Bot

A personal assistant bot for a busy parent. Manages tasks, a daily plan, and Google Calendar appointments. Communicates via Telegram. Natural language in → structured, templated text out. No AI-generated free text in responses.

---

## 1. Project Overview

The bot is designed for one user (whitelisted by Telegram chat_id). She sends casual natural language messages; the bot parses them with Claude into structured JSON, then executes deterministic handlers that read/write Google Sheets and Google Calendar.

**Core capabilities:**
- Add, query, and mark done tasks (by day or by week)
- Daily and weekly scheduled overviews (sent proactively)
- Google Calendar event creation with attendee invites
- Evening triage of unfinished tasks via inline keyboard
- Per-day schedule (who drops off / picks up, work hours) with weekly override support
- Weekly notes
- Interactive mark-done keyboard

---

## 2. Architecture

### File responsibilities

| File | Role |
|---|---|
| `main.py` | Flask app. Telegram webhook + polling entry point. Routes messages. Cloud Scheduler HTTP endpoints. |
| `run_polling.py` | Dev-only. Runs the bot in polling mode (no public URL needed). |
| `handlers.py` | One function per intent. Pure business logic. Returns strings or sentinel dicts. No Telegram imports. |
| `parser.py` | Single call to Claude API. Converts natural language → JSON intent. |
| `sheets.py` | All Google Sheets reads and writes. No business logic, no Telegram. |
| `tg.py` | Telegram platform adapter: `parse_inbound`, `send_outbound`, `send_outbound_async`, mark-done session state (`pending_mark_done`). |
| `calendar_api.py` | All Google Calendar reads and writes. Timezone: `Asia/Jerusalem`. |
| `scheduler.py` | Proactive message logic: `/daily`, `/weekly`, `/triage`. Sends via `tg.send_outbound()`. |
| `config.py` | Loads env vars. Caches config sheet key/value pairs in memory. |
| `utils.py` | `get_week_start(d)` — single source of truth for Israeli Sunday-anchored week start. |
| `image_gen.py` | Pillow-based PNG generation for weekly overview images. |
| `setup_sheets.py` | One-time script. Creates all sheet tabs with correct headers and default rows. |

### Where Telegram appears

```
main.py          ← python-telegram-bot: Application, handlers, callback queries, keyboards
scheduler.py     ← bot.send_message / send_photo (proactive pushes)
run_polling.py   ← wires up Application for local dev
```

`handlers.py`, `sheets.py`, `calendar_api.py`, `parser.py`, `utils.py`, `config.py` — **no Telegram imports**.

### Message flow

```
User message
    │
    ▼
main.py: on_message()
    ├── whitelist check
    ├── pending_mark_done? → numbered-reply mark-done session (tg.py)
    │
    ▼
parser.py: parse_intent()
    └── Claude API → JSON intent (or list of intents)
    │
    ▼
handlers.py: dispatch()
    └── routes by action name → handler function
        └── reads/writes sheets.py / calendar_api.py
    │
    ▼
main.py: send response
    ├── plain string        → reply_text()
    ├── _type: "text"       → reply_text()
    └── _type: "mark_done_keyboard" → send inline keyboard, store state
```

### Callback flow (inline keyboards)

```
User taps button
    │
    ▼
main.py: on_callback_query()
    ├── "done:toggle:{msg_id}:{task_id}"  → toggle checked set, redraw keyboard
    └── "done:confirm:{msg_id}"           → mark_task_done() for each checked, clear state

Triage inline keyboards: not yet implemented (Phase C).
```

### Scheduler flow

```
Cloud Scheduler POST → /daily | /weekly | /triage
    │
    ▼
main.py: Flask endpoint (auth via X-Scheduler-Secret header)
    │
    ▼
scheduler.py
    ├── /daily   → handlers.build_day_view(today) → tg.send_outbound()
    ├── /weekly  → handle_query_week() → tg.send_outbound()
    └── /triage  → fetch today's unfinished tasks → send inline keyboards (Phase C)
```

---

## 3. Google Sheets Structure

All sheets live in one spreadsheet (`GOOGLE_SHEETS_ID`).

### `tasks` *(migrated to Todoist — this schema is for reference only)*
| Column | Type | Notes |
|---|---|---|
| `id` | string | 8-char UUID prefix, auto-generated |
| `text` | string | Task description |
| `subject` | string | Category/label (e.g. "school", "work") |
| `target_date` | ISO date | Specific date. Mutually exclusive with `target_week` |
| `target_week` | ISO date | Sunday of the target week. For "do this week" tasks |
| `created_at` | ISO datetime | UTC |
| `status` | string | `active` / `done` / `dropped` |

### `config`
| Column | Type | Notes |
|---|---|---|
| `key` | string | Config key |
| `value` | string | Config value |

**Current config keys:**
- `telegram_whitelist` — comma-separated Telegram chat IDs allowed to use the bot

### `schedule`
One row per workday. Base weekly routine.

| Column | Notes |
|---|---|
| `day` | Day name (e.g. "Sunday", "Monday") |
| `work_start` | HH:MM |
| `work_end` | HH:MM |
| `who_drops_off` | Name of person doing school drop-off |
| `who_picks_up` | Name of person doing school pickup |

### `schedule_overrides`
One row per (week, day) override. Takes precedence over `schedule`.

| Column | Notes |
|---|---|
| `week_start` | ISO date of that Sunday |
| `day` | Day name |
| `work_start` | HH:MM (or empty to inherit from base) |
| `work_end` | HH:MM |
| `who_drops_off` | Name (or empty) |
| `who_picks_up` | Name (or empty) |

### `weekly_notes`
| Column | Notes |
|---|---|
| `week_start_date` | ISO date of that Sunday |
| `note` | Free text note for the week |

### `triage`
Tracks which tasks have been sent as triage keyboard messages (to avoid duplicates).

| Column | Notes |
|---|---|
| `task_id` | References `tasks.id` |
| `triage_message_id` | Telegram message ID of the keyboard message |
| `triage_date` | ISO date the triage was sent |

Tasks not acted on within 24h: `target_date` set to null (moves to general list).

### `contacts`
| Column | Notes |
|---|---|
| `name` | Full name (e.g. "Akiva Schiff-Blass") |
| `email` | Email address |
| `relationship` | e.g. "husband" — used to auto-invite husband on kids-related events |

Name resolution uses **case-insensitive partial match**: "Akiva" matches "Akiva Schiff-Blass".

### `parser_log` *(planned)*
Not yet built. Will log raw message, parsed intent, and handler response for debugging.

### `subjects` *(planned)*
Not yet built. Will store canonical subject names for the subject picker flow.

---

## 4. Intents Reference

### `add_task`
Add a task to the list.
- `text`: string
- `subject`: string or null
- `target_date`: `"today"` | `"tomorrow"` | ISO date | null
- `target_week`: ISO date of Sunday | null

**Triggers:** "remind me to call the school", "add: buy milk tomorrow"

---

### `add_today_plan`
Add multiple tasks all due today.
- `tasks`: array of strings

**Triggers:** "today I need to: call school, do laundry, book dentist"

---

### `add_appointment`
Create a Google Calendar event.
- `title`: string
- `start_datetime`: ISO datetime or null
- `end_datetime`: ISO datetime or null (for time ranges like "8:00-10:00")
- `kids_related`: boolean — if true, auto-invites husband from contacts sheet
- `invitees`: array of name strings or null
- `notes`: string or null

**Triggers:** "dentist appointment Thursday 8:00-10:00", "tiyul with Akiva next Sunday"

---

### `add_book_reminder`
Add a task to book an appointment (subject = "appointments").
- `appointment_type`: string (e.g. "dentist", "pediatrician")
- `notes`: string or null

**Triggers:** "remind me to book a dentist appointment"

---

### `query_tasks`
Query the task list.
- `filter`: `"all"` | `"today"` | `"tomorrow"` | `"this_week"` | `"next_week"` | `"subject"` | `"recent"` | `"subjects_list"`
- `subject`: string or null
- `include_done`: boolean (default true) — show done tasks at the bottom with ✅ prefix

**Triggers:** "what are my tasks for today", "this week's tasks", "what's left" (include_done=false)

---

### `query_day`
Full combined daily view: routine + calendar + tasks + notes.
- `date`: `"today"` | `"tomorrow"` | ISO date (default: `"today"`)

**Triggers:** "what do I have today", "what's my day", "give me everything about today"

---

### `query_week`
Full combined weekly view: routine (all 5 days) + calendar + tasks + notes.
- (no fields)

**Triggers:** "what does my week look like", "weekly schedule", "what's this week"

---

### `query_calendar`
Query Google Calendar only.
- `filter`: `"today"` | `"this_week"` | `"upcoming"`

**Triggers:** "what appointments do I have this week", "upcoming events"

---

### `query_config`
Show the weekly schedule (work hours, pickup, dropoff) from the schedule sheet.
- (no fields)

**Triggers:** "work schedule", "who picks up on Tuesday", "what's the weekly routine"

---

### `override_schedule`
Write a one-week override to the schedule.
- `day`: string (e.g. "Monday")
- `week`: `"this_week"` | `"next_week"` | ISO date of that Sunday
- `who_drops_off`: string or null
- `who_picks_up`: string or null
- `work_start`: HH:MM or null
- `work_end`: HH:MM or null

**Triggers:** "next Monday Akiva does pickup", "I'm not working this Thursday"

---

### `add_weekly_note`
Append a note to this week's notes.
- `text`: string

**Triggers:** "note: doing Shabbat in Jerusalem this week"

---

### `mark_done`
Mark a single task done by fuzzy text match.
- `text`: string (partial match)

**Triggers:** "done: call school", "I finished the report"

---

### `mark_done_start`
Launch the interactive mark-done keyboard.
- `scope`: `"today"` | `"this_week"` | `"all"` (default: `"today"`)

**Triggers:** "mark tasks done" (today), "mark everything done" (all), "mark this week done" (this_week)

---

### `unknown`
- `raw`: string

**Response:** "Couldn't understand that. Please rephrase."

---

## 5. Known Issues and Limitations

- **Google Calendar attendee invites:** Service accounts cannot invite attendees to calendars they don't manage. If a 403 is returned on event creation with attendees, the bot retries without attendees and shows a warning: "⚠️ [Name] will need to be invited manually." Workaround: share the target person's calendar with the service account (Editor access).

- **`pending_mark_done` is in-memory (`tg.py`):** If the bot restarts, in-flight mark-done keyboard sessions are lost. Tapping buttons on an old keyboard will return "Session expired."

- **Python 3.9 on macOS:** FutureWarning from google-auth. Not breaking, but Python 3.10+ recommended. All code is compatible with 3.10+.

- **Timezone display:** All datetimes are in `Asia/Jerusalem`. Events stored in Google Calendar with other timezones will display correctly after `.astimezone(TZ)` conversion, but the bot always creates new events in `Asia/Jerusalem`.

- **Weekly notes keyed by Sunday:** Notes added and queried by `week_start` (Sunday ISO date). If the week_start calculation is ever inconsistent, notes can appear in the wrong week.

---

## 6. Deployment

### Environment variables

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `ANTHROPIC_API_KEY` | From console.anthropic.com |
| `GOOGLE_SHEETS_ID` | From the Google Sheet URL |
| `GOOGLE_CALENDAR_ID` | Calendar ID (e.g. your Gmail address for primary calendar) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Path to service account JSON file |
| `WEBHOOK_URL` | Public HTTPS URL of the deployed service (Cloud Run URL) |
| `SCHEDULER_SECRET` | Shared secret for Cloud Scheduler endpoint auth (optional in dev) |

### Run locally (polling mode)

```bash
cd "/Users/chagit/Assistant Agent"
pip3 install -r requirements.txt
set -a && source .env && set +a
python3 run_polling.py
```

No public URL needed. The bot polls Telegram for updates.

### First-time sheet setup

```bash
python3 setup_sheets.py path/to/service-account.json YOUR_SPREADSHEET_ID
```

Then fill in the sheets:
- `config`: set `telegram_whitelist` to your Telegram chat ID (get it from @userinfobot)
- `schedule`: add rows for Sunday–Friday with work hours and pickup/dropoff names
- `contacts`: add your husband and other frequent contacts

### Deploy to Cloud Run

```bash
PROJECT_ID=your-gcp-project
SERVICE=assistant-bot
REGION=us-central1

# Build
gcloud builds submit --tag gcr.io/$PROJECT_ID/$SERVICE

# Deploy
gcloud run deploy $SERVICE \
  --image gcr.io/$PROJECT_ID/$SERVICE \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --set-env-vars TELEGRAM_BOT_TOKEN=...,ANTHROPIC_API_KEY=...,GOOGLE_SHEETS_ID=...,\
GOOGLE_CALENDAR_ID=...,GOOGLE_SERVICE_ACCOUNT_JSON=/secrets/sa.json,\
SCHEDULER_SECRET=...,WEBHOOK_URL=https://YOUR_SERVICE_URL

# Mount service account as secret
gcloud secrets create sa-json --data-file=path/to/service-account.json
gcloud run services update $SERVICE \
  --update-secrets /secrets/sa.json=sa-json:latest \
  --region $REGION
```

### Register Telegram webhook

The app calls `bot.set_webhook()` on startup automatically when `WEBHOOK_URL` is set. Or manually:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook?url=https://YOUR_SERVICE_URL/telegram-webhook"
```

### Cloud Scheduler jobs

```bash
TZ="Asia/Jerusalem"
URL="https://YOUR_SERVICE_URL"
SECRET="YOUR_SECRET"

# Daily overview — 07:00 every day
gcloud scheduler jobs create http daily-summary \
  --schedule "0 7 * * *" --uri "$URL/daily" \
  --http-method POST --headers "X-Scheduler-Secret=$SECRET" --time-zone "$TZ"

# Weekly overview — Sunday 07:00
gcloud scheduler jobs create http weekly-summary \
  --schedule "0 7 * * 0" --uri "$URL/weekly" \
  --http-method POST --headers "X-Scheduler-Secret=$SECRET" --time-zone "$TZ"

# Evening triage — 21:00 every day
gcloud scheduler jobs create http evening-triage \
  --schedule "0 21 * * *" --uri "$URL/triage" \
  --http-method POST --headers "X-Scheduler-Secret=$SECRET" --time-zone "$TZ"
```

---

## 7. Planned / Not Yet Built

| Feature | Notes |
|---|---|
| **Cloud Run deployment** | Code is ready. Dockerfile exists. Not yet deployed and tested end-to-end. |
| **Cloud Scheduler testing** | Jobs not yet created. `/daily`, `/weekly`, `/triage` endpoints exist but untested in production. |
| **Subject picker with buttons** | When adding a task without a subject, send an inline keyboard with existing subjects as options. |
| **Subjects sheet** | Canonical list of subject names for autocomplete/picker. |
| **parser_log sheet** | Log every parsed intent for debugging and improving the parser prompt. |
| **Friday triage** | Spec mentioned Friday evening triage for `target_week` tasks. Currently only daily unfinished tasks are triaged. |

---

## 8. Recent Decisions Log

### Google Sheets over a database
Chosen for zero infrastructure overhead and direct editability. The user can view and fix data in the browser. The tradeoff is latency (every read is an API call) and no transactions, but for a single-user low-volume bot this is fine.

### LLM for parsing only, never for response generation
Claude parses intent once per message and returns JSON. All responses are generated from Python string templates. This ensures responses are predictable, fast, and don't hallucinate. The bot never sounds like a chatbot.

### Inline keyboards for triage and mark-done
Evening triage and the mark-done flow use Telegram inline keyboards so the user doesn't have to type. State is held in-memory dicts in `main.py` keyed by `message_id`. This is simple but not persistent across restarts.

### `schedule_overrides` separate from base `schedule`
The base `schedule` sheet holds the default weekly routine. `schedule_overrides` holds exceptions for specific weeks. `get_effective_schedule(day, week_start)` merges them, with overrides winning. This keeps the base clean and overrides auditable.

### `contacts` sheet separate from `config`
Initially contacts were rows in the `config` sheet. Moved to a dedicated `contacts` sheet with `name`, `email`, `relationship` columns. Enables partial/case-insensitive name matching and relationship-based lookups (e.g. auto-invite husband on kids-related events).

### `target_date` vs `target_week`
Tasks can be pinned to a specific date (`target_date`) or left as a floating "this week" task (no due date). This distinction came from real usage: "do X this week" is different from "do X on Tuesday". In Todoist, `target_date` maps to a due date; floating tasks have no due date. The weekly view shows them in separate sections.

### `get_week_start()` in `utils.py`
All week-boundary logic uses a single shared function that anchors to the most recent Sunday (or the next Sunday if today is Saturday). Previously each file had its own inline calculation, causing subtle bugs where different parts of the bot used different week boundaries.

### Timezone: Asia/Jerusalem everywhere
All calendar event creation, querying, and display uses `zoneinfo.ZoneInfo("Asia/Jerusalem")`. The `tzdata` package is included in `requirements.txt` for Linux (Cloud Run). macOS uses system timezone data.
