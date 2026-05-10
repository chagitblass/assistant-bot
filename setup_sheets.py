"""One-time script: create all required worksheets and headers in the spreadsheet."""
import sys

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEETS = {
    "config": ["key", "value"],
    "schedule": ["day", "work_start", "work_end", "who_drops_off", "who_picks_up"],
    "schedule_overrides": ["week_start", "day", "work_start", "work_end", "who_drops_off", "who_picks_up"],
    "weekly_notes": ["week_start_date", "note"],
    "contacts": ["name", "email", "relationship"],
    "parser_log": ["timestamp", "raw_message", "parsed_action", "parsed_json", "latency_ms", "error"],
}

CONFIG_DEFAULTS = [
    ["husband_email", ""],
]

SCHEDULE_DEFAULTS = [
    ["Monday",    "09:00", "17:00", "", ""],  # who_drops_off, who_picks_up
    ["Tuesday",   "09:00", "17:00", "", ""],
    ["Wednesday", "09:00", "17:00", "", ""],
    ["Thursday",  "09:00", "17:00", "", ""],
    ["Friday",    "09:00", "17:00", "", ""],
]


def setup(service_account_json: str, spreadsheet_id: str) -> None:
    creds = Credentials.from_service_account_file(service_account_json, scopes=SCOPES)
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(spreadsheet_id)

    existing = {ws.title for ws in ss.worksheets()}

    for sheet_name, headers in SHEETS.items():
        if sheet_name not in existing:
            ws = ss.add_worksheet(title=sheet_name, rows=1000, cols=len(headers))
            print(f"Created sheet: {sheet_name}")
        else:
            ws = ss.worksheet(sheet_name)
            print(f"Sheet exists: {sheet_name}")

        current_headers = ws.row_values(1)
        if current_headers != headers:
            ws.update("A1", [headers])
            print(f"  Headers set: {headers}")

        if sheet_name == "config" and ws.get_all_values() == [headers]:
            ws.append_rows(CONFIG_DEFAULTS)
            print("  Default config rows added")

        if sheet_name == "schedule" and ws.get_all_values() == [headers]:
            ws.append_rows(SCHEDULE_DEFAULTS)
            print("  Default schedule rows added")

    print("\nSetup complete. Fill in 'config' and 'schedule' sheets with your values before deploying.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python setup_sheets.py <service_account.json> <spreadsheet_id>")
        sys.exit(1)
    setup(sys.argv[1], sys.argv[2])
