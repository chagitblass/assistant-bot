"""All Google Sheets read/write logic via gspread."""
from __future__ import annotations

from datetime import date

from utils import get_week_start

import gspread
from google.oauth2.service_account import Credentials

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

_client: gspread.Client | None = None
_spreadsheet: gspread.Spreadsheet | None = None


def _get_spreadsheet() -> gspread.Spreadsheet:
    global _client, _spreadsheet
    if _spreadsheet is None:
        creds = Credentials.from_service_account_info(
            config.get_service_account_info(), scopes=SCOPES
        )
        _client = gspread.authorize(creds)
        _spreadsheet = _client.open_by_key(config.GOOGLE_SHEETS_ID)
    return _spreadsheet


def _ws(name: str) -> gspread.Worksheet:
    return _get_spreadsheet().worksheet(name)


# ---------------------------------------------------------------------------
# Config sheet
# ---------------------------------------------------------------------------

def load_config() -> list[dict]:
    rows = _ws("config").get_all_records()
    config.load_sheet_config(rows)
    return rows


def get_contacts() -> list[dict]:
    """Return all rows from the contacts sheet."""
    return _ws("contacts").get_all_records()


def resolve_name_to_email(name: str) -> str | None:
    """Case-insensitive, partial-match name→email lookup from contacts sheet."""
    name_lower = name.strip().lower()
    for row in get_contacts():
        if name_lower in str(row.get("name", "")).lower():
            return str(row["email"]).strip() or None
    return None


def get_husband_email() -> str | None:
    """Return the email of the contact with relationship 'husband'."""
    for row in get_contacts():
        if str(row.get("relationship", "")).strip().lower() == "husband":
            return str(row["email"]).strip() or None
    return None


# ---------------------------------------------------------------------------
# Schedule sheet
# ---------------------------------------------------------------------------
# Columns: day | work_start | work_end | who_drops_off | who_picks_up

def get_all_schedule_days() -> list[dict]:
    return _ws("schedule").get_all_records()


def get_schedule_for_day(day_name: str) -> dict:
    for row in get_all_schedule_days():
        if row.get("day", "").strip().lower() == day_name.lower():
            return row
    return {}


# ---------------------------------------------------------------------------
# Schedule overrides sheet
# ---------------------------------------------------------------------------
# Columns: week_start | day | work_start | work_end | who_drops_off | who_picks_up

def get_schedule_override(week_start: str, day_name: str) -> dict:
    rows = _ws("schedule_overrides").get_all_records()
    for row in rows:
        if row.get("week_start") == week_start and row.get("day", "").strip().lower() == day_name.lower():
            return row
    return {}


def set_schedule_override(week_start: str, day_name: str, fields: dict) -> None:
    ws = _ws("schedule_overrides")
    rows = ws.get_all_records()
    headers = ws.row_values(1)

    for i, row in enumerate(rows):
        if row.get("week_start") == week_start and row.get("day", "").strip().lower() == day_name.lower():
            row_idx = i + 2  # 1-indexed + header
            for field, value in fields.items():
                if field in headers:
                    ws.update_cell(row_idx, headers.index(field) + 1, value or "")
            return

    ws.append_row([
        week_start,
        day_name,
        fields.get("work_start", ""),
        fields.get("work_end", ""),
        fields.get("who_drops_off", ""),
        fields.get("who_picks_up", ""),
    ], value_input_option="USER_ENTERED")


def get_effective_schedule(day_name: str, week_start: str) -> dict:
    """Return schedule for day, applying any override for the given week."""
    base = get_schedule_for_day(day_name)
    override = get_schedule_override(week_start, day_name)
    if not override:
        return base
    merged = dict(base)
    for k, v in override.items():
        if v and k not in ("week_start", "day"):
            merged[k] = v
    return merged


def get_today_schedule() -> dict:
    today = date.today()
    return get_effective_schedule(today.strftime("%A"), get_week_start(today).isoformat())


# ---------------------------------------------------------------------------
# Weekly notes sheet
# ---------------------------------------------------------------------------
# Columns: week_start_date | note

def add_weekly_note(text: str) -> dict:
    ws = _ws("weekly_notes")
    week_start = get_week_start(date.today()).isoformat()
    ws.append_row([week_start, text], value_input_option="USER_ENTERED")
    return {"week_start_date": week_start, "note": text}


def get_weekly_notes_for_week(week_start: str) -> list[dict]:
    rows = _ws("weekly_notes").get_all_records()
    return [r for r in rows if r.get("week_start_date") == week_start]


# ---------------------------------------------------------------------------
# Parser log sheet
# ---------------------------------------------------------------------------
# Columns: timestamp | raw_message | parsed_action | parsed_json | latency_ms | error

def log_parser_event(
    timestamp: str,
    raw_message: str,
    parsed_action: str,
    parsed_json: str,
    latency_ms: int,
    error: str = "",
) -> None:
    _ws("parser_log").append_row(
        [timestamp, raw_message, parsed_action, parsed_json, latency_ms, error],
        value_input_option="USER_ENTERED",
    )
