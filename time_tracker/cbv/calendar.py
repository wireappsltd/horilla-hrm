"""
time_tracker/cbv/calendar.py

Calendar / timeline views: day view, week view, and entry edit panel.
"""

from datetime import date, datetime, timedelta

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from horilla.decorators import login_required

from time_tracker.cbv.tracker import (
    _get_employee,
    _project_color,
    _projects_for,
    _combine_time,
)
from time_tracker.methods import format_seconds, get_leave_dates_for_period
from time_tracker.models import TimeEntry, is_entry_locked


def _entry_to_event(entry, can_edit=True):
    """Convert a TimeEntry to a calendar event dict. Returns None if no times set."""
    if not entry.start_time or not entry.end_time:
        return None
    start = timezone.localtime(entry.start_time)
    end = timezone.localtime(entry.end_time)
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    if end_minutes <= start_minutes:
        end_minutes = start_minutes + max(int(entry.duration_seconds / 60), 15)
    duration_minutes = max(end_minutes - start_minutes, 15)
    color = _project_color(entry.project_id) or "#6366f1"
    return {
        "entry": entry,
        "pk": entry.pk,
        "start_minutes": start_minutes,
        "end_minutes": end_minutes,
        "duration_minutes": duration_minutes,
        "color": color,
        "project_name": entry.project_id.title if entry.project_id else "",
        "project_id": entry.project_id_id or "",
        "task_name": entry.task_id.title if entry.task_id else "",
        "description": entry.description or "",
        "duration_display": format_seconds(entry.duration_seconds),
        "start_label": start.strftime("%H:%M"),
        "end_label": end.strftime("%H:%M"),
        "status": entry.status,
        "is_billable": entry.is_billable,
        "can_edit": can_edit and not is_entry_locked(entry),
    }


def _entry_to_unscheduled(entry, can_edit=True):
    """Convert an entry without times to an unscheduled event dict."""
    color = _project_color(entry.project_id) or "#6366f1"
    return {
        "entry": entry,
        "pk": entry.pk,
        "color": color,
        "project_name": entry.project_id.title if entry.project_id else "",
        "project_id": entry.project_id_id or "",
        "description": entry.description or "",
        "duration_display": format_seconds(entry.duration_seconds),
        "status": entry.status,
        "is_billable": entry.is_billable,
        "can_edit": can_edit and not is_entry_locked(entry),
    }


@login_required
def calendar_day(request):
    """
    GET — Day timeline view.
    Query param: ?date=YYYY-MM-DD  (defaults to today)
    """
    today = date.today()
    date_str = request.GET.get("date", "")
    try:
        from datetime import datetime
        view_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        view_date = today

    employee = _get_employee(request)
    can_manage = request.user.is_superuser or request.user.has_perm(
        "time_tracker.change_timeentry"
    )
    events = []
    unscheduled = []
    leave_dates = set()
    projects = _projects_for(employee)

    if employee:
        entries = (
            TimeEntry.objects.filter(employee_id=employee, date=view_date)
            .select_related("project_id", "task_id", "client_id")
            .order_by("start_time")
        )
        for entry in entries:
            can_edit = can_manage or entry.employee_id_id == employee.pk
            ev = _entry_to_event(entry, can_edit=can_edit)
            if ev:
                events.append(ev)
            else:
                unscheduled.append(_entry_to_unscheduled(entry, can_edit=can_edit))
        leave_dates = get_leave_dates_for_period(employee, view_date, view_date)

    prev_date = view_date - timedelta(days=1)
    next_date = view_date + timedelta(days=1)

    # Show hours 0–23; scroll to 8am
    hours = [{"label": f"{h:02d}:00", "value": h} for h in range(24)]

    return render(
        request,
        "time_tracker/calendar/day_view.html",
        {
            "view_date": view_date,
            "is_today": view_date == today,
            "is_leave": view_date in leave_dates,
            "prev_date": prev_date.strftime("%Y-%m-%d"),
            "next_date": next_date.strftime("%Y-%m-%d"),
            "today_str": today.strftime("%Y-%m-%d"),
            "events": events,
            "unscheduled": unscheduled,
            "hours": hours,
            "projects": projects,
            "employee": employee,
            "csrf_token": request.META.get("CSRF_COOKIE", ""),
        },
    )


