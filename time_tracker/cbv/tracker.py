"""
time_tracker/cbv/tracker.py

Views for the time tracker: timer widget, timer controls, time entry CRUD.
"""

import json
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.db.models import Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from horilla.decorators import login_required

from time_tracker.filters import TimeEntryFilter
from time_tracker.forms import TimeEntryForm
from time_tracker.methods import format_seconds
from time_tracker.models import ActiveTimer, RequiredFieldConfig, TimeEntry, is_entry_locked

# Deterministic project colour palette (index = project.pk % len)
_PROJECT_COLORS = [
    "#6366f1",
    "#0ea5e9",
    "#10b981",
    "#f59e0b",
    "#ef4444",
    "#8b5cf6",
]


def _get_employee(request):
    """
    Safely retrieve the Employee linked to the current user.
    Returns None if not found.
    """
    try:
        return request.user.employee_get
    except Exception:
        return None


def _project_color(project):
    """Return a deterministic hex colour for the given Project (or '')."""
    if project is None:
        return ""
    return _PROJECT_COLORS[project.pk % len(_PROJECT_COLORS)]


def _projects_for(employee):
    """Return a list of project dicts (id, title, color) the employee can use."""
    from project.models import Project

    if not employee:
        return []
    qs = (
        Project.objects.filter(members=employee)
        | Project.objects.filter(managers=employee)
    ).distinct()
    return [
        {"id": p.pk, "title": p.title, "color": _project_color(p) or "#6366f1"}
        for p in qs
    ]


def _tags_for(employee):
    """Return a list of tag dicts (id, name, color) available to the employee."""
    from time_tracker.models import Tag

    return [
        {"id": t.pk, "name": t.name, "color": t.color or "#64748b"}
        for t in Tag.objects.all()
    ]


def _bar_context(employee, timer=None):
    """Shared context for the Clockify-style tracker bar fragment."""
    projects = _projects_for(employee)
    tags = _tags_for(employee)
    bar_config = {
        "running": bool(timer),
        "startedAt": timer.started_at.isoformat() if timer else None,
        "description": timer.description if timer else "",
        "projectId": (timer.project_id_id if timer and timer.project_id_id else ""),
        "projectName": (
            timer.project_id.title if timer and timer.project_id else ""
        ),
        "projectColor": _project_color(timer.project_id) if timer else "#6366f1",
        "isBillable": bool(timer.is_billable) if timer else False,
        "selectedTags": (
            list(timer.tag_ids.values_list("id", flat=True)) if timer else []
        ),
        "projects": projects,
        "tags": tags,
    }
    return {
        "active_timer": timer,
        "project_color": _project_color(timer.project_id) if timer else "",
        "projects": projects,
        "tags": tags,
        "bar_config": bar_config,
        "today": date.today(),
    }


def _today_entries(employee):
    """Return (entries_qs, today_total_display, today_date) for the employee."""
    today = date.today()
    entries = TimeEntry.objects.none()
    total = 0
    if employee:
        entries = (
            TimeEntry.objects.filter(employee_id=employee, date=today)
            .select_related("project_id", "task_id", "client_id")
            .order_by("-start_time")
        )
        total = entries.aggregate(t=Sum("duration_seconds"))["t"] or 0
    return entries, format_seconds(total), today


def _tracker_stats(employee):
    """Summary stats for the tracker header strip (today / this week / count)."""
    today = date.today()
    if not employee:
        return {
            "today_total": "00:00:00",
            "week_total": "00:00:00",
            "entries_count": 0,
        }
    iso = today.isocalendar()
    week_start = date.fromisocalendar(iso[0], iso[1], 1)
    week_end = week_start + timedelta(days=6)
    today_qs = TimeEntry.objects.filter(employee_id=employee, date=today)
    week_qs = TimeEntry.objects.filter(
        employee_id=employee, date__gte=week_start, date__lte=week_end
    )
    today_sec = today_qs.aggregate(t=Sum("duration_seconds"))["t"] or 0
    week_sec = week_qs.aggregate(t=Sum("duration_seconds"))["t"] or 0
    return {
        "today_total": format_seconds(today_sec),
        "week_total": format_seconds(week_sec),
        "entries_count": today_qs.count(),
    }


def _render_tracker_bar_running(request, timer):
    """Return the tracker-page timer bar in running state (HTMX partial)."""
    employee = _get_employee(request)
    return render(
        request,
        "time_tracker/tracker/tracker_bar_fragment.html",
        _bar_context(employee, timer),
    )


