"""Context model: which Telegram chat a message came from and what it can do."""
from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass(frozen=True)
class Context:
    name: str
    telegram_chat_id: str
    todoist_project_id: str
    allowed_senders: frozenset[str]
    allowed_intents: frozenset[str]
    schedule_enabled: bool
    calendar_enabled: bool


COUPLE = Context(
    name="couple",
    telegram_chat_id=config.TG_CHAT_COUPLE,
    todoist_project_id=config.TODOIST_PROJECT_COUPLE,
    allowed_senders=config.COUPLE_USERS,
    allowed_intents=frozenset({
        "add_task",
        "add_today_plan",
        "query_tasks",
        "query_day",
        "query_week",
        "mark_done",
        "mark_done_start",
        "reschedule_tasks",
        "add_appointment",
        "add_book_reminder",
        "query_calendar",
        "query_config",
        "override_schedule",
        "add_weekly_note",
    }),
    schedule_enabled=True,
    calendar_enabled=True,
)

PRIVATE = Context(
    name="private",
    telegram_chat_id=config.TG_CHAT_PRIVATE,
    todoist_project_id=config.TODOIST_PROJECT_PRIVATE,
    allowed_senders=config.PRIVATE_USERS,
    allowed_intents=frozenset({
        "add_task",
        "add_today_plan",
        "query_tasks",
        "query_day",
        "mark_done",
        "mark_done_start",
        "reschedule_tasks",
        "add_book_reminder",
    }),
    schedule_enabled=False,
    calendar_enabled=False,
)

CONTEXTS_BY_CHAT_ID: dict[str, Context] = {
    COUPLE.telegram_chat_id:  COUPLE,
    PRIVATE.telegram_chat_id: PRIVATE,
}


def get_context(chat_id: str) -> Context | None:
    return CONTEXTS_BY_CHAT_ID.get(chat_id)