@login_required
def calendar_week(request):
    """
    GET — Week timeline view.
    Query param: ?week=YYYY-WNN  (defaults to current week)
    """
    today = date.today()
    week_param = request.GET.get("week", "")
    try:
        parts = week_param.split("-W")
        year, week_num = int(parts[0]), int(parts[1])
    except (ValueError, IndexError, AttributeError):
        iso = today.isocalendar()
        year, week_num = iso[0], iso[1]

    from time_tracker.methods import get_week_bounds
    week_start, week_end = get_week_bounds(year, week_num)

    employee = _get_employee(request)
    leave_dates = set()

    # Build per-day event lists
    days = []
    if employee:
        leave_dates = get_leave_dates_for_period(employee, week_start, week_end)
        entries = (
            TimeEntry.objects.filter(
                employee_id=employee,
                date__gte=week_start,
                date__lte=week_end,
            )
            .select_related("project_id", "task_id", "client_id")
            .order_by("start_time")
        )
        from collections import defaultdict
        by_date = defaultdict(list)
        for entry in entries:
            ev = _entry_to_event(entry)
            if ev:
                by_date[entry.date].append(ev)

        for i in range(7):
            d = week_start + timedelta(days=i)
            days.append(
                {
                    "date": d,
                    "is_today": d == today,
                    "is_weekend": d.weekday() >= 5,
                    "is_leave": d in leave_dates,
                    "events": by_date[d],
                }
            )
    else:
        for i in range(7):
            d = week_start + timedelta(days=i)
            days.append(
                {
                    "date": d,
                    "is_today": d == today,
                    "is_weekend": d.weekday() >= 5,
                    "is_leave": False,
                    "events": [],
                }
            )

    prev_week_start = week_start - timedelta(weeks=1)
    next_week_start = week_start + timedelta(weeks=1)
    prev_iso = prev_week_start.isocalendar()
    next_iso = next_week_start.isocalendar()
    today_iso = today.isocalendar()

    hours = [{"label": f"{h:02d}:00", "value": h} for h in range(7, 22)]

    return render(
        request,
        "time_tracker/calendar/week_view.html",
        {
            "days": days,
            "week_start": week_start,
            "week_end": week_end,
            "week_label": f"{year}-W{week_num:02d}",
            "prev_week": f"{prev_iso[0]}-W{prev_iso[1]:02d}",
            "next_week": f"{next_iso[0]}-W{next_iso[1]:02d}",
            "current_week": f"{today_iso[0]}-W{today_iso[1]:02d}",
            "hours": hours,
            "employee": employee,
        },
    )


# ---------------------------------------------------------------------------
# Calendar entry edit panel
# ---------------------------------------------------------------------------


@login_required
def cal_entry_panel(request, pk):
    """
    GET  — Return the edit panel HTML fragment for a calendar entry.
    POST — Save changes and return JSON {ok, date} so JS can reload.
    """
    entry = get_object_or_404(TimeEntry, pk=pk)
    employee = _get_employee(request)
    can_manage = request.user.is_superuser or request.user.has_perm(
        "time_tracker.change_timeentry"
    )
    can_edit_this = (
        can_manage or (employee and entry.employee_id_id == employee.pk)
    ) and not is_entry_locked(entry)

    if request.method == "POST":
        if not can_edit_this:
            return JsonResponse({"error": "Forbidden"}, status=403)

        if request.POST.get("_delete"):
            date_str = entry.date.strftime("%Y-%m-%d")
            entry.delete()
            return JsonResponse({"ok": True, "deleted": True, "date": date_str})

        entry.description = request.POST.get("description", "").strip()

        project_id_val = request.POST.get("project_id") or None
        if project_id_val:
            try:
                from project.models import Project
                entry.project_id = Project.objects.filter(pk=int(project_id_val)).first()
            except (ValueError, TypeError):
                entry.project_id = None
        else:
            entry.project_id = None

        date_str = request.POST.get("date", "")
        try:
            entry.date = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            pass

        new_start = _combine_time(entry.date, request.POST.get("start_time", ""))
        new_end = _combine_time(entry.date, request.POST.get("end_time", ""))
        if new_start:
            entry.start_time = new_start
        if new_end:
            entry.end_time = new_end

        billable_val = request.POST.get("is_billable")
        entry.is_billable = billable_val in ("on", "true", "1", "True")

        entry.save()
        return JsonResponse({"ok": True, "date": entry.date.strftime("%Y-%m-%d")})

    projects = _projects_for(employee)
    color = _project_color(entry.project_id) or "#6366f1"
    return render(
        request,
        "time_tracker/calendar/entry_panel.html",
        {
            "entry": entry,
            "projects": projects,
            "can_edit": can_edit_this,
            "color": color,
        },
    )
