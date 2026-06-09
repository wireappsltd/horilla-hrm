"""
time_tracker/cbv/timesheet.py

Views for the weekly timesheet grid.
"""

from datetime import date, timedelta

from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from horilla.decorators import login_required

from time_tracker.cbv.tracker import _project_color
from time_tracker.methods import format_seconds_hhmm, get_week_bounds, week_grid_context


def _get_employee(request):
    """Safely retrieve the Employee linked to the current user."""
    try:
        return request.user.employee_get
    except Exception:
        return None


class TimesheetPageView(TemplateView):
    """
    Redirects to the timesheet_week view for the current ISO week.
    """

    template_name = "time_tracker/timesheet/week_grid.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        today = date.today()
        iso = today.isocalendar()
        return redirect(
            f"/time-tracker/timesheet/week/?week={iso[0]}-W{iso[1]:02d}"
        )


@login_required
def timesheet_week(request):
    """
    GET — Display the weekly timesheet grid.
    Query param: ?week=YYYY-WNN  (e.g. ?week=2026-W23)
    """
    week_param = request.GET.get("week", "")
    today = date.today()

    try:
        # Parse YYYY-WNN
        parts = week_param.split("-W")
        if len(parts) == 2:
            year, week_num = int(parts[0]), int(parts[1])
        else:
            raise ValueError("Bad format")
    except (ValueError, IndexError):
        iso = today.isocalendar()
        year, week_num = iso[0], iso[1]

    week_start, week_end = get_week_bounds(year, week_num)
    today = date.today()

    employee = _get_employee(request)
    grid = {}
    leave_dates = set()

    if employee:
        grid = week_grid_context(employee, week_start, week_end)

        # Load approved leave dates for the week
        try:
            from leave.models import LeaveRequest

            leave_requests = LeaveRequest.objects.filter(
                employee_id=employee,
                start_date__lte=week_end,
                end_date__gte=week_start,
                status="approved",
            )
            for lr in leave_requests:
                current = max(lr.start_date, week_start)
                end = min(lr.end_date, week_end)
                while current <= end:
                    leave_dates.add(current)
                    current += timedelta(days=1)
        except Exception:
            pass

    # ----- Restructure grid data into clean, template-friendly shapes -----
    day_labels = grid.get("day_labels", [])
    rows = grid.get("rows", [])
    n = len(day_labels)

    def _day_flags(d):
        return {
            "date": d,
            "is_today": d == today,
            "is_weekend": d.weekday() >= 5,
            "is_leave": d in leave_dates,
        }

    # Per-day totals (seconds) across all rows
    day_seconds = [0] * n
    for row in rows:
        for i in range(n):
            day_seconds[i] += row["days_list"][i]

    # Day header/footer metadata
    day_meta = []
    for i, d in enumerate(day_labels):
        meta = _day_flags(d)
        meta["total_display"] = (
            format_seconds_hhmm(day_seconds[i]) if day_seconds[i] else ""
        )
        day_meta.append(meta)

    # Attach a clean `cells` list to every row + a project colour dot
    for row in rows:
        cells = []
        for i, d in enumerate(day_labels):
            flags = _day_flags(d)
            flags["display"] = row["days_display"][i]
            flags["has_value"] = bool(row["days_list"][i])
            cells.append(flags)
        row["cells"] = cells
        row["color"] = _project_color(row.get("project_id")) or "#cbd5e1"

    # ----- Summary stats -----
    week_total_seconds = sum(day_seconds)
    days_worked = sum(1 for s in day_seconds if s > 0)
    daily_avg = week_total_seconds // days_worked if days_worked else 0
    project_count = len(
        {r["project_id"].pk for r in rows if r.get("project_id")}
    )

    # ----- Navigation -----
    prev_week_start = week_start - timedelta(weeks=1)
    next_week_start = week_start + timedelta(weeks=1)
    prev_iso = prev_week_start.isocalendar()
    next_iso = next_week_start.isocalendar()
    today_iso = today.isocalendar()
    current_week_label = f"{today_iso[0]}-W{today_iso[1]:02d}"
    this_week_label = f"{year}-W{week_num:02d}"

    context = {
        **grid,
        "rows": rows,
        "day_meta": day_meta,
        "week_start": week_start,
        "week_end": week_end,
        "year": year,
        "week_num": week_num,
        "week_label": this_week_label,
        "prev_week": f"{prev_iso[0]}-W{prev_iso[1]:02d}",
        "next_week": f"{next_iso[0]}-W{next_iso[1]:02d}",
        "current_week": current_week_label,
        "is_current_week": this_week_label == current_week_label,
        "leave_dates": leave_dates,
        "employee": employee,
        "today": today,
        # stat tiles
        "week_total_display": format_seconds_hhmm(week_total_seconds),
        "daily_avg_display": format_seconds_hhmm(daily_avg),
        "days_worked": days_worked,
        "project_count": project_count,
    }

    return render(request, "time_tracker/timesheet/week_grid.html", context)
