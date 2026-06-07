import os
import json


TELEGRAM_BOT_TOKEN          = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY           = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_SHEETS_ID            = os.environ["GOOGLE_SHEETS_ID"]
GOOGLE_CALENDAR_ID          = os.environ["GOOGLE_CALENDAR_ID"]
# Optional extra read-only calendars (comma-separated, or individual vars)
GOOGLE_CALENDAR_ID_2        = os.environ.get("GOOGLE_CALENDAR_ID_2", "").strip()
GOOGLE_CALENDAR_ID_3        = os.environ.get("GOOGLE_CALENDAR_ID_3", "").strip()
# Production: full JSON string. Local dev: file path (auto-detected below).
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

# Telegram chat IDs for each context group
TG_CHAT_COUPLE  = os.environ["TG_CHAT_COUPLE"]
TG_CHAT_PRIVATE = os.environ["TG_CHAT_PRIVATE"]

# Todoist projects (one per context)
TODOIST_PROJECT_COUPLE  = os.environ["TODOIST_PROJECT_COUPLE"]
TODOIST_PROJECT_PRIVATE = os.environ["TODOIST_PROJECT_PRIVATE"]


def _parse_users(raw: str) -> frozenset[str]:
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


# Whitelisted Telegram user IDs (as strings) per context
COUPLE_USERS  = _parse_users(os.environ.get("COUPLE_USERS", ""))
PRIVATE_USERS = _parse_users(os.environ.get("PRIVATE_USERS", ""))

# Loaded at runtime from the config sheet
_sheet_config: dict = {}


def load_sheet_config(config_rows: list[dict]) -> None:
    global _sheet_config
    _sheet_config = {row["key"]: row["value"] for row in config_rows if row.get("key")}


def get(key: str, default: str = "") -> str:
    return str(_sheet_config.get(key, default))


_service_account_info = None  # dict once loaded


def get_service_account_info() -> dict:
    """Return the Google service account credentials as a parsed dict.

    Railway (production): GOOGLE_SERVICE_ACCOUNT_JSON holds the full JSON string.
    Local dev: if the value looks like a file path (starts with /, ./, or ~),
    the file is read from disk — keeps existing path-based .env setups unchanged.
    Result is cached after the first call.
    """
    global _service_account_info
    if _service_account_info is None:
        raw = GOOGLE_SERVICE_ACCOUNT_JSON.strip().strip("\"'")
        if raw.startswith("/") or raw.startswith("./") or raw.startswith("~"):
            path = os.path.expanduser(raw)
            with open(path) as f:
                _service_account_info = json.load(f)
        else:
            _service_account_info = json.loads(raw)
    return _service_account_info
