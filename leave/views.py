"""
views.py
"""

import ast
import contextlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO
from urllib.parse import parse_qs, unquote

import pandas as pd
from django.apps import apps
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, IntegerField, ProtectedError, Q, Value, When
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.encoding import force_str
from django.utils.html import format_html
from django.utils.translation import gettext as __
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods
from xhtml2pdf import pisa

from base.filters import PenaltyFilter
from base.forms import PenaltyAccountForm
from base.methods import (
    choosesubordinates,
    closest_numbers,
    eval_validate,
    export_data,
    filtersubordinates,
    get_key_instances,
    get_pagination,
    is_reportingmanager,
    sortby,
)
from base.models import CompanyLeaves, Holidays, PenaltyAccounts
from employee.models import Employee
from horilla.decorators import (
    hx_request_required,
    logger,
    login_required,
    manager_can_enter,
    owner_can_enter,
    permission_required,
)
from horilla.group_by import group_by_queryset
from horilla.horilla_settings import DYNAMIC_URL_PATTERNS
from horilla.methods import get_horilla_model_class, remove_dynamic_url
from leave.decorators import *
from leave.filters import *
from leave.forms import *
from leave.methods import (
    attendance_days,
    calculate_requested_days,
    company_leave_dates_list,
    company_selection_required,
    filter_conditional_leave_request,
    holiday_dates_list,

)
from leave.models import *
from leave.models import cal_effective_requested_days, leave_requested_dates
from leave.threading import LeaveMailSendThread
from notifications.signals import notify
from openpyxl import Workbook

from horilla_audit.methods import log_activity, log_form_changes


