"""Shared utility functions."""
from __future__ import annotations

from datetime import date, timedelta


def get_week_start(d: date) -> date:
    """Return the Sunday that starts the Israeli work week containing d.

    If d is Saturday, returns the following Sunday (next week).
    Formula: days_since_sunday = (weekday() + 1) % 7
    Python weekday(): Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
    """
    if d.weekday() == 5:          # Saturday → next Sunday
        return d + timedelta(days=1)
    days_since_sunday = (d.weekday() + 1) % 7
    return d - timedelta(days=days_since_sunday)
