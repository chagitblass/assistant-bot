"""Local polling runner — use instead of the webhook for dev/testing.

Imports _handle_message and on_callback_query from main so the core
business logic is never duplicated. The polling wrappers below do nothing
except extract fields from the Update object and hand off to those helpers.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from main import _handle_message, on_callback_query

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def on_message_polling(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    msg = update.message
    if not msg or not msg.text:
        return
    await _handle_message(
        chat_id=str(msg.chat.id),
        sender_id=str(msg.from_user.id) if msg.from_user else "",
        message_id=str(msg.message_id),
        text=msg.text,
    )


async def on_callback_polling(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Stub — Phase C will add inline-button triage handling here."""
    cq = update.callback_query
    await cq.answer()
    # Reuse the same stub logger from main so behaviour is identical to webhook path
    on_callback_query({"callback_query": {"id": cq.id, "data": cq.data}})


def main() -> None:
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message_polling))
    application.add_handler(CallbackQueryHandler(on_callback_polling))
    application.run_polling()


if __name__ == "__main__":
    main()
