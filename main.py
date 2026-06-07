"""Flask app — Telegram webhook and Cloud Scheduler routes."""
from __future__ import annotations

import asyncio
import logging
import os

from flask import Flask, jsonify, request

import config
import handlers
import parser as intent_parser
import scheduler
import tg
import todoist
from context import COUPLE, get_context

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Core message-handling logic — shared by webhook and polling paths
# ---------------------------------------------------------------------------

async def _handle_message(
    chat_id: str,
    sender_id: str,
    message_id: str,
    text: str,
) -> None:
    """Process one inbound text message end-to-end.

    Called via asyncio.run() from the webhook route and via await from
    the polling runner. The Anthropic and Todoist API calls inside are
    synchronous/blocking; that's acceptable for this low-traffic bot.
    """
    ctx = get_context(chat_id)
    if ctx is None:
        return

    if sender_id not in ctx.allowed_senders:
        logger.warning("Rejected sender %s in chat %s", sender_id, chat_id)
        return

    if tg.is_duplicate(message_id):
        return
    tg.mark_seen(message_id)

    # Pending mark-done session takes priority over the parser
    task_ids = tg.resolve_mark_done(chat_id, text)
    if task_ids is not None:
        if not task_ids:
            await tg.send_outbound_async(chat_id, "Cancelled.")
        else:
            for tid in task_ids:
                todoist.mark_task_done(ctx, tid)
            n = len(task_ids)
            await tg.send_outbound_async(
                chat_id, f"Done: {n} task{'s' if n != 1 else ''} marked complete."
            )
        return

    try:
        parsed = intent_parser.parse_intent(text, ctx)
    except Exception as exc:
        logger.error("Parser error: %s", exc)
        await tg.send_outbound_async(chat_id, "Parser error. Try again.")
        return

    is_multi = isinstance(parsed, list) and len(parsed) > 1
    responses = handlers.dispatch(parsed, ctx)

    if not is_multi:
        # Single-intent: send each response as its own message (existing behaviour).
        for resp in responses:
            if resp:
                await tg.send_outbound_async(chat_id, resp)
    else:
        # Multi-intent: collect everything into one combined message.
        parts: list[str] = []
        has_sentinel = False
        for intent, resp in zip(parsed, responses):
            if isinstance(resp, dict) and resp.get("_type") == "mark_done_keyboard":
                has_sentinel = True
                continue
            action = intent.get("action", "unknown")
            if action == "unknown":
                raw = intent.get("raw", "")
                parts.append(f'⚠️ Couldn\'t understand: "{raw}"')
            elif action in handlers.QUERY_ACTIONS:
                parts.append(str(resp))
            else:
                parts.append(f"✓ {resp}")
        if has_sentinel:
            parts.append("ℹ️ Skipped interactive action — please send it as a separate message.")
        combined = "\n\n".join(p for p in parts if p)
        if combined:
            await tg.send_outbound_async(chat_id, combined)


# ---------------------------------------------------------------------------
# Callback-query handler
# ---------------------------------------------------------------------------

def on_callback_query(body: dict) -> None:
    from datetime import date, timedelta
    from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

    cq = body.get("callback_query", {})
    data = cq.get("data", "")
    cq_id = cq.get("id")
    msg = cq.get("message", {})
    chat_id = str(msg.get("chat", {}).get("id", ""))
    message_id = msg.get("message_id")
    ctx = get_context(chat_id)

    async def _handle() -> None:
        async with Bot(config.TELEGRAM_BOT_TOKEN) as bot:
            if data.startswith("triage:done:"):
                task_id = data[len("triage:done:"):]
                if ctx:
                    todoist.mark_task_done(ctx, task_id)
                current_rows = msg.get("reply_markup", {}).get("inline_keyboard", [])
                new_rows = [
                    row for row in current_rows
                    if not any(btn.get("callback_data") == data for btn in row)
                ]
                new_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton(text=btn["text"], callback_data=btn["callback_data"])
                     for btn in row if "callback_data" in btn]
                    for row in new_rows
                ])
                await bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=message_id, reply_markup=new_markup,
                )
                await bot.answer_callback_query(cq_id)

            elif data == "triage:snooze_all":
                if ctx:
                    tomorrow = (date.today() + timedelta(days=1)).isoformat()
                    tasks = todoist.query_tasks(ctx, filter="today", include_done=False)
                    for task in tasks:
                        todoist.set_task_date(ctx, task["id"], tomorrow)
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id,
                    text="Done for today. Remaining tasks moved to tomorrow.",
                )
                await bot.answer_callback_query(cq_id)

            else:
                logger.info("Unhandled callback query: %r", data)
                await bot.answer_callback_query(cq_id)

    asyncio.run(_handle())


