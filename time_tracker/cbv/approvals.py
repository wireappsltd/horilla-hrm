"""
time_tracker/cbv/approvals.py

Approval workflow views: submit, approve, reject, lock management, favourites.
"""

from datetime import date

from django.contrib import messages
from django.db.models import Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from horilla.decorators import login_required

from time_tracker.cbv.tracker import _get_employee, _project_color, _projects_for
from time_tracker.forms import FavouriteForm, TimesheetLockForm
from time_tracker.methods import format_seconds_hhmm, get_week_bounds
from time_tracker.models import (
    Favourite,
    RequiredFieldConfig,
    TimeEntry,
    TimesheetLock,
    TimesheetSubmission,
    is_entry_locked,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_manager(request):
    """True if the user is a superuser or has approval permissions."""
    return request.user.is_superuser or request.user.has_perm(
        "time_tracker.change_timesheetsubmission"
    )


# ---------------------------------------------------------------------------
# Timesheet submission
# ---------------------------------------------------------------------------


@login_required
def timesheet_submit(request):
    """
    POST — Employee submits a timesheet for a week period.
    Expects POST: week=YYYY-WNN
    Creates a TimesheetSubmission and marks all draft entries as submitted.
    """
    if request.method != "POST":
        return HttpResponse(status=405)

    employee = _get_employee(request)
    if not employee:
        return HttpResponse(status=403)

    week_param = request.POST.get("week", "")
    try:
        parts = week_param.split("-W")
        year, week_num = int(parts[0]), int(parts[1])
    except (ValueError, IndexError, AttributeError):
        messages.error(request, _("Invalid week parameter."))
        response = HttpResponse(status=204)
        response["HX-Refresh"] = "true"
        return response

    week_start, week_end = get_week_bounds(year, week_num)

    # Check for existing pending/approved submission for this period
    existing = TimesheetSubmission.objects.filter(
        employee_id=employee,
        period_start=week_start,
        period_end=week_end,
    ).exclude(status="rejected").first()

    if existing:
        messages.warning(
            request,
            _("A submission already exists for this period (%(status)s).")
            % {"status": existing.get_status_display()},
        )
        response = HttpResponse(status=204)
        response["HX-Refresh"] = "true"
        return response

    # Check whether manager approval is required for this company
    config = RequiredFieldConfig.objects.first()
    approval_required = config.approval_required if config else True

    if approval_required:
        submission = TimesheetSubmission.objects.create(
            employee_id=employee,
            period_start=week_start,
            period_end=week_end,
            status="pending",
        )
        TimeEntry.objects.filter(
            employee_id=employee,
            date__gte=week_start,
            date__lte=week_end,
            status="draft",
        ).update(status="submitted", submission=submission)
        messages.success(
            request,
            _("Timesheet for %(week)s submitted for approval.") % {"week": week_param},
        )
    else:
        # Auto-approve — no manager action needed
        submission = TimesheetSubmission.objects.create(
            employee_id=employee,
            period_start=week_start,
            period_end=week_end,
            status="approved",
            reviewed_at=timezone.now(),
        )
        TimeEntry.objects.filter(
            employee_id=employee,
            date__gte=week_start,
            date__lte=week_end,
            status="draft",
        ).update(status="approved", submission=submission)
        messages.success(
            request,
            _("Timesheet for %(week)s marked as complete.") % {"week": week_param},
        )

    response = HttpResponse(status=204)
    response["HX-Refresh"] = "true"
    return response


# ---------------------------------------------------------------------------
# Approval management (for managers)
# ---------------------------------------------------------------------------


class ApprovalListView(TemplateView):
    """Manager view: list of pending timesheet submissions."""

    template_name = "time_tracker/approvals/approval_list.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.shortcuts import redirect
            return redirect("login")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        is_mgr = _is_manager(self.request)
        employee = _get_employee(self.request)

        if is_mgr:
            pending = TimesheetSubmission.objects.filter(status="pending").select_related(
                "employee_id"
            ).order_by("-submitted_at")
            all_submissions = TimesheetSubmission.objects.all().select_related(
                "employee_id", "reviewed_by"
            ).order_by("-submitted_at")[:50]
        else:
            pending = TimesheetSubmission.objects.none()
            all_submissions = TimesheetSubmission.objects.filter(
                employee_id=employee
            ).select_related("reviewed_by").order_by("-submitted_at")

        # Attach entry counts and total hours
        for sub in all_submissions:
            totals = sub.entries.aggregate(
                t=Sum("duration_seconds"), c=Sum("id") * 0 + Sum("id")
            )
            sub.total_display = format_seconds_hhmm(totals.get("t") or 0)
            sub.entry_count = sub.entries.count()

        context.update(
            {
                "pending": pending,
                "all_submissions": all_submissions,
                "is_manager": is_mgr,
                "employee": employee,
            }
        )
        return context


@login_required
def approval_detail(request, pk):
    """GET — Show a submission's entries. POST — Manager approves/rejects."""
    submission = get_object_or_404(TimesheetSubmission, pk=pk)
    employee = _get_employee(request)
    is_mgr = _is_manager(request)

    if not is_mgr and (not employee or submission.employee_id != employee):
        return HttpResponse(status=403)

    if request.method == "POST":
        if not is_mgr:
            return HttpResponse(status=403)

        action = request.POST.get("action")
        notes = request.POST.get("notes", "")

        if action == "approve":
            submission.status = "approved"
            submission.reviewed_by = employee
            submission.reviewed_at = timezone.now()
            submission.notes = notes
            submission.save()
            submission.entries.filter(status="submitted").update(status="approved")
            messages.success(request, _("Timesheet approved."))
        elif action == "reject":
            submission.status = "rejected"
            submission.reviewed_by = employee
            submission.reviewed_at = timezone.now()
            submission.notes = notes
            submission.save()
            submission.entries.filter(status="submitted").update(status="rejected")
            messages.warning(request, _("Timesheet rejected."))

        from django.shortcuts import redirect
        return redirect("time_tracker:approval-list")

    entries = submission.entries.select_related(
        "project_id", "task_id"
    ).order_by("date", "start_time")
    total_secs = entries.aggregate(t=Sum("duration_seconds"))["t"] or 0

    return render(
        request,
        "time_tracker/approvals/approval_detail.html",
        {
            "submission": submission,
            "entries": entries,
            "total_display": format_seconds_hhmm(total_secs),
            "is_manager": is_mgr,
        },
    )


# ---------------------------------------------------------------------------
# Timesheet locks
# ---------------------------------------------------------------------------


class LockListView(TemplateView):
    """Manager view: list and create timesheet locks."""

    template_name = "time_tracker/management/locks.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.shortcuts import redirect
            return redirect("login")
        if not _is_manager(request):
            from django.shortcuts import redirect
            messages.error(request, _("You do not have permission to manage locks."))
            return redirect("time_tracker:timesheet-page")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["locks"] = TimesheetLock.objects.all().select_related(
            "employee_id", "locked_by"
        ).order_by("-locked_at")
        context["form"] = TimesheetLockForm()
        return context


@login_required
def lock_create(request):
    """POST — Create a new TimesheetLock."""
    if not _is_manager(request):
        return HttpResponse(status=403)

    if request.method == "POST":
        form = TimesheetLockForm(request.POST)
        if form.is_valid():
            lock = form.save(commit=False)
            lock.locked_by = _get_employee(request)
            lock.save()
            messages.success(request, _("Lock created."))
            response = HttpResponse(status=204)
            response["HX-Refresh"] = "true"
            return response
        if request.META.get("HTTP_HX_REQUEST") == "true":
            return render(
                request, "time_tracker/management/lock_form.html", {"form": form}
            )
    else:
        form = TimesheetLockForm()

    return render(request, "time_tracker/management/lock_form.html", {"form": form})


@login_required
def lock_delete(request, pk):
    """POST — Remove a TimesheetLock."""
    if not _is_manager(request):
        return HttpResponse(status=403)
    lock = get_object_or_404(TimesheetLock, pk=pk)
    lock.delete()
    messages.success(request, _("Lock removed."))
    response = HttpResponse(status=204)
    response["HX-Refresh"] = "true"
    return response


# ---------------------------------------------------------------------------
# Favourites
# ---------------------------------------------------------------------------


@login_required
def favourites_list(request):
    """GET — Return favourites panel fragment for the current employee."""
    employee = _get_employee(request)
    favs = Favourite.objects.filter(employee_id=employee).select_related(
        "project_id", "task_id", "client_id"
    ) if employee else Favourite.objects.none()
    projects = _projects_for(employee)
    return render(
        request,
        "time_tracker/tracker/favourites_panel.html",
        {
            "favourites": favs,
            "form": FavouriteForm(),
            "projects": projects,
            "employee": employee,
        },
    )


@login_required
def favourite_create(request):
    """POST — Save a new Favourite from the tracker bar current state."""
    employee = _get_employee(request)
    if not employee:
        return HttpResponse(status=403)

    if request.method == "POST":
        form = FavouriteForm(request.POST)
        if form.is_valid():
            fav = form.save(commit=False)
            fav.employee_id = employee
            fav.save()
            form.save_m2m()
            messages.success(request, _("Favourite saved."))
            response = HttpResponse(status=204)
            response["HX-Trigger"] = "tt:favs-updated"
            return response
        return render(
            request,
            "time_tracker/tracker/favourites_panel.html",
            {
                "favourites": Favourite.objects.filter(employee_id=employee),
                "form": form,
            },
        )

    return HttpResponse(status=405)


@login_required
def favourite_delete(request, pk):
    """POST — Delete a Favourite."""
    fav = get_object_or_404(Favourite, pk=pk)
    employee = _get_employee(request)
    if not employee or fav.employee_id != employee:
        return HttpResponse(status=403)
    fav.delete()
    messages.success(request, _("Favourite removed."))
    response = HttpResponse(status=204)
    response["HX-Trigger"] = "tt:favs-updated"
    return response


@login_required
def favourite_apply(request, pk):
    """GET — Return JSON data for a favourite so the bar can be pre-filled."""
    fav = get_object_or_404(Favourite, pk=pk)
    employee = _get_employee(request)
    if not employee or fav.employee_id != employee:
        return HttpResponse(status=403)

    return JsonResponse(
        {
            "project_id": fav.project_id_id or "",
            "project_name": fav.project_id.title if fav.project_id else "",
            "task_id": fav.task_id_id or "",
            "client_id": fav.client_id_id or "",
            "description": fav.description,
            "is_billable": fav.is_billable,
            "tag_ids": list(fav.tag_ids.values_list("id", flat=True)),
        }
    )


# ---------------------------------------------------------------------------
# Break management
# ---------------------------------------------------------------------------


@login_required
def break_start(request):
    """POST — Start a break on the current active timer."""
    if request.method != "POST":
        return HttpResponse(status=405)

    employee = _get_employee(request)
    if not employee:
        return HttpResponse(status=403)

    from time_tracker.models import ActiveTimer, Break

    timer = ActiveTimer.objects.filter(employee_id=employee).first()
    if not timer:
        return JsonResponse({"error": "No active timer."}, status=400)

    # Only one open break at a time
    open_break = timer.breaks.filter(end_time__isnull=True).first()
    if open_break:
        return JsonResponse({"error": "A break is already running."}, status=409)

    brk = Break.objects.create(
        active_timer=timer,
        start_time=timezone.now(),
        break_type=request.POST.get("break_type", "manual"),
    )
    return JsonResponse(
        {"break_id": brk.pk, "started_at": brk.start_time.isoformat()}
    )


@login_required
def break_stop(request):
    """POST — End the current open break."""
    if request.method != "POST":
        return HttpResponse(status=405)

    employee = _get_employee(request)
    if not employee:
        return HttpResponse(status=403)

    from time_tracker.models import ActiveTimer, Break

    timer = ActiveTimer.objects.filter(employee_id=employee).first()
    if not timer:
        return JsonResponse({"error": "No active timer."}, status=400)

    open_break = timer.breaks.filter(end_time__isnull=True).first()
    if not open_break:
        return JsonResponse({"error": "No open break."}, status=400)

    open_break.end_time = timezone.now()
    open_break.save()  # duration_seconds computed in Break.save()

    return JsonResponse(
        {
            "break_id": open_break.pk,
            "duration_seconds": open_break.duration_seconds,
        }
    )


@login_required
def break_state(request):
    """GET — Return current break state for the active timer."""
    employee = _get_employee(request)
    if not employee:
        return JsonResponse({"on_break": False})

    from time_tracker.models import ActiveTimer

    timer = ActiveTimer.objects.filter(employee_id=employee).first()
    if not timer:
        return JsonResponse({"on_break": False})

    open_break = timer.breaks.filter(end_time__isnull=True).first()
    if open_break:
        elapsed = int((timezone.now() - open_break.start_time).total_seconds())
        total_break_secs = (
            timer.breaks.exclude(pk=open_break.pk).aggregate(
                t=Sum("duration_seconds")
            )["t"]
            or 0
        ) + elapsed
        return JsonResponse(
            {
                "on_break": True,
                "break_id": open_break.pk,
                "started_at": open_break.start_time.isoformat(),
                "elapsed_seconds": elapsed,
                "total_break_seconds": total_break_secs,
            }
        )

    total_break_secs = (
        timer.breaks.aggregate(t=Sum("duration_seconds"))["t"] or 0
    )
    return JsonResponse(
        {"on_break": False, "total_break_seconds": total_break_secs}
    )