def _render_tracker_bar_stopped(request, employee, with_entries=False):
    """
    Return the tracker-page timer bar in stopped state (HTMX partial).
    When with_entries is True, also includes an out-of-band refresh of the
    today's-entries list (used after stopping a timer creates a new entry).
    """
    context = _bar_context(employee, None)
    if with_entries:
        entries, today_total, today = _today_entries(employee)
        context.update(
            {
                "oob_entries": True,
                "entries": entries,
                "today_total": today_total,
                "today": today,
                "projects": _projects_for(employee),
                "stats": _tracker_stats(employee),
            }
        )
    return render(
        request,
        "time_tracker/tracker/tracker_bar_fragment.html",
        context,
    )


@login_required
def timer_widget(request):
    """
    GET — Return the timer widget partial (running or stopped state).
    Used for HTMX load on the navbar container.
    """
    employee = _get_employee(request)
    if employee is None:
        return render(
            request,
            "time_tracker/tracker/timer_widget_stopped.html",
            {},
        )

    timer = ActiveTimer.objects.filter(employee_id=employee).first()
    if timer:
        context = {
            "started_at": timer.started_at,
            "project_name": timer.project_id.title if timer.project_id else "",
            "project_color": _project_color(timer.project_id),
        }
        return render(
            request,
            "time_tracker/tracker/timer_widget_running.html",
            context,
        )

    return render(
        request,
        "time_tracker/tracker/timer_widget_stopped.html",
        {},
    )


@login_required
def timer_start(request):
    """
    POST — Start a new timer for the current employee.
    Returns 409 JSON if a timer is already running.
    On success, returns the running widget partial or tracker bar partial
    depending on the HTMX target.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    employee = _get_employee(request)
    if employee is None:
        return JsonResponse({"error": "Employee not found"}, status=400)

    if ActiveTimer.objects.filter(employee_id=employee).exists():
        return JsonResponse(
            {"error": "A timer is already running. Stop it first."}, status=409
        )

    project_id_val = request.POST.get("project_id") or None
    task_id_val = request.POST.get("task_id") or None
    client_id_val = request.POST.get("client_id") or None
    description = request.POST.get("tt-quick-desc", "") or request.POST.get(
        "description", ""
    )
    is_billable = request.POST.get("is_billable") in ("on", "true", "1", "True")
    tag_id_vals = request.POST.getlist("tag_ids")

    project = None
    task = None
    client = None

    if project_id_val:
        try:
            from project.models import Project

            project = Project.objects.filter(pk=int(project_id_val)).first()
        except (ValueError, TypeError):
            pass

    if task_id_val:
        try:
            from project.models import Task

            task = Task.objects.filter(pk=int(task_id_val)).first()
        except (ValueError, TypeError):
            pass

    if client_id_val:
        try:
            from time_tracker.models import Client

            client = Client.objects.filter(pk=int(client_id_val)).first()
        except (ValueError, TypeError):
            pass

    timer = ActiveTimer.objects.create(
        employee_id=employee,
        project_id=project,
        task_id=task,
        client_id=client,
        description=description,
        is_billable=is_billable,
        started_at=timezone.now(),
        last_heartbeat=timezone.now(),
    )

    if tag_id_vals:
        try:
            timer.tag_ids.set([int(t) for t in tag_id_vals if str(t).isdigit()])
        except (ValueError, TypeError):
            pass

    hx_target = request.META.get("HTTP_HX_TARGET", "")
    if "tt-tracker-bar" in hx_target:
        return _render_tracker_bar_running(request, timer)

    # Default: return navbar widget
    context = {
        "started_at": timer.started_at,
        "project_name": project.title if project else "",
        "project_color": _project_color(project),
    }
    return render(
        request,
        "time_tracker/tracker/timer_widget_running.html",
        context,
    )


@login_required
def timer_stop(request):
    """
    POST — Stop the running timer, create a TimeEntry, and delete the ActiveTimer.
    Returns the stopped widget partial or tracker bar partial depending on HX-Target.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    employee = _get_employee(request)
    if employee is None:
        return JsonResponse({"error": "Employee not found"}, status=400)

    timer = ActiveTimer.objects.filter(employee_id=employee).first()
    if timer is None:
        hx_target = request.META.get("HTTP_HX_TARGET", "")
        if "tt-tracker-bar" in hx_target:
            return _render_tracker_bar_stopped(request, employee, with_entries=True)
        return render(
            request,
            "time_tracker/tracker/timer_widget_stopped.html",
            {},
        )

    end_time = timezone.now()

    # Close any open break first
    open_break = timer.breaks.filter(end_time__isnull=True).first()
    if open_break:
        open_break.end_time = end_time
        open_break.save()

    # Total break seconds to deduct from effective duration
    from django.db.models import Sum as _Sum
    total_break_secs = timer.breaks.aggregate(t=_Sum("duration_seconds"))["t"] or 0

    entry = TimeEntry(
        employee_id=employee,
        project_id=timer.project_id,
        task_id=timer.task_id,
        client_id=timer.client_id,
        description=timer.description,
        is_billable=timer.is_billable,
        date=timer.started_at.date(),
        start_time=timer.started_at,
        end_time=end_time,
        status="draft",
    )
    entry.save()

    # Adjust duration for breaks
    if total_break_secs > 0:
        entry.duration_seconds = max(0, entry.duration_seconds - total_break_secs)
        TimeEntry.objects.filter(pk=entry.pk).update(
            duration_seconds=entry.duration_seconds
        )

    # Copy tags from timer to entry
    if timer.tag_ids.exists():
        entry.tag_ids.set(timer.tag_ids.all())

    timer.delete()

    hx_target = request.META.get("HTTP_HX_TARGET", "")
    if "tt-tracker-bar" in hx_target:
        return _render_tracker_bar_stopped(request, employee, with_entries=True)

    return render(
        request,
        "time_tracker/tracker/timer_widget_stopped.html",
        {},
    )


