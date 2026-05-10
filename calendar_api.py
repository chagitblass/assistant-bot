"""All Google Calendar read/write logic."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

import config

TZ = ZoneInfo("Asia/Jerusalem")

SCOPES = ["https://www.googleapis.com/auth/calendar"]

_service = None


def _get_service():
    global _service
    if _service is None:
        creds = Credentials.from_service_account_info(
            config.get_service_account_info(), scopes=SCOPES
        )
        _service = build("calendar", "v3", credentials=creds)
    return _service


def _calendar_id() -> str:
    return config.GOOGLE_CALENDAR_ID


def create_event(
    title: str,
    start_dt: datetime | None,
    notes: str | None,
    end_dt: datetime | None = None,
    attendee_emails: list[str] | None = None,
) -> dict:
    if start_dt:
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=TZ)
        if end_dt is None:
            end_dt = start_dt + timedelta(hours=1)
        elif end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=TZ)
        start = {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Jerusalem"}
        end = {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Jerusalem"}
    else:
        today = date.today().isoformat()
        start = {"date": today}
        end = {"date": today}

    body: dict = {
        "summary": title,
        "description": notes or "",
        "start": start,
        "end": end,
    }

    if attendee_emails:
        body["attendees"] = [{"email": e} for e in attendee_emails]

    return (
        _get_service()
        .events()
        .insert(
            calendarId=_calendar_id(),
            body=body,
            sendUpdates="all" if attendee_emails else "none",
        )
        .execute()
    )


def get_events_for_day(day: date) -> list[dict]:
    start = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=TZ).isoformat()
    end = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=TZ).isoformat()
    return _query_events(start, end)


def get_events_for_week(week_start: date) -> list[dict]:
    start = datetime(week_start.year, week_start.month, week_start.day, 0, 0, 0, tzinfo=TZ).isoformat()
    week_end = week_start + timedelta(days=7)
    end = datetime(week_end.year, week_end.month, week_end.day, 0, 0, 0, tzinfo=TZ).isoformat()
    return _query_events(start, end)


def get_upcoming_events(n: int = 10) -> list[dict]:
    now = datetime.now(tz=TZ).isoformat()
    result = (
        _get_service()
        .events()
        .list(
            calendarId=_calendar_id(),
            timeMin=now,
            maxResults=n,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return result.get("items", [])


def _query_events(time_min: str, time_max: str) -> list[dict]:
    result = (
        _get_service()
        .events()
        .list(
            calendarId=_calendar_id(),
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return result.get("items", [])


_WORK_KEYWORDS = {"work", "interview", "meeting", "zoom", "call", "sync", "standup", "1:1", "presentation", "conference"}
_KIDS_KEYWORDS = {"school", "pickup", "dropoff", "חוג", "נווה", "swim", "שחיה", "מתירות", "אגד", "kids", "gan", "גן", "class", "activity", "פעילות"}
_MEDICAL_KEYWORDS = {"doctor", "dentist", "pediatrician", "medical", "תור", "רופא", "clinic", "hospital", "pharmacy", "vaccine", "injection"}


def _event_emoji(summary: str) -> str:
    lower = summary.lower()
    if any(k in lower for k in _WORK_KEYWORDS):
        return "💼"
    if any(k in lower for k in _KIDS_KEYWORDS):
        return "👶"
    if any(k in lower for k in _MEDICAL_KEYWORDS):
        return "🏥"
    return "📌"


def _event_sort_key(event: dict) -> str:
    start = event.get("start", {})
    return start.get("dateTime") or start.get("date") or ""


def format_events_grouped(events: list[dict]) -> str:
    """Return events grouped by day with bold day headers and emoji prefixes."""
    if not events:
        return ""

    from collections import defaultdict
    by_day: dict[date, list[dict]] = defaultdict(list)

    for event in sorted(events, key=_event_sort_key):
        start = event.get("start", {})
        if "dateTime" in start:
            dt = datetime.fromisoformat(start["dateTime"]).astimezone(TZ)
            day = dt.date()
        else:
            day = date.fromisoformat(start.get("date", date.today().isoformat()))
        by_day[day].append(event)

    parts = []
    for day in sorted(by_day):
        day_str = day.strftime("%a %-d %b")
        lines = [f"*{day_str}*"]
        for event in by_day[day]:
            summary = event.get("summary", "(no title)")
            emoji = _event_emoji(summary)
            start = event.get("start", {})
            if "dateTime" in start:
                dt = datetime.fromisoformat(start["dateTime"]).astimezone(TZ)
                lines.append(f"{emoji} {dt.strftime('%H:%M')} — {summary}")
            else:
                lines.append(f"{emoji} (all day) — {summary}")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)


def format_event(event: dict) -> str:
    summary = event.get("summary", "(no title)")
    emoji = _event_emoji(summary)
    start = event.get("start", {})
    if "dateTime" in start:
        dt = datetime.fromisoformat(start["dateTime"]).astimezone(TZ)
        return f"{emoji} {dt.strftime('%H:%M')} — {summary}"
    else:
        return f"{emoji} (all day) — {summary}"
