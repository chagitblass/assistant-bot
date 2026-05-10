"""Todoist task storage — scoped per Context."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from itertools import chain
from zoneinfo import ZoneInfo

from todoist_api_python.api import TodoistAPI

from context import Context
from utils import get_week_start

TZ = ZoneInfo("Asia/Jerusalem")

_DROPPED_LABEL = "dropped"
_COMPLETED_LOOKBACK_DAYS = 90

_client: TodoistAPI | None = None


def get_client() -> TodoistAPI:
    global _client
    if _client is None:
        _client = TodoistAPI(os.environ["TODOIST_API_TOKEN"])
    return _client


def _collect(iterator) -> list:
    """Flatten the Iterator[list[Task]] that paginated get_tasks calls return."""
    return list(chain.from_iterable(iterator))


def _task_to_dict(task, status: str = "active") -> dict:
    subject = next(
        (lbl for lbl in (task.labels or []) if lbl != _DROPPED_LABEL),
        None,
    )
    # task.due.date is a date object from the library, normalise to ISO string
    due_date = task.due.date if task.due else None
    if due_date is not None and hasattr(due_date, "isoformat"):
        due_date = due_date.isoformat()
    return {
        "id": task.id,
        "text": task.content,
        "subject": subject,
        "target_date": due_date,
        "status": status,
        "created_at": task.created_at,
    }


def _fetch_active(project_id: str) -> list:
    """Return all open, non-dropped tasks for a project."""
    tasks = _collect(get_client().get_tasks(project_id=project_id))
    return [t for t in tasks if _DROPPED_LABEL not in (t.labels or [])]


def _fetch_completed_dicts(project_id: str) -> list[dict]:
    """Best-effort fetch of completed tasks as dicts scoped to project_id.

    Uses get_completed_tasks_by_completion_date (no project_id param in the API)
    and filters client-side. Returns [] on any error (e.g. free-tier restriction).
    """
    try:
        now = datetime.now(tz=TZ)
        since = now - timedelta(days=_COMPLETED_LOOKBACK_DAYS)
        raw = _collect(
            get_client().get_completed_tasks_by_completion_date(since=since, until=now)
        )
        result = []
        for t in raw:
            if t.project_id != project_id:
                continue
            status = "dropped" if _DROPPED_LABEL in (t.labels or []) else "done"
            result.append(_task_to_dict(t, status))
        return result
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_task(
    context: Context,
    text: str,
    subject: str | None = None,
    target_date: str | None = None,
) -> str:
    """Add a task to the context's Todoist project. Returns the new task id."""
    kwargs: dict = {
        "project_id": context.todoist_project_id,
        "labels": [subject] if subject else [],
    }
    if target_date:
        kwargs["due_date"] = date.fromisoformat(target_date)
    task = get_client().add_task(text, **kwargs)
    return task.id


def add_tasks_bulk(
    context: Context,
    texts: list[str],
    target_date: str | None = None,
) -> list[str]:
    """Add multiple tasks, all with the same optional due date. Returns task ids."""
    return [add_task(context, text, target_date=target_date) for text in texts]


def query_tasks(
    context: Context,
    filter: str = "all",
    subject: str | None = None,
    include_done: bool = True,
) -> list[dict]:
    """Return tasks for the context's project, filtered by the given criteria.

    filter values:
        "all"           — all tasks (active + optionally done)
        "today"         — tasks due today
        "tomorrow"      — tasks due tomorrow
        "this_week"     — tasks due this Sunday-anchored week
        "next_week"     — tasks due next week
        "floating"      — active tasks with no due date
        "subject"       — tasks matching the given label (subject kwarg required)
        "recent"        — 10 most-recently-created active tasks
        "subjects_list" — list of {"subject": label} dicts used in this project
    """
    pid = context.todoist_project_id
    today = date.today()

    if filter == "subjects_list":
        tasks = _collect(get_client().get_tasks(project_id=pid))
        labels = sorted({
            lbl
            for t in tasks
            for lbl in (t.labels or [])
            if lbl != _DROPPED_LABEL
        })
        return [{"subject": s} for s in labels]

    active_tasks = _fetch_active(pid)
    active_dicts = [_task_to_dict(t) for t in active_tasks]

    all_dicts = active_dicts[:]
    if include_done:
        all_dicts += _fetch_completed_dicts(pid)

    if filter == "today":
        return [t for t in all_dicts if t["target_date"] == today.isoformat()]

    if filter == "tomorrow":
        tomorrow = (today + timedelta(days=1)).isoformat()
        return [t for t in all_dicts if t["target_date"] == tomorrow]

    if filter == "this_week":
        ws = get_week_start(today)
        we = ws + timedelta(days=6)
        return [
            t for t in all_dicts
            if t["target_date"] and ws.isoformat() <= t["target_date"] <= we.isoformat()
        ]

    if filter == "next_week":
        ws = get_week_start(today) + timedelta(days=7)
        we = ws + timedelta(days=6)
        return [
            t for t in all_dicts
            if t["target_date"] and ws.isoformat() <= t["target_date"] <= we.isoformat()
        ]

    if filter == "floating":
        return [t for t in active_dicts if not t["target_date"]]

    if filter == "subject" and subject:
        return [t for t in all_dicts if (t["subject"] or "").lower() == subject.lower()]

    if filter == "recent":
        return sorted(active_dicts, key=lambda t: t["created_at"] or "", reverse=True)[:10]

    # "all"
    return all_dicts


def mark_task_done(context: Context, task_id: str) -> bool:
    return get_client().complete_task(task_id=task_id)


def drop_task(context: Context, task_id: str) -> bool:
    """Mark task as dropped: add 'dropped' label then complete it."""
    api = get_client()
    task = api.get_task(task_id=task_id)
    existing = [lbl for lbl in (task.labels or []) if lbl != _DROPPED_LABEL]
    api.update_task(task_id=task_id, labels=existing + [_DROPPED_LABEL])
    return api.complete_task(task_id=task_id)


def set_task_date(context: Context, task_id: str, target_date: str | None) -> bool:
    """Set or clear the due date on a task. Pass None to make it floating."""
    if target_date:
        get_client().update_task(task_id=task_id, due_date=date.fromisoformat(target_date))
    else:
        # "no date" is Todoist's NLP token to remove a due date
        get_client().update_task(task_id=task_id, due_string="no date")
    return True


def find_task_by_text(
    context: Context,
    query_text: str,
    scope: str = "active",
) -> str | None:
    """Case-insensitive partial match against active task content. Returns task id or None."""
    tasks = _fetch_active(context.todoist_project_id)
    query_lower = query_text.lower()
    for task in tasks:
        if query_lower in task.content.lower():
            return task.id
    return None