@login_required
def timer_state(request):
    """
    GET — Return JSON state of the current employee's timer.
    """
    employee = _get_employee(request)
    if employee is None:
        return JsonResponse({"running": False, "started_at": None, "elapsed_seconds": 0})

    timer = ActiveTimer.objects.filter(employee_id=employee).first()
    if timer:
        elapsed = int((timezone.now() - timer.started_at).total_seconds())
        return JsonResponse(
            {
                "running": True,
                "started_at": timer.started_at.isoformat(),
                "elapsed_seconds": elapsed,
            }
        )

    return JsonResponse({"running": False, "started_at": None, "elapsed_seconds": 0})


@login_required
def timer_heartbeat(request):
    """
    POST — Update last_heartbeat timestamp on the active timer.
    Returns HTTP 204 No Content.
    """
    if request.method != "POST":
        return HttpResponse(status=405)

    employee = _get_employee(request)
    if employee:
        ActiveTimer.objects.filter(employee_id=employee).update(
            last_heartbeat=timezone.now()
        )

    return HttpResponse(status=204)


@login_required
def description_suggestions(request):
    """
    GET ?q=<text> — return up to 8 distinct past descriptions matching the query.
    Used by the tracker bar autocomplete.
    """
    q = request.GET.get("q", "").strip()
    if len(q) < 1:
        return JsonResponse({"suggestions": []})

    employee = _get_employee(request)
    if not employee:
        return JsonResponse({"suggestions": []})

    # Fetch most-recent entries that match, then deduplicate by description text
    entries = (
        TimeEntry.objects.filter(employee_id=employee, description__icontains=q)
        .exclude(description="")
        .order_by("-date", "-id")
        .select_related("project_id")[:60]
    )

    seen = set()
    results = []
    for entry in entries:
        key = entry.description.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "description": entry.description,
            "project_id": entry.project_id_id or "",
            "project_name": entry.project_id.title if entry.project_id else "",
            "project_color": _project_color(entry.project_id) or "#6366f1",
        })
        if len(results) >= 8:
            break

    return JsonResponse({"suggestions": results})


