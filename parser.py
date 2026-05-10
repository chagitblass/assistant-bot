"""Claude API call — returns parsed intent(s) from a user message via tool use."""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime

import anthropic

import config
import sheets
from context import Context
from utils import get_week_start

_client: anthropic.Anthropic | None = None

# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

_PARSE_MESSAGE_TOOL = {
    "name": "parse_message",
    "description": (
        "Parse the user's message into one or more structured intents. "
        "Always call this tool — never respond with free text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "intents": {
                "type": "array",
                "description": "One or more parsed intents. Use multiple entries when the message contains multiple distinct actions.",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "The action type. Must be one of the values listed in the system prompt.",
                        }
                    },
                    "required": ["action"],
                    "additionalProperties": True,
                },
            }
        },
        "required": ["intents"],
    },
}

# ---------------------------------------------------------------------------
# System prompt (static part — context injected at call time)
# ---------------------------------------------------------------------------

_SYSTEM_BASE = """\
You are an intent parser for a personal assistant bot. Your only job is to convert a \
user's natural language message into structured intents via the parse_message tool. \
You never generate free text responses, explanations, or preamble. \
You never infer information that isn't in the message.

The user is a busy parent managing her own schedule and her kids' schedules. She \
communicates casually and in short messages.

Always call parse_message. Never respond with plain text.

═══ ACTIONS ═══

add_task
  Fields: text, subject (string|null), target_date ("today"|"tomorrow"|ISO date|null)
  Use null for target_date when the user says "this week" or gives no specific day.

add_today_plan
  Fields: tasks (array of strings)
  Use when the user lists multiple things to do today.

add_appointment
  Fields: title, start_datetime (ISO datetime|null), end_datetime (ISO datetime|null),
          kids_related (boolean), invitees (array of names|null), notes (string|null)

add_book_reminder
  Fields: appointment_type (string), notes (string|null)

query_tasks
  Fields: filter ("all"|"today"|"tomorrow"|"this_week"|"next_week"|"subject"|"recent"|"subjects_list"),
          subject (string|null), include_done (boolean, default true)
  Set include_done=false for "only active", "what's left", "what's still open".

query_day
  Fields: date ("today"|"tomorrow"|ISO date, default "today")
  Use for: "what do I have today", "what's my day", "what does Tuesday look like".

query_calendar
  Fields: filter ("today"|"this_week"|"upcoming")
  Use ONLY when the user explicitly asks about calendar/appointments, not for general day overview.

query_week
  No fields. Use for: "what does my week look like", "weekly schedule", "show me this week".

query_config
  No fields. Use for: work schedule, work hours, pickup/dropoff person for a specific day.

override_schedule
  Fields: day (string), week ("this_week"|"next_week"|ISO date of Monday),
          who_drops_off (string|null), who_picks_up (string|null),
          work_start (string HH:MM|null), work_end (string HH:MM|null)

add_weekly_note
  Fields: text (string)

mark_done
  Fields: text (string, partial match OK)
  Use when the user names a specific task to mark complete.

mark_done_start
  Fields: scope ("today"|"all"|"this_week", default "today")
  Use for interactive mode: "mark tasks done", "I finished some stuff".
  "mark all done" → scope "all". "mark weekly done" → scope "this_week".

unknown
  Fields: raw (string), candidates (array, see below)
  Use ONLY when you genuinely cannot determine the intent.
  When using unknown, populate candidates with up to 3 plausible interpretations ranked
  best-first, each with: action, params (the fields you would have used), and a one-line
  reason. Leave candidates empty only if there are truly zero plausible interpretations.

out_of_scope
  Fields: reason (string)
  Use when you understood the intent but it is NOT in the allowed actions list for this context.
  CRITICAL: Never substitute a different allowed action as a workaround for a blocked one. If
  the user's intent maps to a disallowed action, return out_of_scope — not the "closest" allowed action.

═══ FEW-SHOT EXAMPLES ═══

Input: "add milk to shopping list tomorrow"
Tool call: {"intents": [{"action": "add_task", "text": "milk", "subject": "shopping", "target_date": "tomorrow"}]}

Input: "remind me to call the dentist sometime this week"
Tool call: {"intents": [{"action": "add_task", "text": "call dentist", "subject": null, "target_date": null}]}

Input: "add milk and book dentist Thursday 3pm"
Tool call: {"intents": [
  {"action": "add_task", "text": "milk", "subject": "shopping", "target_date": null},
  {"action": "add_appointment", "title": "dentist", "start_datetime": "2025-01-16T15:00:00", "end_datetime": null, "kids_related": false, "invitees": null, "notes": null}
]}

Input: "today I need to: pick up dry cleaning, call mom, prep dinner"
Tool call: {"intents": [{"action": "add_today_plan", "tasks": ["pick up dry cleaning", "call mom", "prep dinner"]}]}

Input: "pediatrician for Yael next Tuesday 10am"
Tool call: {"intents": [{"action": "add_appointment", "title": "pediatrician for Yael", "start_datetime": "2025-01-21T10:00:00", "end_datetime": null, "kids_related": true, "invitees": null, "notes": null}]}

Input: "dentist Tuesday 9-10 with Akiva"
Tool call: {"intents": [{"action": "add_appointment", "title": "dentist", "start_datetime": "2025-01-21T09:00:00", "end_datetime": "2025-01-21T10:00:00", "kids_related": false, "invitees": ["Akiva"], "notes": null}]}

Input: "what do I have today"
Tool call: {"intents": [{"action": "query_day", "date": "today"}]}

Input: "what appointments do I have this week"
Tool call: {"intents": [{"action": "query_calendar", "filter": "this_week"}]}

Input: "mark pick up dry cleaning done"
Tool call: {"intents": [{"action": "mark_done", "text": "pick up dry cleaning"}]}

Input: "mark tasks done"
Tool call: {"intents": [{"action": "mark_done_start", "scope": "today"}]}

Input: "blorg the flonkulator"
Tool call: {"intents": [{"action": "unknown", "raw": "blorg the flonkulator", "candidates": []}]}

Input: "maybe add or check — I dunno, something with the gym next week?"
Tool call: {"intents": [{"action": "unknown", "raw": "maybe add or check — I dunno, something with the gym next week?", "candidates": [
  {"action": "add_task", "params": {"text": "gym", "subject": null, "target_date": null}, "reason": "Could be a task to add for next week"},
  {"action": "query_tasks", "params": {"filter": "next_week", "subject": null, "include_done": true}, "reason": "Could be asking what gym-related tasks exist next week"}
]}]}\
"""


