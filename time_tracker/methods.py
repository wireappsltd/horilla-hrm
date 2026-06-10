"""
time_tracker/methods.py

Utility functions for the Time Tracker app.
"""

import calendar
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


def month_grid_context(employee, year: int, month: int) -> dict:
    """
    Build a monthly grid context for the month timesheet view.

    Returns a dict with:
        weeks: list of week rows, each a list of 7 day dicts
        month_total_seconds: int
        month_total_display: str
    Each day dict: {date, day_num, is_today, is_weekend, is_other_month,
                    total_seconds, display, has_entries}
    """
    from time_tracker.models import TimeEntry

    today = date.today()
    # First day of month, last day of month
    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])

    # Expand to full weeks (Mon–Sun)
    grid_start = first_day - timedelta(days=first_day.weekday())
    grid_end = last_day + timedelta(days=(6 - last_day.weekday()))

    entries = TimeEntry.objects.filter(
        employee_id=employee,
        date__gte=grid_start,
        date__lte=grid_end,
    ).values("date", "duration_seconds")

    day_totals = defaultdict(int)
    for e in entries:
        day_totals[e["date"]] += e["duration_seconds"]

    month_total = 0
    weeks = []
    current = grid_start
    while current <= grid_end:
        week = []
        for _ in range(7):
            secs = day_totals.get(current, 0)
            if current.month == month:
                month_total += secs
            week.append(
                {
                    "date": current,
                    "day_num": current.day,
                    "is_today": current == today,
                    "is_weekend": current.weekday() >= 5,
                    "is_other_month": current.month != month,
                    "total_seconds": secs,
                    "display": format_seconds_hhmm(secs) if secs else "",
                    "has_entries": secs > 0,
                }
            )
            current += timedelta(days=1)
        weeks.append(week)

    return {
        "weeks": weeks,
        "month_total_seconds": month_total,
        "month_total_display": format_seconds_hhmm(month_total),
    }


def get_leave_dates_for_period(employee, date_from: date, date_to: date) -> set:
    """Return set of leave dates for the employee within the given period."""
    leave_dates = set()
    try:
        from leave.models import LeaveRequest

        leave_requests = LeaveRequest.objects.filter(
            employee_id=employee,
            start_date__lte=date_to,
            end_date__gte=date_from,
            status="approved",
        )
        for lr in leave_requests:
            current = max(lr.start_date, date_from)
            end = min(lr.end_date, date_to)
            while current <= end:
                leave_dates.add(current)
                current += timedelta(days=1)
    except Exception:
        pass
    return leave_dates


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