@login_required
def manual_entry_create(request):
    """
    POST — Create a manual TimeEntry (no timer) directly from the tracker bar.
    Expects: description, project_id, date (YYYY-MM-DD),
             start_time (HH:MM), end_time (HH:MM).
    Returns the stopped bar fragment + OOB entries refresh.
    """
    if request.method != "POST":
        return HttpResponse(status=405)

    employee = _get_employee(request)
    if not employee:
        return HttpResponse(status=403)

    description = request.POST.get("description", "").strip()
    project_id_val = request.POST.get("project_id") or None
    date_str = request.POST.get("date", "")
    start_str = request.POST.get("start_time", "")
    end_str = request.POST.get("end_time", "")

    try:
        entry_date = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        entry_date = date.today()

    project = None
    if project_id_val:
        try:
            from project.models import Project
            project = Project.objects.filter(pk=int(project_id_val)).first()
        except (ValueError, TypeError):
            pass

    start_time = _combine_time(entry_date, start_str)
    end_time = _combine_time(entry_date, end_str)

    entry = TimeEntry(
        employee_id=employee,
        description=description,
        project_id=project,
        date=entry_date,
        start_time=start_time,
        end_time=end_time,
        status="draft",
    )
    entry.save()

    messages.success(request, _("Time entry added."))
    return _render_tracker_bar_stopped(request, employee, with_entries=True)


class TrackerPageView(TemplateView):
    """
    Main tracker page — shows today's entries and timer controls.
    """

    template_name = "time_tracker/tracker/tracker_page.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.shortcuts import redirect

            return redirect("login")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = _get_employee(self.request)
        today = date.today()

        entries = TimeEntry.objects.none()
        active_timer = None

        if employee:
            entries = (
                TimeEntry.objects.filter(employee_id=employee, date=today)
                .select_related("project_id", "task_id", "client_id")
                .order_by("-start_time")
            )
            active_timer = ActiveTimer.objects.filter(employee_id=employee).first()

        # Compute today's total duration
        today_total_seconds = entries.aggregate(t=Sum("duration_seconds"))["t"] or 0
        today_total = format_seconds(today_total_seconds)

        # Shared Clockify-bar context (projects, tags, bar_config, etc.)
        context.update(_bar_context(employee, active_timer))

        # Idle timeout config
        config = RequiredFieldConfig.objects.first()
        idle_timeout = config.idle_timeout_minutes if config else 60

        # Favourites for this employee
        from time_tracker.models import Favourite
        favourites = (
            Favourite.objects.filter(employee_id=employee).select_related(
                "project_id"
            )
            if employee
            else Favourite.objects.none()
        )

        # Manager "add for others" — show employee selector if permitted
        can_log_for_others = (
            self.request.user.is_superuser
            or self.request.user.has_perm("time_tracker.add_timeentry")
        )

        context.update(
            {
                "entries": entries,
                "today": today,
                "today_total": today_total,
                "stats": _tracker_stats(employee),
                "form": TimeEntryForm(),
                "idle_timeout_minutes": idle_timeout,
                "favourites": favourites,
                "can_log_for_others": can_log_for_others,
            }
        )
        return context


@login_required
def time_entry_create(request):
    """
    GET — Return form partial.
    POST — Create a new TimeEntry, return HTMX refresh response.
    """
    employee = _get_employee(request)

    if request.method == "POST":
        form = TimeEntryForm(request.POST)
        if form.is_valid():
            instance = form.save(commit=False)
            if employee:
                instance.employee_id = employee
            instance.save()
            form.save_m2m()
            messages.success(request, _("Time entry created."))
            response = HttpResponse(status=204)
            response["HX-Refresh"] = "true"
            return response
        if request.META.get("HTTP_HX_REQUEST") == "true":
            return render(
                request,
                "time_tracker/tracker/entry_form.html",
                {"form": form},
            )
    else:
        initial = {}
        if employee:
            initial["employee_id"] = employee
        form = TimeEntryForm(initial=initial)

    return render(
        request,
        "time_tracker/tracker/entry_form.html",
        {"form": form},
    )


def _can_edit_entry(request, entry):
    """True if the current user may edit/delete the given entry (lock check included)."""
    if is_entry_locked(entry):
        return False
    if request.user.is_superuser:
        return True
    employee = _get_employee(request)
    return employee is not None and entry.employee_id_id == employee.pk


def _combine_time(d, time_str):
    """Combine a date with an 'HH:MM' string into an aware datetime (or None)."""
    if not time_str:
        return None
    try:
        t = datetime.strptime(time_str.strip(), "%H:%M").time()
    except (ValueError, TypeError):
        return None
    naive = datetime.combine(d, t)
    if timezone.is_naive(naive):
        return timezone.make_aware(naive, timezone.get_current_timezone())
    return naive