def _leave_client_ip(request):
    """Best-effort client IP: first X-Forwarded-For hop, else REMOTE_ADDR."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _leave_audit(request, action, target=None, changes=None):
    """Write a leave-module ActivityLog entry for a lifecycle event.

    Thin wrapper over ``log_activity`` so every leave event lands in the "Leave"
    audit tab with a consistent actor and (where available) the client IP
    stamped into ``changes``. Never blocks the request — the underlying helper
    swallows and logs its own failures.

    Leave *types*, *policies* and *settings* are not logged here: they are
    tracked automatically by ``horilla_audit.config_tracking``. Leave Types land
    in this tab; Holidays, Company Leaves, Restrict Leaves and the leave setting
    toggles land in the Configuration tab, matching the nav menu each screen is
    edited from. Logging them again from the views would double every entry.
    """
    changes = dict(changes or {})
    ip = _leave_client_ip(request)
    if ip:
        changes.setdefault("IP", ip)
    log_activity(
        getattr(request, "user", None),
        module="leave",
        action=action,
        target=target,
        changes=changes or None,
    )


def _drop_empty(details):
    """Strip keys whose value is None so audit entries stay readable."""
    return {key: value for key, value in details.items() if value is not None}


def _leave_request_details(leave_request):
    """Standard identifying fields for a leave request audit entry.

    Gives every leave-request event the same shape in the audit tab: the
    reference number, who it is for, which leave type, the dates and the
    current status. ``Employee.__str__`` already carries the badge id, so the
    employee name and ID travel together in one field.
    """
    leave_type = getattr(leave_request, "leave_type_id", None)
    return _drop_empty(
        {
            "Reference": f"LR-{leave_request.pk}" if leave_request.pk else None,
            "Employee": str(leave_request.employee_id)
            if getattr(leave_request, "employee_id", None)
            else None,
            "Leave type": str(leave_type) if leave_type else None,
            "From": str(leave_request.start_date or "") or None,
            "To": str(leave_request.end_date or "") or None,
            "Days": str(leave_request.requested_days or "") or None,
            "Status": leave_request.get_status_display(),
        }
    )


def _allocation_request_details(allocation):
    """Standard identifying fields for a leave allocation request audit entry."""
    leave_type = getattr(allocation, "leave_type_id", None)
    return _drop_empty(
        {
            "Reference": f"LA-{allocation.pk}" if allocation.pk else None,
            "Employee": str(allocation.employee_id)
            if getattr(allocation, "employee_id", None)
            else None,
            "Leave type": str(leave_type) if leave_type else None,
            "Days requested": str(allocation.requested_days or "") or None,
            "Status": allocation.get_status_display(),
        }
    )


def _available_leave_details(available_leave):
    """Standard identifying fields for an assigned-leave/balance audit entry."""
    leave_type = getattr(available_leave, "leave_type_id", None)
    return _drop_empty(
        {
            "Employee": str(available_leave.employee_id)
            if getattr(available_leave, "employee_id", None)
            else None,
            "Leave type": str(leave_type) if leave_type else None,
            "Available days": str(available_leave.available_days),
            "Carryforward days": str(available_leave.carryforward_days),
            "Total leave days": str(available_leave.total_leave_days),
        }
    )


def _status_change(before, after):
    """Render a before/after status pair in the diff shape the audit UI uses."""
    return {"Status": {"from": before, "to": after}}


def generate_error_report(error_list, error_data, file_name):
    """
    Function used to generate error excle file for imported datas
    """
    for item in error_list:
        for key, value in error_data.items():
            if key in item:
                value.append(item[key])
            else:
                value.append(None)
    keys_to_remove = [
        key for key, value in error_data.items() if all(v is None for v in value)
    ]
    for key in keys_to_remove:
        del error_data[key]
    data_frame = pd.DataFrame(error_data, columns=error_data.keys())
    styled_data_frame = data_frame.style.applymap(
        lambda x: "text-align: center", subset=pd.IndexSlice[:, :]
    )
    response = HttpResponse(content_type="application/ms-excel")
    response["Content-Disposition"] = f'attachment; filename="{file_name}"'
    writer = pd.ExcelWriter(response, engine="xlsxwriter")
    styled_data_frame.to_excel(writer, index=False, sheet_name="Sheet1")
    worksheet = writer.sheets["Sheet1"]
    worksheet.set_column("A:Z", 30)
    writer.close()

    def get_error_sheet(request):
        remove_dynamic_url(path_info)
        return response

    from leave.urls import path, urlpatterns

    path_info = f"error-sheet-{uuid.uuid4()}"
    urlpatterns.append(path(path_info, get_error_sheet, name=path_info))
    DYNAMIC_URL_PATTERNS.append(path_info)
    path_info = f"leave/{path_info}"
    return path_info


@login_required
@permission_required("leave.add_leavetype")
def leave_type_creation(request):
    """
    function used to create leave type.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return leave type creation template
    POST : return leave view
    """
    form = LeaveTypeForm()
    if request.method == "POST":
        form = LeaveTypeForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, _("New leave type Created.."))
            return redirect(leave_type_view)
    return render(request, "leave/leave_type/leave_type_creation.html", {"form": form})


def paginator_qry(qryset, page_number):
    """
    function used to paginate query set
    """
    paginator = Paginator(qryset, get_pagination())
    qryset = paginator.get_page(page_number)
    return qryset


def order_leave_requests(request, queryset):
    """
    By default the pending ``requested`` items are floated to the very top and
    the rest are ordered by the most recent start date. This default is applied on the initial page load AND on every
    paginated / filtered request, so the ordering "sticks" across all pages.

    """
    queryset = queryset.order_by(
        Case(
            When(status="requested", then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ),
        "-start_date",
        "-id",
    )
    if request.GET.get("sortby"):
        queryset = sortby(request, queryset, "sortby")
    return queryset


@login_required
@permission_required("leave.view_leavetype")
def leave_type_view(request):
    """
    function used to view leave type.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return leave type template
    """

    queryset = LeaveType.objects.all().exclude(is_compensatory_leave=True)
    if (
        LeaveGeneralSetting.objects.first()
        and LeaveGeneralSetting.objects.first().compensatory_leave
    ):
        queryset = LeaveType.objects.all()
    page_number = request.GET.get("page")
    page_obj = paginator_qry(queryset, page_number)
    previous_data = request.GET.urlencode()
    leave_type_filter = LeaveTypeFilter()
    requests_ids = json.dumps(list(queryset.values_list("id", flat=True)))
    if not queryset.exists():
        template_name = "leave/leave_type/leave_type_empty_view.html"
    else:
        template_name = "leave/leave_type/leave_type_view.html"
    return render(
        request,
        template_name,
        {
            "leave_types": page_obj,
            "form": leave_type_filter.form,
            "pd": previous_data,
            "requests_ids": requests_ids,
        },
    )


@login_required
@hx_request_required
@manager_can_enter("leave.view_leavetype")
def leave_type_individual_view(request, id):
    """
    function used to view one leave type.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return one leave type view template
    """
    leave_type = LeaveType.find(id)
    requests_ids_json = request.GET.get("instances_ids")
    compensatory = request.GET.get("compensatory")
    context = {"leave_type": leave_type, "compensatory": compensatory}
    if requests_ids_json:
        requests_ids = json.loads(requests_ids_json)
        previous_id, next_id = closest_numbers(requests_ids, id)
        context["previous"] = previous_id
        context["next"] = next_id
        context["requests_ids"] = requests_ids_json
    return render(request, "leave/leave_type/leave_type_individual_view.html", context)


@login_required
@hx_request_required
@permission_required("leave.view_leavetype")
def leave_type_filter(request):
    """
    function used to filter view leave type.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return leave types template
    """
    queryset = LeaveType.objects.all()
    page_number = request.GET.get("page")
    leave_type_filter = LeaveTypeFilter(request.GET, queryset).qs
    page_obj = paginator_qry(leave_type_filter, page_number)
    previous_data = request.GET.urlencode()
    requests_ids = json.dumps(list(leave_type_filter.values_list("id", flat=True)))
    data_dict = parse_qs(previous_data)
    get_key_instances(LeaveType, data_dict)
    return render(
        request,
        "leave/leave_type/leave_types.html",
        {
            "leave_types": page_obj,
            "pd": previous_data,
            "filter_dict": data_dict,
            "requests_ids": requests_ids,
        },
    )


@login_required
@permission_required("leave.change_leavetype")
def leave_type_update(request, id, **kwargs):
    """
    function used to update leave type.

    request (HttpRequest): The HTTP request object.
    id : leave type id

    Returns:
    GET : return leave type update template
    POST : return leave type view
    """
    try:
        leave_type = LeaveType.objects.get(id=id)
    except (LeaveType.DoesNotExist, OverflowError, ValueError):
        messages.error(request, _("Leave type not found"))
        return redirect(leave_type_view)
    form = UpdateLeaveTypeForm(instance=leave_type)
    compensatory = request.GET.get("compensatory")
    redirect_url = leave_type_view
    if compensatory:
        redirect_url = compensatory_leave_settings_view
    if request.method == "POST":
        form_data = UpdateLeaveTypeForm(
            request.POST, request.FILES, instance=leave_type
        )
        if form_data.is_valid():
            form_data.save()
            messages.success(request, _("Leave type is updated successfully.."))
            return redirect(redirect_url)
    return render(
        request,
        "leave/leave_type/leave_type_update.html",
        {"form": form, "compensatory": compensatory},
    )


@login_required
@permission_required("leave.delete_leavetype")
def leave_type_delete(request, obj_id):
    """
    function used to delete leave type.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id : leave type id

    Returns:
    GET : return leave type view template
    """
    try:
        LeaveType.objects.get(id=obj_id).delete()
        messages.success(request, _("Leave type deleted successfully.."))
    except (LeaveType.DoesNotExist, OverflowError, ValueError):
        messages.error(request, _("Leave type not found."))
    except ProtectedError as e:
        models_verbose_name_sets = set()
        for obj in e.protected_objects:
            models_verbose_name_sets.add(__(obj._meta.verbose_name))
        models_verbose_name_str = (",").join(models_verbose_name_sets)
        messages.error(
            request,
            _(
                "This leave types are already in use for {}".format(
                    models_verbose_name_str
                )
            ),
        )
    if request.META.get("HTTP_HX_REQUEST") == "true":
        if request.META.get("HTTP_HX_TARGET") == "objectDetailsModalTarget":
            instances_ids = request.GET.get("instances_ids")
            instances_list = json.loads(instances_ids)
            if obj_id in instances_list:
                instances_list.remove(obj_id)
            previous_instance, next_instance = closest_numbers(
                json.loads(instances_ids), obj_id
            )
            return redirect(
                f"/leave/leave-type-individual-view/{next_instance}?instances_ids={instances_list}"
            )
        return redirect(f"/leave/type-filter?{request.GET.urlencode()}")
    return redirect(leave_type_view)


@login_required
@hx_request_required
@manager_can_enter("leave.add_leaverequest")
def get_employee_leave_types(request):
    employee_id = request.GET.get("employee_id")
    form = (
        LeaveRequestUpdationForm()
        if request.GET.get("form")
        and request.GET.get("form") == "LeaveRequestUpdationForm"
        else LeaveRequestCreationForm()
    )

    if employee_id:
        employee = get_object_or_404(Employee, id=employee_id)
        assigned_leave_types = LeaveType.objects.filter(
            id__in=employee.available_leave.values_list("leave_type_id", flat=True)
        )
        form.fields["leave_type_id"].queryset = assigned_leave_types
    else:
        form.fields["leave_type_id"].queryset = LeaveType.objects.none()

    leave_type_field_html = render_to_string(
        "leave/leave_request/leave_type_field.html",
        {
            "form": form,
            "field_name": "leave_type_id",
            "field": form.fields["leave_type_id"],
        },
    )
    return HttpResponse(leave_type_field_html)


def multiple_approvals_check(id):
    approvals = LeaveRequestConditionApproval.objects.filter(leave_request_id=id)
    requested_query = approvals.filter(is_approved=False).order_by("sequence")
    approved_query = approvals.filter(is_approved=True).order_by("sequence")
    managers = []
    for manager in approvals:
        managers.append(manager.manager_id)
    if approvals.exists():
        result = {
            "managers": managers,
            "approved": approved_query,
            "requested": requested_query,
            "approvals": approvals,
        }
    else:
        result = False
    return result


@login_required
@hx_request_required
@manager_can_enter("leave.add_leaverequest")
def leave_request_creation(request, type_id=None, emp_id=None):
    """
    function used to create leave request.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return leave request form template
    POST : return leave request view
    """
    referer_parts = [
        part for part in request.META.get("HTTP_REFERER").split("/") if part != ""
    ]
    if request.GET.urlencode().startswith("pd="):
        previous_data = unquote(request.GET.urlencode())[len("pd=") :]
    else:
        request_copy = request.GET.copy()
        if "confirm" in request_copy:
            request_copy.pop("confirm")
        previous_data = request_copy.urlencode()

    form = LeaveRequestCreationForm()
    if request:
        employee_qs = form.fields["employee_id"].queryset
        post_emp_id = request.POST.get("employee_id")
        employee = employee_qs.filter(id=post_emp_id).first() or (
            request.user.employee_get
            if request.user.employee_get in employee_qs
            else employee_qs.first()
        )

        if employee:
            leave_type_ids = employee.available_leave.values_list(
                "leave_type_id", flat=True
            )
            assigned_leave_types = LeaveType.objects.filter(id__in=leave_type_ids)

            form.fields["leave_type_id"].queryset = assigned_leave_types

    if type_id and emp_id:
        initial_data = {
            "leave_type_id": type_id,
            "employee_id": emp_id,
        }
        form = LeaveRequestCreationForm(initial=initial_data)

    form = choosesubordinates(request, form, "leave.add_leaverequest")
    if request.method == "POST":
        form = LeaveRequestCreationForm(request.POST, request.FILES)
        # Set the queryset again on the bound form for the validation
        form.fields["leave_type_id"].queryset = assigned_leave_types
        form = choosesubordinates(request, form, "leave.add_leaverequest")
        if form.is_valid():
            leave_request = form.save(commit=False)
            save = True

            if leave_request.leave_type_id.require_approval == "no":
                employee_id = leave_request.employee_id
                leave_type_id = leave_request.leave_type_id
                available_leave = AvailableLeave.objects.get(
                    leave_type_id=leave_type_id, employee_id=employee_id
                )
                leave_request.created_by = request.user.employee_get
                leave_request.save()
                # Only deduct from the current year's bucket. A prior- or
                # future-year leave must not consume this year's allocation —
                # leave_taken() is year-bounded, so it would never add the
                # consumed days back to total_leaves and the balance would
                # silently shrink.
                if (
                    leave_request.start_date
                    and leave_request.start_date.year == date.today().year
                ):
                    if leave_request.requested_days > available_leave.available_days:
                        leave = (
                            leave_request.requested_days - available_leave.available_days
                        )
                        leave_request.approved_available_days = (
                            available_leave.available_days
                        )
                        available_leave.available_days = 0
                        available_leave.carryforward_days = (
                            available_leave.carryforward_days - leave
                        )
                        leave_request.approved_carryforward_days = leave
                    else:
                        available_leave.available_days = (
                            available_leave.available_days - leave_request.requested_days
                        )
                        leave_request.approved_available_days = leave_request.requested_days
                else:
                    # Prior/future-year: keep the approved_* fields at 0 so a
                    # later cancel doesn't inflate the current-year balance
                    # (cancel adds these back to available_days).
                    leave_request.approved_available_days = 0
                    leave_request.approved_carryforward_days = 0
                leave_request.status = "approved"
                leave_request.reviewed_by = getattr(
                    request.user, "employee_get", None
                )
                leave_request.reviewed_at = timezone.now()
            if save:
                leave_request.created_by = request.user.employee_get
                leave_request.save()
                try:
                    available_leave.save()
                except:
                    pass

                if multiple_approvals_check(leave_request.id):
                    conditional_requests = multiple_approvals_check(leave_request.id)
                    managers = []
                    for manager in conditional_requests["managers"]:
                        managers.append(manager.employee_user_id)
                    with contextlib.suppress(Exception):
                        notify.send(
                            request.user.employee_get,
                            recipient=managers[0],
                            verb="You have a new leave request to validate.",
                            verb_ar="لديك طلب إجازة جديد يجب التحقق منه.",
                            verb_de="Sie haben eine neue Urlaubsanfrage zur Validierung.",
                            verb_es="Tiene una nueva solicitud de permiso que debe validar.",
                            verb_fr="Vous avez une nouvelle demande de congé à valider.",
                            icon="people-circle",
                            redirect=f"/leave/request-view?id={leave_request.id}",
                        )

                mail_thread = LeaveMailSendThread(
                    request, leave_request, type="request"
                )
                mail_thread.start()
                messages.success(request, _("Leave request created successfully.."))
                audit_changes = _leave_request_details(leave_request)
                audit_changes["Reason"] = leave_request.description
                if leave_request.leave_type_id.require_approval == "no":
                    audit_changes["Auto-approved"] = (
                        "Leave type does not require approval"
                    )
                _leave_audit(
                    request,
                    "Leave request created",
                    target=leave_request,
                    changes=audit_changes,
                )
                with contextlib.suppress(Exception):
                    notify.send(
                        request.user.employee_get,
                        recipient=leave_request.employee_id.employee_work_info.reporting_manager_id.employee_user_id,
                        verb=f"New leave request created for {leave_request.employee_id}.",
                        verb_ar=f"تم إنشاء طلب إجازة جديد لـ {leave_request.employee_id}.",
                        verb_de=f"Neuer Urlaubsantrag erstellt für {leave_request.employee_id}.",
                        verb_es=f"Nueva solicitud de permiso creada para {leave_request.employee_id}.",
                        verb_fr=f"Nouvelle demande de congé créée pour {leave_request.employee_id}.",
                        icon="people-circle",
                        redirect=reverse("request-view") + f"?id={leave_request.id}",
                    )
                form = LeaveRequestCreationForm()
                if referer_parts[-2] == "employee-view":
                    return _trigger_leave_stats_refresh(
                        HttpResponse("<script>window.location.reload();</script>")
                    )

            leave_requests = LeaveRequest.objects.all()
            if len(leave_requests) == 1:
                return _trigger_leave_stats_refresh(
                    HttpResponse("<script>window.location.reload()</script>")
                )
            _leave_saved = True
        else:
            _leave_saved = False
    else:
        _leave_saved = False
    referrer = request.META.get("HTTP_REFERER", "")
    referrer = "/" + "/".join(referrer.split("/")[3:])
    if referrer == "/":
        hx_url = reverse("leave-request-and-approve")
        hx_target = "#leaveApproveCardBody"
    else:
        hx_url = "/leave/request-filter?"
        hx_target = "#leaveRequest"
    response = render(
        request,
        "leave/leave_request/leave_request_form.html",
        {
            "form": form,
            "pd": previous_data,
            "hx_url": hx_url,
            "hx_target": hx_target,
        },
    )
    if _leave_saved:
        _trigger_leave_stats_refresh(response)
    return response


@login_required
@manager_can_enter("leave.view_leaverequest")
def leave_request_view(request):
    """
    function used to view leave request.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return leave request view template
    """
    queryset = LeaveRequestFilter(request.GET).qs.order_by("-id").distinct()
    multiple_approvals = filter_conditional_leave_request(request).distinct()
    normal_requests = filtersubordinates(request, queryset, "leave.view_leaverequest")

    if not request.user.is_superuser:
        multi_approve_requests = LeaveRequestConditionApproval.objects.filter(
            is_approved=False, is_rejected=False
        )

        multi_ids = [request.leave_request_id.id for request in multi_approve_requests]

        # Create a new list excluding leave requests with IDs in multi_ids
        normal_requests = [
            leave.id for leave in normal_requests if leave.id not in multi_ids
        ]

        # Convert the list of IDs back to a queryset
        normal_requests = LeaveRequest.objects.filter(id__in=normal_requests).distinct()

    queryset = normal_requests | multiple_approvals
    queryset = order_leave_requests(request, queryset)
    page_number = request.GET.get("page")
    page_obj = paginator_qry(queryset, page_number)
    leave_request_filter = LeaveRequestFilter()

    # Fetching leave requests
    leave_requests = queryset

    leave_requests_with_interview = []
    if apps.is_installed("recruitment"):
        for leave_request in leave_requests:

            # Fetch interviews for the employee within the requested leave period
            InterviewSchedule = get_horilla_model_class(
                app_label="recruitment", model="interviewschedule"
            )

            interviews = InterviewSchedule.objects.filter(
                employee_id=leave_request.employee_id,
                interview_date__range=[
                    leave_request.start_date,
                    leave_request.end_date,
                ],
            )
            if interviews:
                # If interview exists then adding the leave request to the list
                leave_requests_with_interview.append(leave_request)

    requests = queryset.filter(status="requested").count()
    requests_ids = json.dumps(list(page_obj.object_list.values_list("id", flat=True)))
    approved_requests = queryset.filter(status="approved").count()
    rejected_requests = queryset.filter(status="cancelled").count()
    previous_data = request.GET.urlencode()
    data_dict = parse_qs(previous_data)
    get_key_instances(LeaveRequest, data_dict)
    return render(
        request,
        "leave/leave_request/request_view.html",
        {
            "leave_requests": page_obj,
            "pd": previous_data,
            "form": leave_request_filter.form,
            "requests": requests,
            "approved_requests": approved_requests,
            "rejected_requests": rejected_requests,
            "gp_fields": LeaveRequestReGroup.fields,
            "requests_ids": requests_ids,
            "current_date": date.today(),
            "filter_dict": data_dict,
            "leave_requests_with_interview": leave_requests_with_interview,
        },
    )


@login_required
@manager_can_enter("leave.view_leaverequest")
def leave_requests_export(request):
    if request.META.get("HTTP_HX_REQUEST") == "true":
        excel_column = LeaveRequestExportForm()
        export_filter = LeaveRequestFilter()
        context = {
            "excel_column": excel_column,
            "export_filter": export_filter.form,
        }

        return render(
            request,
            "leave/leave_request/leave_requests_export_filter.html",
            context=context,
        )
    # Only the non-htmx branch actually streams a file back; the htmx branch
    # above just renders the column/filter picker.
    _leave_audit(
        request,
        "Leave requests exported",
        changes={
            "File": "Leave_requests.xlsx",
            "Filters": request.GET.urlencode() or "None",
        },
    )
    return export_data(
        request=request,
        model=LeaveRequest,
        filter_class=LeaveRequestFilter,
        form_class=LeaveRequestExportForm,
        file_name="Leave_requests",
        perm="leave.view_leaverequest",
    )


def generate_leave_request_pdf(template_path, context, html=False):
    """
    Generate a PDF file from an HTML template and context data.

    Args:
        template_path (str): The path to the HTML template.
        context (dict): The context data to render the template.
        html (bool): If True, return raw HTML instead of a PDF.

    Returns:
        HttpResponse: A response with the generated PDF file or raw HTML.
    """
    try:
        html_content = render_to_string(template_path, context)

        if html:
            return HttpResponse(html_content)

        result = BytesIO()
        pdf_status = pisa.CreatePDF(src=html_content, dest=result)

        if pdf_status.err:
            logger.error("Error creating PDF")
            return HttpResponse("Error generating PDF", status=500)

        response = HttpResponse(result.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = 'inline; filename="leave_request.pdf"'
        return response

    except Exception as e:
        logger.exception("Error generating PDF")
        return HttpResponse(f"Error generating PDF: {str(e)}", status=500)


@login_required
@manager_can_enter("leave.view_leaverequest")
def create_leave_report(request):
    """
    Generate a Leave Report as a PDF and return it in an HttpResponse.

    Args:
        request (HttpRequest): The request object.

    Returns:
        HttpResponse: A response containing the PDF content.
    """
    employee_data = {}
    company_id = request.session.get("selected_company")
    if company_id == "all" or not company_id:
        company = Company.objects.all()
    else:
        company = Company.objects.filter(id=company_id).first()

    leave_requests = LeaveRequest.objects.filter(status="approved").select_related(
        "employee_id", "leave_type_id"
    )
    used_days_map = defaultdict(float)
    leave_request_map = defaultdict(list)

    for lreq in leave_requests:
        key = (
            lreq.employee_id.id,
            lreq.leave_type_id.id if lreq.leave_type_id else None,
        )
        used_days_map[key] += lreq.requested_days
        leave_request_map[lreq.employee_id.id].append(lreq)

    employees = Employee.objects.all()

    for employee in employees:
        employee_id = employee.id
        emp_data = {
            "employee": employee,
            "total_leave_days": 0,
            "used_leave_days": 0,
            "remaining_leave_days": 0,
            "leave_requests": leave_request_map.get(employee_id, []),
            "leave_types_counted": set(),
            "new_hire": False,
        }

        if employee.employee_work_info:
            hire_date = employee.employee_work_info.date_joining
            if hire_date and (date.today() - hire_date) <= timedelta(days=365):
                emp_data["new_hire"] = True

        assigned_leave_types = LeaveType.objects.filter(
            id__in=employee.available_leave.values_list("leave_type_id", flat=True)
        )

        for leave_type in assigned_leave_types:
            leave_type_id = leave_type.id

            if leave_type_id in emp_data["leave_types_counted"]:
                continue

            emp_data["leave_types_counted"].add(leave_type_id)

            total_days = leave_type.total_days or 0
            emp_data["total_leave_days"] += total_days

            used_days = used_days_map.get((employee_id, leave_type_id), 0)
            emp_data["used_leave_days"] += used_days

        emp_data["remaining_leave_days"] = (
            emp_data["total_leave_days"] - emp_data["used_leave_days"]
        )

        sorted_reqs = sorted(
            emp_data["leave_requests"],
            key=lambda x: (x.end_date - x.start_date).days,
            reverse=True,
        )
        for i in range(3):
            if i < len(sorted_reqs):
                emp_data[f"period{i+1}_start"] = sorted_reqs[i].start_date
                emp_data[f"period{i+1}_end"] = sorted_reqs[i].end_date
            else:
                emp_data[f"period{i+1}_start"] = ""
                emp_data[f"period{i+1}_end"] = ""

        employee_data[employee_id] = emp_data

    final_employee_data = list(employee_data.values())
    final_employee_data.sort(key=lambda x: x["employee"].get_full_name())

    context = {
        "employee_data": final_employee_data,
        "company_data": company,
        "report_creation_date": date.today(),
        "request": request,
    }

    template_path = "leave/leave_request/leave_request_pdf.html"
    _leave_audit(
        request,
        "Leave report exported (PDF)",
        changes={
            "Employees included": str(len(final_employee_data)),
            "Company scope": str(company_id or "all"),
        },
    )
    return generate_leave_request_pdf(template_path, context=context, html=False)


@login_required
@hx_request_required
# @manager_can_enter("leave.view_leaverequest")
def leave_request_filter(request):
    """
    function used to filter leave request.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return leave request view template
    """
    previous_data = request.GET.urlencode()
    queryset = LeaveRequestFilter(request.GET).qs.order_by("-id")

    # Fetching leave requests
    leave_requests = queryset

    leave_requests_with_interview = []
    if apps.is_installed("recruitment"):
        for leave_request in leave_requests:

            # Fetch interviews for the employee within the requested leave period
            InterviewSchedule = get_horilla_model_class(
                app_label="recruitment", model="interviewschedule"
            )

            interviews = InterviewSchedule.objects.filter(
                employee_id=leave_request.employee_id,
                interview_date__range=[
                    leave_request.start_date,
                    leave_request.end_date,
                ],
            )
            if interviews:
                # If interview exists then adding the leave request to the list
                leave_requests_with_interview.append(leave_request)

    field = request.GET.get("field")
    multiple_approvals = filter_conditional_leave_request(request)
    queryset = filtersubordinates(request, queryset, "leave.view_leaverequest")

    if not request.user.is_superuser:
        multi_approve_requests = LeaveRequestConditionApproval.objects.filter(
            is_approved=False, is_rejected=False
        )

        multi_ids = [request.leave_request_id.id for request in multi_approve_requests]

        # Create a new list excluding leave requests with IDs in multi_ids
        queryset = [leave.id for leave in queryset if leave.id not in multi_ids]

        # Convert the list of IDs back to a queryset
        queryset = LeaveRequest.objects.filter(id__in=queryset)

    queryset = queryset.distinct()
    multiple_approvals = multiple_approvals.distinct()

    queryset = queryset | multiple_approvals
    leave_request_filter = LeaveRequestFilter(request.GET, queryset).qs
    leave_request_filter = order_leave_requests(request, leave_request_filter)
    page_number = request.GET.get("page")
    template = ("leave/leave_request/leave_requests.html",)

    if field != "" and field is not None:
        leave_request_filter = group_by_queryset(
            leave_request_filter, field, request.GET.get("page"), "page"
        )
        list_values = [entry["list"] for entry in leave_request_filter]
        id_list = []
        for value in list_values:
            for instance in value.object_list:
                id_list.append(instance.id)

        requests_ids = json.dumps(list(id_list))
        template = "leave/leave_request/group_by.html"

    else:
        leave_request_filter = paginator_qry(
            leave_request_filter, request.GET.get("page")
        )
        requests_ids = json.dumps(
            [instance.id for instance in leave_request_filter.object_list]
        )

    data_dict = []

    if not request.GET.get("dashboard"):
        data_dict = parse_qs(previous_data)
        get_key_instances(LeaveRequest, data_dict)

    if "status" in data_dict:
        status_list = data_dict["status"]
        if len(status_list) > 1:
            data_dict["status"] = [status_list[-1]]
    return render(
        request,
        template,
        {
            "leave_requests": leave_request_filter,
            "pd": previous_data,
            "filter_dict": data_dict,
            "field": field,
            "dashboard": request.GET.get("dashboard"),
            "requests_ids": requests_ids,
            "current_date": date.today(),
            "leave_requests_with_interview": leave_requests_with_interview,
        },
    )


@login_required
@hx_request_required
@manager_can_enter("leave.change_leaverequest")
def leave_request_update(request, id):
    """
    function used to update leave request.
    """
    leave_request = LeaveRequest.objects.get(id=id)
    if request.method == "POST":
        form = LeaveRequestUpdationForm(
            request.POST, request.FILES, instance=leave_request
        )
        form = choosesubordinates(request, form, "leave.add_leaverequest")
        if form.is_valid():
            leave_request = form.save(commit=False)
            save = True

            if save:
                leave_request.save()
                messages.success(request, _("Leave request is updated successfully.."))
                log_form_changes(
                    request.user,
                    "leave",
                    "Leave request updated",
                    form=form,
                    target=leave_request,
                )
                with contextlib.suppress(Exception):
                    notify.send(
                        request.user.employee_get,
                        recipient=leave_request.employee_id.employee_work_info.reporting_manager_id.employee_user_id,
                        verb=f"Leave request updated for {leave_request.employee_id}.",
                        verb_ar=f"تم تحديث طلب الإجازة لـ {leave_request.employee_id}.",
                        verb_de=f"Urlaubsantrag aktualisiert für {leave_request.employee_id}.",
                        verb_es=f"Solicitud de permiso actualizada para {leave_request.employee_id}.",
                        verb_fr=f"Demande de congé mise à jour pour {leave_request.employee_id}.",
                        icon="people-circle",
                        redirect=reverse("request-view") + f"?id={leave_request.id}",
                    )
                response = render(
                    request,
                    "leave/leave_request/request_update_form.html",
                    {"form": form, "id": id},
                )
                return HttpResponse(
                    response.content.decode("utf-8")
                    + "<script>location.reload();</script>"
                )
    else:
        form = LeaveRequestUpdationForm(instance=leave_request)
        form = choosesubordinates(request, form, "leave.add_leaverequest")

    return render(
        request,
        "leave/leave_request/request_update_form.html",
        {
            "form": form,
            "id": id,
        },
    )


@login_required
@hx_request_required
@manager_can_enter("leave.delete_leaverequest")
def leave_request_delete(request, id):
    """
    function used to delete leave request.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id : leave request id

    Returns:
    GET : return leave request view template
    """
    previous_data = request.GET.urlencode()
    try:
        leave_request = LeaveRequest.objects.get(id=id)
        # Snapshot before delete — the instance is unusable afterwards.
        audit_changes = _leave_request_details(leave_request)
        messages.success(request, _("Leave request deleted successfully.."))
        leave_request.delete()
        _leave_audit(request, "Leave request deleted", changes=audit_changes)
    except (LeaveRequest.DoesNotExist, OverflowError, ValueError):
        messages.error(request, _("Leave request not found."))
    except ProtectedError:
        messages.error(request, _("Related entries exists"))
    hx_target = request.META.get("HTTP_HX_TARGET", None)
    if hx_target == "leaveRequest":
        leave_requests = LeaveRequest.objects.all()
        if leave_requests.exists():
            return redirect(f"/leave/request-filter?{previous_data}")
        else:
            return HttpResponse("<script>window.location.reload();</script>")
    return redirect(leave_request_view)


@login_required
@manager_can_enter("leave.change_leaverequest")
def leave_request_approve(request, id, emp_id=None):
    """
    function used to approve a leave request.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id : leave request id
    emp_id : employee id if the approval operation comes from "/employee/employee-view/{employee_id}/" template.


    Returns:
    GET : If `emp_id` is provided, it returns to the "/employee/employee-view/{employee_id}/" template after approval.
          Otherwise, it returns to the default leave request view template.
    """
    leave_request = LeaveRequest.objects.get(id=id)
    employee_id = leave_request.employee_id
    leave_type_id = leave_request.leave_type_id
    available_leave = AvailableLeave.objects.get(
        leave_type_id=leave_type_id, employee_id=employee_id
    )
    total_available_leave = (
        available_leave.available_days + available_leave.carryforward_days
    )
    send_notification = False
    # Captured before any mutation so the audit entry can report the real
    # before/after status rather than the already-updated in-memory value.
    status_before = leave_request.get_status_display()
    balance_before = (
        f"{available_leave.available_days} available / "
        f"{available_leave.carryforward_days} carryforward"
    )
    approval_sequence = None
    if leave_request.status != "approved":
        # Prior- or future-year leaves must not deduct from the current year's
        # bucket — leave_taken() is year-bounded, so any deduction here would
        # not be added back to total_leaves and the current balance would
        # silently shrink. Skip the balance check too: approving retroactively
        # is a record-keeping action, not a draw against this year's pool.
        is_current_year = (
            leave_request.start_date is not None
            and leave_request.start_date.year == date.today().year
        )
        can_approve = (not is_current_year) or (
            total_available_leave >= leave_request.requested_days
        )
        if can_approve:
            if is_current_year:
                if leave_request.requested_days > available_leave.carryforward_days:
                    leave = leave_request.requested_days - available_leave.carryforward_days
                    leave_request.approved_carryforward_days = (
                        available_leave.carryforward_days
                    )
                    available_leave.carryforward_days = 0
                    available_leave.available_days = available_leave.available_days - leave
                    leave_request.approved_available_days = leave
                else:
                    temp = available_leave.carryforward_days
                    available_leave.carryforward_days = temp - leave_request.requested_days
                    leave_request.approved_carryforward_days = leave_request.requested_days
            else:
                # Prior/future-year: keep approved_* fields at 0 so a later
                # cancel doesn't inflate the current-year balance.
                leave_request.approved_available_days = 0
                leave_request.approved_carryforward_days = 0
            leave_request.status = "approved"
            leave_request.reviewed_by = getattr(request.user, "employee_get", None)
            leave_request.reviewed_at = timezone.now()
            if not leave_request.multiple_approvals():
                leave_request.save()
                available_leave.save()
                send_notification = True
            else:
                if request.user.is_superuser:
                    LeaveRequestConditionApproval.objects.filter(
                        leave_request_id=leave_request
                    ).update(is_approved=True)
                    leave_request.save()
                    available_leave.save()
                    send_notification = True
                else:
                    conditional_requests = leave_request.multiple_approvals()
                    approver = next(
                        (
                            manager
                            for manager in conditional_requests["managers"]
                            if manager == request.user.employee_get
                        ),
                        None,
                    )
                    condition_approval = LeaveRequestConditionApproval.objects.filter(
                        manager_id=approver, leave_request_id=leave_request
                    ).first()
                    condition_approval.is_approved = True
                    managers = []
                    for manager in conditional_requests["managers"]:
                        managers.append(manager.employee_user_id)
                    if len(managers) > condition_approval.sequence:
                        with contextlib.suppress(Exception):
                            notify.send(
                                request.user.employee_get,
                                recipient=managers[condition_approval.sequence],
                                verb="You have a new leave request to validate.",
                                verb_ar="لديك طلب إجازة جديد يجب التحقق منه.",
                                verb_de="Sie haben eine neue Urlaubsanfrage zur Validierung.",
                                verb_es="Tiene una nueva solicitud de permiso que debe validar.",
                                verb_fr="Vous avez une nouvelle demande de congé à valider.",
                                icon="people-circle",
                                redirect=f"/leave/request-view?id={leave_request.id}",
                            )

                    condition_approval.save()
                    approval_sequence = condition_approval.sequence
                    if approver == conditional_requests["managers"][-1]:
                        leave_request.save()
                        available_leave.save()
                        send_notification = True
            messages.success(request, _("Leave request approved successfully.."))
            if send_notification:
                audit_changes = _leave_request_details(leave_request)
                audit_changes.update(
                    _status_change(status_before, leave_request.get_status_display())
                )
                audit_changes["Balance"] = {
                    "from": balance_before,
                    "to": (
                        f"{available_leave.available_days} available / "
                        f"{available_leave.carryforward_days} carryforward"
                    ),
                }
                _leave_audit(
                    request,
                    "Leave request approved",
                    target=leave_request,
                    changes=audit_changes,
                )
            else:
                # Multi-level approval: this manager signed off but the request
                # stays unsaved (and therefore unapproved) until the final
                # approver in the chain acts, so report the persisted status.
                audit_changes = _leave_request_details(leave_request)
                audit_changes["Status"] = status_before
                if approval_sequence is not None:
                    audit_changes["Approval level"] = str(approval_sequence)
                _leave_audit(
                    request,
                    "Leave request approved at one level (awaiting further approval)",
                    target=leave_request,
                    changes=audit_changes,
                )
            if send_notification:
                with contextlib.suppress(Exception):
                    notify.send(
                        request.user.employee_get,
                        recipient=leave_request.employee_id.employee_user_id,
                        verb="Your Leave request has been approved",
                        verb_ar="تمت الموافقة على طلب الإجازة الخاص بك",
                        verb_de="Ihr Urlaubsantrag wurde genehmigt",
                        verb_es="Se ha aprobado su solicitud de permiso",
                        verb_fr="Votre demande de congé a été approuvée",
                        icon="people-circle",
                        redirect=reverse("user-request-view")
                        + f"?id={leave_request.id}",
                    )

                mail_thread = LeaveMailSendThread(
                    request, leave_request, type="approve"
                )
                mail_thread.start()
        else:
            messages.error(
                request,
                f"{employee_id} dont have enough leave days to approve the request..",
            )
    else:
        messages.error(request, _("Leave request already approved"))
    if emp_id is not None:
        employee_id = emp_id
        return redirect(f"/employee/employee-view/{employee_id}/")
    # Preserve the table state (page, sort column/direction and filters) when the
    # approval was triggered from the leave request list. The list partial is
    # loaded over htmx, so the browser referer does not carry that state; instead
    # the approve link forwards the current query string ("pd") which we use to
    # rebuild a stateful redirect back to the list.
    query = request.GET.urlencode()
    if query:
        redirect_url = f"{reverse('request-view')}?{query}"
    else:
        redirect_url = request.META.get("HTTP_REFERER", "/")
    return _trigger_leave_stats_refresh(
        HttpResponseRedirect(redirect_url),
        request,
    )


@login_required
@manager_can_enter("leave.change_leaverequest")
def leave_request_bulk_approve(request):
    if request.method == "POST":
        request_ids = request.POST.getlist("ids")
        for request_id in request_ids:
            try:
                leave_request = (
                    LeaveRequest.objects.get(id=int(request_id)) if request_id else None
                )
                if leave_request.status == "requested" and (
                    leave_request.start_date >= datetime.today().date()
                    or request.user.has_perm("leave.change_leaverequest")
                ):
                    leave_request_approve(request, leave_request.id)
                else:
                    if leave_request.status == "approved":
                        messages.info(
                            request,
                            _("{} {} request already approved").format(
                                leave_request.employee_id, leave_request.leave_type_id
                            ),
                        )
                    elif leave_request.start_date < datetime.today().date():
                        messages.warning(
                            request,
                            _("{} {} request date exceeded").format(
                                leave_request.employee_id, leave_request.leave_type_id
                            ),
                        )
                    else:
                        messages.warning(
                            request,
                            _("{} {} can't approve.").format(
                                leave_request.employee_id, leave_request.leave_type_id
                            ),
                        )
            except (ValueError, OverflowError, LeaveRequest.DoesNotExist):
                messages.error(request, _("Leave request not found"))
                pass
    return _trigger_leave_stats_refresh(
        HttpResponse("<script>window.location.reload();</script>")
    )


@login_required
@manager_can_enter("leave.change_leaverequest")
def leave_bulk_reject(request):
    request_ids = request.POST.getlist("request_ids")

    for request_id in request_ids:
        leave_request = (
            LeaveRequest.objects.get(id=int(request_id)) if request_id else None
        )
        leave_request_cancel(request, leave_request.id)

    return _trigger_leave_stats_refresh(
        HttpResponse("<script>window.location.reload();</script>")
    )


@login_required
@manager_can_enter("leave.change_leaverequest")
def leave_request_cancel(request, id, emp_id=None):
    """
    function used to Reject leave request.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id : leave request id
    emp_id : employee id if the cancel operation comes from "/employee/employee-view/{employee_id}/" template.

    Returns:
    GET : If `emp_id` is provided, it returns to the "/employee/employee-view/{employee_id}/" template after cancel.
          Otherwise, it returns to the default leave request view template.

    """
    form = RejectForm()
    if request.method == "POST":
        form = RejectForm(request.POST)
        if form.is_valid():
            leave_request = LeaveRequest.objects.get(id=id)
            employee_id = leave_request.employee_id
            leave_type_id = leave_request.leave_type_id
            available_leave = AvailableLeave.objects.get(
                leave_type_id=leave_type_id, employee_id=employee_id
            )
            if leave_request.status != "rejected":
                was_approved = leave_request.status == "approved"
                # Captured before mutation for the audit before/after values.
                status_before = leave_request.get_status_display()
                balance_before = (
                    f"{available_leave.available_days} available / "
                    f"{available_leave.carryforward_days} carryforward"
                )
                available_leave.available_days += leave_request.approved_available_days
                available_leave.carryforward_days += (
                    leave_request.approved_carryforward_days
                )
                leave_request.approved_available_days = 0
                leave_request.approved_carryforward_days = 0
                leave_request.status = "cancelled" if was_approved else "rejected"
                leave_request.leave_clashes_count = 0
                leave_request.reviewed_by = getattr(
                    request.user, "employee_get", None
                )
                leave_request.reviewed_at = timezone.now()
                if was_approved:
                    leave_request.cancellation_status = "approved"
                    leave_request.cancellation_note = form.cleaned_data["reason"]
                    leave_request.cancellation_reviewed_by = getattr(
                        request.user, "employee_get", None
                    )
                    leave_request.cancellation_reviewed_at = timezone.now()

                if leave_request.multiple_approvals() and not request.user.is_superuser:
                    conditional_requests = leave_request.multiple_approvals()
                    approver = [
                        manager
                        for manager in conditional_requests["managers"]
                        if manager.employee_user_id == request.user
                    ]
                    condition_approval = LeaveRequestConditionApproval.objects.filter(
                        manager_id=approver[0], leave_request_id=leave_request
                    ).first()
                    condition_approval.is_approved = False
                    condition_approval.is_rejected = True
                    condition_approval.save()

                leave_request.reject_reason = form.cleaned_data["reason"]
                leave_request.save()
                available_leave.save()
                comment = LeaverequestComment()
                comment.request_id = leave_request
                comment.employee_id = request.user.employee_get
                comment.comment = leave_request.reject_reason
                comment.save()

                if was_approved:
                    messages.success(
                        request, _("Leave request cancelled successfully..")
                    )
                    notify_verb = "Your approved leave request has been cancelled."
                    notify_verb_ar = "تم إلغاء طلب الإجازة المعتمد الخاص بك"
                    notify_verb_de = "Ihr genehmigter Urlaubsantrag wurde storniert"
                    notify_verb_es = "Su solicitud de permiso aprobada ha sido cancelada"
                    notify_verb_fr = "Votre demande de congé approuvée a été annulée"
                    mail_type = "cancel"
                else:
                    messages.success(
                        request, _("Leave request rejected successfully..")
                    )
                    notify_verb = "Your leave request has been rejected."
                    notify_verb_ar = "تم رفض طلب الإجازة الخاص بك"
                    notify_verb_de = "Ihr Urlaubsantrag wurde abgelehnt"
                    notify_verb_es = "Tu solicitud de permiso ha sido rechazada"
                    notify_verb_fr = "Votre demande de congé a été rejetée"
                    mail_type = "reject"

                audit_changes = _leave_request_details(leave_request)
                audit_changes.update(
                    _status_change(status_before, leave_request.get_status_display())
                )
                audit_changes["Reason"] = leave_request.reject_reason
                audit_changes["Balance"] = {
                    "from": balance_before,
                    "to": (
                        f"{available_leave.available_days} available / "
                        f"{available_leave.carryforward_days} carryforward"
                    ),
                }
                _leave_audit(
                    request,
                    "Leave request cancelled" if was_approved else "Leave request rejected",
                    target=leave_request,
                    changes=audit_changes,
                )

                with contextlib.suppress(Exception):
                    notify.send(
                        request.user.employee_get,
                        recipient=leave_request.employee_id.employee_user_id,
                        verb=notify_verb,
                        verb_ar=notify_verb_ar,
                        verb_de=notify_verb_de,
                        verb_es=notify_verb_es,
                        verb_fr=notify_verb_fr,
                        icon="people-circle",
                        redirect=reverse("user-request-view")
                        + f"?id={leave_request.id}",
                    )

                mail_thread = LeaveMailSendThread(
                    request, leave_request, type=mail_type
                )
                mail_thread.start()

                if leave_request.manager:
                    manager = leave_request.manager
                    if manager.employee_user_id and manager.employee_user_id.email:
                        LeaveMailSendThread(
                            request,
                            leave_request,
                            type="manager_reject_mail",
                        ).start()

            else:
                messages.error(request, _("Leave request already rejected."))

            if emp_id is not None:
                employee_id = emp_id
                return redirect(f"/employee/employee-view/{employee_id}/")
            # Preserve the table state (page, sort column/direction and filters).
            # The reject form posts back with the current query string ("pd"),
            # which we use to rebuild a stateful redirect to the list. Since this
            # is an htmx request, ``_trigger_leave_stats_refresh`` converts the
            # redirect into an ``HX-Redirect`` so the browser performs a full
            # navigation to the stateful URL.
            query = request.GET.urlencode()
            redirect_url = reverse("request-view")
            if query:
                redirect_url = f"{redirect_url}?{query}"
            return _trigger_leave_stats_refresh(
                HttpResponseRedirect(redirect_url), request
            )
    return render(
        request,
        "leave/leave_request/cancel_form.html",
        {"form": form, "id": id, "pd": request.GET.urlencode()},
    )


@login_required
@hx_request_required
def user_leave_cancel(request, id):
    """
    function used to cancel approved leave request by employee.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id : leave request id

    Returns:
    GET :  it returns to the default my leave request view template.

    """
    leave_request = LeaveRequest.objects.get(id=id)
    employee_id = leave_request.employee_id
    if employee_id.employee_user_id.id == request.user.id:
        current_date = date.today()
        if (
            leave_request.status == "approved"
            and leave_request.end_date >= current_date
        ):
            form = RejectForm()
            if request.method == "POST":
                form = RejectForm(request.POST)
                if form.is_valid():
                    status_before = leave_request.get_status_display()
                    leave_request.reject_reason = form.cleaned_data["reason"]
                    leave_request.status = "cancelled"
                    leave_request.reviewed_by = getattr(
                        request.user, "employee_get", None
                    )
                    leave_request.reviewed_at = timezone.now()
                    leave_request.save()

                    messages.success(
                        request, _("Leave request cancelled successfully..")
                    )
                    audit_changes = _leave_request_details(leave_request)
                    audit_changes.update(
                        _status_change(
                            status_before, leave_request.get_status_display()
                        )
                    )
                    audit_changes["Reason"] = leave_request.reject_reason
                    _leave_audit(
                        request,
                        "Leave request cancelled by employee",
                        target=leave_request,
                        changes=audit_changes,
                    )

                    mail_thread = LeaveMailSendThread(
                        request, leave_request, type="cancel"
                    )
                    mail_thread.start()
                    return _trigger_leave_stats_refresh(
                        HttpResponse("<script>location.reload();</script>")
                    )
            return render(
                request,
                "leave/leave_request/user_cancel_form.html",
                {"form": form, "id": id},
            )
        elif leave_request.status == "requested":
            status_before = leave_request.get_status_display()
            leave_request.status = "cancelled"
            leave_request.reviewed_by = getattr(request.user, "employee_get", None)
            leave_request.reviewed_at = timezone.now()
            leave_request.save()
            messages.success(request, _("Leave request cancelled successfully.."))
            audit_changes = _leave_request_details(leave_request)
            audit_changes.update(
                _status_change(status_before, leave_request.get_status_display())
            )
            _leave_audit(
                request,
                "Leave request cancelled by employee",
                target=leave_request,
                changes=audit_changes,
            )
            return _trigger_leave_stats_refresh(
                HttpResponse("<script>location.reload();</script>")
            )
        messages.error(request, _("You can't cancel this leave request."))
        return _trigger_leave_stats_refresh(
            HttpResponse("<script>location.reload();</script>")
        )
    messages.error(request, _("You don't have the permission."))
    return _trigger_leave_stats_refresh(
        HttpResponse("<script>location.reload();</script>")
    )


@login_required
@hx_request_required
def user_leave_cancellation_request(request, id):
    """
    Allows an employee to raise a cancellation request against their own
    already-approved leave, including leaves whose date has already passed.

    Instead of cancelling the leave directly, this records a cancellation
    request that HR/Admin will later review and either approve or reject.
    """
    leave_request = LeaveRequest.objects.get(id=id)
    if leave_request.employee_id.employee_user_id.id != request.user.id:
        messages.error(request, _("You don't have the permission."))
        return _trigger_leave_stats_refresh(
            HttpResponse("<script>location.reload();</script>")
        )
    if leave_request.status != "approved":
        messages.error(
            request,
            _("Only approved leave requests can be requested for cancellation."),
        )
        return _trigger_leave_stats_refresh(
            HttpResponse("<script>location.reload();</script>")
        )
    if leave_request.cancellation_status == "requested":
        messages.info(
            request, _("A cancellation request is already pending review.")
        )
        return _trigger_leave_stats_refresh(
            HttpResponse("<script>location.reload();</script>")
        )
    form = CancellationRequestForm()
    if request.method == "POST":
        form = CancellationRequestForm(request.POST)
        if form.is_valid():
            leave_request.cancellation_status = "requested"
            leave_request.cancellation_reason = form.cleaned_data["reason"]
            leave_request.cancellation_requested_at = timezone.now()
            # Reset any previous review details in case this is a re-request
            # after an earlier rejection.
            leave_request.cancellation_reviewed_by = None
            leave_request.cancellation_reviewed_at = None
            leave_request.cancellation_note = ""
            leave_request.save()

            comment = LeaverequestComment()
            comment.request_id = leave_request
            comment.employee_id = request.user.employee_get
            comment.comment = (
                _("Cancellation requested: ") + leave_request.cancellation_reason
            )
            comment.save()

            messages.success(
                request, _("Cancellation request submitted successfully.")
            )
            audit_changes = _leave_request_details(leave_request)
            audit_changes["Cancellation status"] = {
                "from": "None",
                "to": leave_request.get_cancellation_status_display(),
            }
            audit_changes["Reason"] = leave_request.cancellation_reason
            _leave_audit(
                request,
                "Leave cancellation requested",
                target=leave_request,
                changes=audit_changes,
            )
            with contextlib.suppress(Exception):
                LeaveMailSendThread(
                    request, leave_request, type="cancellation_request"
                ).start()
            return _trigger_leave_stats_refresh(
                HttpResponse("<script>location.reload();</script>")
            )
    return render(
        request,
        "leave/leave_request/cancellation_request_form.html",
        {"form": form, "id": id},
    )



@login_required
@manager_can_enter("leave.change_leaverequest")
def cancellation_request_approve(request, id):
    """
    HR/Admin approves an employee's leave cancellation request.

    Approving cancels the underlying leave and restores the employee's leave
    balance. A note is mandatory.
    """
    leave_request = LeaveRequest.objects.get(id=id)
    if leave_request.cancellation_status != "requested":
        messages.error(request, _("No pending cancellation request to approve."))
        return _trigger_leave_stats_refresh(
            HttpResponse("<script>location.reload();</script>"), request
        )
    form = CancellationReviewForm()
    if request.method == "POST":
        form = CancellationReviewForm(request.POST)
        if form.is_valid():
            note = form.cleaned_data["note"]
            employee_id = leave_request.employee_id
            leave_type_id = leave_request.leave_type_id
            available_leave = AvailableLeave.objects.get(
                leave_type_id=leave_type_id, employee_id=employee_id
            )
            # Captured before mutation for the audit before/after values.
            status_before = leave_request.get_status_display()
            cancellation_status_before = (
                leave_request.get_cancellation_status_display()
            )
            balance_before = (
                f"{available_leave.available_days} available / "
                f"{available_leave.carryforward_days} carryforward"
            )
            available_leave.available_days += leave_request.approved_available_days
            available_leave.carryforward_days += (
                leave_request.approved_carryforward_days
            )
            leave_request.approved_available_days = 0
            leave_request.approved_carryforward_days = 0
            leave_request.status = "cancelled"
            leave_request.leave_clashes_count = 0
            leave_request.reject_reason = note
            leave_request.reviewed_by = getattr(request.user, "employee_get", None)
            leave_request.reviewed_at = timezone.now()
            leave_request.cancellation_status = "approved"
            leave_request.cancellation_note = note
            leave_request.cancellation_reviewed_by = getattr(
                request.user, "employee_get", None
            )
            leave_request.cancellation_reviewed_at = timezone.now()
            leave_request.save()
            available_leave.save()

            comment = LeaverequestComment()
            comment.request_id = leave_request
            comment.employee_id = request.user.employee_get
            comment.comment = _("Cancellation approved: ") + note
            comment.save()

            messages.success(request, _("Leave cancellation request approved."))
            audit_changes = _leave_request_details(leave_request)
            audit_changes.update(
                _status_change(status_before, leave_request.get_status_display())
            )
            audit_changes["Cancellation status"] = {
                "from": cancellation_status_before,
                "to": leave_request.get_cancellation_status_display(),
            }
            audit_changes["Reason"] = leave_request.cancellation_reason
            audit_changes["Note"] = note
            audit_changes["Balance"] = {
                "from": balance_before,
                "to": (
                    f"{available_leave.available_days} available / "
                    f"{available_leave.carryforward_days} carryforward"
                ),
            }
            _leave_audit(
                request,
                "Leave cancellation request approved",
                target=leave_request,
                changes=audit_changes,
            )
            with contextlib.suppress(Exception):
                notify.send(
                    request.user.employee_get,
                    recipient=leave_request.employee_id.employee_user_id,
                    verb="Your leave cancellation request has been approved.",
                    verb_ar="تمت الموافقة على طلب إلغاء الإجازة الخاص بك",
                    verb_de="Ihr Antrag auf Stornierung des Urlaubs wurde genehmigt.",
                    verb_es="Tu solicitud de cancelación de permiso ha sido aprobada.",
                    verb_fr="Votre demande d'annulation de congé a été approuvée.",
                    icon="checkmark-circle",
                    redirect=reverse("user-request-view")
                    + f"?id={leave_request.id}",
                )
            with contextlib.suppress(Exception):
                LeaveMailSendThread(
                    request, leave_request, type="cancellation_approve"
                ).start()
            return _trigger_leave_stats_refresh(
                HttpResponse("<script>location.reload();</script>"), request
            )
    return render(
        request,
        "leave/leave_request/cancellation_review_form.html",
        {"form": form, "id": id, "action": "approve"},
    )


@login_required
@manager_can_enter("leave.change_leaverequest")
def cancellation_request_reject(request, id):
    """
    HR/Admin rejects an employee's leave cancellation request. The leave stays
    approved. A note is mandatory.
    """
    leave_request = LeaveRequest.objects.get(id=id)
    if leave_request.cancellation_status != "requested":
        messages.error(request, _("No pending cancellation request to reject."))
        return _trigger_leave_stats_refresh(
            HttpResponse("<script>location.reload();</script>"), request
        )
    form = CancellationReviewForm()
    if request.method == "POST":
        form = CancellationReviewForm(request.POST)
        if form.is_valid():
            note = form.cleaned_data["note"]
            cancellation_status_before = (
                leave_request.get_cancellation_status_display()
            )
            leave_request.cancellation_status = "rejected"
            leave_request.cancellation_note = note
            leave_request.cancellation_reviewed_by = getattr(
                request.user, "employee_get", None
            )
            leave_request.cancellation_reviewed_at = timezone.now()
            # Leave remains approved.
            leave_request.save()

            comment = LeaverequestComment()
            comment.request_id = leave_request
            comment.employee_id = request.user.employee_get
            comment.comment = _("Cancellation rejected: ") + note
            comment.save()

            messages.success(request, _("Leave cancellation request rejected."))
            audit_changes = _leave_request_details(leave_request)
            audit_changes["Cancellation status"] = {
                "from": cancellation_status_before,
                "to": leave_request.get_cancellation_status_display(),
            }
            audit_changes["Reason"] = leave_request.cancellation_reason
            audit_changes["Note"] = note
            _leave_audit(
                request,
                "Leave cancellation request rejected",
                target=leave_request,
                changes=audit_changes,
            )
            with contextlib.suppress(Exception):
                notify.send(
                    request.user.employee_get,
                    recipient=leave_request.employee_id.employee_user_id,
                    verb="Your leave cancellation request has been rejected.",
                    verb_ar="تم رفض طلب إلغاء الإجازة الخاص بك",
                    verb_de="Ihr Antrag auf Stornierung des Urlaubs wurde abgelehnt.",
                    verb_es="Tu solicitud de cancelación de permiso ha sido rechazada.",
                    verb_fr="Votre demande d'annulation de congé a été rejetée.",
                    icon="close-circle",
                    redirect=reverse("user-request-view")
                    + f"?id={leave_request.id}",
                )
            with contextlib.suppress(Exception):
                LeaveMailSendThread(
                    request, leave_request, type="cancellation_reject"
                ).start()
            return _trigger_leave_stats_refresh(
                HttpResponse("<script>location.reload();</script>"), request
            )
    return render(
        request,
        "leave/leave_request/cancellation_review_form.html",
        {"form": form, "id": id, "action": "reject"},
    )


@login_required
@hx_request_required
@manager_can_enter("leave.view_leaverequest")
def one_request_view(request, id):
    """
    function used to view one leave request.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return one leave request view template
    """
    leave_request = LeaveRequest.objects.get(id=id)
    context = {
        "leave_request": leave_request,
        "current_date": date.today(),
        "dashboard": request.GET.get("dashboard"),
    }
    requests_ids_json = request.GET.get("instances_ids")

    if requests_ids_json:
        requests_ids = json.loads(requests_ids_json)
        previous_id, next_id = closest_numbers(requests_ids, id)
        context["previous"] = previous_id
        context["next"] = next_id
        context["requests_ids"] = requests_ids_json
    return render(request, "leave/leave_request/one_request_view.html", context)


@login_required
@hx_request_required
@manager_can_enter("leave.add_availableleave")
def leave_assign_one(request, obj_id):
    """
    Assigns leave types to employees.

    Parameters:
    request (HttpRequest): The HTTP request object.
    obj_id: ID of the leave type.

    Returns:
    GET: Renders the leave type assignment form template.
    POST: Processes and assigns the leave type to selected employees.
    """
    form = LeaveOneAssignForm()
    form = choosesubordinates(request, form, "leave.add_availableleave")

    # Fetch the leave type
    leave_type = LeaveType.objects.filter(id=obj_id).first()
    if not leave_type:
        messages.error(request, _("Leave type not found."))
        return render(
            request,
            "leave/leave_assign/leave_assign_one_form.html",
            {"form": form, "id": obj_id},
        )

    if request.method == "POST":
        if leave_type.is_compensatory_leave:
            messages.info(
                request, _("Compensatory leave type cannot be assigned manually.")
            )
            return render(
                request,
                "leave/leave_assign/leave_assign_one_form.html",
                {"form": form, "id": obj_id},
            )

        employee_ids = list(map(int, request.POST.getlist("employee_id")))

        existing_leaves_set = set(
            AvailableLeave.objects.filter(
                leave_type_id=leave_type, employee_id__in=employee_ids
            ).values_list("employee_id", flat=True)
        )

        expiry_date = (
            leave_type.carryforward_expire_date
            if leave_type.carryforward_expire_date
            else None
        )
        new_employees = list(set(employee_ids) - existing_leaves_set)

        assigned_count = 0
        if new_employees:
            available_leaves = []
            for employee_id in new_employees:
                leave = AvailableLeave(
                    leave_type_id=leave_type,
                    employee_id_id=employee_id,
                    available_days=leave_type.total_days,
                )
                if leave.reset_date is None:
                    if leave_type.reset:
                        leave.reset_date = leave.set_reset_date(
                            assigned_date=leave.assigned_date, available_leave=leave
                        )

                if leave_type.carryforward_type == "carryforward expire":
                    if not expiry_date:
                        expiry_date = leave.assigned_date
                    leave.expired_date = expiry_date

                leave.total_leave_days = max(
                    leave.available_days + leave.carryforward_days, 0
                )
                leave.carryforward_days = max(leave.carryforward_days, 0)
                available_leaves.append(leave)

            AvailableLeave.objects.bulk_create(available_leaves)
            assigned_count = len(available_leaves)

            messages.success(
                request,
                _("Successfully assigned leave type to {} employees.").format(
                    assigned_count
                ),
            )
            form = LeaveOneAssignForm()

            employees = Employee.objects.filter(id__in=new_employees).only(
                "id", "employee_user_id"
            )
            _leave_audit(
                request,
                "Leave type assigned to employees",
                target=leave_type,
                changes={
                    "Leave type": str(leave_type),
                    "Employees": ", ".join(str(emp) for emp in employees),
                    "Count": str(assigned_count),
                    "Days allocated": str(leave_type.total_days),
                },
            )
            notifications = [
                notify.send(
                    request.user.employee_get,
                    recipient=employee.employee_user_id,
                    verb="New leave type is assigned to you",
                    verb_ar="تم تعيين نوع إجازة جديد لك",
                    verb_de="Ihnen wurde ein neuer Urlaubstyp zugewiesen",
                    verb_es="Se le ha asignado un nuevo tipo de permiso",
                    verb_fr="Un nouveau type de congé vous a été attribué",
                    icon="people-circle",
                    redirect=reverse("user-request-view"),
                )
                for employee in employees
            ]

        if len(employee_ids) != assigned_count:
            messages.info(
                request,
                _(
                    "Leave type is already assigned to some selected {} employees."
                ).format(len(employee_ids) - assigned_count),
            )

    return render(
        request,
        "leave/leave_assign/leave_assign_one_form.html",
        {"form": form, "id": obj_id},
    )


@login_required
@manager_can_enter("leave.view_availableleave")
def leave_assign_view(request):
    """
    Function to view assigned employee leaves.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return leave assigned view template
    """
    queryset = filtersubordinates(
        request, AvailableLeave.objects.all(), "leave.view_availableleave"
    )
    previous_data = request.GET.urlencode() or "field=leave_type_id"
    field = request.GET.get("field", "leave_type_id")
    page_number = request.GET.get("page")

    # Paginate and group queryset by field
    page_obj = group_by_queryset(queryset.order_by("-id"), field, page_number)
    available_leave_ids = json.dumps(
        [instance.id for entry in page_obj for instance in entry["list"].object_list]
    )

    # Setting a condition for the template
    request.GET = request.GET.copy()
    request.GET["field"] = True

    initial_filter_dict = parse_qs(previous_data)
    initial_filter_dict.pop("field", None)

    return render(
        request,
        "leave/leave_assign/assign_view.html",
        {
            "available_leaves": page_obj,
            "f": AssignedLeaveFilter(),
            "pd": previous_data,
            "filter_dict": initial_filter_dict,
            "gp_fields": LeaveAssignReGroup.fields,
            "assign_form": AssignLeaveForm(),
            "available_leave_ids": available_leave_ids,
        },
    )


@login_required
@hx_request_required
@manager_can_enter("leave.view_availableleave")
def available_leave_single_view(request, obj_id):
    get_data = request.GET.copy()
    get_data.pop("instances_ids", None)
    previous_data = get_data.urlencode()

    available_leave = AvailableLeave.objects.filter(id=obj_id).first()
    instance_ids = json.loads(request.GET.get("instances_ids", "[]"))
    previous_instance, next_instance = (
        closest_numbers(instance_ids, obj_id) if instance_ids else (None, None)
    )

    content = {
        "available_leave": available_leave,
        "previous_instance": previous_instance,
        "next_instance": next_instance,
        "instance_ids_json": json.dumps(instance_ids),
        "pd": previous_data,
    }
    return render(
        request, "leave/leave_assign/single_assign_view.html", context=content
    )


@login_required
@hx_request_required
@manager_can_enter("leave.view_availableleave")
def leave_assign_filter(request):
    """
    function used to filter assign leave type to employees.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return leave type assigned view template
    """
    queryset = AvailableLeave.objects.all()
    assign_form = AssignLeaveForm()
    queryset = filtersubordinates(request, queryset, "leave.view_availableleave")
    assigned_leave_filter = AssignedLeaveFilter(request.GET, queryset).qs
    previous_data = request.GET.urlencode()
    field = request.GET.get("field") or "leave_type_id"
    page_number = request.GET.get("page")
    template = ("leave/leave_assign/assigned_leave.html",)
    available_leaves = assigned_leave_filter.order_by("-id")
    if request.GET.get("sortby"):
        available_leaves = sortby(request, available_leaves, "sortby")
        available_leave_ids = json.dumps(
            [instance.id for instance in paginator_qry(available_leaves, None)]
        )
    if field != "" and field is not None:
        page_obj = group_by_queryset(available_leaves, field, page_number)
        list_values = [entry["list"] for entry in page_obj]
        id_list = []
        for value in list_values:
            for instance in value.object_list:
                id_list.append(instance.id)
        available_leave_ids = json.dumps(list(id_list))
        template = "leave/leave_assign/group_by.html"
    else:
        available_leave_ids = json.dumps(
            [instance.id for instance in paginator_qry(available_leaves, None)]
        )
        page_obj = paginator_qry(available_leaves, page_number)

    data_dict = parse_qs(previous_data)
    data_dict.pop("field", None)
    get_key_instances(AvailableLeave, data_dict)
    return render(
        request,
        template,
        {
            "available_leaves": page_obj,
            "pd": previous_data,
            "filter_dict": data_dict,
            "field": field,
            "assign_form": assign_form,
            "available_leave_ids": available_leave_ids,
        },
    )


@login_required
@hx_request_required
@manager_can_enter("leave.add_availableleave")
def leave_assign(request):
    """
    Function to assign multiple leave types to employees.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET: Render the leave assign form template.
    POST: Handle the leave type assignment.
    """
    form = AssignLeaveForm()
    form = choosesubordinates(request, form, "leave.add_availableleave")
    page_reload = AvailableLeave.objects.count() == 0

    if request.method == "POST":
        leave_type_ids = request.POST.getlist("leave_type_id")
        employee_ids = request.POST.getlist("employee_id")

        if leave_type_ids and employee_ids:
            leave_types = LeaveType.objects.filter(id__in=leave_type_ids).exclude(
                is_compensatory_leave=True
            )
            employees = Employee.objects.filter(id__in=employee_ids)

            existing_assignments = set(
                AvailableLeave.objects.filter(
                    leave_type_id__in=leave_type_ids, employee_id__in=employee_ids
                ).values_list("leave_type_id", "employee_id")
            )

            new_assignments = []
            success_messages = set()
            info_messages = set()

            for employee in employees:
                for leave_type in leave_types:
                    assignment_key = (leave_type.id, employee.id)
                    if assignment_key not in existing_assignments:
                        new_assignment = AvailableLeave(
                            leave_type_id=leave_type,
                            employee_id=employee,
                            available_days=leave_type.total_days,
                        )
                        new_assignments.append(new_assignment)
                        new_assignment.pre_save_processing()
                        success_messages.add(employee.employee_user_id)
                    else:
                        info_messages.add(employee.employee_user_id)

            # Bulk create new assignments
            if new_assignments:
                with transaction.atomic():
                    AvailableLeave.objects.bulk_create(new_assignments)
                    for user_id in success_messages:
                        with contextlib.suppress(Exception):
                            notify.send(
                                request.user.employee_get,
                                recipient=user_id,
                                verb="New leave type is assigned to you",
                                verb_ar="تم تعيين نوع إجازة جديد لك",
                                verb_de="Dir wurde ein neuer Urlaubstyp zugewiesen",
                                verb_es="Se te ha asignado un nuevo tipo de permiso",
                                verb_fr="Un nouveau type de congé vous a été attribué",
                                icon="people-circle",
                                redirect=reverse("user-request-view"),
                            )
                    messages.success(request, _("Leave types assigned successfully."))
                    # new_assignments holds one row per (employee, leave type)
                    # pair, so de-duplicate the employee names for readability.
                    assigned_employees = dict.fromkeys(
                        str(assignment.employee_id)
                        for assignment in new_assignments
                    )
                    _leave_audit(
                        request,
                        "Leave types assigned to employees",
                        changes={
                            "Leave types": ", ".join(str(lt) for lt in leave_types),
                            "Employees": ", ".join(assigned_employees),
                            "Assignments created": str(len(new_assignments)),
                        },
                    )

            if info_messages:
                messages.info(
                    request,
                    _("Some leave types were already assigned to {} employees.").format(
                        len(info_messages)
                    ),
                )

        if page_reload:
            return HttpResponse("<script>window.location.reload()</script>")

    return render(
        request, "leave/leave_assign/leave_assign_form.html", {"assign_form": form}
    )


@login_required
@hx_request_required
@manager_can_enter("leave.change_availableleave")
def available_leave_update(request, id):
    """
    function used to update available leave of an assigned leave type of an employee.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id : available leave id

    Returns:
    GET : return available leave update form template
    POST : return leave type assigned  view
    """
    leave_assign = AvailableLeave.objects.get(id=id)
    form = AvailableLeaveUpdateForm(instance=leave_assign)
    previous_data = request.GET.urlencode() or "field=leave_type_id"
    if request.method == "POST":
        form = AvailableLeaveUpdateForm(request.POST, instance=leave_assign)
        if form.is_valid():
            # Snapshot the balance before saving so the audit entry can show the
            # adjustment as before -> after alongside the employee/leave type.
            balance_before = {
                "Available days": str(leave_assign.available_days),
                "Carryforward days": str(leave_assign.carryforward_days),
                "Total leave days": str(leave_assign.total_leave_days),
            }
            available_leave = form.save()
            messages.success(request, _("Available leaves updated successfully..."))
            audit_changes = {
                "Employee": str(available_leave.employee_id),
                "Leave type": str(available_leave.leave_type_id),
            }
            balance_after = {
                "Available days": str(available_leave.available_days),
                "Carryforward days": str(available_leave.carryforward_days),
                "Total leave days": str(available_leave.total_leave_days),
            }
            for label, before in balance_before.items():
                after = balance_after[label]
                if before != after:
                    audit_changes[label] = {"from": before, "to": after}
            _leave_audit(
                request,
                "Leave balance adjusted",
                target=available_leave,
                changes=audit_changes,
            )
            with contextlib.suppress(Exception):
                notify.send(
                    request.user.employee_get,
                    recipient=available_leave.employee_id.employee_user_id,
                    verb=f"Your {available_leave.leave_type_id} leave type updated.",
                    verb_ar=f"تم تحديث نوع الإجازة {available_leave.leave_type_id} الخاص بك.",
                    verb_de=f"Ihr Urlaubstyp {available_leave.leave_type_id} wurde aktualisiert.",
                    verb_es=f"Se ha actualizado su tipo de permiso {available_leave.leave_type_id}.",
                    verb_fr=f"Votre type de congé {available_leave.leave_type_id} a été mis à jour.",
                    icon="people-circle",
                    redirect=reverse("user-request-view"),
                )
    return render(
        request,
        "leave/leave_assign/available_update_form.html",
        {"form": form, "id": id, "pd": previous_data},
    )


@login_required
@hx_request_required
@manager_can_enter("leave.delete_availableleave")
def leave_assign_delete(request, obj_id):
    """
    Function to delete an assigned leave type of an employee.

    Parameters:
    - request (HttpRequest): The HTTP request object.
    - obj_id (int): Available leave ID.

    Returns:
    - Redirects to the assigned leave type view or refreshes the page.
    """
    pd = request.GET.urlencode()

    try:
        assigned_leave = AvailableLeave.objects.get(id=obj_id)
        # Snapshot before delete — the instance is unusable afterwards.
        audit_changes = _available_leave_details(assigned_leave)
        assigned_leave.delete()
        messages.success(request, _("Assigned leave successfully deleted."))
        _leave_audit(request, "Assigned leave deleted", changes=audit_changes)
    except AvailableLeave.DoesNotExist:
        messages.error(request, _("Assigned leave not found."))
    except ProtectedError:
        messages.error(request, _("Related entries exist."))

    if instances_ids := request.GET.get("instances_ids"):
        instances_list = json.loads(instances_ids)
        previous_instance, next_instance = closest_numbers(instances_list, obj_id)
        if obj_id in instances_list:
            instances_list.remove(obj_id)
        return redirect(
            f"/leave/available-leave-single-view/{next_instance}/?{pd}&instances_ids={json.dumps(instances_list)}"
        )

    if not AvailableLeave.objects.exists():
        return HttpResponse("<script>window.location.reload()</script>")
    return redirect(f"/leave/assign-filter?{pd}")


@require_http_methods(["POST"])
@permission_required("leave.delete_availableleave")
def leave_assign_bulk_delete(request):
    """
    This method is used to delete bulk of assigned leaves
    """
    ids = request.POST["ids"]
    ids = json.loads(ids)
    count = 0
    deleted = []
    for assigned_leave_id in ids:
        try:
            assigned_leave = AvailableLeave.objects.get(id=assigned_leave_id)
            # Describe before delete — the instance is unusable afterwards.
            summary = (
                f"{assigned_leave.employee_id} / {assigned_leave.leave_type_id} "
                f"({assigned_leave.total_leave_days} days)"
            )
            assigned_leave.delete()
            deleted.append(summary)
            count += 1
        except Exception as e:
            messages.error(request, _("Assigned leave not found."))
    messages.success(
        request, _("{} assigned leaves deleted successfully ").format(count)
    )
    if deleted:
        _leave_audit(
            request,
            "Assigned leaves bulk deleted",
            changes={"Count": str(count), "Assigned leaves": ", ".join(deleted)},
        )
    return JsonResponse({"message": "Success"})


def assign_leave_type_excel(_request):
    """
    Generate an empty Excel template for asisgn leave type to employee with predefined columns.

    Returns:
        HttpResponse: An HTTP response containing an empty Excel template with predefined columns.
    """
    try:
        columns = [
            "Employee Badge ID",
            "Leave Type",
        ]
        data_frame = pd.DataFrame(columns=columns)
        response = HttpResponse(content_type="application/ms-excel")
        response["Content-Disposition"] = (
            'attachment; filename="assign_leave_type_excel.xlsx"'
        )
        data_frame.to_excel(response, index=False)
        _leave_audit(
            _request,
            "Assigned leave import template downloaded",
            changes={"File": "assign_leave_type_excel.xlsx"},
        )
        return response
    except Exception as exception:
        return HttpResponse(exception)


@login_required
@manager_can_enter("leave.add_availableleave")
def assign_leave_type_import(request):
    """
    This function accepts a POST request containing an Excel file with assigned leave type to employee data.
    It processes the data, checks for errors, and either assigns leave types to employees
    or generates an error report in the form of an Excel file.
    """
    error_data = {
        "Employee Badge ID": [],
        "Leave Type": [],
        "Badge ID Error": [],
        "Leave Type Error": [],
        "Assigned Error": [],
        "Available Days": [],
        "Carry Forward Days": [],
        "Other Errors": [],
    }

    if request.method == "POST":
        file = request.FILES["assign_leave_type_import"]
        data_frame = pd.read_excel(file)
        assign_leave_dicts = data_frame.to_dict("records")

        # Pre-fetch all employees and leave types
        employees = {
            emp.badge_id.lower(): emp for emp in Employee.objects.all() if emp.badge_id
        }
        leave_types = {lt.name.lower(): lt for lt in LeaveType.objects.all()}
        available_leaves = {
            (al.leave_type_id.id, al.employee_id.id): al
            for al in AvailableLeave.objects.all()
        }

        assign_leave_list = []
        error_list = []

        for assign_leave in assign_leave_dicts:
            badge_id = assign_leave.get("Employee Badge ID", "").strip().lower()
            assign_leave_type = assign_leave.get("Leave Type", "").strip().lower()
            available_days = assign_leave.get("Available Days", "0")
            cfd = assign_leave.get("Carry Forward Days", "0")
            employee = employees.get(badge_id)
            leave_type = leave_types.get(assign_leave_type)

            errors = []
            if employee is None:
                errors.append(_("This badge id does not exist."))
            if leave_type is None:
                errors.append(_("This leave type does not exist."))
            if leave_type is not None and leave_type.is_compensatory_leave:
                errors.append(
                    _("Compensatory leave type cannot be assigned manually.")
                )
            if errors:
                assign_leave[
                    "Badge ID Error" if "badge id" in errors[0] else "Leave Type Error"
                ] = " ".join(force_str(error) for error in errors)
                error_list.append(assign_leave)
                continue

            # Check if leave type has already been assigned to the employee
            if (leave_type.id, employee.id) in available_leaves:
                assign_leave["Assigned Error"] = _(
                    "Leave type has already been assigned to the employee."
                )
                error_list.append(assign_leave)
                continue

            # If no errors, create the AvailableLeave instance
            if available_days == 0:
                available_days = leave_type.total_days

            available_leave = AvailableLeave(
                leave_type_id=leave_type,
                employee_id=employee,
                available_days=available_days,
            )
            if cfd:
                available_leave.carryforward_days = cfd
                available_leave.expired_date = leave_type.carryforward_expire_date
                try:
                    available_leave.reset_date = leave_type.leave_type_next_reset_date()
                except:
                    pass
                available_leave.assigned_date = datetime.today()
                available_leave.total_leave_days = (
                    available_leave.carryforward_days + available_leave.available_days
                )
            assign_leave_list.append(available_leave)

        # Bulk create available leaves
        if assign_leave_list:
            AvailableLeave.objects.bulk_create(assign_leave_list)

        # Generate error report if there are errors
        path_info = None
        if error_list:
            path_info = generate_error_report(
                error_list, error_data, "AssignLeaveError.xlsx"
            )

        context = {
            "created_count": len(assign_leave_dicts) - len(error_list),
            "error_count": len(error_list),
            "model": _("Assigned Leaves"),
            "path_info": path_info,
        }
        _leave_audit(
            request,
            "Assigned leaves imported",
            changes={
                "File": getattr(file, "name", ""),
                "Rows in file": str(len(assign_leave_dicts)),
                "Assignments created": str(len(assign_leave_list)),
                "Rows rejected": str(len(error_list)),
            },
        )
        html = render_to_string("import_popup.html", context)
        return HttpResponse(html)


@login_required
def assigned_leaves_export(request):
    hx_request = request.META.get("HTTP_HX_REQUEST")
    if hx_request:
        export_filter = AssignedLeaveFilter()
        export_column = AvailableLeaveColumnExportForm()
        content = {
            "export_filter": export_filter,
            "export_column": export_column,
        }
        return render(
            request,
            "leave/leave_assign/assigned_leaves_export_form.html",
            context=content,
        )
    # Only the non-htmx branch actually streams a file back; the htmx branch
    # above just renders the column/filter picker.
    _leave_audit(
        request,
        "Assigned leaves exported",
        changes={
            "File": "Assign_Leave.xlsx",
            "Filters": request.GET.urlencode() or "None",
        },
    )
    return export_data(
        request=request,
        model=AvailableLeave,
        filter_class=AssignedLeaveFilter,
        form_class=AvailableLeaveColumnExportForm,
        file_name="Assign_Leave",
        perm="leave.view_availableleave",
    )


@login_required
@hx_request_required
def get_job_positions(request):
    department_id = request.GET.get("department")
    form = RestrictLeaveForm()
    form.fields["job_position"].queryset = JobPosition.objects.filter(
        department_id=department_id
    )

    return render(request, "leave/job_position_field.html", {"form": form})


@login_required
@hx_request_required
@permission_required("leave.add_restrictleave")
def restrict_creation(request):
    """
    function used to create restricted days.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return restricted days creation form template
    POST : return restricted days view template
    """

    query_string = request.GET.urlencode()
    if query_string.startswith("pd="):
        previous_data = unquote(query_string[len("pd=") :])
    else:
        previous_data = unquote(query_string)
    form = RestrictLeaveForm()

    if request.method == "POST":
        form = RestrictLeaveForm(request.POST)
        if form.is_valid():
            form.save()
            form = RestrictLeaveForm()
            messages.success(request, _("Restricted day created successfully.."))
            if RestrictLeave.objects.filter().count() == 1:
                return HttpResponse("<script>window.location.reload();</script>")
    return render(
        request,
        "leave/restrict/restrict_form.html",
        {"form": form, "pd": previous_data},
    )


@login_required
def restrict_view(request):
    """
    function used to view restricted days.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return restricted days view  template
    """
    queryset = RestrictLeave.objects.all()[::-1]
    previous_data = request.GET.urlencode()
    page_number = request.GET.get("page")
    page_obj = paginator_qry(queryset, page_number)
    restrictday_filter = RestrictLeaveFilter()
    return render(
        request,
        "leave/restrict/view_restrict.html",
        {
            "restrictday": page_obj,
            "form": restrictday_filter.form,
            "pd": previous_data,
        },
    )


@login_required
@hx_request_required
def restrict_filter(request):
    """
    function used to filter restricted days.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return restricted days view template
    """
    queryset = RestrictLeave.objects.all()
    previous_data = request.GET.urlencode()
    restrictday_filter = RestrictLeaveFilter(request.GET, queryset).qs
    if request.GET.get("sortby"):
        restrictday_filter = sortby(request, restrictday_filter, "sortby")
    page_number = request.GET.get("page")
    page_obj = paginator_qry(restrictday_filter[::-1], page_number)
    data_dict = parse_qs(previous_data)
    get_key_instances(RestrictLeave, data_dict)
    return render(
        request,
        "leave/restrict/restrict.html",
        {"restrictday": page_obj, "pd": previous_data, "filter_dict": data_dict},
    )


@login_required
@hx_request_required
@permission_required("leave.change_restrictleave")
def restrict_update(request, id):
    """
    function used to update restricted days.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id : restricted days id

    Returns:
    GET : return restricted days update form template
    POST : return restricted days view template
    """
    query_string = request.GET.urlencode()
    if query_string.startswith("pd="):
        previous_data = unquote(query_string[len("pd=") :])
    else:
        previous_data = unquote(query_string)
    restrictday = RestrictLeave.objects.get(id=id)
    form = RestrictLeaveForm(instance=restrictday)
    if request.method == "POST":
        form = RestrictLeaveForm(request.POST, instance=restrictday)
        if form.is_valid():
            form.save()
            messages.success(request, _("Restricted day updated successfully.."))
    return render(
        request,
        "leave/restrict/restrict_update_form.html",
        {"form": form, "id": id, "pd": previous_data},
    )


@login_required
@hx_request_required
@permission_required("leave.delete_restrictleave")
def restrict_delete(request, id):
    """
    function used to delete restricted days.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id : restricted days id

    Returns:
    GET : return restricted days view template
    """
    query_string = request.GET.urlencode()
    try:
        RestrictLeave.objects.get(id=id).delete()
        messages.success(request, _("Restricted day deleted successfully.."))
    except RestrictLeave.DoesNotExist:
        messages.error(request, _("Restricted day not found."))
    except ProtectedError:
        messages.error(request, _("Related entries exists"))
    if not RestrictLeave.objects.filter():
        return HttpResponse("<script>window.location.reload();</script>")
    return redirect(f"/leave/restrict-filter?{query_string}")


@login_required
@hx_request_required
@permission_required("leave.delete_restrictleave")
def restrict_days_bulk_delete(request):
    """
    function used to delete multiple restricted days.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return restricted days view template
    """
    pd = request.GET.urlencode()
    if request.method == "POST":
        restrict_day_ids = request.POST.getlist("ids")
        try:
            restrict_days = RestrictLeave.objects.filter(
                id__in=restrict_day_ids
            ).delete()
            count = len(restrict_day_ids)
            messages.success(
                request,
                _("{} Leave restricted days deleted successfully").format(count),
            )
        except (OverflowError, ValueError):
            messages.error(request, _("Restricted Days not found"))
        except:
            messages.error(request, _("Something went wrong"))
    return redirect(f"/leave/restrict-filter?{pd}")


@login_required
@permission_required("leave.add_restrictleave")
def restrict_day_select(request):
    page_number = request.GET.get("page")
    if page_number == "all":
        restrict_days = RestrictLeave.objects.all()
    restrict_day_ids = [str(day.id) for day in restrict_days]
    total_count = len(restrict_day_ids)
    context = {"restrict_day_ids": restrict_day_ids, "total_count": total_count}
    return JsonResponse(context)


@login_required
@permission_required("leave.add_restrictleave")
def restrict_day_select_filter(request):
    page_number = request.GET.get("page")
    filtered = request.GET.get("filter")
    filters = json.loads(filtered) if filtered else {}

    if page_number == "all":
        restrictday_filter = RestrictLeaveFilter(
            filters, queryset=RestrictLeave.objects.all()
        )
        restrictday_filter = restrictday_filter.qs
        restrictday_ids = [str(restrictday.id) for restrictday in restrictday_filter]
        total_count = restrictday_filter.count()
        context = {"restrict_day_ids": restrictday_ids, "total_count": total_count}
        return JsonResponse(context)


@login_required
@hx_request_required
def user_leave_request(request, id):
    """
    function used to create user leave request.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id : leave type id

    Returns:
    GET : return user leave request creation form template
    POST : user my leave view template
    """
    previous_data = unquote(request.GET.urlencode())[len("pd=") :]
    employee = request.user.employee_get
    leave_type = LeaveType.objects.get(id=id)
    form = UserLeaveRequestForm(
        initial={"employee_id": employee, "leave_type_id": leave_type}
    )
    if request.method == "POST":
        if company_selection_required(request):
            messages.error(
                request,
                _(
                    "You are associated with multiple companies. Please select "
                    "your company before applying for a leave request."
                ),
            )
            return HttpResponse("<script>window.location.reload();</script>")
        form = UserLeaveRequestForm(request.POST, request.FILES, employee=employee)
        start_date = datetime.strptime(request.POST.get("start_date"), "%Y-%m-%d")
        end_date = datetime.strptime(request.POST.get("end_date"), "%Y-%m-%d")
        start_date_breakdown = request.POST.get("start_date_breakdown")
        end_date_breakdown = request.POST.get("end_date_breakdown")
        available_leave = AvailableLeave.objects.get(
            employee_id=employee, leave_type_id=leave_type
        )
        available_total_leave = (
            available_leave.available_days + available_leave.carryforward_days
        )
        requested_days = calculate_requested_days(
            start_date, end_date, start_date_breakdown, end_date_breakdown
        )
        requested_dates = leave_requested_dates(start_date, end_date)
        requested_dates = [date.date() for date in requested_dates]
        holidays = Holidays.objects.all()
        holiday_dates = holiday_dates_list(holidays)
        company_leaves = CompanyLeaves.objects.all()
        company_leave_dates = company_leave_dates_list(company_leaves, start_date)
        if (
            leave_type.exclude_company_leave == "yes"
            and leave_type.exclude_holiday == "yes"
        ):
            total_leaves = list(set(holiday_dates + company_leave_dates))
            total_leave_count = sum(
                requested_date in total_leaves for requested_date in requested_dates
            )
            requested_days = requested_days - total_leave_count
        else:
            holiday_count = 0
            if leave_type.exclude_holiday == "yes":
                for requested_date in requested_dates:
                    if requested_date in holiday_dates:
                        holiday_count += 1
                requested_days = requested_days - holiday_count
            if leave_type.exclude_company_leave == "yes":
                company_leave_count = sum(
                    requested_date in company_leave_dates
                    for requested_date in requested_dates
                )
                requested_days = requested_days - company_leave_count

        if form.is_valid():
            leave_request = form.save(commit=False)
            save = True
            leave_request.leave_type_id = leave_type
            leave_request.employee_id = employee

            if leave_request.leave_type_id.require_approval == "no":
                employee_id = leave_request.employee_id
                leave_type_id = leave_request.leave_type_id
                available_leave = AvailableLeave.objects.get(
                    leave_type_id=leave_type_id, employee_id=employee_id
                )
                # See approve view: skip current-year deduction for prior/
                # future-year leaves, otherwise the balance shrinks silently.
                if (
                    leave_request.start_date
                    and leave_request.start_date.year == date.today().year
                ):
                    if leave_request.requested_days > available_leave.available_days:
                        leave = (
                            leave_request.requested_days - available_leave.available_days
                        )
                        leave_request.approved_available_days = (
                            available_leave.available_days
                        )
                        available_leave.available_days = 0
                        available_leave.carryforward_days = (
                            available_leave.carryforward_days - leave
                        )
                        leave_request.approved_carryforward_days = leave
                    else:
                        available_leave.available_days = (
                            available_leave.available_days - leave_request.requested_days
                        )
                        leave_request.approved_available_days = leave_request.requested_days
                else:
                    # Prior/future-year: keep approved_* fields at 0 so a later
                    # cancel doesn't inflate the current-year balance.
                    leave_request.approved_available_days = 0
                    leave_request.approved_carryforward_days = 0
                leave_request.status = "approved"
                leave_request.reviewed_by = employee
                leave_request.reviewed_at = timezone.now()
                available_leave.save()
            if save:
                leave_request.created_by = employee
                leave_request.save()

                if multiple_approvals_check(leave_request.id):
                    conditional_requests = multiple_approvals_check(leave_request.id)
                    managers = []
                    for manager in conditional_requests["managers"]:
                        managers.append(manager.employee_user_id)
                    with contextlib.suppress(Exception):
                        notify.send(
                            request.user.employee_get,
                            recipient=managers[0],
                            verb="You have a new leave request to validate.",
                            verb_ar="لديك طلب إجازة جديد يجب التحقق منه.",
                            verb_de="Sie haben eine neue Urlaubsanfrage zur Validierung.",
                            verb_es="Tiene una nueva solicitud de permiso que debe validar.",
                            verb_fr="Vous avez une nouvelle demande de congé à valider.",
                            icon="people-circle",
                            redirect=f"/leave/request-view?id={leave_request.id}",
                        )
                mail_thread = LeaveMailSendThread(
                    request, leave_request, type="request"
                )
                mail_thread.start()
                messages.success(request, _("Leave request created successfully.."))
                audit_changes = _leave_request_details(leave_request)
                audit_changes["Reason"] = leave_request.description
                if leave_request.leave_type_id.require_approval == "no":
                    audit_changes["Auto-approved"] = (
                        "Leave type does not require approval"
                    )
                _leave_audit(
                    request,
                    "Leave applied",
                    target=leave_request,
                    changes=audit_changes,
                )
                with contextlib.suppress(Exception):
                    notify.send(
                        request.user.employee_get,
                        recipient=leave_request.employee_id.employee_work_info.reporting_manager_id.employee_user_id,
                        verb="You have a new leave request to validate.",
                        verb_ar="لديك طلب إجازة جديد يجب التحقق منه.",
                        verb_de="Sie haben eine neue Urlaubsanfrage zur Validierung.",
                        verb_es="Tiene una nueva solicitud de permiso que debe validar.",
                        verb_fr="Vous avez une nouvelle demande de congé à valider.",
                        icon="people-circle",
                        redirect=reverse("request-view") + f"?id={leave_request.id}",
                    )
                if len(
                    LeaveRequest.objects.filter(employee_id=employee)
                ) == 1 or request.META.get("HTTP_REFERER").endswith(
                    "employee-profile/"
                ):
                    return _trigger_leave_stats_refresh(
                        HttpResponse("<script>window.location.reload();</script>")
                    )
                _leave_saved = True
            else:
                _leave_saved = False
        else:
            _leave_saved = False

        response = render(
            request,
            "leave/user_leave/user_request_form.html",
            {
                "form": form,
                "id": id,
                "leave_type": leave_type,
                "pd": previous_data,
            },
        )
        if _leave_saved:
            _trigger_leave_stats_refresh(response)
        return response
    form.fields["leave_type_id"].queryset = LeaveType.objects.filter(id=id)
    return render(
        request,
        "leave/user_leave/user_request_form.html",
        {
            "form": form,
            "id": id,
            "leave_type": leave_type,
            "pd": previous_data,
        },
    )


@login_required
@hx_request_required
def user_request_update(request, id):
    """
    function used to update user leave request.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id : leave request id

    Returns:
    GET : return user leave request update form template
    POST : return user leave request view template
    """
    previous_data = request.GET.urlencode()
    leave_request = LeaveRequest.objects.get(id=id)
    try:
        if (
            request.user.employee_get == leave_request.employee_id
            and leave_request.status != "approved"
        ):
            form = UserLeaveRequestForm(
                employee=leave_request.employee_id, instance=leave_request
            )
            if request.method == "POST":
                form = UserLeaveRequestForm(
                    request.POST,
                    request.FILES,
                    instance=leave_request,
                    employee=leave_request.employee_id,
                )
                if form.is_valid():
                    leave_request = form.save(commit=False)

                    start_date = leave_request.start_date
                    end_date = leave_request.end_date
                    start_date_breakdown = leave_request.start_date_breakdown
                    end_date_breakdown = leave_request.end_date_breakdown
                    leave_type = leave_request.leave_type_id
                    employee = request.user.employee_get
                    available_leave = AvailableLeave.objects.get(
                        employee_id=employee, leave_type_id=leave_type
                    )
                    available_total_leave = (
                        available_leave.available_days
                        + available_leave.carryforward_days
                    )
                    requested_days = calculate_requested_days(
                        start_date, end_date, start_date_breakdown, end_date_breakdown
                    )
                    requested_dates = leave_requested_dates(start_date, end_date)
                    holidays = Holidays.objects.all()
                    holiday_dates = holiday_dates_list(holidays)
                    company_leaves = CompanyLeaves.objects.all()
                    company_leave_dates = company_leave_dates_list(
                        company_leaves, start_date
                    )
                    if (
                        leave_type.exclude_company_leave == "yes"
                        and leave_type.exclude_holiday == "yes"
                    ):
                        total_leaves = list(set(holiday_dates + company_leave_dates))
                        total_leave_count = sum(
                            requested_date in total_leaves
                            for requested_date in requested_dates
                        )
                        requested_days = requested_days - total_leave_count
                    else:
                        holiday_count = 0
                        if leave_type.exclude_holiday == "yes":
                            for requested_date in requested_dates:
                                if requested_date in holiday_dates:
                                    holiday_count += 1
                            requested_days = requested_days - holiday_count
                        if leave_type.exclude_company_leave == "yes":
                            company_leave_count = sum(
                                requested_date in company_leave_dates
                                for requested_date in requested_dates
                            )
                            requested_days = requested_days - company_leave_count
                    if requested_days <= available_total_leave:
                        leave_request.save()
                        messages.success(
                            request, _("Leave request updated successfully..")
                        )
                        log_form_changes(
                            request.user,
                            "leave",
                            "Leave request updated",
                            form=form,
                            target=leave_request,
                        )
                    else:
                        form.add_error(
                            None,
                            _("You dont have enough leave days to make the request.."),
                        )
            response = render(
                request,
                "leave/user_leave/user_request_update.html",
                {
                    "form": form,
                    "id": id,
                    "pd": previous_data,
                },
            )
            # Trigger stats refresh whenever the user submitted a successful
            # update — detected via the success message above.
            if request.method == "POST" and form.is_valid():
                _trigger_leave_stats_refresh(response, request)
            return response
        else:
            messages.error(request, _("You can't update this leave request..."))
            return _trigger_leave_stats_refresh(
                HttpResponse("<script>window.location.reload();</script>"),
                request,
            )
    except Exception as e:
        messages.error(request, _("User has no leave request.."))
    return render(
        request,
        "leave/user_leave/user_request_update.html",
        {
            "form": form,
            "id": id,
            "pd": previous_data,
        },
    )


@login_required
@hx_request_required
def user_request_delete(request, id):
    """
    function used to delete user leave request.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id : leave request id

    Returns:
    GET : return user leave request view template
    """
    previous_data = request.GET.urlencode()
    try:
        leave_request = LeaveRequest.objects.get(id=id)
        if request.user.employee_get == leave_request.employee_id:
            # Snapshot before delete — the instance is unusable afterwards.
            audit_changes = _leave_request_details(leave_request)
            messages.success(request, _("Leave request deleted successfully.."))
            leave_request.delete()
            _leave_audit(request, "Leave request deleted", changes=audit_changes)
    except LeaveRequest.DoesNotExist:
        messages.error(request, _("User has no leave request.."))
    except ProtectedError:
        messages.error(request, _("Related entries exists"))
    if not LeaveRequest.objects.filter(employee_id=request.user.employee_get):
        return _trigger_leave_stats_refresh(
            HttpResponse("<script>window.location.reload();</script>"),
            request,
        )
    else:
        return _trigger_leave_stats_refresh(
            redirect(f"/leave/user-request-filter?{previous_data}"),
            request,
        )


@login_required
@hx_request_required
def user_leave_filter(request):
    """
    function used to filter user assigned leave types.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return user assigned leave types view template
    """
    try:
        employee = request.user.employee_get
        queryset = employee.available_leave.all()
        previous_data = request.GET.urlencode()
        page_number = request.GET.get("page")
        assigned_leave_filter = AssignedLeaveFilter(request.GET, queryset).qs
        data_dict = parse_qs(previous_data)
        get_key_instances(AvailableLeave, data_dict)
        page_obj = paginator_qry(assigned_leave_filter, page_number)
        return render(
            request,
            "leave/user_leave/user_leave.html",
            {"user_leaves": page_obj, "pd": previous_data, "filter_dict": data_dict},
        )
    except Exception as e:
        messages.error(request, _("User is not an employee.."))
        return redirect("/")


@login_required
def user_request_view(request):
    """
    function used to view user leave request.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return user leave request view template
    """
    try:
        user = request.user.employee_get
        queryset = user.leaverequest_set.all()
        previous_data = request.GET.urlencode()
        page_number = request.GET.get("page")

        # Fetching leave requests
        leave_requests = queryset

        leave_requests_with_interview = []
        if apps.is_installed("recruitment"):
            for leave_request in leave_requests:

                # Fetch interviews for the employee within the requested leave period
                InterviewSchedule = get_horilla_model_class(
                    app_label="recruitment", model="interviewschedule"
                )

                interviews = InterviewSchedule.objects.filter(
                    employee_id=leave_request.employee_id,
                    interview_date__range=[
                        leave_request.start_date,
                        leave_request.end_date,
                    ],
                )
                if interviews:
                    # If interview exists then adding the leave request to the list
                    leave_requests_with_interview.append(leave_request)

        user_request_filter = UserLeaveRequestFilter(request.GET, queryset=queryset)
        page_obj = paginator_qry(user_request_filter.qs.order_by("-id"), page_number)
        request_ids = json.dumps(
            list(page_obj.object_list.values_list("id", flat=True))
        )
        user_leave = AvailableLeave.objects.filter(employee_id=user.id).exclude(
            leave_type_id__is_compensatory_leave=True
        )
        if (
            LeaveGeneralSetting.objects.first()
            and LeaveGeneralSetting.objects.first().compensatory_leave
        ):
            user_leave = AvailableLeave.objects.filter(employee_id=user.id)
        current_date = date.today()
        return render(
            request,
            "leave/user_leave/user_request_view.html",
            {
                "leave_requests": page_obj,
                "form": user_request_filter.form,
                "pd": previous_data,
                "current_date": current_date,
                "gp_fields": MyLeaveRequestReGroup.fields,
                "request_ids": request_ids,
                "user_leaves": user_leave,
                "leave_requests_with_interview": leave_requests_with_interview,
            },
        )
    except Exception as e:
        messages.error(request, _("User is not an employee.."))
        return redirect("/")


@login_required
@hx_request_required
def user_request_filter(request):
    """
    function used to filter user leave request.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return user leave request view template
    """
    try:
        user = request.user.employee_get
        queryset = user.leaverequest_set.all().order_by("-id")
        previous_data = request.GET.urlencode()
        page_number = request.GET.get("page")
        field = request.GET.get("field")

        # Fetching leave requests
        leave_requests = queryset

        leave_requests_with_interview = []
        if apps.is_installed("recruitment"):

            for leave_request in leave_requests:

                # Fetch interviews for the employee within the requested leave period
                InterviewSchedule = get_horilla_model_class(
                    app_label="recruitment", model="interviewschedule"
                )

                interviews = InterviewSchedule.objects.filter(
                    employee_id=leave_request.employee_id,
                    interview_date__range=[
                        leave_request.start_date,
                        leave_request.end_date,
                    ],
                )
                if interviews:
                    # If interview exists then adding the leave request to the list
                    leave_requests_with_interview.append(leave_request)

        queryset = sortby(request, queryset, "sortby")
        user_request_filter = UserLeaveRequestFilter(request.GET, queryset).qs
        template = ("leave/user_leave/user_requests.html",)

        if field != "" and field is not None:
            user_request_filter = group_by_queryset(
                user_request_filter, field, request.GET.get("page"), "page"
            )
            list_values = [entry["list"] for entry in user_request_filter]
            id_list = []
            for value in list_values:
                for instance in value.object_list:
                    id_list.append(instance.id)

            requests_ids = json.dumps(list(id_list))
            template = "leave/user_leave/group_by.html"

        else:
            user_request_filter = paginator_qry(
                user_request_filter, request.GET.get("page")
            )
            requests_ids = json.dumps(
                [instance.id for instance in user_request_filter.object_list]
            )

        data_dict = parse_qs(previous_data)
        get_key_instances(LeaveRequest, data_dict)
        if "status" in data_dict:
            status_list = data_dict["status"]
            if len(status_list) > 1:
                data_dict["status"] = [status_list[-1]]

        user_leave = AvailableLeave.objects.filter(employee_id=user.id)

        context = {
            "leave_requests": user_request_filter,
            "pd": previous_data,
            "filter_dict": data_dict,
            "field": field,
            "current_date": date.today(),
            "request_ids": requests_ids,
            "user_leaves": user_leave,
            "leave_requests_with_interview": leave_requests_with_interview,
        }
        return render(request, template, context=context)
    except Exception as e:
        messages.error(request, _("User is not an employee.."))
        return redirect("/")


@login_required
@hx_request_required
def user_request_one(request, id):
    """
    function used to view one user leave request.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return one user leave request view template
    """
    leave_request = LeaveRequest.objects.get(id=id)
    try:
        requests_ids_json = request.GET.get("instances_ids")
        if requests_ids_json:
            requests_ids = json.loads(requests_ids_json)
            previous_id, next_id = closest_numbers(requests_ids, id)
        return render(
            request,
            "leave/user_leave/user_request_one.html",
            {
                "leave_request": leave_request,
                "instances_ids": requests_ids_json,
                "previous": previous_id,
                "next": next_id,
            },
        )
    except Exception as e:
        messages.error(request, _("User has no leave request.."))
        return redirect("/")


@login_required
@manager_can_enter("leave.view_leaverequest")
def employee_leave(request):
    """
    function used to view employees are leave today.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return Json response of employee
    """
    leaves = LeaveRequest.employees_on_leave_today(status="approved")
    requests_ids = list(leaves.values_list("id", flat=True))
    today_holidays = Holidays.today_holidays()
    return render(
        request,
        "leave/dashboard/on_leave.html",
        {
            "leaves": leaves,
            "requests_ids": requests_ids,
            "today_holidays": today_holidays,
        },
    )


@login_required
def overall_leave(request):
    """
    function used to view overall leave in the company.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return Json response of labels, data
    """

    labels = []
    data = []
    departments = Department.objects.all()
    leave_requests = LeaveRequestFilter(request.GET).qs
    for department in departments:
        count = leave_requests.filter(
            employee_id__employee_work_info__department_id=department
        ).count()
        if count:
            labels.append(department.department)
            data.append(count)
    return JsonResponse({"labels": labels, "data": data})


@login_required
@permission_required("leave.delete_leaverequest")
def dashboard(request):
    """
    function used to view Admin dashboard in the leave module.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return Admin dasboard template.
    """
    today = date.today()
    holidays = Holidays.objects.filter(start_date__gte=today)
    next_holiday = holidays.order_by("start_date").first() if holidays else None

    context = _admin_leave_stats_context()
    context.update(
        {
            "next_holiday": next_holiday,
            "dashboard": "dashboard",
        }
    )
    return render(request, "leave/dashboard.html", context)


@login_required
def employee_dashboard(request):
    """
    function used to view Employee dashboard in the leave module.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return Employee dasboard template.
    """
    today = date.today()
    user = Employee.objects.get(employee_user_id=request.user)
    leave_requests = LeaveRequest.objects.filter(employee_id=user)

    holidays = Holidays.objects.filter(start_date__gte=today)
    next_holiday = (
        holidays.order_by("start_date").first() if holidays.exists() else None
    )

    context = _employee_leave_stats_context(request)
    context.update(
        {
            "leave_requests": leave_requests,
            "next_holiday": next_holiday,
            "dashboard": "dashboard",
        }
    )
    return render(request, "leave/employee_dashboard.html", context)


# ---------------------------------------------------------------------------
# Leave statistics auto-refresh helpers
# ---------------------------------------------------------------------------
# A single HTMX trigger event is broadcast from leave action endpoints
# (apply / approve / reject / cancel) so that the leave dashboard statistic
# cards (and the available-leaves chart) re-render automatically without a
# full page reload.
LEAVE_STATS_TRIGGER = "leaveStatsRefresh"


def _trigger_leave_stats_refresh(response, request=None):
    """Attach the HX-Trigger header used to auto-refresh leave dashboard stats.

    Browsers transparently follow 3xx redirects, which strips any custom
    response header (including ``HX-Trigger``) from the original response. To
    keep the trigger reaching htmx we transform redirect responses into a
    ``204`` carrying ``HX-Redirect`` + ``HX-Trigger`` whenever the originating
    request is an htmx request — htmx will then both fire the trigger and
    perform the redirect itself, instead of letting the browser swallow the
    header.
    """
    if response is None:
        return response
    is_htmx = False
    if request is not None:
        is_htmx = (
            request.headers.get("HX-Request") == "true"
            or getattr(request, "htmx", False)
        )
    if is_htmx and getattr(response, "status_code", 200) in (301, 302, 303, 307, 308):
        location = response.get("Location", "/")
        response = HttpResponse(status=204)
        response["HX-Redirect"] = location
    existing = response.get("HX-Trigger", "")
    events = [e.strip() for e in existing.split(",") if e.strip()]
    if LEAVE_STATS_TRIGGER not in events:
        events.append(LEAVE_STATS_TRIGGER)
    response["HX-Trigger"] = ", ".join(events)
    return response


def _admin_leave_stats_context():
    """Compute the admin leave dashboard statistic context."""
    today = date.today()
    requested = LeaveRequest.objects.filter(start_date__gte=today, status="requested")
    approved = LeaveRequest.objects.filter(
        status="approved", start_date__month=today.month
    )
    rejected = LeaveRequest.objects.filter(
        status="rejected", start_date__month=today.month
    )
    return {
        "requested": requested,
        "approved": approved,
        "rejected": rejected,
        "today": today.strftime("%Y-%m-%d"),
        "first_day": today.replace(day=1).strftime("%Y-%m-%d"),
        "last_day": date(
            today.year, today.month, calendar.monthrange(today.year, today.month)[1]
        ).strftime("%Y-%m-%d"),
    }


def _employee_leave_stats_context(request):
    """Compute the personal leave dashboard statistic context for the user."""
    user = Employee.objects.get(employee_user_id=request.user)
    leave_requests = LeaveRequest.objects.filter(employee_id=user)
    return {
        "requested": leave_requests.filter(status="requested"),
        "approved": leave_requests.filter(status="approved"),
        "rejected": leave_requests.filter(status="rejected"),
    }


@login_required
@permission_required("leave.delete_leaverequest")
def dashboard_stats(request):
    """Return the admin leave dashboard statistic cards (HTMX partial)."""
    return render(
        request,
        "leave/dashboard/leave_stats.html",
        _admin_leave_stats_context(),
    )


@login_required
def employee_dashboard_stats(request):
    """Return the personal leave dashboard statistic cards (HTMX partial)."""
    return render(
        request,
        "leave/dashboard/employee_leave_stats.html",
        _employee_leave_stats_context(request),
    )


@login_required
def dashboard_leave_request(request):
    """
    function used to view leave request table.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return leave requests table.
    """
    requests_ids = []
    today = date.today()
    day = request.GET.get("date")
    employee = request.user.employee_get
    leave_requests = LeaveRequest.objects.filter(employee_id=employee)

    if day:
        day = datetime.strptime(day, "%Y-%m")
        leave_requests = leave_requests.filter(
            start_date__month=day.month, start_date__year=day.year
        )
    else:
        leave_requests = leave_requests.filter(
            start_date__month=today.month, start_date__year=today.year
        )

    requests_ids = [request.id for request in leave_requests]
    context = {
        "leave_requests": leave_requests,
        "dashboard": "dashboard",
        "requests_ids": requests_ids,
    }
    return render(request, "leave/leave_request/dashboard_leave_requests.html", context)


@login_required
def available_leave_chart(request):
    """
    function used to generate available leave chart in employee dashboard.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return Json response of labels, dataset, message.
    """
    user = Employee.objects.get(employee_user_id=request.user)
    available_leaves = AvailableLeave.objects.filter(employee_id=user).exclude(
        available_days=0
    )
    leave_count = []
    labels = []
    for leave in available_leaves:
        leave_count.append(leave.available_days + leave.carryforward_days)

        labels.append(leave.leave_type_id.name)
    dataset = [
        {
            "label": _("Total leaves available"),
            "data": leave_count,
        },
    ]
    response = {
        "labels": labels,
        "dataset": dataset,
        "message": _("Oops!! No leaves available for you this month..."),
    }
    return JsonResponse(response)


@login_required
def employee_leave_chart(request):
    """
    function used to generate employee leave chart in Admin dashboard.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return Json response of labels, dataset, message.
    """

    day = date.today()
    if request.GET.get("date"):
        day = request.GET.get("date")
        day = datetime.strptime(day, "%Y-%m")

    leave_requests = LeaveRequest.objects.filter(
        employee_id__is_active=True, status="approved"
    )
    leave_requests = leave_requests.filter(
        start_date__month=day.month, start_date__year=day.year
    )

    leave_types = leave_requests.values_list("leave_type_id__name", flat=True)

    labels = []
    dataset = []
    for employee in leave_requests.filter(
        start_date__month=day.month, start_date__year=day.year
    ):
        labels.append(employee.employee_id)

    for leave_type in list(set(leave_types)):
        dataset.append(
            {
                "label": leave_type,
                "data": [],
            }
        )

    labels = list(set(labels))
    total_leave_with_type = defaultdict(lambda: defaultdict(float))

    for label in labels:
        leaves = leave_requests.filter(
            employee_id=label, start_date__month=day.month, start_date__year=day.year
        )
        for leave in leaves:
            total_leave_with_type[leave.leave_type_id.name][label] += round(
                leave.requested_days, 2
            )

    for data in dataset:
        dataset_label = data["label"]
        data["data"] = [total_leave_with_type[dataset_label][label] for label in labels]

    employee_label = []
    for employee in list(set(labels)):
        employee_label.append(
            f"{employee.employee_first_name} {employee.employee_last_name}"
        )
    response = {
        "labels": employee_label,
        "dataset": dataset,
        "message": _("No leave request this month"),
    }
    return JsonResponse(response)


@login_required
def department_leave_chart(request):
    """
    function used to generate department leave chart in Admin dashboard.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return Json response of labels, dataset.
    """
    day = date.today()
    if request.GET.get("date"):
        day = request.GET.get("date")
        day = datetime.strptime(day, "%Y-%m")

    departments = Department.objects.all()
    department_counts = {dep.department: 0 for dep in departments}
    leave_request = LeaveRequest.objects.filter(status="approved")
    leave_request = leave_request.filter(
        start_date__month=day.month, start_date__year=day.year
    )
    leave_dates = []
    labels = []
    for leave in leave_request:
        for leave_date in leave.requested_dates():
            leave_dates.append(leave_date.strftime("%Y-%m-%d"))

        for dep in departments:
            if dep == leave.employee_id.employee_work_info.department_id:
                department_counts[dep.department] += leave.requested_days

    for department, count in department_counts.items():
        if count != 0:
            labels.append(department)
    values = list(department_counts.values())
    values = [value for value in values if value != 0]
    dataset = [
        {
            "label": _(""),
            "data": values,
        },
    ]
    response = {
        "labels": labels,
        "dataset": dataset,
        "message": _("No leave requests for this month."),
    }
    return JsonResponse(response)


@login_required
def leave_type_chart(request):
    """
    function used to generate leave type chart in Admin dashboard.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return Json response of labels, dataset.
    """
    day = date.today()
    if request.GET.get("date"):
        day = request.GET.get("date")
        day = datetime.strptime(day, "%Y-%m")

    leave_types = LeaveType.objects.all()
    leave_type_count = {types.name: 0 for types in leave_types}
    leave_request = LeaveRequest.objects.filter(status="approved")
    leave_request = leave_request.filter(
        start_date__month=day.month, start_date__year=day.year
    )
    for leave in leave_request:
        for lev in leave_types:
            if lev == leave.leave_type_id:
                leave_type_count[lev.name] += leave.requested_days

    # labels = [leave_type.name for leave_type in leave_types]
    labels = []
    for leave_type, count in leave_type_count.items():
        if count != 0:
            labels.append(leave_type)
    values = list(leave_type_count.values())
    values = [value for value in values if value != 0]

    response = {
        "labels": labels,
        "dataset": [
            {
                "data": values,
            },
        ],
        "message": _("No leave requests for any leave type this month."),
    }
    return JsonResponse(response)


@login_required
def leave_over_period(request):
    """
    function used to generate leave over a week chart in Admin dashboard.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return Json response of labels, dataset.
    """
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())
    week_dates = [start_of_week + timedelta(days=i) for i in range(6)]

    leave_in_week = []

    leave_request = LeaveRequest.objects.filter(status="approved")
    leave_dates = []
    for leave in leave_request:
        for leave_date in leave.requested_dates():
            leave_dates.append(leave_date)

    filtered_dates = [
        day
        for day in leave_dates
        if day.month == today.month and day.year == today.year
    ]
    for week_date in week_dates:
        days = []
        for filtered_date in filtered_dates:
            if filtered_date == week_date:
                days.append(filtered_date)
        leave_in_week.append(len(days))

    dataset = (
        {
            "label": _("Leave Trends"),
            "data": leave_in_week,
        },
    )

    labels = [week_date.strftime("%d-%m-%Y") for week_date in week_dates]

    response = {
        "labels": labels,
        "dataset": dataset,
    }
    return JsonResponse(response)


@login_required
@hx_request_required
def leave_request_create(request):
    """
    function used to create leave request from calendar.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return leave request form template
    POST : return leave request view
    """
    previous_data = unquote(request.GET.urlencode())[len("pd=") :]
    emp = request.user.employee_get
    emp_id = emp.id

    form = UserLeaveRequestCreationForm(employee=emp)
    if request.method == "POST":
        if company_selection_required(request):
            messages.error(
                request,
                _(
                    "You are associated with multiple companies. Please select "
                    "your company before applying for a leave request."
                ),
            )
            return HttpResponse("<script>window.location.reload();</script>")
        form = UserLeaveRequestCreationForm(request.POST, request.FILES, employee=emp)
        if int(form.data["employee_id"]) == int(emp_id):
            if form.is_valid():
                leave_request = form.save(commit=False)
                save = True

                if leave_request.leave_type_id.require_approval == "no":
                    employee_id = leave_request.employee_id
                    leave_type_id = leave_request.leave_type_id
                    available_leave = AvailableLeave.objects.get(
                        leave_type_id=leave_type_id, employee_id=employee_id
                    )
                    # See approve view: skip current-year deduction for prior/
                    # future-year leaves, otherwise the balance shrinks silently.
                    if (
                        leave_request.start_date
                        and leave_request.start_date.year == date.today().year
                    ):
                        if leave_request.requested_days > available_leave.available_days:
                            leave = (
                                leave_request.requested_days
                                - available_leave.available_days
                            )
                            leave_request.approved_available_days = (
                                available_leave.available_days
                            )
                            available_leave.available_days = 0
                            available_leave.carryforward_days = (
                                available_leave.carryforward_days - leave
                            )
                            leave_request.approved_carryforward_days = leave
                        else:
                            available_leave.available_days = (
                                available_leave.available_days
                                - leave_request.requested_days
                            )
                            leave_request.approved_available_days = (
                                leave_request.requested_days
                            )
                    else:
                        # Prior/future-year: keep approved_* fields at 0 so a
                        # later cancel doesn't inflate the current-year balance.
                        leave_request.approved_available_days = 0
                        leave_request.approved_carryforward_days = 0
                    leave_request.status = "approved"
                    leave_request.reviewed_by = getattr(
                        request.user, "employee_get", None
                    )
                    leave_request.reviewed_at = timezone.now()
                    available_leave.save()
                if save:
                    leave_request.created_by = request.user.employee_get
                    leave_request.save()

                    if multiple_approvals_check(leave_request.id):
                        conditional_requests = multiple_approvals_check(
                            leave_request.id
                        )
                        managers = []
                        for manager in conditional_requests["managers"]:
                            managers.append(manager.employee_user_id)
                        with contextlib.suppress(Exception):
                            notify.send(
                                request.user.employee_get,
                                recipient=managers[0],
                                verb="You have a new leave request to validate.",
                                verb_ar="لديك طلب إجازة جديد يجب التحقق منه.",
                                verb_de="Sie haben eine neue Urlaubsanfrage zur Validierung.",
                                verb_es="Tiene una nueva solicitud de permiso que debe validar.",
                                verb_fr="Vous avez une nouvelle demande de congé à valider.",
                                icon="people-circle",
                                redirect=f"/leave/request-view?id={leave_request.id}",
                            )

                    messages.success(request, _("Leave request created successfully.."))
                    audit_changes = _leave_request_details(leave_request)
                    audit_changes["Reason"] = leave_request.description
                    if leave_request.leave_type_id.require_approval == "no":
                        audit_changes["Auto-approved"] = (
                            "Leave type does not require approval"
                        )
                    _leave_audit(
                        request,
                        "Leave request created",
                        target=leave_request,
                        changes=audit_changes,
                    )
                    with contextlib.suppress(Exception):
                        notify.send(
                            request.user.employee_get,
                            recipient=leave_request.employee_id.employee_work_info.reporting_manager_id.employee_user_id,
                            verb=f"New leave request created for {leave_request.employee_id}.",
                            verb_ar=f"تم إنشاء طلب إجازة جديد لـ {leave_request.employee_id}.",
                            verb_de=f"Neuer Urlaubsantrag für {leave_request.employee_id} erstellt.",
                            verb_es=f"Nueva solicitud de permiso creada para {leave_request.employee_id}.",
                            verb_fr=f"Nouvelle demande de congé créée pour {leave_request.employee_id}.",
                            icon="people-circle",
                            redirect=reverse("request-view")
                            + f"?id={leave_request.id}",
                        )

                    mail_thread = LeaveMailSendThread(
                        request, leave_request, type="request"
                    )
                    mail_thread.start()
                    form = UserLeaveRequestCreationForm(employee=emp)
                    if len(LeaveRequest.objects.filter(employee_id=emp_id)) == 1:
                        return _trigger_leave_stats_refresh(
                            HttpResponse(
                                "<script>window.location.reload();</script>"
                            )
                        )
                    _leave_saved = True
                else:
                    _leave_saved = False
            else:
                _leave_saved = False
            response = render(
                request,
                "leave/user_leave/request_form.html",
                {
                    "form": form,
                    "pd": previous_data,
                },
            )
            if _leave_saved:
                _trigger_leave_stats_refresh(response)
            return response
        else:
            messages.error(request, _("You don't have permission"))
            response = render(
                request, "leave/user_leave/request_form.html", {"form": form}
            )
            return _trigger_leave_stats_refresh(
                HttpResponse(
                    response.content.decode("utf-8")
                    + "<script>location.reload();</script>"
                )
            )
    return render(
        request,
        "leave/user_leave/request_form.html",
        {
            "form": form,
            "pd": previous_data,
        },
    )


@login_required
def leave_allocation_request_view(request):
    """
    function used to view leave allocation request.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return leave allocation request view template
    """
    employee = request.user.employee_get
    queryset = LeaveAllocationRequest.objects.all().order_by("-id")
    queryset = LeaveAllocationRequestFilter(request.GET, queryset).qs
    queryset = filtersubordinates(
        request, queryset, "leave.view_leaveallocationrequest"
    )
    page_number = request.GET.get("page")
    leave_allocation_requests = paginator_qry(queryset, page_number)
    requests_ids = json.dumps(
        list(leave_allocation_requests.object_list.values_list("id", flat=True))
    )
    my_leave_allocation_requests = LeaveAllocationRequest.objects.filter(
        employee_id=employee.id
    ).order_by("-id")
    my_leave_allocation_requests = LeaveAllocationRequestFilter(
        request.GET, my_leave_allocation_requests
    ).qs
    my_page_number = request.GET.get("m_page")
    my_leave_allocation_requests = paginator_qry(
        my_leave_allocation_requests, my_page_number
    )
    my_requests_ids = json.dumps(
        list(my_leave_allocation_requests.object_list.values_list("id", flat=True))
    )
    leave_allocation_request_filter = LeaveAllocationRequestFilter()
    previous_data = request.GET.urlencode()
    data_dict = parse_qs(previous_data)
    data_dict = get_key_instances(LeaveAllocationRequest, data_dict)
    context = {
        "leave_allocation_requests": leave_allocation_requests,
        "my_leave_allocation_requests": my_leave_allocation_requests,
        "pd": previous_data,
        "form": leave_allocation_request_filter.form,
        "filter_dict": data_dict,
        "gp_fields": LeaveAllocationRequestReGroup.fields,
        "requests_ids": requests_ids,
        "my_requests_ids": my_requests_ids,
    }
    return render(
        request,
        "leave/leave_allocation_request/leave_allocation_request_view.html",
        context=context,
    )


@login_required
@hx_request_required
def leave_allocation_request_single_view(request, req_id):
    """
    function used to present the leave allocation request detailed view.

    Parameters:
    request (HttpRequest): The HTTP request object.
    req_id : leave allocation request id

    Returns:
    return leave allocation request single view
    """
    my_request = False
    if request.GET.get("my_request") == "True":
        my_request = True
    requests_ids_json = request.GET.get("instances_ids")
    if requests_ids_json:
        requests_ids = json.loads(requests_ids_json)
        previous_id, next_id = closest_numbers(requests_ids, req_id)
    leave_allocation_request = LeaveAllocationRequest.find(req_id)
    context = {
        "leave_allocation_request": leave_allocation_request,
        "my_request": my_request,
        "instances_ids": requests_ids_json,
        "previous": previous_id,
        "next": next_id,
        "dashboard": request.GET.get("dashboard"),
    }
    return render(
        request,
        "leave/leave_allocation_request/leave_allocation_request_single_view.html",
        context=context,
    )


@login_required
@hx_request_required
def leave_allocation_request_create(request):
    """
    function used to create leave allocation request.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return leave allocation request form template
    POST : return leave allocation request view
    """
    employee = request.user.employee_get
    form = LeaveAllocationRequestForm(initial={"employee_id": employee})
    form = choosesubordinates(request, form, "leave.add_leaveallocationrequest")
    form.fields["employee_id"].queryset = form.fields[
        "employee_id"
    ].queryset | Employee.objects.filter(employee_user_id=request.user)
    if request.method == "POST":
        form = LeaveAllocationRequestForm(request.POST, request.FILES)
        if form.is_valid():
            leave_allocation_request = form.save(commit=False)
            leave_allocation_request.skip_history = False
            leave_allocation_request.save()
            messages.success(request, _("New Leave allocation request is created"))
            audit_changes = _allocation_request_details(leave_allocation_request)
            audit_changes["Reason"] = leave_allocation_request.description
            _leave_audit(
                request,
                "Leave allocation requested",
                target=leave_allocation_request,
                changes=audit_changes,
            )
            with contextlib.suppress(Exception):
                notify.send(
                    request.user.employee_get,
                    recipient=leave_allocation_request.employee_id.employee_work_info.reporting_manager_id.employee_user_id,
                    verb=f"New leave allocation request created for {leave_allocation_request.employee_id}.",
                    verb_ar=f"تم إنشاء طلب تخصيص إجازة جديد لـ {leave_allocation_request.employee_id}.",
                    verb_de=f"Neue Anfrage zur Urlaubszuweisung erstellt für {leave_allocation_request.employee_id}.",
                    verb_es=f"Nueva solicitud de asignación de permisos creada para {leave_allocation_request.employee_id}.",
                    verb_fr=f"Nouvelle demande d'allocation de congé créée pour {leave_allocation_request.employee_id}.",
                    icon="people-cicle",
                    redirect=reverse("leave-allocation-request-view")
                    + f"?id={leave_allocation_request.id}",
                )
            response = render(
                request,
                "leave/leave_allocation_request/leave_allocation_request_create.html",
                {"form": form},
            )
            return HttpResponse(
                response.content.decode("utf-8")
                + "<script>location. reload();</script>"
            )
    context = {"form": form}
    return render(
        request,
        "leave/leave_allocation_request/leave_allocation_request_create.html",
        context=context,
    )


@login_required
@hx_request_required
def leave_allocation_request_filter(request):
    field = request.GET.get("field")
    employee = request.user.employee_get
    page_number = request.GET.get("page")
    my_page_number = request.GET.get("m_page")
    previous_data = request.GET.urlencode()
    template = "leave/leave_allocation_request/leave_allocation_request_list.html"

    # Filter leave allocation requests
    leave_allocation_requests_filtered = LeaveAllocationRequestFilter(
        request.GET
    ).qs.order_by("-id")
    my_leave_allocation_requests_filtered = LeaveAllocationRequest.objects.filter(
        employee_id=employee.id
    ).order_by("-id")
    my_leave_allocation_requests_filtered = LeaveAllocationRequestFilter(
        request.GET, my_leave_allocation_requests_filtered
    ).qs
    leave_allocation_requests_filtered = filtersubordinates(
        request, leave_allocation_requests_filtered, "leave.view_leaveallocationrequest"
    )

    # Sort leave allocation requests if requested
    if request.GET.get("sortby"):
        leave_allocation_requests_filtered = sortby(
            request, leave_allocation_requests_filtered, "sortby"
        )

    # Group leave allocation requests if field parameter is provided
    if field:
        leave_allocation_requests = group_by_queryset(
            leave_allocation_requests_filtered, field, page_number, "page"
        )
        my_leave_allocation_requests = group_by_queryset(
            my_leave_allocation_requests_filtered, field, my_page_number, "m_page"
        )

        # Convert IDs to JSON format for details view
        list_values = [entry["list"] for entry in leave_allocation_requests]
        id_list = [
            instance.id for value in list_values for instance in value.object_list
        ]
        requests_ids = json.dumps(list(id_list))

        list_values = [entry["list"] for entry in my_leave_allocation_requests]
        id_list = [
            instance.id for value in list_values for instance in value.object_list
        ]
        my_requests_ids = json.dumps(list(id_list))
        template = (
            "leave/leave_allocation_request/leave_allocation_request_group_by.html"
        )
    else:
        leave_allocation_requests = paginator_qry(
            leave_allocation_requests_filtered, page_number
        )
        my_leave_allocation_requests = paginator_qry(
            my_leave_allocation_requests_filtered, my_page_number
        )
        requests_ids = json.dumps(
            list(leave_allocation_requests.object_list.values_list("id", flat=True))
        )
        my_requests_ids = json.dumps(
            list(my_leave_allocation_requests.object_list.values_list("id", flat=True))
        )

    # Parse previous data and construct context for filter tag
    data_dict = parse_qs(previous_data)
    data_dict = get_key_instances(LeaveAllocationRequest, data_dict)
    data_dict.pop("m_page", None)

    context = {
        "leave_allocation_requests": leave_allocation_requests,
        "my_leave_allocation_requests": my_leave_allocation_requests,
        "pd": previous_data,
        "filter_dict": data_dict,
        "field": field,
        "requests_ids": requests_ids,
        "my_requests_ids": my_requests_ids,
    }
    return render(request, template, context=context)


@login_required
@hx_request_required
@leave_allocation_change_permission()
def leave_allocation_request_update(request, req_id):
    """
    function used to update leave allocation request.

    Parameters:
    request (HttpRequest): The HTTP request object.
    req_id : leave allocation request id

    Returns:
    GET : return leave allocation request update template
    POST : return leave allocation request view
    """
    leave_allocation_request = LeaveAllocationRequest.objects.get(id=req_id)
    form = LeaveAllocationRequestForm(instance=leave_allocation_request)
    form = choosesubordinates(request, form, "leave.add_leaveallocationrequest")
    form.fields["employee_id"].queryset = form.fields[
        "employee_id"
    ].queryset | Employee.objects.filter(employee_user_id=request.user)
    if leave_allocation_request.status != "approved":
        if request.method == "POST":
            form = LeaveAllocationRequestForm(
                request.POST, request.FILES, instance=leave_allocation_request
            )
            if form.is_valid():
                leave_allocation_request = form.save(commit=False)
                leave_allocation_request.skip_history = False
                leave_allocation_request.save()
                messages.success(
                    request, _("Leave allocation request is updated successfully.")
                )
                log_form_changes(
                    request.user,
                    "leave",
                    "Leave allocation request updated",
                    form=form,
                    target=leave_allocation_request,
                )
                with contextlib.suppress(Exception):
                    notify.send(
                        request.user.employee_get,
                        recipient=leave_allocation_request.employee_id.employee_work_info.reporting_manager_id.employee_user_id,
                        verb=f"Leave allocation request updated for {leave_allocation_request.employee_id}.",
                        verb_ar=f"تم تحديث طلب تخصيص الإجازة لـ {leave_allocation_request.employee_id}.",
                        verb_de=f"Urlaubszuteilungsanforderung aktualisiert für {leave_allocation_request.employee_id}.",
                        verb_es=f"Solicitud de asignación de licencia actualizada para {leave_allocation_request.employee_id}.",
                        verb_fr=f"Demande d'allocation de congé mise à jour pour {leave_allocation_request.employee_id}.",
                        icon="people-cicle",
                        redirect=reverse("leave-allocation-request-view")
                        + f"?id={leave_allocation_request.id}",
                    )
                response = render(
                    request,
                    "leave/leave_allocation_request/leave_allocation_request_update.html",
                    {"form": form, "req_id": req_id},
                )
                return HttpResponse(
                    response.content.decode("utf-8")
                    + "<script>location. reload();</script>"
                )
        return render(
            request,
            "leave/leave_allocation_request/leave_allocation_request_update.html",
            {"form": form, "req_id": req_id},
        )
    else:
        messages.error(request, _("You can't update this request..."))
        return HttpResponse("<script>window.location.reload();</script>")


@login_required
@leave_allocation_reject_permission()
def leave_allocation_request_approve(request, req_id):
    """
    function used to approve a leave allocation request.

    Parameters:
    request (HttpRequest): The HTTP request object.
    req_id : leave allocation request id

    Returns:
    GET :It returns to the default leave allocation request view template.
    """
    leave_allocation_request = LeaveAllocationRequest.objects.get(id=req_id)
    if leave_allocation_request.status == "requested":
        employee = leave_allocation_request.employee_id
        if (
            employee.available_leave.all()
            .filter(leave_type_id=leave_allocation_request.leave_type_id)
            .exists()
        ):
            available_leave = (
                employee.available_leave.all()
                .filter(leave_type_id=leave_allocation_request.leave_type_id)
                .first()
            )
        else:
            available_leave = AvailableLeave(
                leave_type_id=leave_allocation_request.leave_type_id,
                employee_id=employee,
            )
        # Captured before mutation for the audit before/after values.
        status_before = leave_allocation_request.get_status_display()
        balance_before = available_leave.available_days
        available_leave.available_days += leave_allocation_request.requested_days
        available_leave.save()
        leave_allocation_request.status = "approved"
        leave_allocation_request.save()
        messages.success(request, _("Leave allocation request approved successfully"))
        audit_changes = _allocation_request_details(leave_allocation_request)
        audit_changes.update(
            _status_change(
                status_before, leave_allocation_request.get_status_display()
            )
        )
        audit_changes["Available days"] = {
            "from": str(balance_before),
            "to": str(available_leave.available_days),
        }
        _leave_audit(
            request,
            "Leave allocation approved",
            target=leave_allocation_request,
            changes=audit_changes,
        )
        with contextlib.suppress(Exception):
            notify.send(
                request.user.employee_get,
                recipient=leave_allocation_request.employee_id.employee_user_id,
                verb="Your leave allocation request has been approved",
                verb_ar="تمت الموافقة على طلب تخصيص إجازتك",
                verb_de="Ihr Antrag auf Urlaubszuweisung wurde genehmigt",
                verb_es="Se ha aprobado su solicitud de asignación de vacaciones",
                verb_fr="Votre demande d'allocation de congé a été approuvée",
                icon="people-circle",
                redirect=reverse("leave-allocation-request-view")
                + f"?id={leave_allocation_request.id}",
            )
    else:
        messages.error(request, _("The leave allocation request can't be approved"))
    return _trigger_leave_stats_refresh(
        HttpResponseRedirect(request.META.get("HTTP_REFERER", "/")),
        request,
    )


@login_required
@hx_request_required
@leave_allocation_reject_permission()
def leave_allocation_request_reject(request, req_id):
    """
    function used to Reject leave allocation request.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id : leave allocation request id

    Returns:
    GET : It returns to the default leave allocation request view template.

    """
    leave_allocation_request = LeaveAllocationRequest.objects.get(id=req_id)
    if (
        leave_allocation_request.status == "requested"
        or leave_allocation_request.status == "approved"
    ):
        form = LeaveAllocationRequestRejectForm()
        if request.method == "POST":
            form = LeaveAllocationRequestRejectForm(request.POST)
            if form.is_valid():
                # Captured before mutation for the audit before/after values.
                status_before = leave_allocation_request.get_status_display()
                balance_change = None
                leave_allocation_request.reject_reason = form.cleaned_data["reason"]
                if leave_allocation_request.status == "approved":
                    leave_type = leave_allocation_request.leave_type_id
                    requested_days = leave_allocation_request.requested_days
                    available_leave = AvailableLeave.objects.filter(
                        leave_type_id=leave_type,
                        employee_id=leave_allocation_request.employee_id,
                    ).first()
                    balance_before = available_leave.available_days
                    available_leave.available_days = max(
                        0, available_leave.available_days - requested_days
                    )

                    available_leave.save()
                    balance_change = {
                        "from": str(balance_before),
                        "to": str(available_leave.available_days),
                    }
                leave_allocation_request.status = "rejected"
                leave_allocation_request.save()
                messages.success(
                    request, _("Leave allocation request rejected successfully")
                )
                audit_changes = _allocation_request_details(leave_allocation_request)
                audit_changes.update(
                    _status_change(
                        status_before,
                        leave_allocation_request.get_status_display(),
                    )
                )
                audit_changes["Reason"] = leave_allocation_request.reject_reason
                if balance_change:
                    # Rejecting an already-approved allocation claws the days back.
                    audit_changes["Available days"] = balance_change
                _leave_audit(
                    request,
                    "Leave allocation rejected",
                    target=leave_allocation_request,
                    changes=audit_changes,
                )
                with contextlib.suppress(Exception):
                    notify.send(
                        request.user.employee_get,
                        recipient=leave_allocation_request.employee_id.employee_user_id,
                        verb="Your leave allocation request has been rejected",
                        verb_ar="تم رفض طلب تخصيص إجازتك",
                        verb_de="Ihr Antrag auf Urlaubszuweisung wurde abgelehnt",
                        verb_es="Se ha rechazado su solicitud de asignación de vacaciones",
                        verb_fr="Votre demande d'allocation de congé a été rejetée",
                        icon="people-circle",
                        redirect=reverse("leave-allocation-request-view")
                        + f"?id={leave_allocation_request.id}",
                    )
                return _trigger_leave_stats_refresh(
                    HttpResponse("<script>location.reload();</script>"),
                    request,
                )
        return render(
            request,
            "leave/leave_allocation_request/leave_allocation_request_reject_form.html",
            {"form": form, "req_id": req_id},
        )
    else:
        messages.error(request, _("The leave allocation request can't be rejected"))
        return _trigger_leave_stats_refresh(
            HttpResponse("<script>location.reload();</script>"),
            request,
        )


@login_required
@hx_request_required
@leave_allocation_delete_permission()
def leave_allocation_request_delete(request, req_id):
    """
    function used to delete leave allocation request.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id : leave allocation request id

    Returns:
    GET : return leave allocation request view template
    """

    try:
        leave_allocation_request = LeaveAllocationRequest.objects.get(id=req_id)

        if leave_allocation_request.status != "approved":
            # Snapshot before delete — the instance is unusable afterwards.
            audit_changes = _allocation_request_details(leave_allocation_request)
            leave_allocation_request.delete()
            messages.success(
                request, _("Leave allocation request deleted successfully..")
            )
            _leave_audit(
                request, "Leave allocation request deleted", changes=audit_changes
            )
        else:
            messages.error(request, _("Approved request can't be deleted."))
    except LeaveAllocationRequest.DoesNotExist:
        messages.error(request, _("Leave allocation request not found."))
    except OverflowError:
        messages.error(request, _("Leave allocation request not found."))

    except ProtectedError:
        messages.error(request, _("Related entries exist"))
    hx_target = request.META.get("HTTP_HX_TARGET")
    previous_data = request.GET.urlencode()
    if hx_target and hx_target == "view-container":
        leave_allocations = LeaveAllocationRequest.objects.all()
        if leave_allocations.exists():
            return _trigger_leave_stats_refresh(
                redirect(f"/leave/leave-allocation-request-filter?{previous_data}"),
                request,
            )
        else:
            return _trigger_leave_stats_refresh(
                HttpResponse("<script>location.reload();</script>"),
                request,
            )
    elif hx_target and hx_target == "objectDetailsModalW25Target":
        instances_ids = request.GET.get("instances_ids")
        instances_list = json.loads(instances_ids)
        if req_id in instances_list:
            instances_list.remove(req_id)
        previous_instance, next_instance = closest_numbers(
            json.loads(instances_ids), req_id
        )
        return _trigger_leave_stats_refresh(
            redirect(
                f"/leave/leave-allocation-request-single-view/{next_instance}?{previous_data}"
            ),
            request,
        )

    return _trigger_leave_stats_refresh(
        redirect(leave_allocation_request_view), request
    )


@login_required
def assigned_leave_select(request):
    page_number = request.GET.get("page")

    if page_number == "all":
        if request.user.has_perm("leave.view_availableleave"):
            employees = AvailableLeave.objects.all()
        else:
            employees = AvailableLeave.objects.filter(
                employee_id__employee_work_info__reporting_manager_id__employee_user_id=request.user
            )

    employee_ids = [str(emp.id) for emp in employees]
    total_count = employees.count()

    context = {"employee_ids": employee_ids, "total_count": total_count}

    return JsonResponse(context, safe=False)


@login_required
def assigned_leave_select_filter(request):
    page_number = request.GET.get("page")
    filtered = request.GET.get("filter")
    filters = json.loads(filtered) if filtered else {}

    if page_number == "all":
        if request.user.has_perm("leave.view_availableleave"):
            employee_filter = AssignedLeaveFilter(
                filters, queryset=AvailableLeave.objects.all()
            )
        else:
            employee_filter = AssignedLeaveFilter(
                filters,
                queryset=AvailableLeave.objects.filter(
                    employee_id__employee_work_info__reporting_manager_id__employee_user_id=request.user
                ),
            )

        # Get the filtered queryset
        filtered_employees = employee_filter.qs

        employee_ids = [str(emp.id) for emp in filtered_employees]
        total_count = filtered_employees.count()

        context = {"employee_ids": employee_ids, "total_count": total_count}

        return JsonResponse(context)


@login_required
@require_http_methods(["POST"])
@manager_can_enter("leave.delete_leaverequest")
def leave_request_bulk_delete(request):
    """
    This method is used to delete a bulk of leave requests.
    """
    ids = request.POST["ids"]
    ids = json.loads(ids)
    count = 0  # To track the number of successfully deleted requests
    deleted = []
    for leave_request_id in ids:
        try:
            leave_request = LeaveRequest.objects.get(id=leave_request_id)
            employee = leave_request.employee_id
            if leave_request.status == "requested":
                # Describe before delete — the instance is unusable afterwards.
                summary = (
                    f"LR-{leave_request.pk} {employee} "
                    f"({leave_request.leave_type_id}, {leave_request.start_date})"
                )
                leave_request.delete()
                deleted.append(summary)
                count += 1
            else:
                messages.error(
                    request,
                    _("{}'s leave request cannot be deleted.".format(employee)),
                )
        except Exception as e:
            messages.error(request, _("An error occurred: {}.".format(str(e))))

    if count > 0:
        messages.success(
            request,
            _("{count}  leave request(s) successfully deleted.".format(count=count)),
        )
        _leave_audit(
            request,
            "Leave requests bulk deleted",
            changes={"Count": str(count), "Leave requests": ", ".join(deleted)},
        )

    return JsonResponse({"message": "Success"})


@login_required
def leave_request_select(request):
    page_number = request.GET.get("page")

    if page_number == "all":
        if request.user.has_perm("leave.view_leaverequest"):
            employees = LeaveRequest.objects.all()
        else:
            employees = LeaveRequest.objects.filter(
                employee_id__employee_work_info__reporting_manager_id__employee_user_id=request.user
            )

    employee_ids = [str(emp.id) for emp in employees]
    total_count = employees.count()

    context = {"employee_ids": employee_ids, "total_count": total_count}

    return JsonResponse(context, safe=False)


@login_required
def leave_request_select_filter(request):
    page_number = request.GET.get("page")
    filtered = request.GET.get("filter")
    filters = json.loads(filtered) if filtered else {}

    if page_number == "all":
        if request.user.has_perm("leave.view_leaverequest"):
            employee_filter = LeaveRequestFilter(
                filters, queryset=LeaveRequest.objects.all()
            )
        else:
            employee_filter = LeaveRequestFilter(
                filters,
                queryset=LeaveRequest.objects.filter(
                    employee_id__employee_work_info__reporting_manager_id__employee_user_id=request.user
                ),
            )

        # Get the filtered queryset
        filtered_employees = employee_filter.qs

        employee_ids = [str(emp.id) for emp in filtered_employees]
        total_count = filtered_employees.count()

        context = {"employee_ids": employee_ids, "total_count": total_count}

        return JsonResponse(context)


@require_http_methods(["POST"])
@login_required
def user_request_bulk_delete(request):
    """
    This method is used to delete bulk of leaves requests
    """
    ids = request.POST["ids"]
    ids = json.loads(ids)
    deleted = []
    for leave_request_id in ids:
        try:
            leave_request = LeaveRequest.objects.get(id=leave_request_id)
            status = leave_request.status
            if leave_request.status == "requested":
                # Describe before delete — the instance is unusable afterwards.
                summary = (
                    f"LR-{leave_request.pk} {leave_request.employee_id} "
                    f"({leave_request.leave_type_id}, {leave_request.start_date})"
                )
                leave_request.delete()
                deleted.append(summary)
                messages.success(
                    request,
                    _("Leave request deleted."),
                )
            else:
                messages.error(
                    request,
                    _("You cannot delete leave request with status {}.".format(status)),
                )
        except Exception as e:
            messages.error(request, _("Leave request not found."))
    if deleted:
        _leave_audit(
            request,
            "Leave requests bulk deleted",
            changes={
                "Count": str(len(deleted)),
                "Leave requests": ", ".join(deleted),
            },
        )
    return JsonResponse({"message": "Success"})


@login_required
def user_request_select(request):
    page_number = request.GET.get("page")
    user = request.user.employee_get

    if page_number == "all":
        employees = LeaveRequest.objects.filter(employee_id=user)

    employee_ids = [str(emp.id) for emp in employees]
    total_count = employees.count()

    context = {"employee_ids": employee_ids, "total_count": total_count}

    return JsonResponse(context, safe=False)


@login_required
def user_request_select_filter(request):
    page_number = request.GET.get("page")
    filtered = request.GET.get("filter")
    filters = json.loads(filtered) if filtered else {}
    user = request.user.employee_get

    if page_number == "all":
        employee_filter = UserLeaveRequestFilter(
            filters, queryset=LeaveRequest.objects.filter(employee_id=user)
        )

        # Get the filtered queryset
        filtered_employees = employee_filter.qs

        employee_ids = [str(emp.id) for emp in filtered_employees]
        total_count = filtered_employees.count()

        context = {"employee_ids": employee_ids, "total_count": total_count}

        return JsonResponse(context)


@login_required
@hx_request_required
def employee_available_leave_count(request):
    leave_type_id = request.GET.get("leave_type_id")
    hx_target = request.META.get("HTTP_HX_TARGET")
    start_date_str = request.GET.get("start_date")

    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        start_date = None

    require_attachment = False
    leave_type = None

    if not leave_type_id or not start_date:
        return render(
            request,
            "leave/leave_request/employee_available_leave_count.html",
            {"hx_target": hx_target},
        )

    employee_id = request.GET.getlist("employee_id")
    employee_id = employee_id[0] if employee_id else None

    available_leave = (
        AvailableLeave.objects.select_related("leave_type_id", "employee_id")
        .filter(leave_type_id=leave_type_id, employee_id=employee_id)
        .first()
    )

    total_leave_days = 0
    forcasted_days = 0
    pending_requests_days = 0

    if available_leave:
        leave_type = available_leave.leave_type_id
        # Mirror AvailableLeave.balance_leaves so this card matches the
        # number on the leave statistics tab. Using cached available_days
        # / carryforward_days here drifted from the year-scoped balance
        # whenever the cache was stale (pre-recalc state, prior-year
        # approvals, scheduler hiccups). Computing from totals + live CF
        # + year-scoped request data keeps both views in sync regardless
        # of cache state.
        #
        # gross_available = total_days + cf + used_cf_year - taken_year
        #                 = balance_leaves() + pending_leaves()
        # The pending subtraction below leaves balance_leaves() — same
        # number as the stats tab.
        if getattr(leave_type, "is_compensatory_leave", False):
            total_leave_days = (available_leave.available_days or 0)
        else:
            total_leave_days = (
                (leave_type.total_days or 0)
                + available_leave.carryforward_days
                + available_leave.used_carryforward_days()
                - available_leave.leave_taken()
            )

        if leave_type:
            require_attachment = leave_type.require_attachment == "yes"
        else:
            require_attachment = False

        next_reset = leave_type.leave_type_next_reset_date()
        if next_reset and start_date >= next_reset:
            forcasted_days = available_leave.forcasted_leaves(start_date)

            if leave_type.carryforward_type == "no carryforward":
                total_leave_days = 0
            elif (
                leave_type.carryforward_type in ["carryforward", "carryforward expire"]
                and leave_type.carryforward_max is not None
                and leave_type.carryforward_max < total_leave_days
            ):
                total_leave_days = leave_type.carryforward_max

            total_leave_days += forcasted_days

        # Year-scoped pending matches AvailableLeave.pending_leaves(), so
        # the gross→net subtraction below leaves balance_leaves() exactly.
        # The previous filter (status + leave_type, no year) inflated the
        # subtraction with stale prior-year requests when any existed.
        pending_requests_days = available_leave.pending_leaves()

    available_days_for_request = total_leave_days - pending_requests_days

    if hx_target == "attachmentLabelWrapper":
        print("attachmentLabelWrapper")
        return render(
            request,
            "leave/leave_request/attachment_label_wrapper.html",
            {"require_attachment": require_attachment},
        )


    context = {
        "hx_target": hx_target,
        "leave_type_id": leave_type_id,
        "available_leave": available_leave,
        "total_leave_days": available_days_for_request,
        "forcasted_days": forcasted_days,
        "pending_requests": pending_requests_days,
        "require_attachment": require_attachment,
    }

    return render(
        request, "leave/leave_request/employee_available_leave_count.html", context
    )


@login_required
@hx_request_required
def employee_leave_count(request):
    """
    Returns the read-only leave count the employee is applying for, computed
    from the selected start/end dates, the start/end date breakdowns (half-day
    selections) and the leave type).
    """
    leave_type_id = request.GET.get("leave_type_id")
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")
    start_date_breakdown = request.GET.get("start_date_breakdown") or "full_day"
    end_date_breakdown = request.GET.get("end_date_breakdown") or "full_day"

    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        start_date = None
    try:
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        end_date = None

    leave_count = None
    if start_date:
        # Fall back to a single-day request when no (or an invalid) end date is
        # chosen; ignore ranges where the end date precedes the start date.
        if end_date is None:
            end_date = start_date
        if end_date >= start_date:
            leave_count = calculate_requested_days(
                start_date, end_date, start_date_breakdown, end_date_breakdown
            )
            leave_type = LeaveType.objects.filter(id=leave_type_id).first()
            if leave_type:
                leave_count = cal_effective_requested_days(
                    start_date, end_date, leave_type, leave_count
                )

    if leave_count is None:
        return HttpResponse(
            format_html(
                '<span class="text-muted">{}</span>',
                _("Select dates to calculate"),
            )
        )
    return HttpResponse(leave_count)


@login_required
@manager_can_enter("base.add_penaltyaccounts")
def cut_available_leave(request, instance_id):
    """
    This method is used to create the penalties
    """
    previous_data = request.GET.urlencode()
    instance = LeaveRequest.objects.get(id=instance_id)
    form = PenaltyAccountForm(employee=instance.employee_id)
    available = AvailableLeave.objects.filter(employee_id=instance.employee_id)
    if request.method == "POST":
        form = PenaltyAccountForm(request.POST)
        if form.is_valid():
            penalty_instance = form.instance
            penalty = PenaltyAccounts()
            penalty.leave_request_id = instance
            penalty.deduct_from_carry_forward = (
                penalty_instance.deduct_from_carry_forward
            )
            penalty.employee_id = instance.employee_id
            penalty.leave_type_id = penalty_instance.leave_type_id
            penalty.minus_leaves = penalty_instance.minus_leaves
            penalty.penalty_amount = penalty_instance.penalty_amount
            penalty.save()
            form = PenaltyAccountForm()
            messages.success(request, "Penalty/Fine added")
            _leave_audit(
                request,
                "Leave penalty applied",
                target=penalty,
                changes=_drop_empty(
                    {
                        "Reference": f"LR-{instance.pk}",
                        "Employee": str(penalty.employee_id),
                        "Leave type": str(penalty.leave_type_id)
                        if penalty.leave_type_id
                        else None,
                        "Leave days deducted": str(penalty.minus_leaves or 0),
                        "Deducted from carryforward": "Yes"
                        if penalty.deduct_from_carry_forward
                        else "No",
                        "Penalty amount": str(penalty.penalty_amount or 0),
                    }
                ),
            )
    return render(
        request,
        "leave/leave_request/penalty/form.html",
        {
            "available": available,
            "form": form,
            "instance": instance,
            "pd": previous_data,
        },
    )


@login_required
@hx_request_required
def create_leaverequest_comment(request, leave_id):
    """
    This method renders form and template to create Leave request comments
    """
    leave = LeaveRequest.objects.filter(id=leave_id).first()
    emp = request.user.employee_get
    form = LeaverequestcommentForm(
        initial={"employee_id": emp.id, "request_id": leave_id}
    )
    target = request.GET.get("target")
    url = "request-filter" if target == "leaveRequest" else "user-request-filter"
    previous_data = request.GET.urlencode()
    if request.method == "POST":
        form = LeaverequestcommentForm(request.POST)
        if form.is_valid():
            form.instance.employee_id = emp
            form.instance.request_id = leave
            comment_instance = form.save()
            _leave_audit(
                request,
                "Leave request comment added",
                target=comment_instance,
                changes={
                    "Reference": f"LR-{leave.pk}",
                    "Employee": str(leave.employee_id),
                    "Comment": comment_instance.comment,
                },
            )
            comments = LeaverequestComment.objects.filter(request_id=leave_id).order_by(
                "-created_at"
            )
            no_comments = False
            if not comments.exists():
                no_comments = True
            form = LeaverequestcommentForm(
                initial={"employee_id": emp.id, "request_id": leave_id}
            )
            messages.success(request, _("Comment added successfully!"))
            work_info = EmployeeWorkInformation.objects.filter(
                employee_id=leave.employee_id
            )
            if work_info.exists():
                if (
                    leave.employee_id.employee_work_info.reporting_manager_id
                    is not None
                ):
                    if request.user.employee_get.id == leave.employee_id.id:
                        rec = (
                            leave.employee_id.employee_work_info.reporting_manager_id.employee_user_id
                        )
                        notify.send(
                            request.user.employee_get,
                            recipient=rec,
                            verb=f"{leave.employee_id}'s leave request has received a comment.",
                            verb_ar=f"تلقت طلب إجازة {leave.employee_id} تعليقًا.",
                            verb_de=f"{leave.employee_id}s Urlaubsantrag hat einen Kommentar erhalten.",
                            verb_es=f"La solicitud de permiso de {leave.employee_id} ha recibido un comentario.",
                            verb_fr=f"La demande de congé de {leave.employee_id} a reçu un commentaire.",
                            redirect=reverse("request-view") + f"?id={leave.id}",
                            icon="chatbox-ellipses",
                        )
                    elif (
                        request.user.employee_get.id
                        == leave.employee_id.employee_work_info.reporting_manager_id.id
                    ):
                        rec = leave.employee_id.employee_user_id
                        notify.send(
                            request.user.employee_get,
                            recipient=rec,
                            verb="Your leave request has received a comment.",
                            verb_ar="تلقى طلب إجازتك تعليقًا.",
                            verb_de="Ihr Urlaubsantrag hat einen Kommentar erhalten.",
                            verb_es="Tu solicitud de permiso ha recibido un comentario.",
                            verb_fr="Votre demande de congé a reçu un commentaire.",
                            redirect=reverse("user-request-view") + f"?id={leave.id}",
                            icon="chatbox-ellipses",
                        )
                    else:
                        rec = [
                            leave.employee_id.employee_user_id,
                            leave.employee_id.employee_work_info.reporting_manager_id.employee_user_id,
                        ]
                        notify.send(
                            request.user.employee_get,
                            recipient=rec,
                            verb=f"{leave.employee_id}'s leave request has received a comment.",
                            verb_ar=f"تلقت طلب إجازة {leave.employee_id} تعليقًا.",
                            verb_de=f"{leave.employee_id}s Urlaubsantrag hat einen Kommentar erhalten.",
                            verb_es=f"La solicitud de permiso de {leave.employee_id} ha recibido un comentario.",
                            verb_fr=f"La demande de congé de {leave.employee_id} a reçu un commentaire.",
                            redirect=reverse("request-view") + f"?id={leave.id}",
                            icon="chatbox-ellipses",
                        )
                else:
                    rec = leave.employee_id.employee_user_id
                    notify.send(
                        request.user.employee_get,
                        recipient=rec,
                        verb="Your leave request has received a comment.",
                        verb_ar="تلقى طلب إجازتك تعليقًا.",
                        verb_de="Ihr Urlaubsantrag hat einen Kommentar erhalten.",
                        verb_es="Tu solicitud de permiso ha recibido un comentario.",
                        verb_fr="Votre demande de congé a reçu un commentaire.",
                        redirect=reverse("user-request-view") + f"?id={leave.id}",
                        icon="chatbox-ellipses",
                    )
            return render(
                request,
                "leave/leave_request/leave_comment.html",
                {
                    "comments": comments,
                    "no_comments": no_comments,
                    "request_id": leave_id,
                    "leave_request": leave,
                },
            )
    return render(
        request,
        "leave/leave_request/leave_comment.html",
        {
            "form": form,
            "request_id": leave_id,
            "pd": previous_data,
            "target": target,
            "url": url,
            "leave_request": leave,
        },
    )


@login_required
@hx_request_required
def view_leaverequest_comment(request, leave_id):
    """
    This method is used to show Leave request comments
    """
    leave_request = LeaveRequest.find(leave_id)
    if not (
        request.user.employee_get == leave_request.employee_id
        or request.user.has_perm("leave.view_leaverequestcomment")
        or is_reportingmanager(request)
    ):
        messages.warning(request, _("You don't have permission"))
        return render(request, "decorator_404.html")

    comments = LeaverequestComment.objects.filter(request_id=leave_id).order_by(
        "-created_at"
    )
    no_comments = False
    if not comments.exists():
        no_comments = True

    if request.FILES:
        files = request.FILES.getlist("files")
        comment_id = request.GET["comment_id"]
        comment = LeaverequestComment.objects.get(id=comment_id)
        attachments = []
        for file in files:
            file_instance = LeaverequestFile()
            file_instance.file = file
            file_instance.save()
            attachments.append(file_instance)
        comment.files.add(*attachments)
        _leave_audit(
            request,
            "Leave request attachment uploaded",
            target=comment,
            changes={
                "Reference": f"LR-{leave_id}",
                "Files": ", ".join(str(a.file) for a in attachments),
                "Count": str(len(attachments)),
            },
        )

    return render(
        request,
        "leave/leave_request/leave_comment.html",
        {
            "comments": comments,
            "no_comments": no_comments,
            "leave_request": leave_request,
        },
    )


@login_required
@hx_request_required
def create_allocationrequest_comment(request, leave_id):
    """
    This method renders form and template to create Allocation request comments
    """
    previous_data = request.GET.urlencode()
    leave = LeaveAllocationRequest.objects.filter(id=leave_id).first()
    emp = request.user.employee_get
    form = LeaveallocationrequestcommentForm(
        initial={"employee_id": emp.id, "request_id": leave_id}
    )

    if request.method == "POST":
        form = LeaveallocationrequestcommentForm(request.POST)
        if form.is_valid():
            form.instance.employee_id = emp
            form.instance.request_id = leave
            comment_instance = form.save()
            _leave_audit(
                request,
                "Leave allocation request comment added",
                target=comment_instance,
                changes={
                    "Reference": f"LA-{leave.pk}",
                    "Employee": str(leave.employee_id),
                    "Comment": comment_instance.comment,
                },
            )
            comments = LeaveallocationrequestComment.objects.filter(
                request_id=leave_id
            ).order_by("-created_at")
            no_comments = False
            if not comments.exists():
                no_comments = True
            form = LeaveallocationrequestcommentForm(
                initial={"employee_id": emp.id, "request_id": leave_id}
            )
            messages.success(request, _("Comment added successfully!"))
            work_info = EmployeeWorkInformation.objects.filter(
                employee_id=leave.employee_id
            )
            if work_info.exists():
                if (
                    leave.employee_id.employee_work_info.reporting_manager_id
                    is not None
                ):
                    if request.user.employee_get.id == leave.employee_id.id:
                        rec = (
                            leave.employee_id.employee_work_info.reporting_manager_id.employee_user_id
                        )
                        notify.send(
                            request.user.employee_get,
                            recipient=rec,
                            verb=f"{leave.employee_id}'s leave allocation request has received a comment.",
                            verb_ar=f"تلقت طلب تخصيص الإجازة لـ {leave.employee_id} تعليقًا.",
                            verb_de=f"{leave.employee_id}s Anfrage zur Urlaubszuweisung hat einen Kommentar erhalten.",
                            verb_es=f"La solicitud de asignación de permisos de {leave.employee_id} ha recibido un comentario.",
                            verb_fr=f"La demande d'allocation de congé de {leave.employee_id} a reçu un commentaire.",
                            redirect=reverse("leave-allocation-request-view")
                            + f"?id={leave.id}",
                            icon="chatbox-ellipses",
                        )
                    elif (
                        request.user.employee_get.id
                        == leave.employee_id.employee_work_info.reporting_manager_id.id
                    ):
                        rec = leave.employee_id.employee_user_id
                        notify.send(
                            request.user.employee_get,
                            recipient=rec,
                            verb="Your leave allocation request has received a comment.",
                            verb_ar="تلقى طلب تخصيص الإجازة الخاص بك تعليقًا.",
                            verb_de="Ihr Antrag auf Urlaubszuweisung hat einen Kommentar erhalten.",
                            verb_es="Tu solicitud de asignación de permisos ha recibido un comentario.",
                            verb_fr="Votre demande d'allocation de congé a reçu un commentaire.",
                            redirect=reverse("leave-allocation-request-view")
                            + f"?id={leave.id}",
                            icon="chatbox-ellipses",
                        )
                    else:
                        rec = [
                            leave.employee_id.employee_user_id,
                            leave.employee_id.employee_work_info.reporting_manager_id.employee_user_id,
                        ]
                        notify.send(
                            request.user.employee_get,
                            recipient=rec,
                            verb=f"{leave.employee_id}'s leave allocation request has received a comment.",
                            verb_ar=f"تلقت طلب تخصيص الإجازة لـ {leave.employee_id} تعليقًا.",
                            verb_de=f"{leave.employee_id}s Anfrage zur Urlaubszuweisung hat einen Kommentar erhalten.",
                            verb_es=f"La solicitud de asignación de permisos de {leave.employee_id} ha recibido un comentario.",
                            verb_fr=f"La demande d'allocation de congé de {leave.employee_id} a reçu un commentaire.",
                            redirect=reverse("leave-allocation-request-view")
                            + f"?id={leave.id}",
                            icon="chatbox-ellipses",
                        )
                else:
                    rec = leave.employee_id.employee_user_id
                    notify.send(
                        request.user.employee_get,
                        recipient=rec,
                        verb="Your leave allocation request has received a comment.",
                        verb_ar="تلقى طلب تخصيص الإجازة الخاص بك تعليقًا.",
                        verb_de="Ihr Antrag auf Urlaubszuweisung hat einen Kommentar erhalten.",
                        verb_es="Tu solicitud de asignación de permisos ha recibido un comentario.",
                        verb_fr="Votre demande d'allocation de congé a reçu un commentaire.",
                        redirect=reverse("leave-allocation-request-view")
                        + f"?id={leave.id}",
                        icon="chatbox-ellipses",
                    )
            return render(
                request,
                "leave/leave_allocation_request/leave_allocation_comment.html",
                {
                    "comments": comments,
                    "no_comments": no_comments,
                    "request_id": leave_id,
                },
            )
    return render(
        request,
        "leave/leave_allocation_request/leave_allocation_comment.html",
        {"form": form, "request_id": leave_id, "pd": previous_data},
    )


@login_required
@hx_request_required
def view_allocationrequest_comment(request, leave_id):
    """
    This method is used to show Allocation request comments
    """
    leave_alloc_request = LeaveAllocationRequest.find(leave_id)
    if not (
        request.user.employee_get == leave_alloc_request.employee_id
        or request.user.has_perm("leave.view_leaveallocationrequestcomment")
        or is_reportingmanager(request)
    ):
        messages.warning(request, _("You don't have permission"))
        return render(request, "decorator_404.html")
    comments = LeaveallocationrequestComment.objects.filter(
        request_id=leave_id
    ).order_by("-created_at")
    no_comments = False
    if not comments.exists():
        no_comments = True

    if request.FILES:
        files = request.FILES.getlist("files")
        comment_id = request.GET["comment_id"]
        comment = LeaveallocationrequestComment.objects.get(id=comment_id)
        attachments = []
        for file in files:
            file_instance = LeaverequestFile()
            file_instance.file = file
            file_instance.save()
            attachments.append(file_instance)
        comment.files.add(*attachments)
        _leave_audit(
            request,
            "Leave allocation request attachment uploaded",
            target=comment,
            changes={
                "Reference": f"LA-{leave_id}",
                "Files": ", ".join(str(a.file) for a in attachments),
                "Count": str(len(attachments)),
            },
        )

    return render(
        request,
        "leave/leave_allocation_request/leave_allocation_comment.html",
        {
            "comments": comments,
            "no_comments": no_comments,
            "request_id": leave_id,
            "leave_alloc_request": leave_alloc_request,
        },
    )


@login_required
@hx_request_required
def delete_allocationrequest_comment(request, comment_id):
    """
    This method is used to delete Allocation request comments
    """
    script = ""
    comment = LeaveallocationrequestComment.find(comment_id)
    request_id = comment.request_id.id
    if (
        request.user.employee_get == comment.employee_id
        or request.user.has_perm("leave.delete_leaveallocationrequestcomment")
        or is_reportingmanager(request)
    ):
        # Snapshot before delete — the instance is unusable afterwards.
        audit_changes = {
            "Reference": f"LA-{request_id}",
            "Author": str(comment.employee_id),
            "Comment": comment.comment,
        }
        comment.delete()
        messages.success(request, _("Comment deleted successfully!"))
        _leave_audit(
            request,
            "Leave allocation request comment deleted",
            changes=audit_changes,
        )
    else:
        script = f"""
                    <span hx-get="/leave/allocation-request-view-comment/{request_id}/" hx-target="#commentContainer" hx-trigger="load"></span>
                """
        messages.warning(request, _("You don't have permission"))
    return HttpResponse(script)


@login_required
def delete_allocation_comment_file(request):
    """
    Used to delete attachment
    """
    script = ""
    ids = request.GET.getlist("ids")
    leave_id = request.GET["leave_id"]
    comment_id = request.GET["comment_id"]
    comment = LeaveallocationrequestComment.find(comment_id)
    if (
        request.user.employee_get == comment.employee_id
        or request.user.has_perm("leave.delete_leaverequestfile")
        or is_reportingmanager(request)
    ):
        # Describe before delete — the rows are gone afterwards.
        deleted_files = ", ".join(
            str(attachment.file)
            for attachment in LeaverequestFile.objects.filter(id__in=ids)
        )
        LeaverequestFile.objects.filter(id__in=ids).delete()
        messages.success(request, _("File deleted successfully"))
        _leave_audit(
            request,
            "Leave allocation request attachment deleted",
            changes={
                "Reference": f"LA-{leave_id}",
                "Files": deleted_files,
                "Count": str(len(ids)),
            },
        )
    else:
        messages.warning(request, _("You don't have permission"))
        script = f"""
                <span hx-get='/leave/allocation-request-view-comment/{leave_id}/' hx-target='#commentContainer' hx-trigger='load'></span>
                """
    return HttpResponse(script)


@login_required
@hx_request_required
def view_clashes(request, leave_request_id):
    """
    This method is used to filter or view the leave clashes
    """
    record = get_object_or_404(LeaveRequest, id=leave_request_id)

    if record.status == "rejected" or record.status == "cancelled":
        overlapping_requests = LeaveRequest.objects.none()
        clashed_due_to_department = LeaveRequest.objects.none()
        clashed_due_to_job_position = LeaveRequest.objects.none()
    else:
        overlapping_requests = (
            LeaveRequest.objects.filter(
                (
                    Q(
                        employee_id__employee_work_info__department_id=record.employee_id.employee_work_info.department_id
                    )
                    | Q(
                        employee_id__employee_work_info__job_position_id=record.employee_id.employee_work_info.job_position_id
                    )
                )
                & Q(
                    employee_id__employee_work_info__company_id=record.employee_id.employee_work_info.company_id
                ),
                start_date__lte=record.end_date,
                end_date__gte=record.start_date,
            )
            .exclude(id=leave_request_id)
            .exclude(Q(status="cancelled") | Q(status="rejected"))
        )

        clashed_due_to_department = overlapping_requests.filter(
            employee_id__employee_work_info__department_id=record.employee_id.employee_work_info.department_id
        )

        clashed_due_to_job_position = overlapping_requests.filter(
            employee_id__employee_work_info__job_position_id=record.employee_id.employee_work_info.job_position_id
        )

    leave_request_filter = LeaveRequestFilter(request.GET, overlapping_requests).qs
    leave_request_filter = paginator_qry(leave_request_filter, request.GET.get("page"))

    requests_ids = json.dumps(
        [instance.id for instance in leave_request_filter.object_list]
    )

    return render(
        request,
        "leave/leave_request/leave_clashes.html",
        {
            "leave_request": record,
            "records": overlapping_requests,
            "current_date": date.today(),
            "requests_ids": requests_ids,
            "clashed_due_to_department": clashed_due_to_department,
            "clashed_due_to_job_position": clashed_due_to_job_position,
        },
    )


@login_required
@permission_required("leave.view_leavegeneralsetting")
def compensatory_leave_settings_view(request):
    enabled_compensatory = (
        LeaveGeneralSetting.objects.exists()
        and LeaveGeneralSetting.objects.first().compensatory_leave
    )
    leave_type, create = LeaveType.objects.get_or_create(
        is_compensatory_leave=True,
        defaults={"name": "Compensatory Leave Type", "payment": "paid"},
    )
    context = {"enabled_compensatory": enabled_compensatory, "leave_type": leave_type}
    return render(request, "compensatory_settings.html", context)


@login_required
@permission_required("leave.add_leavegeneralsetting")
def enable_compensatory_leave(request):
    """
    This method is used to enable/disable the compensatory leave feature
    """
    compensatory_leave = LeaveGeneralSetting.objects.first()
    compensatory_leave = (
        compensatory_leave if compensatory_leave else LeaveGeneralSetting()
    )
    compensatory_leave.compensatory_leave = "compensatory_leave" in request.GET.keys()
    compensatory_leave.save()
    if "compensatory_leave" in request.GET.keys():
        messages.success(request, _("Compensatory leave is enabled successfully!"))
    else:
        messages.success(request, _("Compensatory leave is disabled successfully!"))
    return HttpResponse("")


@login_required
@hx_request_required
def delete_leaverequest_comment(request, comment_id):
    """
    This method is used to delete Leave request comments
    """
    script = ""
    comment = LeaverequestComment.find(comment_id)
    if (
        request.user.employee_get == comment.employee_id
        or request.user.has_perm("leave.delete_leaverequestcomment")
        or is_reportingmanager(request)
    ):
        # Snapshot before delete — the instance is unusable afterwards.
        audit_changes = {
            "Reference": f"LR-{comment.request_id.id}",
            "Author": str(comment.employee_id),
            "Comment": comment.comment,
        }
        comment.delete()
        messages.success(request, _("Comment deleted successfully!"))
        _leave_audit(
            request, "Leave request comment deleted", changes=audit_changes
        )
    else:
        messages.warning(request, _("You don't have permission"))
        script = f"""
            <span hx-get="/leave/leave-request-view-comment/{comment.request_id.id}/?&amp;target=leaveRequest" hx-target="#commentContainer" hx-trigger="load"></span>
        """
    return HttpResponse(script)


@login_required
def delete_leave_comment_file(request):
    """
    Used to delete attachment
    """
    script = ""
    ids = request.GET.getlist("ids")
    leave_id = request.GET["leave_id"]
    comment_id = request.GET["comment_id"]
    comment = LeaverequestComment.find(comment_id)
    if (
        request.user.employee_get == comment.employee_id
        or request.user.has_perm("leave.delete_leaverequestfile")
        or is_reportingmanager(request)
    ):
        # Describe before delete — the rows are gone afterwards.
        deleted_files = ", ".join(
            str(attachment.file)
            for attachment in LeaverequestFile.objects.filter(id__in=ids)
        )
        LeaverequestFile.objects.filter(id__in=ids).delete()
        messages.success(request, _("File deleted successfully"))
        _leave_audit(
            request,
            "Leave request attachment deleted",
            changes={
                "Reference": f"LR-{leave_id}",
                "Files": deleted_files,
                "Count": str(len(ids)),
            },
        )
    else:
        messages.warning(request, _("You don't have permission"))
        script = f"""
            <span hx-get="/leave/leave-request-view-comment/{leave_id}/?&amp;target=leaveRequest" hx-target="#commentContainer" hx-trigger="load"></span>
        """
    return HttpResponse(script)


if apps.is_installed("attendance"):
    from leave.models import CompensatoryLeaveRequest, CompensatoryLeaverequestComment

    @login_required
    def get_leave_attendance_dates(request):
        """
        function used to return attendance dates that taken on leave days .

        Parameters:
        request (HttpRequest): The HTTP request object.

        Returns:
        GET : return attendance dates
        """
        if request.GET.get("employee_id"):
            employee = Employee.objects.get(id=request.GET.get("employee_id"))
            holiday_attendance = get_leave_day_attendance(employee)
            # Get a list of tuples containing (id, attendance_date)
            attendance_dates = list(
                holiday_attendance.values_list("id", "attendance_date")
            )
            form = CompensatoryLeaveForm()
            form.fields["attendance_id"].choices = attendance_dates
            attendance_id = render_to_string(
                "leave/compensatory_leave/attendance_id.html",
                {
                    "form": form,
                },
            )
            return HttpResponse(f"{attendance_id}")

    @login_required
    def delete_comment_compensatory_file(request):
        """
        Used to delete attachment
        """
        ids = request.GET.getlist("ids")
        # Describe before delete — the rows are gone afterwards.
        deleted_files = ", ".join(
            str(attachment.file)
            for attachment in LeaverequestFile.objects.filter(id__in=ids)
        )
        LeaverequestFile.objects.filter(id__in=ids).delete()
        leave_id = request.GET["leave_id"]
        _leave_audit(
            request,
            "Compensatory leave attachment deleted",
            changes={
                "Reference": f"CL-{leave_id}",
                "Files": deleted_files,
                "Count": str(len(ids)),
            },
        )
        comments = CompensatoryLeaverequestComment.objects.all()
        if not request.user.has_perm("leave.delete_compensatoryleaverequestcomment"):
            comments = comments.filter(employee_id__employee_user_id=request.user)
        if request.GET.get("compensatory"):
            comments = comments.filter(request_id=leave_id).order_by("-created_at")
            template = "leave/compensatory_leave/compensatory_leave_comment.html"
        else:
            comments = comments.filter(request_id=leave_id).order_by("-created_at")
            template = "leave/leave_request/leave_comment.html"
        return render(
            request,
            template,
            {
                "comments": comments,
                "request_id": leave_id,
            },
        )

    @login_required
    @hx_request_required
    def delete_leaverequest_compensatory_comment(request, comment_id):
        """
        This method is used to delete Leave request comments
        """
        if request.GET.get("compensatory"):
            comment = CompensatoryLeaverequestComment.objects.filter(id=comment_id)
            if not request.user.has_perm(
                "leave.delete_compensatoryleaverequestcomment"
            ):
                comment = comment.filter(employee_id__employee_user_id=request.user)
            redirect_url = "view-compensatory-leave-comment"
        else:
            comment = LeaverequestComment.objects.filter(id=comment_id)
            if not request.user.has_perm("leave.delete_leaverequestcomment"):
                comment = comment.filter(employee_id__employee_user_id=request.user)
            redirect_url = "leave-request-view-comment"
        first_comment = comment.first()
        leave_id = first_comment.request_id.id
        # Snapshot before delete — the rows are gone afterwards.
        audit_changes = {
            "Reference": (
                f"CL-{leave_id}" if request.GET.get("compensatory") else f"LR-{leave_id}"
            ),
            "Author": str(first_comment.employee_id),
            "Comment": first_comment.comment,
        }
        comment.delete()
        messages.success(request, _("Comment deleted successfully!"))
        _leave_audit(
            request,
            "Compensatory leave comment deleted"
            if request.GET.get("compensatory")
            else "Leave request comment deleted",
            changes=audit_changes,
        )
        return redirect(redirect_url, leave_id)

    @login_required
    @is_compensatory_leave_enabled()
    def view_compensatory_leave(request):
        """
        function used to view compensatory leave requests.

        Parameters:
        request (HttpRequest): The HTTP request object.

        Returns:
        GET : return compensetory leave request view template
        """
        employee = request.user.employee_get
        queryset = CompensatoryLeaveRequest.objects.all().order_by("-id")
        queryset = CompensatoryLeaveRequestFilter(request.GET, queryset).qs
        queryset = filtersubordinates(
            request, queryset, "leave.view_compensatoryleaverequest"
        )
        page_number = request.GET.get("page")
        comp_leave_requests = paginator_qry(queryset, page_number)
        requests_ids = json.dumps(
            list(comp_leave_requests.object_list.values_list("id", flat=True))
        )
        my_comp_leave_requests = CompensatoryLeaveRequest.objects.filter(
            employee_id=employee.id
        ).order_by("-id")
        my_comp_leave_requests = CompensatoryLeaveRequestFilter(
            request.GET, my_comp_leave_requests
        ).qs
        my_page_number = request.GET.get("m_page")
        my_comp_leave_requests = paginator_qry(my_comp_leave_requests, my_page_number)
        my_requests_ids = json.dumps(
            list(my_comp_leave_requests.object_list.values_list("id", flat=True))
        )
        comp_leave_requests_filter = CompensatoryLeaveRequestFilter()
        previous_data = request.GET.urlencode()
        data_dict = parse_qs(previous_data)
        data_dict = get_key_instances(CompensatoryLeaveRequest, data_dict)
        context = {
            "my_comp_leave_requests": my_comp_leave_requests,
            "comp_leave_requests": comp_leave_requests,
            "pd": previous_data,
            "form": comp_leave_requests_filter.form,
            "filter_dict": data_dict,
            "gp_fields": LeaveAllocationRequestReGroup.fields,
            "requests_ids": requests_ids,
            "my_requests_ids": my_requests_ids,
        }
        return render(
            request, "leave/compensatory_leave/compensatory_leave_view.html", context
        )

    @login_required
    @is_compensatory_leave_enabled()
    @hx_request_required
    def filter_compensatory_leave(request):
        """
        function used to view compensatory leave requests.
        """
        field = request.GET.get("field")
        employee = request.user.employee_get
        page_number = request.GET.get("page")
        my_page_number = request.GET.get("m_page")
        previous_data = request.GET.urlencode()
        template = "leave/compensatory_leave/compensatory_leave_req_list.html"

        # Filter compensatory leave requests
        comp_leave_requests_filtered = CompensatoryLeaveRequestFilter(
            request.GET
        ).qs.order_by("-id")
        my_comp_leave_requests_filtered = CompensatoryLeaveRequest.objects.filter(
            employee_id=employee.id
        ).order_by("-id")
        my_comp_leave_requests_filtered = CompensatoryLeaveRequestFilter(
            request.GET, my_comp_leave_requests_filtered
        ).qs
        comp_leave_requests_filtered = filtersubordinates(
            request, comp_leave_requests_filtered, "leave.view_leaveallocationrequest"
        )

        # Sort compensatory leave requests if requested
        if request.GET.get("sortby"):
            comp_leave_requests_filtered = sortby(
                request, comp_leave_requests_filtered, "sortby"
            )
            my_comp_leave_requests_filtered = sortby(
                request, my_comp_leave_requests_filtered, "sortby"
            )

        # Group compensatory leave requests if field parameter is provided
        if field:
            comp_leave_requests = group_by_queryset(
                comp_leave_requests_filtered, field, page_number, "page"
            )
            my_comp_leave_requests = group_by_queryset(
                my_comp_leave_requests_filtered, field, my_page_number, "m_page"
            )

            # Convert IDs to JSON format for details view
            list_values = [entry["list"] for entry in comp_leave_requests]
            id_list = [
                instance.id for value in list_values for instance in value.object_list
            ]
            requests_ids = json.dumps(list(id_list))

            list_values = [entry["list"] for entry in my_comp_leave_requests]
            id_list = [
                instance.id for value in list_values for instance in value.object_list
            ]
            my_requests_ids = json.dumps(list(id_list))
            template = (
                "leave/leave_allocation_request/leave_allocation_request_group_by.html"
            )
        else:
            comp_leave_requests = paginator_qry(
                comp_leave_requests_filtered, page_number
            )
            my_comp_leave_requests = paginator_qry(
                my_comp_leave_requests_filtered, my_page_number
            )
            requests_ids = json.dumps(
                list(comp_leave_requests.object_list.values_list("id", flat=True))
            )
            my_requests_ids = json.dumps(
                list(my_comp_leave_requests.object_list.values_list("id", flat=True))
            )

        # Parse previous data and construct context for filter tag
        data_dict = parse_qs(previous_data)
        data_dict = get_key_instances(CompensatoryLeaveRequest, data_dict)
        data_dict.pop("m_page", None)

        context = {
            "comp_leave_requests": comp_leave_requests,
            "my_comp_leave_requests": my_comp_leave_requests,
            "pd": previous_data,
            "filter_dict": data_dict,
            "field": field,
            "requests_ids": requests_ids,
            "my_requests_ids": my_requests_ids,
        }
        return render(request, template, context=context)

    @login_required
    @is_compensatory_leave_enabled()
    @hx_request_required
    def create_compensatory_leave(request, comp_id=None):
        """
        function used to create or update compensatory leave request.

        Parameters:
        request (HttpRequest): The HTTP request object.

        Returns:
        GET : return leave allocation request form template
        POST : return leave allocation request view
        """
        employee = request.user.employee_get
        template = "leave/compensatory_leave/comp_leave_form.html"
        instance = None
        if comp_id != None:
            instance = CompensatoryLeaveRequest.objects.get(id=comp_id)
        form = CompensatoryLeaveForm(instance=instance)
        if request.method == "POST":
            form = CompensatoryLeaveForm(request.POST, instance=instance)
            if form.is_valid():
                comp_req = form.save()
                comp_req.requested_days = attendance_days(
                    comp_req.employee_id, comp_req.attendance_id.all()
                )
                comp_req.save()
                if comp_id != None:
                    messages.success(request, _("Compensatory Leave updated."))
                    log_form_changes(
                        request.user,
                        "leave",
                        "Compensatory leave request updated",
                        form=form,
                        target=comp_req,
                    )
                else:
                    messages.success(request, _("Compensatory Leave created."))
                    _leave_audit(
                        request,
                        "Compensatory leave requested",
                        target=comp_req,
                        changes={
                            "Reference": f"CL-{comp_req.pk}",
                            "Employee": str(comp_req.employee_id),
                            "Days requested": str(comp_req.requested_days),
                            "Status": comp_req.get_status_display(),
                        },
                    )
                return HttpResponse("<script>window.location.reload();</script>")

        context = {
            "employee": employee,
            "form": form,
        }
        return render(request, template, context)

    @login_required
    @is_compensatory_leave_enabled()
    @hx_request_required
    @owner_can_enter(
        perm="leave.delete_compensatoryleaverequest",
        model=CompensatoryLeaveRequest,
        manager_access=True,
    )
    def delete_compensatory_leave(request, comp_id):
        """
        function used to delete compensatory leave request,
        and reload the list view of compensatory leave requests.
        """
        try:
            comp_leave_req = CompensatoryLeaveRequest.objects.get(id=comp_id)
            # Snapshot before delete — the instance is unusable afterwards.
            audit_changes = {
                "Reference": f"CL-{comp_leave_req.pk}",
                "Employee": str(comp_leave_req.employee_id),
                "Days requested": str(comp_leave_req.requested_days),
                "Status": comp_leave_req.get_status_display(),
            }
            comp_leave_req.delete()
            messages.success(request, _("Compensatory leave request deleted."))
            _leave_audit(
                request,
                "Compensatory leave request deleted",
                changes=audit_changes,
            )

        except:
            messages.error(request, _("Sorry, something went wrong!"))
        if request.GET.get("list") == "True":
            return redirect(filter_compensatory_leave)
        else:
            return HttpResponse("<script>location.reload();</script>")

    @login_required
    @is_compensatory_leave_enabled()
    @hx_request_required
    @manager_can_enter(perm="leave.change_compensatoryleaverequest")
    def approve_compensatory_leave(request, comp_id):
        """
        function used to approve compensatory leave request,
        and reload the list view of compensatory leave requests.
        """
        try:
            comp_leave_req = CompensatoryLeaveRequest.objects.get(id=comp_id)
            if comp_leave_req.status == "requested":
                status_before = comp_leave_req.get_status_display()
                comp_leave_req.status = "approved"
                comp_leave_req.assign_compensatory_leave_type()
                comp_leave_req.save()
                messages.success(request, _("Compensatory leave request approved."))
                _leave_audit(
                    request,
                    "Compensatory leave approved",
                    target=comp_leave_req,
                    changes={
                        "Reference": f"CL-{comp_leave_req.pk}",
                        "Employee": str(comp_leave_req.employee_id),
                        "Days requested": str(comp_leave_req.requested_days),
                        **_status_change(
                            status_before, comp_leave_req.get_status_display()
                        ),
                    },
                )
                with contextlib.suppress(Exception):
                    notify.send(
                        request.user.employee_get,
                        recipient=comp_leave_req.employee_id.employee_user_id,
                        verb="Your compensatory leave request has been approved",
                        verb_ar="تمت الموافقة على طلب إجازة الاعتذار الخاص بك",
                        verb_de="Ihr Antrag auf Freizeitausgleich wurde genehmigt",
                        verb_es="Su solicitud de permiso compensatorio ha sido aprobada",
                        verb_fr="Votre demande de congé compensatoire a été approuvée",
                        redirect=reverse("view-compensatory-leave")
                        + f"?id={comp_leave_req.id}",
                    )
            else:
                messages.info(
                    request,
                    _(
                        "The compensatory leave request is not in the 'requested' status."
                    ),
                )
        except:
            messages.error(request, _("Sorry, something went wrong!"))
        if request.GET.get("individual"):
            return _trigger_leave_stats_refresh(
                HttpResponse("<script>location.reload();</script>"),
                request,
            )
        return _trigger_leave_stats_refresh(
            redirect(filter_compensatory_leave), request
        )

    @login_required
    @is_compensatory_leave_enabled()
    @hx_request_required
    @manager_can_enter(perm="leave.delete_compensatoryleaverequest")
    def reject_compensatory_leave(request, comp_id):
        """
        function used to Reject compensatoey leave request.

        Parameters:
        request (HttpRequest): The HTTP request object.
        comp_id : compensatory leave request id

        Returns:
        GET : It returns to the default compensatory leave request view template.

        """
        comp_leave_req = CompensatoryLeaveRequest.objects.get(id=comp_id)
        if comp_leave_req.status == "requested" or comp_leave_req.status == "approved":
            form = CompensatoryLeaveRequestRejectForm()
            if request.method == "POST":
                form = CompensatoryLeaveRequestRejectForm(request.POST)
                if form.is_valid():
                    status_before = comp_leave_req.get_status_display()
                    comp_leave_req.reject_reason = form.cleaned_data["reason"]
                    comp_leave_req.status = "rejected"
                    comp_leave_req.exclude_compensatory_leave()
                    comp_leave_req.save()
                    messages.success(request, _("Compensatory Leave request rejected."))
                    _leave_audit(
                        request,
                        "Compensatory leave rejected",
                        target=comp_leave_req,
                        changes={
                            "Reference": f"CL-{comp_leave_req.pk}",
                            "Employee": str(comp_leave_req.employee_id),
                            "Days requested": str(comp_leave_req.requested_days),
                            "Reason": comp_leave_req.reject_reason,
                            **_status_change(
                                status_before, comp_leave_req.get_status_display()
                            ),
                        },
                    )
                    with contextlib.suppress(Exception):
                        notify.send(
                            request.user.employee_get,
                            recipient=comp_leave_req.employee_id.employee_user_id,
                            verb="Your compensatory leave request has been rejected",
                            verb_ar="تم رفض طلبك للإجازة التعويضية",
                            verb_de="Ihr Antrag auf Freizeitausgleich wurde abgelehnt",
                            verb_es="Se ha rechazado su solicitud de permiso compensatorio",
                            verb_fr="Votre demande de congé compensatoire a été rejetée",
                            redirect=reverse("view-compensatory-leave")
                            + f"?id={comp_leave_req.id}",
                        )
                    return _trigger_leave_stats_refresh(
                        HttpResponse("<script>location.reload();</script>"),
                        request,
                    )
            return render(
                request,
                "leave/compensatory_leave/compensatory_leave_reject_form..html",
                {"form": form, "comp_id": comp_id},
            )
        else:
            messages.error(request, _("The leave allocation request can't be rejected"))
            return _trigger_leave_stats_refresh(
                HttpResponse("<script>location.reload();</script>"),
                request,
            )

    @login_required
    @is_compensatory_leave_enabled()
    @hx_request_required
    def compensatory_leave_individual_view(request, comp_leave_id):
        """
        function used to present the compensatory leave request detailed view.

        Parameters:
        request (HttpRequest): The HTTP request object.
        comp_leave_id : compensatory leave request id

        Returns:
        return compensatory leave request single view
        """
        requests_ids_json = request.GET.get("instances_ids")
        if requests_ids_json:
            requests_ids = json.loads(requests_ids_json)
            previous_id, next_id = closest_numbers(requests_ids, comp_leave_id)
        comp_leave_req = CompensatoryLeaveRequest.objects.get(id=comp_leave_id)
        context = {
            "comp_leave_req": comp_leave_req,
            "my_request": eval_validate(request.GET.get("my_request")),
            "instances_ids": requests_ids_json,
            "previous": previous_id,
            "next": next_id,
        }
        return render(
            request,
            "leave/compensatory_leave/individual_view_compensatory.html",
            context=context,
        )

    @login_required
    @is_compensatory_leave_enabled()
    @hx_request_required
    def view_compensatory_leave_comment(request, comp_leave_id):
        """
        This method is used to show Leave request comments
        """
        comments = CompensatoryLeaverequestComment.objects.filter(
            request_id=comp_leave_id
        ).order_by("-created_at")
        no_comments = False
        if not comments.exists():
            no_comments = True

        if request.FILES:
            files = request.FILES.getlist("files")
            comment_id = request.GET["comment_id"]
            comment = CompensatoryLeaverequestComment.objects.get(id=comment_id)
            attachments = []
            for file in files:
                file_instance = LeaverequestFile()
                file_instance.file = file
                file_instance.save()
                attachments.append(file_instance)
            comment.files.add(*attachments)
            _leave_audit(
                request,
                "Compensatory leave attachment uploaded",
                target=comment,
                changes={
                    "Reference": f"CL-{comp_leave_id}",
                    "Files": ", ".join(str(a.file) for a in attachments),
                    "Count": str(len(attachments)),
                },
            )

        return render(
            request,
            "leave/compensatory_leave/compensatory_leave_comment.html",
            {
                "comments": comments,
                "no_comments": no_comments,
                "request_id": comp_leave_id,
            },
        )

    @login_required
    @is_compensatory_leave_enabled()
    @hx_request_required
    def create_compensatory_leave_comment(request, comp_leave_id):
        """
        This method renders form and template to create Compensatory leave comments
        """
        comp_leave = CompensatoryLeaveRequest.objects.filter(id=comp_leave_id).first()
        emp = request.user.employee_get
        form = CompensatoryLeaveRequestcommentForm(
            initial={"employee_id": emp.id, "request_id": comp_leave}
        )
        target = request.GET.get("target")
        url = "request-filter" if target == "leaveRequest" else "user-request-filter"
        previous_data = request.GET.urlencode()
        if request.method == "POST":
            form = CompensatoryLeaveRequestcommentForm(request.POST)
            if form.is_valid():
                form.instance.employee_id = emp
                form.instance.request_id = comp_leave
                comment_instance = form.save()
                _leave_audit(
                    request,
                    "Compensatory leave comment added",
                    target=comment_instance,
                    changes={
                        "Reference": f"CL-{comp_leave.pk}",
                        "Employee": str(comp_leave.employee_id),
                        "Comment": comment_instance.comment,
                    },
                )
                comments = CompensatoryLeaverequestComment.objects.filter(
                    request_id=comp_leave
                ).order_by("-created_at")
                no_comments = False
                if not comments.exists():
                    no_comments = True
                form = CompensatoryLeaveRequestcommentForm(
                    initial={"employee_id": emp.id, "request_id": comp_leave}
                )
                messages.success(request, _("Comment added successfully!"))
                work_info = EmployeeWorkInformation.objects.filter(
                    employee_id=comp_leave.employee_id
                )
                if work_info.exists():
                    if (
                        comp_leave.employee_id.employee_work_info.reporting_manager_id
                        is not None
                    ):
                        if request.user.employee_get.id == comp_leave.employee_id.id:
                            rec = (
                                comp_leave.employee_id.employee_work_info.reporting_manager_id.employee_user_id
                            )
                            notify.send(
                                request.user.employee_get,
                                recipient=rec,
                                verb=f"{comp_leave.employee_id}'s Compensatory leave request has received a comment.",
                                verb_ar=f"تلقى طلب إجازة الاعتذار لـ {comp_leave.employee_id} تعليقًا.",
                                verb_de=f"Der Antrag auf Freizeitausgleich von {comp_leave.employee_id} hat einen Kommentar erhalten.",
                                verb_es=f"La solicitud de permiso compensatorio de {comp_leave.employee_id} ha recibido un comentario.",
                                verb_fr=f"La demande de congé compensatoire de {comp_leave.employee_id} a reçu un commentaire.",
                                redirect=reverse("view-compensatory-leave")
                                + f"?id={comp_leave.id}",
                                icon="chatbox-ellipses",
                            )
                        elif (
                            request.user.employee_get.id
                            == comp_leave.employee_id.employee_work_info.reporting_manager_id.id
                        ):
                            rec = comp_leave.employee_id.employee_user_id
                            notify.send(
                                request.user.employee_get,
                                recipient=rec,
                                verb="Your compensatory leave request has received a comment.",
                                verb_ar="تلقى طلب إجازة العوض الخاص بك تعليقًا.",
                                verb_de="Ihr Antrag auf Freizeitausgleich hat einen Kommentar erhalten.",
                                verb_es="Su solicitud de permiso compensatorio ha recibido un comentario.",
                                verb_fr="Votre demande de congé compensatoire a reçu un commentaire.",
                                redirect=reverse("view-compensatory-leave")
                                + f"?id={comp_leave.id}",
                                icon="chatbox-ellipses",
                            )
                        else:
                            rec = [
                                comp_leave.employee_id.employee_user_id,
                                comp_leave.employee_id.employee_work_info.reporting_manager_id.employee_user_id,
                            ]
                            notify.send(
                                request.user.employee_get,
                                recipient=rec,
                                verb=f"{comp_leave.employee_id}'s compensatory leave request has received a comment.",
                                verb_ar=f"تلقى طلب إجازة التعويض لـ {comp_leave.employee_id} تعليقًا.",
                                verb_de=f"Der Antrag auf Freizeitausgleich von {comp_leave.employee_id} hat einen Kommentar erhalten.",
                                verb_es=f"El pedido de permiso compensatorio de {comp_leave.employee_id} ha recibido un comentario.",
                                verb_fr=f"La demande de congé compensatoire de {comp_leave.employee_id} a reçu un commentaire.",
                                redirect=reverse("view-compensatory-leave")
                                + f"?id={comp_leave.id}",
                                icon="chatbox-ellipses",
                            )
                    else:
                        rec = comp_leave.employee_id.employee_user_id
                        notify.send(
                            request.user.employee_get,
                            recipient=rec,
                            verb="Your compensatory leave request has received a comment.",
                            verb_ar="تلقى طلب إجازة العوض الخاص بك تعليقًا.",
                            verb_de="Ihr Antrag auf Freizeitausgleich hat einen Kommentar erhalten.",
                            verb_es="Su solicitud de permiso compensatorio ha recibido un comentario.",
                            verb_fr="Votre demande de congé compensatoire a reçu un commentaire.",
                            redirect=reverse("view-compensatory-leave")
                            + f"?id={comp_leave.id}",
                            icon="chatbox-ellipses",
                        )
                return render(
                    request,
                    "leave/compensatory_leave/compensatory_leave_comment.html",
                    {
                        "comments": comments,
                        "no_comments": no_comments,
                        "request_id": comp_leave_id,
                    },
                )
        return render(
            request,
            "leave/compensatory_leave/compensatory_leave_comment.html",
            {
                "form": form,
                "request_id": comp_leave_id,
                "pd": previous_data,
                "target": target,
                "url": url,
            },
        )


if apps.is_installed("recruitment"):

    def check_interview_conflicts(request):
        start_date = request.GET.get("start_date")
        end_date = request.GET.get("end_date")
        employee_id = request.GET.get("employee_id")

        try:
            start_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
            end_date_obj = datetime.strptime(end_date, "%Y-%m-%d")
            delta = start_date_obj - end_date_obj
            date_list = [
                start_date_obj + timedelta(days=i) for i in range(delta.days + 1)
            ]
            InterviewSchedule = get_horilla_model_class(
                app_label="recruitment", model="interviewschedule"
            )

            interviews = InterviewSchedule.objects.filter(
                employee_id=employee_id, interview_date__in=date_list
            )

            response = {
                "interviews": list(
                    interviews.values_list("candidate_id__name", flat=True)
                ),
            }
            return JsonResponse(response)
        except Exception as e:
            logger.error(e)
            return JsonResponse(e)


@login_required
@permission_required("leave.view_leavegeneralsetting")
def employee_past_leave_restriction(request):
    enabled_restriction = EmployeePastLeaveRestrict.objects.first()
    if not enabled_restriction:
        enabled_restriction = EmployeePastLeaveRestrict.objects.create(enabled=True)
    if request.method == "POST":
        enabled = request.POST.get("enabled")
        if enabled:
            enabled_restriction.enabled = True
        else:
            enabled_restriction.enabled = False
        enabled_restriction.save()

    return render(
        request,
        "leave/settings/past_leave_restrict_view.html",
        {"enabled_restriction": enabled_restriction},
    )


@login_required
def employee_profile_leave_tab(request):
    """
    This method is used to view own profile of employee.
    """
    user = request.user
    employee = request.user.employee_get
    if apps.is_installed("leave"):
        instances = LeaveRequest.objects.filter(employee_id=employee)
        user_leaves = employee.available_leave.all().exclude(
            leave_type_id__is_compensatory_leave=True
        )
        if (
            LeaveGeneralSetting.objects.first()
            and LeaveGeneralSetting.objects.first().compensatory_leave
        ):
            user_leaves = employee.available_leave.all()
    else:
        user_leaves = None
        instances = None

    leave_request_ids = (
        json.dumps([instance.id for instance in instances])
        if instances
        else json.dumps([])
    )
    today = datetime.today()
    now = timezone.now()
    # Scope the leave-request list to the current calendar year so it lines
    # up with the year-bounded kanban totals (leave_taken, pending, balance).
    current_year = today.year
    current_year_leave_requests = (
        LeaveRequest.objects.filter(
            employee_id=employee,
            start_date__year=current_year,
        ).order_by("-start_date")
        if apps.is_installed("leave")
        else LeaveRequest.objects.none()
    )
    return render(
        request,
        "employee/profile/profile_view.html",
        {
            "employee": employee,
            "user_leaves": user_leaves,
            "leave_request_ids": leave_request_ids,
            "current_date": today,
            "now": now,
            "current_year_leave_requests": current_year_leave_requests,
        },
    )


@login_required
def employee_view_individual_leave_tab(request, obj_id, **kwargs):
    """
    This method is used to view profile of an employee.
    """
    employee = Employee.objects.get(id=obj_id)
    instances = (
        LeaveRequest.objects.filter(employee_id=employee)
        if apps.is_installed("leave")
        else None
    )
    leave_request_ids = (
        json.dumps([instance.id for instance in instances])
        if instances
        else json.dumps([])
    )
    employee_leaves = employee.available_leave.all()
    filtered_employee_ids = request.session.get("filtered_employees", [])
    filtered_employees = Employee.objects.filter(id__in=filtered_employee_ids)

    request_ids_str = json.dumps(
        [
            instance.id
            for instance in paginator_qry(
                filtered_employees, request.GET.get("page")
            ).object_list
        ]
    )

    # Convert the string to an actual list of integers
    requests_ids = (
        ast.literal_eval(request_ids_str)
        if isinstance(request_ids_str, str)
        else request_ids_str
    )

    employee_id = employee.id
    previous_id = None
    next_id = None

    for index, req_id in enumerate(requests_ids):
        if req_id == employee_id:

            if index == len(requests_ids) - 1:
                next_id = None
            else:
                next_id = requests_ids[index + 1]
            if index == 0:
                previous_id = None
            else:
                previous_id = requests_ids[index - 1]
            break

    # Scope the leave-request list to the current calendar year so it lines
    # up with the year-bounded kanban totals (leave_taken, pending, balance).
    current_year = date.today().year
    current_year_leave_requests = LeaveRequest.objects.filter(
        employee_id=employee,
        start_date__year=current_year,
    ).order_by("-start_date")

    context = {
        "employee": employee,
        "previous": previous_id,
        "next": next_id,
        "requests_ids": requests_ids,
        "current_date": date.today(),
        "leave_request_ids": leave_request_ids,
        "current_year_leave_requests": current_year_leave_requests,
    }
    # if the requesting user opens own data
    if request.user.employee_get == employee:
        context["user_leaves"] = employee_leaves
    else:
        context["employee_leaves"] = employee_leaves

    return render(request, "tabs/leave-tab.html", context=context)


@login_required
def leave_request_and_approve(request):
    previous_data = request.GET.urlencode()
    page_number = request.GET.get("page")
    leave_requests = LeaveRequest.objects.filter(
        status="requested",
        employee_id__is_active=True,
        start_date__gte=date.today(),
    )
    leave_requests = filtersubordinates(
        request, leave_requests, "leave.change_leaverequest"
    )

    # Filter the initial query set for multi_approve_requests
    multi_approve_requests = LeaveRequestConditionApproval.objects.filter(
        is_approved=False, is_rejected=False
    )
    if multi_approve_requests:
        multi_ids = [request.leave_request_id.id for request in multi_approve_requests]

        # Create a new list excluding leave requests with IDs in multi_ids
        leave_requests = [
            leave for leave in leave_requests if leave.id not in multi_ids
        ]

    leave_requests = paginator_qry(leave_requests, page_number)
    leave_requests_ids = json.dumps([instance.id for instance in leave_requests])
    return render(
        request,
        "leave/dashboard/leave_request_approve.html",
        {
            "leave_requests": leave_requests,
            "requests_ids": leave_requests_ids,
            "pd": previous_data,
            # "current_date":date.today(),
        },
    )


@login_required
def leave_allocation_approve(request):
    previous_data = request.GET.urlencode()
    page_number = request.GET.get("page")
    allocation_requests = LeaveAllocationRequest.objects.filter(
        status="requested", employee_id__is_active=True
    )
    allocation_requests = filtersubordinates(
        request, allocation_requests, "leave.view_leaveallocationrequest"
    )
    allocation_requests = paginator_qry(allocation_requests, page_number)
    allocation_reqests_ids = json.dumps(
        [instance.id for instance in allocation_requests]
    )
    return render(
        request,
        "leave/dashboard/leave_allocation_approve.html",
        {
            "allocation_requests": allocation_requests,
            "reqests_ids": allocation_reqests_ids,
            "pd": previous_data,
            # "current_date":date.today(),
        },
    )

def monthly_leave_report_form(request):
    return render(request, "leave/monthly_leave_report_form.html")



def monthly_leave_report(request):
    start = request.POST.get("start_date")
    end = request.POST.get("end_date")

    leaves = LeaveRequest.objects.filter(
        start_date__gte=start,
        end_date__lte=end,
        is_active=True
    ).select_related("employee_id", "leave_type_id")

    wb = Workbook()
    ws = wb.active
    ws.title = "Leave Report"

    headers = [
        "Number",
        "Name",
        "Department",
        "Leave Type",
        "Leave Reason",
        "Status",
        "Start Date",
        "End Date",
        "Session Time",
        "No Of Days",
        "Remarks"
    ]

    ws.append(headers)

    for leave in leaves:
        ws.append([
            leave.employee_id.badge_id,
            leave.employee_id.get_full_name(),
            str(leave.employee_id.get_department() or ""),
            leave.leave_type_id.name,
            leave.description or "",
            leave.status,
            leave.start_date.strftime("%Y-%m-%d"),
            leave.end_date.strftime("%Y-%m-%d"),
            leave.start_date_breakdown or "",
            leave.requested_days,
            leave.reject_reason or "",
        ])

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="monthly_leave_report.xlsx"'

    wb.save(response)
    _leave_audit(
        request,
        "Monthly leave report exported (Excel)",
        changes={
            "File": "monthly_leave_report.xlsx",
            "From": start,
            "To": end,
            "Leave requests included": str(len(leaves)),
        },
    )
    return response

@login_required
@manager_can_enter("leave.view_leaverequest")
def monthly_leave_report_pdf(request):
    if request.method != "POST":
        return HttpResponseBadRequest(_("Invalid request method."))

    start = request.POST.get("start_date")
    end = request.POST.get("end_date")

    if not start or not end:
        return HttpResponseBadRequest(_("Start date and end date are required."))

    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        return HttpResponseBadRequest(_("Invalid date format."))

    if start_dt > end_dt:
        return HttpResponseBadRequest(_("Start date cannot be later than end date."))

    try:
        max_end = start_dt.replace(year=start_dt.year + 1)
    except ValueError:
        max_end = start_dt.replace(year=start_dt.year + 1, day=28)
    if end_dt > max_end:
        return HttpResponseBadRequest(
            _("Report generation is limited to a maximum of 12 months. Please select a shorter date range.")
        )

    request_employee = getattr(request.user, "employee_get", None)
    # print("REQUEST EMPLOYEE:", request_employee)

    leaves = (
        LeaveRequest.objects.filter(
            start_date__gte=start,
            end_date__lte=end,
            is_active=True
        )
        .select_related("employee_id", "leave_type_id")
    )

    ids = set()
    for l in leaves:
        val = getattr(l, "modified_by_id", None)
        if val:
            ids.add(int(val))

    user_map = {}
    if ids:
        employees = Employee.objects.filter(id__in=ids).select_related("employee_user_id")
        # print("EMPLOYEES FOUND:", employees.count())
        # print("Names", [emp.get_full_name() for emp in employees])

        for emp in employees:
            name = emp.get_full_name()

            if not name or name == str(emp).strip():
                user = getattr(emp, "employee_user_id", None)
                if user:
                    user_full = (user.get_full_name() or "").strip()
                    if user_full:
                        name = user_full

            user_map[emp.id] = name or "-"

    for l in leaves:
        key = getattr(l, "modified_by_id", None)
        l.modified_by_name = user_map.get(int(key), "-") if key else "-"

    context = {
        "leaves": leaves,
        "start": start,
        "end": end,
        "request": request,
        "request_user": request_employee,
        "report_creation_date": timezone.now(),
    }

    template_path = "leave/reports/monthly_leave_report_pdf.html"
    try:
        resp = generate_leave_request_pdf(template_path, context=context, html=False)
    except Exception:
        logger.exception("PDF generation crashed for monthly leave report.")
        return HttpResponse(_("Failed to generate PDF report."), status=500)

    resp["Content-Disposition"] = 'attachment; filename="monthly_leave_report.pdf"'
    _leave_audit(
        request,
        "Monthly leave report exported (PDF)",
        changes={
            "File": "monthly_leave_report.pdf",
            "From": start,
            "To": end,
            "Leave requests included": str(len(leaves)),
        },
    )
    return resp


# --- Dev-only manual triggers for the leave_reset scheduler ----------------
# These mirror leave.scheduler.leave_reset() but bypass the date checks so
# QA can verify carryforward reset and carryforward-expire behavior without
# waiting for the real reset/expire dates. Guarded by settings.DEBUG and a
# superuser check so they cannot be hit in production.

def _require_dev_superuser(request):
    from django.conf import settings
    print(
        "[QA-LEAVE] dev-trigger hit path=%s method=%s user=%s authed=%s super=%s DEBUG=%s"
        % (request.path, request.method, request.user,
           request.user.is_authenticated,
           getattr(request.user, "is_superuser", False), settings.DEBUG),
        flush=True,
    )
    if not settings.DEBUG:
        return JsonResponse(
            {"ok": False, "reason": "Not available — DEBUG is False on the server."},
            status=404,
        )
    if not request.user.is_authenticated:
        return JsonResponse(
            {"ok": False, "reason": "Not logged in."}, status=401
        )
    if not request.user.is_superuser:
        return JsonResponse(
            {
                "ok": False,
                "reason": (
                    "User '%s' is not a superuser. Run `python manage.py "
                    "createsuperuser` or promote this user via "
                    "`python manage.py shell` -> "
                    "User.objects.filter(username='%s').update(is_superuser=True, is_staff=True)`."
                ) % (request.user.username, request.user.username),
            },
            status=403,
        )
    return None


def _qa_log(msg):
    """Print to runserver stdout AND emit via logger so output is visible
    regardless of LOGGING config."""
    print("[QA-LEAVE] " + msg, flush=True)
    logger.info("[QA-LEAVE] %s", msg)


@login_required
@require_http_methods(["POST"])
def force_carryforward_reset(request):
    """
    Simulate the reset day passing: roll over unused available_days into
    carryforward_days (capped at carryforward_max), refill available_days
    from leave_type.total_days, and bump reset_date — for EVERY AvailableLeave
    whose leave type has any carryforward enabled, regardless of reset flag
    or current reset_date. Lets QA verify that carryforward_days does not
    compound across resets.
    """
    deny = _require_dev_superuser(request)
    if deny is not None:
        return deny

    from datetime import datetime as _dt

    today_date = _dt.now().date()
    _qa_log("=== force_carryforward_reset START user=%s today=%s ===" % (request.user, today_date))
    # Match the scheduler's eligibility (reset=True) AND keep CF-enabled types
    # so this trigger covers both "reset-only" leave types and CF leave types.
    # Excluding non-CF reset types caused last_reset_date never to update for
    # them, leaving total_leaves stuck on pre-reset approvals.
    reset_types = LeaveType.objects.filter(
        Q(reset=True) | ~Q(carryforward_type="no carryforward")
    )
    type_count = reset_types.count()
    _qa_log("Eligible leave types (reset=True or CF-enabled): %d" % type_count)
    affected = 0
    errors = []
    for leave_type in reset_types:
        rows = list(leave_type.employee_available_leave.all())
        _qa_log(
            "leave_type id=%s name=%r cf_type=%s cf_max=%s total_days=%s rows=%d"
            % (leave_type.id, leave_type.name, leave_type.carryforward_type,
               leave_type.carryforward_max, leave_type.total_days, len(rows))
        )
        for available_leave in rows:
            before_avail = available_leave.available_days
            before_cf = available_leave.carryforward_days
            before_reset = available_leave.reset_date
            try:
                available_leave.update_carryforward()
            except Exception as exc:
                msg = ("update_carryforward FAILED al_id=%s emp=%s lt=%s: %r"
                       % (available_leave.id, available_leave.employee_id,
                          leave_type.name, exc))
                _qa_log(msg)
                errors.append(msg)
                continue
            try:
                available_leave.reset_date = available_leave.set_reset_date(
                    assigned_date=today_date, available_leave=available_leave
                )
            except Exception as exc:
                _qa_log(
                    "set_reset_date skipped al_id=%s lt=%s: %r"
                    % (available_leave.id, leave_type.name, exc)
                )
            available_leave.last_reset_date = today_date.replace(month=1, day=1)
            try:
                available_leave.save()
            except Exception as exc:
                msg = ("save FAILED al_id=%s emp=%s lt=%s: %r"
                       % (available_leave.id, available_leave.employee_id,
                          leave_type.name, exc))
                _qa_log(msg)
                errors.append(msg)
                continue
            _qa_log(
                "  row al_id=%s emp=%s | avail %s -> %s | cf %s -> %s | reset_date %s -> %s"
                % (available_leave.id, available_leave.employee_id,
                   before_avail, available_leave.available_days,
                   before_cf, available_leave.carryforward_days,
                   before_reset, available_leave.reset_date)
            )
            affected += 1

    _qa_log("=== force_carryforward_reset END affected=%d errors=%d ===" % (affected, len(errors)))

    if affected:
        _leave_audit(
            request,
            "Leave carryforward reset forced (bulk balance adjustment)",
            changes={
                "Leave types affected": str(type_count),
                "Balances updated": str(affected),
                "Errors": str(len(errors)),
            },
        )

    if type_count == 0:
        return JsonResponse(
            {
                "ok": False,
                "reason": (
                    "No leave types are eligible for reset. Enable Reset on a "
                    "leave type, or set Carryforward Type to 'Carry Forward' "
                    "/ 'Carry Forward with Expire' on at least one leave type."
                ),
                "types": 0,
                "rows": 0,
            }
        )
    if affected == 0:
        return JsonResponse(
            {
                "ok": False,
                "reason": (
                    ("%d eligible leave type(s), but 0 rows updated. "
                     "Errors: %s. Check the runserver console for [QA-LEAVE] logs.")
                    % (type_count, "; ".join(errors) if errors else "no AvailableLeave rows exist — assign the leave type to an employee first")
                ),
                "types": type_count,
                "rows": 0,
                "errors": errors,
            }
        )
    return JsonResponse(
        {
            "ok": True,
            "reason": (
                "Leave Reset rolled over %d row(s) across %d leave type(s)%s. "
                "See runserver console [QA-LEAVE] for per-row before/after."
                % (affected, type_count, (" with %d error(s)" % len(errors)) if errors else "")
            ),
            "types": type_count,
            "rows": affected,
            "errors": errors,
        }
    )


@login_required
@require_http_methods(["POST"])
def force_carryforward_expire(request):
    """
    Simulate the carryforward expiry passing: zero carryforward_days on every
    employee for every leave type that has any carryforward, and bump the
    carryforward_expire_date forward (when applicable). Lets QA verify that
    expired carryforward really disappears from leave statistics.
    """
    deny = _require_dev_superuser(request)
    if deny is not None:
        return deny

    from datetime import datetime as _dt

    today_date = _dt.now().date()
    _qa_log("=== force_carryforward_expire START user=%s today=%s ===" % (request.user, today_date))
    cf_types = LeaveType.objects.exclude(carryforward_type="no carryforward")
    type_count = cf_types.count()
    _qa_log("CF-enabled leave types: %d" % type_count)
    affected_rows = 0
    affected_types = 0
    errors = []
    for leave_type in cf_types:
        rows = list(leave_type.employee_available_leave.all())
        nonzero = sum(1 for r in rows if r.carryforward_days)
        _qa_log(
            "leave_type id=%s name=%r cf_type=%s cf_expire_date=%s rows=%d rows_with_cf=%d"
            % (leave_type.id, leave_type.name, leave_type.carryforward_type,
               leave_type.carryforward_expire_date, len(rows), nonzero)
        )
        for available_leave in rows:
            # `> 0` rather than truthy: a corrupted negative balance
            # would otherwise be captured as a negative expired stat.
            if available_leave.carryforward_days > 0:
                before_cf = available_leave.carryforward_days
                before_expired_date = available_leave.expired_date
                try:
                    # AvailableLeave.set_expired_date itself captures CF
                    # into expired_carryforward_days and zeros CF. Invoking
                    # it after a manual capture+zero would overwrite the
                    # captured stat back to 0 (max(0, 0)), so call it FIRST
                    # for "carryforward expire" types and only fall back to
                    # a manual capture+zero when it doesn't apply or fails.
                    applied_via_model = False
                    if leave_type.carryforward_type == "carryforward expire":
                        try:
                            available_leave.expired_date = (
                                available_leave.set_expired_date(
                                    available_leave=available_leave,
                                    assigned_date=today_date,
                                )
                            )
                            applied_via_model = True
                        except Exception as exc:
                            _qa_log(
                                "  set_expired_date skipped al_id=%s lt=%s: %r"
                                % (available_leave.id, leave_type.name, exc)
                            )
                    if not applied_via_model:
                        available_leave.expired_carryforward_days = before_cf
                        available_leave.carryforward_days = 0
                    available_leave.save()
                except Exception as exc:
                    msg = ("save FAILED al_id=%s emp=%s lt=%s: %r"
                           % (available_leave.id, available_leave.employee_id,
                              leave_type.name, exc))
                    _qa_log(msg)
                    errors.append(msg)
                    continue
                _qa_log(
                    "  row al_id=%s emp=%s | cf %s -> 0 (expired_cf=%s "
                    "expired_date %s -> %s)"
                    % (available_leave.id, available_leave.employee_id,
                       before_cf, available_leave.expired_carryforward_days,
                       before_expired_date, available_leave.expired_date)
                )
                affected_rows += 1
        if leave_type.carryforward_type == "carryforward expire":
            before_expire = leave_type.carryforward_expire_date
            try:
                leave_type.carryforward_expire_date = leave_type.set_expired_date(
                    today_date
                )
                leave_type.save()
                _qa_log(
                    "  leave_type cf_expire_date %s -> %s"
                    % (before_expire, leave_type.carryforward_expire_date)
                )
            except Exception as exc:
                msg = ("set_expired_date/save FAILED lt=%s: %r" % (leave_type.name, exc))
                _qa_log(msg)
                errors.append(msg)
        affected_types += 1

    _qa_log(
        "=== force_carryforward_expire END affected_rows=%d types=%d errors=%d ==="
        % (affected_rows, affected_types, len(errors))
    )

    if affected_rows:
        _leave_audit(
            request,
            "Leave carryforward expiry forced (bulk balance adjustment)",
            changes={
                "Leave types affected": str(affected_types),
                "Balances updated": str(affected_rows),
                "Errors": str(len(errors)),
            },
        )

    if type_count == 0:
        return JsonResponse(
            {
                "ok": False,
                "reason": "No leave types have carryforward enabled — nothing to expire.",
                "types": 0,
                "rows": 0,
            }
        )
    if affected_rows == 0:
        return JsonResponse(
            {
                "ok": False,
                "reason": (
                    ("%d leave type(s) with carryforward, but no AvailableLeave "
                     "rows currently have carryforward_days > 0 to clear. "
                     "Click 'Leave Reset' first to generate some, then come back. "
                     "Errors: %s")
                    % (type_count, "; ".join(errors) if errors else "none")
                ),
                "types": type_count,
                "rows": 0,
                "errors": errors,
            }
        )
    return JsonResponse(
        {
            "ok": True,
            "reason": (
                "Carryforward Expiry cleared %d row(s) across %d leave type(s)%s. "
                "See runserver console [QA-LEAVE] for per-row before/after."
                % (affected_rows, affected_types,
                   (" with %d error(s)" % len(errors)) if errors else "")
            ),
            "types": affected_types,
            "rows": affected_rows,
            "errors": errors,
        }
    )


@login_required
@require_http_methods(["POST"])
def recalculate_leave_balances(request):
    """
    Repair tool: rebuild every AvailableLeave row's `available_days` and
    `carryforward_days` from the authoritative LeaveRequest history. Use
    this when balances have drifted from request data due to buggy
    scheduler runs.

    Assumes annual reset on Jan 1 (per project policy).

    Per row, with current_year = today.year:
      current_period:  [Jan 1 of current_year, Dec 31 of current_year]
                       — full calendar year, including future-dated
                         approvals so the balance reflects the year's
                         total commitments.
      prior_period:    [Jan 1 of (current_year-1), Dec 31 of (current_year-1)]

      consumed_total      = Σ requested_days for approved LRs
                            with start_date in current_period
      prior_used_normal   = Σ requested_days for approved LRs
                            with start_date in prior_period

      starting_cf = min(carryforward_max, max(0, total_days - prior_used_normal))
                    (= 0 if no CF on this leave type, or if employee was
                     assigned this year so there is no prior period yet)

      consumed_cf_now     = min(starting_cf, consumed_total)   # CF drains first
      consumed_avail_now  = consumed_total - consumed_cf_now
      expected_available  = max(0, total_days - consumed_avail_now)
      unused_cf_at_expiry = max(0, starting_cf - consumed_cf_now)

    requested_days is used as the source of truth rather than the cached
    approved_available_days / approved_carryforward_days fields, which can
    drift from requested_days (legacy data, prior-year approvals, edits).

      If CF has not yet expired this period:
          expected_cf         = unused_cf_at_expiry
          expected_expired_cf = 0
      If CF has already expired (carryforward_expire_date <= today):
          expected_cf         = 0
          expected_expired_cf = unused_cf_at_expiry
                                (only the *unused* portion actually expired
                                 — used CF was already drawn down)

    Pending (status='requested') leave requests do NOT deduct from the
    stored balance — they are surfaced via the live `pending_leaves()`
    method on AvailableLeave, which Balance Leave / Total Leave Count
    already incorporate at display time.

    Both fields are overwritten when they differ. Logs every row's
    before/after via [QA-LEAVE] for audit.
    """
    import math
    from datetime import date as _date

    from django.db.models import Sum

    from leave.models import AvailableLeave, LeaveRequest

    deny = _require_dev_superuser(request)
    if deny is not None:
        return deny

    today = _date.today()
    current_year = today.year
    current_period_start = _date(current_year, 1, 1)
    current_period_end = _date(current_year, 12, 31)
    prior_period_start = _date(current_year - 1, 1, 1)
    prior_period_end = _date(current_year - 1, 12, 31)

    _qa_log(
        "=== recalculate_leave_balances START user=%s today=%s "
        "current_period=[%s..%s] prior_period=[%s..%s] ==="
        % (
            request.user,
            today,
            current_period_start,
            current_period_end,
            prior_period_start,
            prior_period_end,
        )
    )
    rows = AvailableLeave.objects.select_related("leave_type_id", "employee_id").all()
    total = rows.count()
    _qa_log("inspecting %d AvailableLeave row(s)" % total)

    avail_fixed = 0
    cf_fixed = 0
    expired_cf_fixed = 0
    errors = []

    for available_leave in rows:
        leave_type = available_leave.leave_type_id
        if leave_type is None:
            _qa_log("  SKIP al_id=%s — no leave_type assigned" % available_leave.id)
            continue

        # Current period spans the full calendar year so future-dated
        # approvals (e.g. employee approved for December while it's only
        # May) are deducted up front. Clamp to assigned_date to avoid
        # summing pre-assignment leaves.
        eff_period_start = max(current_period_start, available_leave.assigned_date)

        # Use requested_days as the source of truth for consumption — the
        # cached approved_available_days / approved_carryforward_days fields
        # can drift from requested_days (e.g. the prior-year approval guard
        # writes them as 0, manual edits, legacy data) and recalc must not
        # silently rely on stale splits. We compute the split ourselves below.
        current_agg = LeaveRequest.objects.filter(
            employee_id=available_leave.employee_id,
            leave_type_id=leave_type,
            status="approved",
            start_date__gte=eff_period_start,
            start_date__lte=current_period_end,
        ).aggregate(sum_requested=Sum("requested_days"))
        consumed_total = current_agg["sum_requested"] or 0

        # Prior period — only meaningful if employee was assigned before
        # this year. Otherwise there's nothing to roll forward.
        if available_leave.assigned_date < current_period_start:
            prior_agg = LeaveRequest.objects.filter(
                employee_id=available_leave.employee_id,
                leave_type_id=leave_type,
                status="approved",
                start_date__gte=prior_period_start,
                start_date__lte=prior_period_end,
            ).aggregate(sum_requested=Sum("requested_days"))
            prior_used_normal = prior_agg["sum_requested"] or 0
            prior_unused_normal = max(0, (leave_type.total_days or 0) - prior_used_normal)
        else:
            prior_used_normal = 0
            prior_unused_normal = 0

        # Starting CF = unused-from-prior, capped at carryforward_max.
        # carryforward_max is None for unbounded — treat as +inf.
        if leave_type.carryforward_type == "no carryforward":
            starting_cf = 0
        else:
            cf_max = leave_type.carryforward_max
            cap = cf_max if cf_max is not None else math.inf
            starting_cf = min(cap, prior_unused_normal)

        # If CF has already expired DURING the current period, the live
        # balance must be 0 regardless of consumption math.
        # Three expiry signals can fire within the year:
        #   - LeaveType.carryforward_expire_date (per-leave-type sweep)
        #   - AvailableLeave.expired_date         (per-employee sweep)
        #   - AvailableLeave.expired_carryforward_days > 0
        #     (trailing stat — update_carryforward zeroes this at every
        #     period reset, so any positive value means expiry already
        #     captured CF this period. Needed because both expire dates
        #     get bumped forward AFTER expiry runs, making the date-based
        #     checks return False even though expiry did happen.)
        # A stale expire date from a prior year does NOT count — the
        # scheduler should have bumped it forward, but if it didn't, we
        # must not retroactively wipe CF.
        type_expired_this_period = (
            leave_type.carryforward_expire_date is not None
            and current_period_start
            <= leave_type.carryforward_expire_date
            <= today
        )
        row_expired_this_period = (
            available_leave.expired_date is not None
            and current_period_start
            <= available_leave.expired_date
            <= today
        )
        stat_indicates_expired_this_period = (
            (available_leave.expired_carryforward_days or 0) > 0
        )
        cf_already_expired = (
            leave_type.carryforward_type == "carryforward expire"
            and (
                type_expired_this_period
                or row_expired_this_period
                or stat_indicates_expired_this_period
            )
        )

        # Split consumed_total into CF and regular allocation. CF drains
        # first (matches the explicit approve view's draw order). Anything
        # beyond starting_cf must come out of regular allocation.
        consumed_cf = min(starting_cf, consumed_total)
        consumed_avail = consumed_total - consumed_cf
        expected_available = max(0, (leave_type.total_days or 0) - consumed_avail)
        # Unused portion of CF (what would have been at the moment of
        # expiry). Used CF was already drawn from the rolled-over balance,
        # so the amount that actually expired is starting - consumed.
        unused_cf_at_expiry = max(0, starting_cf - consumed_cf)
        if cf_already_expired:
            expected_cf = 0
            if stat_indicates_expired_this_period and not (
                type_expired_this_period or row_expired_this_period
            ):
                # Expiry detected only via the trailing stat — the stored
                # value IS the snapshot captured at expiry time. Preserve
                # it rather than overwriting with our reconstruction,
                # which can disagree if request data has shifted since.
                expected_expired_cf = available_leave.expired_carryforward_days
            elif (available_leave.carryforward_days or 0) > 0:
                # Date-based detection AND there's still a live value in
                # carryforward_days. That live value is the post-drain
                # unused CF (frozen at approval time) — move it into
                # expired_carryforward_days so it survives the
                # expected_cf=0 wipe. Falling through to
                # unused_cf_at_expiry here would silently lose CF
                # whenever prior-year request data is incomplete (new
                # hires, imports) and the reconstruction undershoots.
                expected_expired_cf = available_leave.carryforward_days
            else:
                expected_expired_cf = unused_cf_at_expiry
        else:
            expected_cf = unused_cf_at_expiry
            # No signal of expiry this period — and the stat-based signal
            # ruled out a stored value > 0 above, so the stored value is
            # already 0 (or this leave type is non-expiring CF, in which
            # case zeroing a leftover stat is the right cleanup).
            expected_expired_cf = 0

        before_avail = available_leave.available_days
        before_cf = available_leave.carryforward_days
        before_expired_cf = available_leave.expired_carryforward_days
        avail_changed = round(before_avail, 6) != round(expected_available, 6)
        cf_changed = round(before_cf, 6) != round(expected_cf, 6)
        expired_cf_changed = round(before_expired_cf, 6) != round(
            expected_expired_cf, 6
        )

        if not avail_changed and not cf_changed and not expired_cf_changed:
            continue

        update_fields = []
        if avail_changed:
            available_leave.available_days = expected_available
            update_fields.append("available_days")
        if cf_changed:
            available_leave.carryforward_days = expected_cf
            update_fields.append("carryforward_days")
        if expired_cf_changed:
            available_leave.expired_carryforward_days = expected_expired_cf
            update_fields.append("expired_carryforward_days")

        try:
            available_leave.save(update_fields=update_fields)
        except Exception as exc:
            msg = (
                "save FAILED al_id=%s emp=%s lt=%r: %r"
                % (
                    available_leave.id,
                    available_leave.employee_id,
                    leave_type.name,
                    exc,
                )
            )
            _qa_log(msg)
            errors.append(msg)
            continue

        _qa_log(
            "  FIX al_id=%s emp=%s lt=%r | avail %s -> %s | cf %s -> %s "
            "| expired_cf %s -> %s (consumed_avail=%s consumed_cf=%s "
            "prior_used_normal=%s starting_cf=%s cf_expired=%s)"
            % (
                available_leave.id,
                available_leave.employee_id,
                leave_type.name,
                before_avail,
                expected_available,
                before_cf,
                expected_cf,
                before_expired_cf,
                expected_expired_cf,
                consumed_avail,
                consumed_cf,
                prior_used_normal,
                starting_cf,
                cf_already_expired,
            )
        )
        if avail_changed:
            avail_fixed += 1
        if cf_changed:
            cf_fixed += 1
        if expired_cf_changed:
            expired_cf_fixed += 1

    _qa_log(
        "=== recalculate_leave_balances END inspected=%d avail_fixed=%d "
        "cf_fixed=%d expired_cf_fixed=%d errors=%d ==="
        % (total, avail_fixed, cf_fixed, expired_cf_fixed, len(errors))
    )

    if avail_fixed or cf_fixed or expired_cf_fixed:
        _leave_audit(
            request,
            "Leave balances recalculated (bulk balance adjustment)",
            changes={
                "Rows inspected": str(total),
                "Available days corrected": str(avail_fixed),
                "Carryforward days corrected": str(cf_fixed),
                "Expired carryforward corrected": str(expired_cf_fixed),
                "Errors": str(len(errors)),
            },
        )

    return JsonResponse(
        {
            "ok": True,
            "reason": (
                "Recalculation complete: inspected %d row(s), corrected "
                "available_days on %d, carryforward_days on %d, "
                "expired_carryforward_days on %d%s. See runserver console "
                "[QA-LEAVE] for per-row detail."
                % (
                    total,
                    avail_fixed,
                    cf_fixed,
                    expired_cf_fixed,
                    (" — %d error(s)" % len(errors)) if errors else "",
                )
            ),
            "inspected": total,
            "avail_fixed": avail_fixed,
            "cf_fixed": cf_fixed,
            "expired_cf_fixed": expired_cf_fixed,
            "errors": errors,
        }
    )