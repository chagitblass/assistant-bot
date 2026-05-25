"""One handler function per parsed intent. Each returns a plain string."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

import calendar_api
import sheets
import todoist
import tg
from context import Context
from utils import get_week_start

logger = logging.getLogger(__name__)

# Israeli work week: Sunday–Thursday
_WORK_DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"]
_DIVIDER = "———————————————"


def _section(emoji: str, title: str, content: str) -> str:
    return f"{_DIVIDER}\n{emoji} *{title}*\n{_DIVIDER}\n\n{content}"


def build_day_view(target: date, context: Context) -> str:
    """Build the full combined daily view string. Used by handle_query_day and run_daily."""
    week_start = get_week_start(target)
    week_start_iso = week_start.isoformat()
    day_name = target.strftime("%A")

    # Date banner
    date_str = target.strftime("%a %-d %b").upper()
    banner = f"{_DIVIDER}\n📅 *{date_str}*\n{_DIVIDER}"

    parts = [banner]

    # Routine (only when schedule is enabled for this context)
    if context.schedule_enabled:
        eff = sheets.get_effective_schedule(day_name, week_start_iso)
        routine_lines = []
        if eff.get("who_drops_off"):
            routine_lines.append(f"🚗 Drop-off: {eff['who_drops_off']}")
        if eff.get("who_picks_up"):
            routine_lines.append(f"🚗 Pick-up: {eff['who_picks_up']}")
        ws = eff.get("work_start", "")
        we = eff.get("work_end", "")
        if ws and we:
            routine_lines.append(f"💼 {_work_hours_str(ws, we)}")
        routine_block = "🗓 *ROUTINE*\n" + ("\n".join(routine_lines) if routine_lines else "No routine set.")
        parts.append(routine_block)

    # Calendar (only when calendar is enabled for this context)
    if context.calendar_enabled:
        events = calendar_api.get_events_for_day(target)
        cal_content = "\n".join(calendar_api.format_event(e) for e in events) if events else "No appointments."
        parts.append(_section("📆", "CALENDAR", cal_content))

    # Tasks
    tasks_content = build_day_tasks_block(target, context, include_done=True)
    label = "TODAY'S TASKS" if target == date.today() else "TASKS"
    parts.append(_section("✅", label, tasks_content or "No tasks."))

    # Weekly notes (only when schedule is enabled)
    if context.schedule_enabled:
        notes = sheets.get_weekly_notes_for_week(week_start_iso)
        if notes:
            notes_content = "\n".join(f"• {n['note']}" for n in notes)
            parts.append(_section("📝", "NOTES", notes_content))

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_date(d: str | None) -> str:
    if not d:
        return "no date"
    try:
        return date.fromisoformat(d).strftime("%a %-d %b")
    except ValueError:
        return d


def _resolve_date(raw: str | None) -> str | None:
    if not raw:
        return None
    today = date.today()
    if raw == "today":
        return today.isoformat()
    if raw == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if raw == "this_week":
        return "this_week"  # handled specially by set_task_date
    return raw  # already ISO


def _fmt_task(task: dict, show_date: bool = True) -> str:
    subject = f" `[{task['subject']}]`" if task.get("subject") else ""
    date_part = f" — {_fmt_date(task.get('target_date'))}" if (show_date and task.get("target_date")) else ""
    return f"• {task['text']}{subject}{date_part}"


def _task_prefix(t: dict) -> str:
    return "✅" if t.get("status") == "done" else "•"


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_add_task(data: dict, context: Context) -> str:
    target_date = _resolve_date(data.get("target_date"))
    todoist.add_task(
        context,
        text=data["text"],
        subject=data.get("subject"),
        target_date=target_date,
    )
    subject_part = f" `[{data['subject']}]`" if data.get("subject") else ""
    date_part = f"\nDate: {_fmt_date(target_date)}" if target_date else ""
    return f"Added:{subject_part} {data['text']}{date_part}"


def handle_add_today_plan(data: dict, context: Context) -> str:
    raw_date = data.get("target_date", "today")
    target_date = _resolve_date(raw_date) if raw_date else date.today().isoformat()

    raw_tasks = data.get("tasks", [])
    added_lines = []
    for task in raw_tasks:
        if isinstance(task, str):
            text, subject = task, None
        else:
            text = task.get("text", "")
            subject = task.get("subject") or None
        todoist.add_task(context, text, subject=subject, target_date=target_date)
        subject_part = f" `[{subject}]`" if subject else ""
        added_lines.append(f"• {text}{subject_part}")

    if target_date == "this_week":
        date_label = "this week"
    elif target_date:
        date_label = _fmt_date(target_date)
    else:
        date_label = "today"
    return f"Added for {date_label}:\n" + "\n".join(added_lines)


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("Asia/Jerusalem"))
        return dt
    except (ValueError, TypeError):
        return None


def handle_add_appointment(data: dict, context: Context) -> str:
    title: str = data["title"]
    kids_related: bool = data.get("kids_related", False)
    notes: str | None = data.get("notes")

    start_dt = _parse_dt(data.get("start_datetime") or data.get("datetime"))
    end_dt = _parse_dt(data.get("end_datetime"))

    # Resolve invitees to emails
    contacts = sheets.get_contacts()
    seen_emails: set[str] = set()
    attendee_emails: list[str] = []
    attendee_names: list[str] = []

    invitee_names: list[str] = list(data.get("invitees") or [])
    if kids_related:
        for row in contacts:
            if str(row.get("relationship", "")).lower() == "husband":
                invitee_names.append(str(row["name"]))
                break

    for raw_name in invitee_names:
        email = sheets.resolve_name_to_email(raw_name) or (raw_name if "@" in raw_name else None)
        if not email or email in seen_emails:
            continue
        seen_emails.add(email)
        display = raw_name
        for row in contacts:
            if str(row.get("email", "")).strip() == email:
                display = str(row["name"])
                break
        attendee_emails.append(email)
        attendee_names.append(display)

    from googleapiclient.errors import HttpError
    manual_invite: list[str] = []
    try:
        calendar_api.create_event(
            title=title, start_dt=start_dt, notes=notes,
            end_dt=end_dt, attendee_emails=attendee_emails or None,
        )
    except HttpError as e:
        if e.resp.status == 403 and attendee_emails:
            calendar_api.create_event(title=title, start_dt=start_dt, notes=notes, end_dt=end_dt)
            manual_invite = attendee_names
        else:
            raise

    date_str = _fmt_date(start_dt.date().isoformat()) if start_dt else "no date"
    start_time = start_dt.strftime("%H:%M") if start_dt else None
    end_time = end_dt.strftime("%H:%M") if end_dt else None
    time_part = f" {start_time}" if start_time else ""
    if start_time and end_time:
        time_part = f" {start_time}–{end_time}"

    lines = [f"Appointment added: *{title}*\n{date_str}{time_part}"]
    if attendee_names and not manual_invite:
        lines.append(f"Invited: {', '.join(attendee_names)}")
    for name in manual_invite:
        lines.append(f"⚠️ {name} will need to be invited manually until calendar access is set up.")
    if notes:
        lines.append(f"Notes: {notes}")
    return "\n".join(lines)


def handle_add_book_reminder(data: dict, context: Context) -> str:
    appt_type: str = data.get("appointment_type", "appointment")
    notes: str | None = data.get("notes")
    text = f"Book {appt_type}" + (f" — {notes}" if notes else "")
    todoist.add_task(context, text, subject="appointments")
    return f"Reminder added: {text}"


def handle_query_tasks(data: dict, context: Context) -> str:
    filter_val = data.get("filter", "all")
    subject = data.get("subject")
    include_done = data.get("include_done", True)

    if filter_val == "subjects_list":
        items = todoist.query_tasks(context, filter="subjects_list")
        if not items:
            return "No subjects found."
        lines = "\n".join(f"• {s['subject']}" for s in items)
        return f"Subjects:\n{lines}"

    if filter_val in ("this_week", "next_week"):
        today = date.today()
        week_start = get_week_start(today)
        if filter_val == "next_week":
            week_start = week_start + timedelta(days=7)
        week_end = week_start + timedelta(days=6)
        week_start_iso = week_start.isoformat()
        week_end_iso = week_end.isoformat()

        block = build_week_tasks_block(week_start_iso, week_end_iso, context, include_done=include_done)
        label = "THIS WEEK" if filter_val == "this_week" else "NEXT WEEK"
        if not block:
            return f"No tasks {label.lower()}. _(checked {week_start_iso} – {week_end_iso})_"
        return _section("🗓", label, block)

    tasks = todoist.query_tasks(context, filter=filter_val, subject=subject, include_done=include_done)
    if not tasks:
        label = f"subject '{subject}'" if subject else filter_val
        return f"No tasks for {label}."

    show_date = filter_val != "today"
    active_tasks = [t for t in tasks if t.get("status") == "active"]
    done_tasks   = [t for t in tasks if t.get("status") == "done"]

    lines = [_fmt_task(t, show_date=show_date) for t in active_tasks]
    lines += [f"✅ {t['text']}" for t in done_tasks]

    label_map = {
        "all":      "All tasks",
        "today":    "Today",
        "tomorrow": "Tomorrow",
        "floating": "Tasks with no date",
        "subject":  f"Subject: {subject}",
        "recent":   "Recent tasks",
    }
    header = label_map.get(filter_val, "Tasks")
    return f"*{header}*\n" + "\n".join(lines)


def handle_query_calendar(data: dict, context: Context) -> str:
    filter_val = data.get("filter", "today")
    today = date.today()

    if filter_val == "today":
        events = calendar_api.get_events_for_day(today)
        header = f"*{today.strftime('%a %-d %b')}*"
    elif filter_val == "this_week":
        week_start = get_week_start(today)
        events = calendar_api.get_events_for_week(week_start)
        header = None
    else:
        events = calendar_api.get_upcoming_events()
        header = None

    if not events:
        return f"{header or '*Calendar*'}\nNo appointments."

    grouped = calendar_api.format_events_grouped(events)

    if header and not grouped.startswith(f"*{today.strftime('%a')}"):
        return f"{header}\n{grouped}"
    return grouped


def build_day_tasks_block(target: date, context: Context, include_done: bool = True) -> str:
    """Return formatted task lines for a single day. Active first, done at the bottom with ✅."""
    target_iso = target.isoformat()
    all_tasks = todoist.query_tasks(context, filter="all", include_done=include_done)
    tasks = [t for t in all_tasks if t.get("target_date") == target_iso]
    if not tasks:
        return ""
    active = [t for t in tasks if t.get("status") == "active"]
    done   = [t for t in tasks if t.get("status") == "done"]
    lines  = [f"• {t['text']}" + (f" `[{t['subject']}]`" if t.get("subject") else "") for t in active]
    lines += [f"✅ {t['text']}" for t in done]
    return "\n".join(lines)


def build_week_tasks_block(
    week_start_iso: str,
    week_end_iso: str,
    context: Context,
    include_done: bool = True,
) -> str:
    """Return a formatted string of tasks for a week, grouped under day headers.

    Returns empty string if there are no tasks.
    """
    all_tasks = todoist.query_tasks(context, filter="all", include_done=include_done)
    daily = [
        t for t in all_tasks
        if t.get("target_date") and week_start_iso <= t["target_date"] <= week_end_iso
    ]
    if not daily:
        return ""

    by_day: dict[str, list] = defaultdict(list)
    for t in sorted(daily, key=lambda t: t.get("target_date", "")):
        by_day[t["target_date"]].append(t)

    lines: list[str] = []
    for d_iso in sorted(by_day):
        try:
            d_label = date.fromisoformat(d_iso).strftime("%a %-d %b")
        except ValueError:
            d_label = d_iso
        lines.append(f"*{d_label}*")
        lines += [f"{_task_prefix(t)} {t['text']}" for t in by_day[d_iso]]

    return "\n".join(lines)


def _work_hours_str(work_start: str, work_end: str) -> str:
    try:
        sh, sm = map(int, work_start.split(":"))
        eh, em = map(int, work_end.split(":"))
        total = (eh * 60 + em) - (sh * 60 + sm)
        hrs = total / 60
        hrs_label = f"{int(hrs)} hrs" if hrs == int(hrs) else f"{hrs:.1f} hrs"
        return f"{work_start}–{work_end} ({hrs_label})"
    except (ValueError, AttributeError):
        return f"{work_start}–{work_end}"


def handle_query_week(data: dict, context: Context) -> str:
    today = date.today()
    week_start = get_week_start(today)
    week_start_iso = week_start.isoformat()
    work_dates = [week_start + timedelta(days=i) for i in range(5)]
    week_end_iso = work_dates[-1].isoformat()

    parts = []

    # Routine section
    if context.schedule_enabled:
        routine_day_blocks = []
        for day_name, day_date in zip(_WORK_DAYS, work_dates):
            eff = sheets.get_effective_schedule(day_name, week_start_iso)
            day_str = day_date.strftime("%a %-d %b")
            lines = [f"*{day_str}*"]
            if eff.get("who_drops_off"):
                lines.append(f"🚗 Drop-off: {eff['who_drops_off']}")
            if eff.get("who_picks_up"):
                lines.append(f"🚗 Pick-up: {eff['who_picks_up']}")
            ws = eff.get("work_start", "")
            we = eff.get("work_end", "")
            if ws and we:
                lines.append(f"💼 {_work_hours_str(ws, we)}")
            routine_day_blocks.append("\n".join(lines))
        parts.append(_section("🗓", "ROUTINE", "\n\n".join(routine_day_blocks)))

    # Calendar section
    if context.calendar_enabled:
        events = calendar_api.get_events_for_week(week_start)
        calendar_content = calendar_api.format_events_grouped(events) if events else ""
        if calendar_content:
            parts.append(_section("📅", "CALENDAR", calendar_content))

    # Tasks this week
    week_tasks_block = build_week_tasks_block(week_start_iso, week_end_iso, context, include_done=True)
    if week_tasks_block:
        parts.append(_section("✅", "TASKS THIS WEEK", week_tasks_block))

    # Weekly notes
    if context.schedule_enabled:
        notes = sheets.get_weekly_notes_for_week(week_start_iso)
        if notes:
            note_lines = "\n".join(f"• {n['note']}" for n in notes)
            parts.append(_section("📝", "NOTES", note_lines))

    return "\n\n".join(parts)


def handle_override_schedule(data: dict, context: Context) -> str:
    day = data.get("day", "")
    week_raw = data.get("week", "this_week")

    today = date.today()
    this_sunday = get_week_start(today)
    if week_raw == "this_week":
        week_start = this_sunday.isoformat()
        week_label = "this week"
    elif week_raw == "next_week":
        week_start = (this_sunday + timedelta(days=7)).isoformat()
        week_label = "next week"
    else:
        week_start = week_raw
        week_label = f"week of {_fmt_date(week_start)}"

    fields = {
        "who_drops_off": data.get("who_drops_off") or "",
        "who_picks_up": data.get("who_picks_up") or "",
        "work_start": data.get("work_start") or "",
        "work_end": data.get("work_end") or "",
    }

    notes = data.get("notes") or ""

    if not any(fields.values()):
        if notes:
            sheets.add_weekly_note(notes)
            return f"Noted for {week_label}: _{notes}_\nIf you want to update the actual schedule, let me know who handles drop-off/pick-up and what time."
        return (
            f"I see {day} has a different schedule, but I need specifics to update it. "
            f"What time are you leaving? Who handles drop-off/pick-up?"
        )

    sheets.set_schedule_override(week_start, day, fields)
    if notes:
        sheets.add_weekly_note(notes)
    changed = [k.replace("_", " ") for k, v in fields.items() if v]
    return f"Got it — updated {day}'s routine for {week_label} ({', '.join(changed)})."


def handle_query_config(data: dict, context: Context) -> str:
    rows = sheets._ws("schedule").get_all_records()
    if not rows:
        return "No schedule configured."
    lines = []
    for row in rows:
        day = row.get("day", "")
        ws = row.get("work_start", "")
        we = row.get("work_end", "")
        pickup = row.get("who_picks_up", "")
        dropoff = row.get("who_drops_off", "")
        parts = []
        if ws and we:
            parts.append(f"work {ws}–{we}")
        if dropoff:
            parts.append(f"drop-off: {dropoff}")
        if pickup:
            parts.append(f"pickup: {pickup}")
        if parts:
            lines.append(f"*{day}:* {', '.join(parts)}")
    return "\n".join(lines) if lines else "Schedule is empty."


def handle_add_weekly_note(data: dict, context: Context) -> str:
    note = sheets.add_weekly_note(data["text"])
    return f"Note saved for week of {_fmt_date(note['week_start_date'])}:\n_{note['note']}_"


def handle_reschedule_tasks(data: dict, context: Context) -> str:
    scope = data.get("scope", "floating")
    from_date = _resolve_date(data.get("from_date"))
    target_date = _resolve_date(data.get("target_date"))

    if scope == "date" and from_date:
        all_tasks = todoist.query_tasks(context, filter="all", include_done=False)
        tasks = [t for t in all_tasks if t.get("target_date") == from_date]
        scope_label = f"tasks from {_fmt_date(from_date)}"
    else:
        filter_map = {"today": "today", "floating": "floating", "all": "all"}
        tasks = todoist.query_tasks(context, filter=filter_map.get(scope, "floating"), include_done=False)
        scope_label = {"today": "today's tasks", "floating": "floating tasks", "all": "all tasks"}.get(scope, "tasks")

    if not tasks:
        return "No tasks to move."

    for task in tasks:
        todoist.set_task_date(context, task["id"], target_date)

    n = len(tasks)
    if target_date == "this_week":
        date_label = "this week"
    elif target_date:
        date_label = _fmt_date(target_date)
    else:
        date_label = "no date"
    return f"Moved {n} {scope_label} to {date_label}."


def handle_mark_done(data: dict, context: Context) -> str:
    text = data.get("text", "")
    task_id = todoist.find_task_by_text(context, text)
    if not task_id:
        return f"No active task matching \"{text}\" found."
    task = todoist.get_client().get_task(task_id=task_id)
    task_text = task.content
    todoist.mark_task_done(context, task_id)
    return f"Done: ~~{task_text}~~"


def handle_query_day(data: dict, context: Context) -> str:
    raw = data.get("date", "today")
    resolved = _resolve_date(raw) or date.today().isoformat()
    try:
        target = date.fromisoformat(resolved)
    except ValueError:
        target = date.today()
    return build_day_view(target, context)


def handle_mark_done_start(data: dict, context: Context) -> str:
    scope = data.get("scope", "today")
    filter_map = {"today": "today", "this_week": "this_week", "all": "all"}
    todoist_filter = filter_map.get(scope, "today")
    tasks = todoist.query_tasks(context, filter=todoist_filter, include_done=False)
    if not tasks:
        return f"No active tasks found ({scope.replace('_', ' ')})."
    return tg.start_mark_done(context.telegram_chat_id, tasks)


def handle_out_of_scope(data: dict, context: Context) -> str:
    return "That's not something I track in this group. Try the other group."


def handle_unknown(data: dict, context: Context) -> str:
    return "Couldn't understand that. Please rephrase."


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

HANDLER_MAP = {
    "add_task":           handle_add_task,
    "add_today_plan":     handle_add_today_plan,
    "add_appointment":    handle_add_appointment,
    "add_book_reminder":  handle_add_book_reminder,
    "query_tasks":        handle_query_tasks,
    "query_day":          handle_query_day,
    "query_calendar":     handle_query_calendar,
    "query_week":         handle_query_week,
    "query_config":       handle_query_config,
    "override_schedule":  handle_override_schedule,
    "add_weekly_note":    handle_add_weekly_note,
    "mark_done":          handle_mark_done,
    "mark_done_start":    handle_mark_done_start,
    "reschedule_tasks":   handle_reschedule_tasks,
    "out_of_scope":       handle_out_of_scope,
    "unknown":            handle_unknown,
}

# Actions the dispatch always allows regardless of context.allowed_intents.
_META_ACTIONS = frozenset({"unknown", "out_of_scope"})

# Interactive actions that open a session with side-effects — suppressed in multi-intent.
_INTERACTIVE_ACTIONS = frozenset({"mark_done_start"})

# Sentinel placed in responses list when an interactive action is skipped.
SENTINEL_INTERACTIVE = {"_type": "mark_done_keyboard"}

# Actions whose responses are pure data (no ✓ prefix in multi-intent rendering).
QUERY_ACTIONS = frozenset({
    "query_day", "query_week", "query_tasks",
    "query_calendar", "query_config",
})


def dispatch(parsed: list | dict, context: Context) -> list[str | dict]:
    """Accept a single intent dict or list of intents. Returns a list of response strings.

    In multi-intent mode (parsed is a list with >1 item), interactive actions
    that open a session (mark_done_start) are skipped and replaced with
    SENTINEL_INTERACTIVE so the caller can render a "send separately" note
    without triggering the session side-effect.
    """
    multi = isinstance(parsed, list) and len(parsed) > 1
    if isinstance(parsed, dict):
        parsed = [parsed]
    responses: list[str | dict] = []
    for intent in parsed:
        action = intent.get("action", "unknown")
        if action not in _META_ACTIONS and action not in context.allowed_intents:
            logger.warning(
                "Blocked disallowed action=%r context=%s allowed=%s",
                action, context.name, sorted(context.allowed_intents),
            )
            responses.append(handle_out_of_scope({}, context))
            continue
        if multi and action in _INTERACTIVE_ACTIONS:
            responses.append(SENTINEL_INTERACTIVE)
            continue
        handler = HANDLER_MAP.get(action, handle_unknown)
        try:
            responses.append(handler(intent, context))
        except Exception as exc:
            logger.error("Exception in handler action=%r: %s", action, exc, exc_info=True)
            responses.append("Something went wrong, please try again.")
    return responses