def _build_system_prompt(context: Context) -> str:
    now = datetime.now()
    week_start = get_week_start(now.date())
    allowed = ", ".join(sorted(context.allowed_intents))

    ctx_block = (
        f"\n\n═══ REQUEST CONTEXT ═══\n"
        f"Allowed actions: {allowed}\n"
        f"Schedule/routine queries enabled: {context.schedule_enabled}\n"
        f"Calendar queries enabled: {context.calendar_enabled}\n"
        f"Today's date: {now.date().isoformat()} ({now.strftime('%A')})\n"
        f"Current week starts (Sunday): {week_start.isoformat()}\n"
        f"Current time: {now.strftime('%H:%M')}"
    )
    return _SYSTEM_BASE + ctx_block


# ---------------------------------------------------------------------------
# Logging — fires a background thread so the sheet write never blocks the user
# ---------------------------------------------------------------------------

def _log_parse(
    raw_message: str,
    parsed_result: list[dict] | dict,
    latency_ms: float,
    error: str = "",
) -> None:
    # Derive all values before entering the thread (avoid closure over mutable state).
    if isinstance(parsed_result, list):
        parsed_action = "multi" if len(parsed_result) > 1 else (
            parsed_result[0].get("action", "unknown") if parsed_result else "unknown"
        )
    else:
        parsed_action = parsed_result.get("action", "unknown")

    ts = datetime.utcnow().isoformat() + "Z"
    raw_trunc = raw_message[:500]
    json_trunc = json.dumps(parsed_result, ensure_ascii=False)[:2000]
    lat = int(latency_ms)

    def _write() -> None:
        try:
            sheets.log_parser_event(
                timestamp=ts,
                raw_message=raw_trunc,
                parsed_action=parsed_action,
                parsed_json=json_trunc,
                latency_ms=lat,
                error=error,
            )
        except Exception as exc:
            print(f"[parser] sheet log failed: {exc}", file=sys.stderr)

    threading.Thread(target=_write, daemon=True).start()


# ---------------------------------------------------------------------------
# Client singleton
# ---------------------------------------------------------------------------

def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_intent(text: str, context: Context) -> list[dict] | dict:
    t0 = time.monotonic()
    fallback: dict = {"action": "unknown", "raw": text, "candidates": []}

    # Build per-request tool with dynamic action enum
    allowed = sorted(context.allowed_intents | {"unknown", "out_of_scope"})
    tool = dict(_PARSE_MESSAGE_TOOL)
    tool["input_schema"] = dict(tool["input_schema"])
    tool["input_schema"]["properties"] = dict(tool["input_schema"]["properties"])
    items_schema = dict(tool["input_schema"]["properties"]["intents"]["items"])
    items_schema = {
        **items_schema,
        "properties": {
            "action": {
                "type": "string",
                "enum": allowed,
            }
        },
    }
    tool["input_schema"]["properties"]["intents"] = {
        **tool["input_schema"]["properties"]["intents"],
        "items": items_schema,
    }

    try:
        response = _get_client().messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
            system=_build_system_prompt(context),
            tools=[tool],
            tool_choice={"type": "tool", "name": "parse_message"},
            messages=[{"role": "user", "content": text}],
        )
    except Exception as exc:
        print(f"[parser] API error: {exc}", file=sys.stderr)
        latency_ms = (time.monotonic() - t0) * 1000
        _log_parse(text, fallback, latency_ms, error=f"{type(exc).__name__}: {exc}")
        return fallback

    latency_ms = (time.monotonic() - t0) * 1000

    if response.stop_reason != "tool_use":
        print(
            f"[parser] Unexpected stop_reason={response.stop_reason!r} for message={text!r}",
            file=sys.stderr,
        )
        _log_parse(text, fallback, latency_ms, error=f"stop_reason={response.stop_reason!r}")
        return fallback

    tool_block = next(
        (b for b in response.content if b.type == "tool_use" and b.name == "parse_message"),
        None,
    )
    if tool_block is None:
        print(f"[parser] No parse_message tool_use block found for message={text!r}", file=sys.stderr)
        _log_parse(text, fallback, latency_ms, error="no_tool_use_block")
        return fallback

    intents: list[dict] = tool_block.input.get("intents", [])
    if not intents:
        _log_parse(text, fallback, latency_ms, error="empty_intents")
        return fallback

    result: list[dict] | dict = intents if len(intents) > 1 else intents[0]
    _log_parse(text, result, latency_ms)
    return result
