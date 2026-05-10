"""Telegram platform adapter.

Inbound:  parse_inbound, is_duplicate, mark_seen
Outbound: format_reply, send_outbound
State:    start_mark_done, resolve_mark_done, clear_mark_done
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config

TZ = ZoneInfo("Asia/Jerusalem")


# ---------------------------------------------------------------------------
# Inbound parsing
# ---------------------------------------------------------------------------

def parse_inbound(update_dict: dict) -> dict:
    """Parse a Telegram Update JSON payload into a normalised message dict.

    Only plain `message` updates are handled. Callback queries, edited
    messages, etc. raise ValueError — main.py routes those separately.

    Raises ValueError on bad input or unsupported update type.
    """
    if not isinstance(update_dict, dict):
        raise ValueError("Payload must be a JSON object")

    msg = update_dict.get("message")
    if not msg:
        update_type = next(
            (k for k in update_dict if k not in ("update_id",) and k != "message"),
            "unknown",
        )
        raise ValueError(f"Unsupported update type: {update_type!r} — no 'message' field")

    text = msg.get("text") or msg.get("caption") or ""
    if not text:
        raise ValueError("Message has no text")

    sender = msg.get("from") or {}
    first = sender.get("first_name", "")
    last  = sender.get("last_name", "")
    sender_name = f"{first} {last}".strip() or sender.get("username", "")

    # Telegram timestamps are Unix seconds (int)
    ts_raw = msg.get("date")
    timestamp = (
        datetime.utcfromtimestamp(ts_raw).strftime("%Y-%m-%dT%H:%M:%SZ")
        if ts_raw else ""
    )

    reply_to = msg.get("reply_to_message")
    reply_to_message_id = str(reply_to["message_id"]) if reply_to else None

    chat = msg.get("chat") or {}

    return {
        "chat_id":             str(chat.get("id", "")),
        "sender_id":           str(sender.get("id", "")),
        "sender_name":         sender_name,
        "message_id":          str(msg["message_id"]),
        "text":                text,
        "timestamp":           timestamp,
        "reply_to_message_id": reply_to_message_id,
    }


# ---------------------------------------------------------------------------
# Outbound sending
# ---------------------------------------------------------------------------

def send_outbound(
    chat_id: str,
    text: str,
    reply_markup: dict | None = None,
) -> None:
    """Send a proactive message to a Telegram chat.

    Synchronous: uses asyncio.run() internally so callers (handlers,
    scheduler) don't need to be async.

    Raises telegram.error.TelegramError on API failure. No retries —
    caller decides.
    """
    from telegram import Bot

    async def _send() -> None:
        async with Bot(config.TELEGRAM_BOT_TOKEN) as bot:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )

    asyncio.run(_send())


async def send_outbound_async(
    chat_id: str,
    text: str,
    reply_markup: dict | None = None,
) -> None:
    """Async version of send_outbound for use inside a running event loop.

    Use this from async handlers (polling path, _handle_message coroutine).
    Use send_outbound from sync Flask routes (scheduler, /triage).
    """
    from telegram import Bot

    async with Bot(config.TELEGRAM_BOT_TOKEN) as bot:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )


# ---------------------------------------------------------------------------
# Idempotency — dict-based FIFO eviction, max 1000 entries
# ---------------------------------------------------------------------------

_MAX_SEEN = 1000
# Insertion-ordered dict; value is None (we only care about key presence)
_seen_message_ids: dict[str, None] = {}


def is_duplicate(message_id: str) -> bool:
    return message_id in _seen_message_ids


def mark_seen(message_id: str) -> None:
    if message_id in _seen_message_ids:
        return
    if len(_seen_message_ids) >= _MAX_SEEN:
        _seen_message_ids.pop(next(iter(_seen_message_ids)))
    _seen_message_ids[message_id] = None


# ---------------------------------------------------------------------------
# Numbered-reply mark-done session
# ---------------------------------------------------------------------------

_SESSION_TTL = timedelta(minutes=10)

# chat_id → {"task_ids": list[str], "expires_at": datetime}
pending_mark_done: dict[str, dict] = {}


def start_mark_done(chat_id: str, tasks: list[dict]) -> str:
    """Open a mark-done session for a chat and return the prompt message.

    `tasks` is a list of dicts with at least "id" and "text" keys —
    the same shape returned by todoist.query_tasks.

    The returned string is sent to the chat as the bot's reply.
    The user responds with numbers ("1 3", "1, 3"), "all", or "cancel".
    """
    pending_mark_done[chat_id] = {
        "task_ids":   [t["id"] for t in tasks],
        "expires_at": datetime.now(tz=TZ) + _SESSION_TTL,
    }
    lines = ["Which tasks are done? Reply with numbers, *all*, or *cancel*:"]
    for i, task in enumerate(tasks, start=1):
        lines.append(f"{i}. {task['text']}")
    return "\n".join(lines)


def resolve_mark_done(chat_id: str, reply_text: str) -> list[str] | None:
    """Try to resolve a user reply against the pending mark-done session.

    Returns:
        list[task_id]   tasks the user selected (empty list means "cancel")
        None            no active/valid session, or reply isn't a numeric answer
                        (caller should fall through to normal intent parsing)
    """
    session = pending_mark_done.get(chat_id)
    if not session:
        return None

    if datetime.now(tz=TZ) > session["expires_at"]:
        del pending_mark_done[chat_id]
        return None

    task_ids = session["task_ids"]
    stripped = reply_text.strip().lower()

    if stripped == "cancel":
        del pending_mark_done[chat_id]
        return []

    if stripped == "all":
        del pending_mark_done[chat_id]
        return list(task_ids)

    # Accept "1 3 5", "1, 3, 5", or any mix of spaces and commas
    tokens = re.split(r"[\s,]+", stripped)
    indices: list[int] = []
    seen_indices: set[int] = set()
    for tok in tokens:
        if not tok:
            continue
        if not tok.isdigit():
            return None
        idx = int(tok) - 1  # convert to 0-based
        if idx < 0 or idx >= len(task_ids):
            return None
        if idx not in seen_indices:
            seen_indices.add(idx)
            indices.append(idx)

    if not indices:
        return None

    del pending_mark_done[chat_id]
    return [task_ids[i] for i in indices]


def clear_mark_done(chat_id: str) -> None:
    pending_mark_done.pop(chat_id, None)


# ---------------------------------------------------------------------------
# Triage inline keyboard
# ---------------------------------------------------------------------------

def send_triage(chat_id: str, tasks: list[dict]) -> None:
    """Send the evening triage message with one button per task."""
    from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    for task in tasks:
        label = task["text"] if len(task["text"]) <= 40 else task["text"][:37] + "…"
        rows.append([InlineKeyboardButton(label, callback_data=f"triage:done:{task['id']}")])
    rows.append([InlineKeyboardButton("Move rest → tomorrow", callback_data="triage:snooze_all")])

    async def _send() -> None:
        async with Bot(config.TELEGRAM_BOT_TOKEN) as bot:
            await bot.send_message(
                chat_id=chat_id,
                text="Evening check-in — tap what you finished today:",
                reply_markup=InlineKeyboardMarkup(rows),
            )

    asyncio.run(_send())
