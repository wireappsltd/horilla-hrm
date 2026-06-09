"""
time_tracker/cbv/reports.py

Views for reports and data export.
"""

from datetime import date, timedelta
from io import BytesIO

from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from horilla.decorators import login_required

from time_tracker.cbv.tracker import _PROJECT_COLORS, _project_color
from time_tracker.filters import TimeEntryFilter
from time_tracker.methods import format_seconds_hhmm
from time_tracker.models import TimeEntry


def _color_for_pk(pk):
    """Deterministic colour for a project pk (or grey for none)."""
    if not pk:
        return "#cbd5e1"
    return _PROJECT_COLORS[pk % len(_PROJECT_COLORS)]


def _get_employee(request):
    """Safely retrieve the Employee linked to the current user."""
    try:
        return request.user.employee_get
    except Exception:
        return None


class ReportsDashboard(TemplateView):
    """
    Reports dashboard showing summary stats cards.
    """

    template_name = "time_tracker/reports/reports_dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.shortcuts import redirect

            return redirect("login")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = _get_employee(self.request)
        today = date.today()

        # Week bounds
        iso = today.isocalendar()
        week_start = date.fromisocalendar(iso[0], iso[1], 1)
        week_end = week_start + timedelta(days=6)

        # Month bounds
        month_start = today.replace(day=1)

        base_qs = TimeEntry.objects.all()
        if employee:
            # Non-managers see only their own entries
            if not self.request.user.has_perm("time_tracker.view_timeentry"):
                base_qs = base_qs.filter(employee_id=employee)

        week_qs = base_qs.filter(date__gte=week_start, date__lte=week_end)
        month_qs = base_qs.filter(date__gte=month_start, date__lte=today)
        today_qs = base_qs.filter(date=today)

        week_seconds = week_qs.aggregate(total=Sum("duration_seconds"))["total"] or 0
        month_seconds = month_qs.aggregate(total=Sum("duration_seconds"))["total"] or 0
        billable_month_seconds = (
            month_qs.filter(is_billable=True).aggregate(
                total=Sum("duration_seconds")
            )["total"]
            or 0
        )
        today_count = today_qs.count()

        # ----- Last 7 days bar chart (single grouped query) -----
        week_ago = today - timedelta(days=6)
        by_date = {
            row["date"]: row["t"] or 0
            for row in base_qs.filter(date__gte=week_ago, date__lte=today)
            .values("date")
            .annotate(t=Sum("duration_seconds"))
        }
        max_day = max(by_date.values()) if by_date else 0
        last7 = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            sec = by_date.get(d, 0)
            last7.append(
                {
                    "label": d.strftime("%a"),
                    "day": d.strftime("%d"),
                    "display": format_seconds_hhmm(sec) if sec else "",
                    "pct": int(sec * 100 / max_day) if max_day else 0,
                    "is_today": d == today,
                }
            )

        # ----- Top projects this month (proportion bars) -----
        top_q = (
            month_qs.values("project_id", "project_id__title")
            .annotate(t=Sum("duration_seconds"), c=Count("id"))
            .order_by("-t")[:5]
        )
        top_max = top_q[0]["t"] if top_q and top_q[0]["t"] else 0
        top_projects = []
        for r in top_q:
            sec = r["t"] or 0
            top_projects.append(
                {
                    "name": r["project_id__title"] or _("No Project"),
                    "color": _color_for_pk(r["project_id"]),
                    "display": format_seconds_hhmm(sec),
                    "pct": int(sec * 100 / top_max) if top_max else 0,
                    "count": r["c"],
                }
            )

        # ----- Recent activity (attach project colour) -----
        recent_entries = list(
            base_qs.order_by("-date", "-start_time").select_related(
                "employee_id", "project_id"
            )[:8]
        )
        for e in recent_entries:
            e.color = _project_color(e.project_id) or "#cbd5e1"

        context.update(
            {
                "today": today,
                "hours_this_week": format_seconds_hhmm(week_seconds),
                "hours_this_month": format_seconds_hhmm(month_seconds),
                "billable_this_month": format_seconds_hhmm(billable_month_seconds),
                "entries_today": today_count,
                "last7": last7,
                "top_projects": top_projects,
                "recent_entries": recent_entries,
            }
        )
        return context


@login_required
def summary_report(request):
    """
    GET — Filterable summary report grouped by project.
    """
    employee = _get_employee(request)
    base_qs = TimeEntry.objects.all().select_related(
        "project_id", "employee_id"
    )

    if employee and not request.user.has_perm("time_tracker.view_timeentry"):
        base_qs = base_qs.filter(employee_id=employee)

    f = TimeEntryFilter(request.GET, queryset=base_qs)
    filtered_qs = f.qs

    # Aggregate by project
    project_summary = (
        filtered_qs.values("project_id", "project_id__title")
        .annotate(
            total_seconds=Sum("duration_seconds"),
            billable_seconds=Sum(
                "duration_seconds",
                filter=Q(is_billable=True),
            ),
            entry_count=Count("id"),
        )
        .order_by("-total_seconds")
    )

    # Format for template
    rows = []
    total_secs = 0
    total_billable = 0
    total_count = 0
    summary = list(project_summary)
    grand_total = sum((r["total_seconds"] or 0) for r in summary) or 0
    for row in summary:
        secs = row["total_seconds"] or 0
        bill_secs = row["billable_seconds"] or 0
        total_secs += secs
        total_billable += bill_secs
        total_count += row["entry_count"]
        rows.append(
            {
                "project_name": row["project_id__title"] or _("No Project"),
                "color": _color_for_pk(row["project_id"]),
                "total_hours": format_seconds_hhmm(secs),
                "billable_hours": format_seconds_hhmm(bill_secs),
                "entry_count": row["entry_count"],
                "pct": int(secs * 100 / grand_total) if grand_total else 0,
            }
        )

    context = {
        "filter": f,
        "rows": rows,
        "total_hours": format_seconds_hhmm(total_secs),
        "total_billable": format_seconds_hhmm(total_billable),
        "total_count": total_count,
    }
    return render(request, "time_tracker/reports/summary_report.html", context)


@login_required
def report_export(request):
    """
    GET — Export filtered time entries to XLSX.
    """
    try:
        import pandas as pd
    except ImportError:
        return HttpResponse(
            _("pandas is not installed. Cannot export."), status=500
        )

    employee = _get_employee(request)
    base_qs = TimeEntry.objects.all().select_related(
        "employee_id", "project_id", "task_id", "client_id"
    )

    if employee and not request.user.has_perm("time_tracker.view_timeentry"):
        base_qs = base_qs.filter(employee_id=employee)

    f = TimeEntryFilter(request.GET, queryset=base_qs)
    qs = f.qs

    data = []
    for entry in qs:
        data.append(
            {
                "Date": entry.date.strftime("%Y-%m-%d"),
                "Employee": str(entry.employee_id),
                "Project": entry.project_id.title if entry.project_id else "",
                "Task": entry.task_id.title if entry.task_id else "",
                "Client": entry.client_id.name if entry.client_id else "",
                "Description": entry.description,
                "Duration": entry.duration_display,
                "Billable": "Yes" if entry.is_billable else "No",
                "Status": entry.get_status_display(),
            }
        )

    df = pd.DataFrame(data)

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Time Entries")

    output.seek(0)
    response = HttpResponse(
        output.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="time_entries_export.xlsx"'
    return response