def _render_entries_list(request, employee):
    """
    Render the today's-entries table (swapped into #tt-entries-list) plus an
    out-of-band refresh of the header stat tiles.
    """
    entries, today_total, today = _today_entries(employee)
    return render(
        request,
        "time_tracker/tracker/entries_refresh.html",
        {
            "entries": entries,
            "today_total": today_total,
            "today": today,
            "projects": _projects_for(employee),
            "stats": _tracker_stats(employee),
        },
    )


@login_required
def time_entry_row(request, pk):
    """GET — Return a single entry row in DISPLAY mode (used by Cancel)."""
    entry = get_object_or_404(TimeEntry, pk=pk)
    return render(request, "time_tracker/tracker/entry_row.html", {"entry": entry})


@login_required
def time_entry_edit_row(request, pk):
    """GET — Return a single entry row in inline EDIT mode."""
    entry = get_object_or_404(TimeEntry, pk=pk)
    if entry.is_locked or not _can_edit_entry(request, entry):
        return render(
            request, "time_tracker/tracker/entry_row.html", {"entry": entry}
        )
    employee = _get_employee(request)
    return render(
        request,
        "time_tracker/tracker/entry_row_edit.html",
        {
            "entry": entry,
            "projects": _projects_for(employee),
            "project_color": _project_color(entry.project_id),
        },
    )


@login_required
def time_entry_update(request, pk):
    """
    POST — Inline-update a TimeEntry from the editable row, then re-render the
    whole today's-entries list (so duration + daily total stay correct).
    """
    entry = get_object_or_404(TimeEntry, pk=pk)
    employee = _get_employee(request)

    if entry.is_locked or not _can_edit_entry(request, entry):
        return HttpResponse(status=403)

    if request.method != "POST":
        return time_entry_edit_row(request, pk)

    entry.description = request.POST.get("description", "").strip()
    # Billable toggle is hidden for MVP; only update if explicitly submitted.
    if "is_billable" in request.POST:
        entry.is_billable = request.POST.get("is_billable") in (
            "on",
            "true",
            "1",
            "True",
        )

    project_id_val = request.POST.get("project_id") or None
    if project_id_val:
        from project.models import Project

        entry.project_id = Project.objects.filter(pk=project_id_val).first()
    else:
        entry.project_id = None

    # Editable date (defaults to existing date if blank/invalid)
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

    entry.save()  # recomputes duration_seconds from start/end
    messages.success(request, _("Time entry updated."))

    # Auto-save: update the row's duration cell + the footer total + the header
    # stat tiles via OOB swaps, leaving the editable inputs (and focus) intact.
    return render(
        request,
        "time_tracker/tracker/tracker_oob.html",
        {"entry": entry, "stats": _tracker_stats(employee)},
    )


@login_required
def time_entry_delete(request, pk):
    """
    POST — Delete a non-locked TimeEntry, then re-render the today's-entries
    list so the daily total and empty state update.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    entry = get_object_or_404(TimeEntry, pk=pk)
    employee = _get_employee(request)

    if entry.is_locked or not _can_edit_entry(request, entry):
        return HttpResponse(status=403)

    entry.delete()
    messages.success(request, _("Time entry deleted."))
    return _render_entries_list(request, employee)


@login_required
def time_entry_bulk_delete(request):
    """
    POST — Bulk delete non-locked TimeEntry records.
    Expects JSON body: {"ids": [1, 2, 3]}
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
        ids = data.get("ids", [])
    except (json.JSONDecodeError, AttributeError):
        ids = request.POST.getlist("ids")

    if not ids:
        return JsonResponse({"error": "No IDs provided."}, status=400)

    entries = TimeEntry.objects.filter(pk__in=ids, is_locked=False)
    count = entries.count()
    entries.delete()

    return JsonResponse({"success": True, "deleted": count})


class TimeEntryListView(TemplateView):
    """
    Filtered list of time entries supporting date navigation.
    Employees without the view_timeentry permission only see their own entries.
    """

    template_name = "time_tracker/tracker/entry_list.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.shortcuts import redirect

            return redirect("login")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = _get_employee(self.request)
        queryset = TimeEntry.objects.all().select_related(
            "employee_id", "project_id", "task_id", "client_id"
        )
        if employee and not self.request.user.has_perm("time_tracker.view_timeentry"):
            queryset = queryset.filter(employee_id=employee)
        f = TimeEntryFilter(self.request.GET, queryset=queryset)
        context["filter"] = f
        context["entries"] = f.qs
        context["employee"] = employee
        return context
