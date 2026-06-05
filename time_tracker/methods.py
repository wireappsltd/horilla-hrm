"""
time_tracker/methods.py

Utility functions for the Time Tracker app.
"""

from datetime import date, timedelta
from collections import defaultdict

from django.utils.translation import gettext_lazy as _


def get_week_bounds(year: int, week: int):
    """
    Return (monday, sunday) date objects for the given ISO year/week.

    Args:
        year: ISO year (e.g. 2026)
        week: ISO week number (1-53)

    Returns:
        Tuple of (week_start: date, week_end: date)
    """
    # ISO week starts on Monday
    week_start = date.fromisocalendar(year, week, 1)  # Monday
    week_end = week_start + timedelta(days=6)  # Sunday
    return week_start, week_end


def week_grid_context(employee, week_start: date, week_end: date) -> dict:
    """
    Build a week grid context dict for the timesheet view.

    Groups TimeEntry objects by (project_id, task_id) and maps each to a
    dict of {weekday_index: total_seconds} where weekday_index is 0=Monday.

    Args:
        employee: Employee instance
        week_start: Monday of the week
        week_end: Sunday of the week

    Returns:
        dict with keys:
            rows: list of dicts with keys:
                project_id, task_id, project_name, task_name,
                days: {0..6: total_seconds}, total_seconds
            week_total_seconds: int
    """
    from time_tracker.models import TimeEntry

    entries = TimeEntry.objects.filter(
        employee_id=employee,
        date__gte=week_start,
        date__lte=week_end,
    ).select_related("project_id", "task_id")

    # Group: (project_id_pk, task_id_pk) → {weekday_index: total_seconds}
    grid = defaultdict(lambda: defaultdict(int))
    row_meta = {}

    for entry in entries:
        key = (
            entry.project_id_id,
            entry.task_id_id,
        )
        weekday = entry.date.weekday()  # 0=Monday
        grid[key][weekday] += entry.duration_seconds
        if key not in row_meta:
            row_meta[key] = {
                "project_id": entry.project_id,
                "task_id": entry.task_id,
                "project_name": entry.project_id.title if entry.project_id else _("No Project"),
                "task_name": entry.task_id.title if entry.task_id else "",
            }

    rows = []
    week_total = 0
    day_totals_by_weekday = [0] * 7  # sum per weekday across all rows

    for key, day_totals in grid.items():
        row_total = sum(day_totals.values())
        week_total += row_total
        # Build a flat list of 7 values (index 0=Mon … 6=Sun)
        days_list = [day_totals.get(i, 0) for i in range(7)]
        for i, secs in enumerate(days_list):
            day_totals_by_weekday[i] += secs
        rows.append(
            {
                **row_meta[key],
                "days_list": days_list,          # list[7] of ints for template iteration
                "days_display": [format_seconds_hhmm(s) if s else "" for s in days_list],
                "total_seconds": row_total,
                "total_display": format_seconds_hhmm(row_total),
            }
        )

    # Build day-label list: Mon..Sun with dates
    day_labels = [week_start + timedelta(days=i) for i in range(7)]

    return {
        "rows": rows,
        "day_labels": day_labels,
        "day_totals": [format_seconds_hhmm(s) if s else "" for s in day_totals_by_weekday],
        "week_total_seconds": week_total,
        "week_total_display": format_seconds_hhmm(week_total),
    }


def format_seconds(seconds: int) -> str:
    """
    Format a duration in seconds as HH:MM:SS string.

    Args:
        seconds: integer number of seconds

    Returns:
        Formatted string like "02:34:56"
    """
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_seconds_hhmm(seconds: int) -> str:
    """
    Format a duration in seconds as HH:MM string (no seconds component).

    Args:
        seconds: integer number of seconds

    Returns:
        Formatted string like "02:34"
    """
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h:02d}:{m:02d}"