# ---------------------------------------------------------------------------
# Telegram webhook — single endpoint for ALL Telegram updates
# ---------------------------------------------------------------------------

@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    body = request.get_json(force=True) or {}

    if "callback_query" in body:
        on_callback_query(body)
        return "", 200

    if "message" in body and body["message"].get("text"):
        try:
            msg = tg.parse_inbound(body)
        except ValueError as exc:
            logger.warning("Bad inbound payload: %s", exc)
            return "", 200
        asyncio.run(_handle_message(
            msg["chat_id"], msg["sender_id"], msg["message_id"], msg["text"]
        ))
        return "", 200

    logger.debug("Unhandled update type, keys=%s", sorted(body.keys()))
    return "", 200


# ---------------------------------------------------------------------------
# Cloud Scheduler routes
# ---------------------------------------------------------------------------

def _scheduler_auth_ok() -> bool:
    expected = os.environ.get("SCHEDULER_SECRET", "")
    if not expected:
        return True  # dev mode: no secret configured
    return request.headers.get("X-Scheduler-Secret") == expected


@app.route("/daily", methods=["POST"])
def daily():
    if not _scheduler_auth_ok():
        return jsonify({"error": "forbidden"}), 403
    scheduler.run_daily(COUPLE)
    return jsonify({"ok": True})


@app.route("/weekly", methods=["POST"])
def weekly():
    if not _scheduler_auth_ok():
        return jsonify({"error": "forbidden"}), 403
    scheduler.run_weekly(COUPLE)
    return jsonify({"ok": True})


@app.route("/triage", methods=["POST"])
def triage():
    if not _scheduler_auth_ok():
        return jsonify({"error": "forbidden"}), 403
    tasks = todoist.query_tasks(COUPLE, filter="today", include_done=False)
    if not tasks:
        tg.send_outbound(COUPLE.telegram_chat_id, "No open tasks today. 🎉")
    else:
        tg.send_triage(COUPLE.telegram_chat_id, tasks)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/debug-cal", methods=["GET"])
def debug_cal():
    """Temporary diagnostic: report calendar config and test access."""
    import config as cfg
    import calendar_api
    from datetime import date
    raw_2 = os.environ.get("GOOGLE_CALENDAR_ID_2", "__MISSING__")
    raw_3 = os.environ.get("GOOGLE_CALENDAR_ID_3", "__MISSING__")
    result = {
        "cal_id_1": cfg.GOOGLE_CALENDAR_ID,
        "cal_id_2": cfg.GOOGLE_CALENDAR_ID_2 or "(not set)",
        "cal_id_3": cfg.GOOGLE_CALENDAR_ID_3 or "(not set)",
        "raw_env_2": raw_2,
        "raw_env_2_len": len(raw_2),
        "raw_env_3": raw_3,
        "raw_env_3_len": len(raw_3),
        "all_ids": calendar_api._all_calendar_ids(),
        "events_today": [],
        "errors": [],
    }
    today = date.today()
    for cal_id in calendar_api._all_calendar_ids():
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            TZ = ZoneInfo("Asia/Jerusalem")
            start = datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=TZ).isoformat()
            end   = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=TZ).isoformat()
            events = calendar_api._query_events(start, end, cal_id)
            result["events_today"].append({
                "calendar": cal_id,
                "count": len(events),
                "titles": [e.get("summary", "(no title)") for e in events],
            })
        except Exception as e:
            result["errors"].append({"calendar": cal_id, "error": str(e)})
    return jsonify(result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
