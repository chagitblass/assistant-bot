"""Proactive scheduled message logic: daily and weekly."""
from __future__ import annotations

from datetime import date

import tg
from context import Context
from handlers import build_day_view, handle_query_week


def run_daily(context: Context) -> None:
    text = build_day_view(date.today(), context)
    tg.send_outbound(context.telegram_chat_id, text)


def run_weekly(context: Context) -> None:
    # TODO: wire weekly image via telegram.send_photo + image_gen.build_weekly_image
    text = handle_query_week({}, context)
    tg.send_outbound(context.telegram_chat_id, text)
