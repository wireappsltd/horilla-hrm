"""
policies.py

This module is used to write operation related to policies
"""

import datetime
import json
from datetime import timedelta
from urllib.parse import parse_qs

from django.contrib import messages
from django.contrib.auth.models import User
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from base.methods import (
    eval_validate,
    filtersubordinates,
    get_key_instances,
    paginator_qry,
)
from employee.audit import emp_audit, emp_form_audit, policy_details
from employee.filters import DisciplinaryActionFilter, PolicyFilter
from employee.forms import DisciplinaryActionForm, PolicyForm
from employee.models import (
    Actiontype,
    DisciplinaryAction,
    Employee,
    Policy,
    PolicyMultipleFile,
)
from horilla.decorators import hx_request_required, login_required, permission_required
from notifications.signals import notify


@login_required
def view_policies(request):
    """
    Method is used render template to view all the policy records
    """
    policies = Policy.objects.all()
    if not request.user.has_perm("employee.view_policy"):
        policies = policies.filter(is_visible_to_all=True)
    return render(
        request,
        "policies/view_policies.html",
        {"policies": paginator_qry(policies, request.GET.get("page"))},
    )


@login_required
@hx_request_required
@permission_required("employee.add_policy")
def create_policy(request):
    """
    Method is used to create/update new policy
    """
    instance_id = request.GET.get("instance_id")
    instance = None
    if isinstance(eval_validate(str(instance_id)), int):
        instance = Policy.objects.filter(id=instance_id).first()
    form = PolicyForm(instance=instance)
    if request.method == "POST":
        form = PolicyForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            # PolicyForm.save returns (instance, attachments), not the bare
            # instance a plain ModelForm would give back.
            saved, _attachments = form.save()
            # One view serves both create and edit; `instance` is only set when
            # an existing policy was loaded, so it decides which action is
            # recorded. Edits carry the field-level diff, creates the snapshot.
            if instance is None:
                emp_audit(
                    request,
                    "Policy created",
                    target=saved,
                    changes=policy_details(saved),
                )
            else:
                emp_form_audit(
                    request,
                    "Policy updated",
                    form=form,
                    target=saved,
                    extra=policy_details(saved),
                )
            messages.success(request, "Policy saved")
            form = PolicyForm()
            # return HttpResponse("<script>window.location.reload()</script>")
    return render(request, "policies/form.html", {"form": form})


@login_required
@hx_request_required
def search_policies(request):
    """
    This method is used to search in policies
    """
    policies = PolicyFilter(request.GET).qs
    if not request.user.has_perm("employee.view_policy"):
        policies = policies.filter(is_visible_to_all=True)
    return render(
        request,
        "policies/records.html",
        {
            "policies": paginator_qry(policies, request.GET.get("page")),
            "pd": request.GET.urlencode(),
        },
    )


@login_required
@hx_request_required
def view_policy(request):
    """
    This method is used to view the policy
    """
    instance_id = request.GET["instance_id"]
    policy = Policy.objects.filter(id=instance_id).first()
    return render(
        request,
        "policies/view_policy.html",
        {
            "policy": policy,
        },
    )


@login_required
@permission_required("employee.delete_policy")
def delete_policies(request):
    """
    This method is to delete policy
    """
    try:
        ids = request.GET.getlist("ids")
        # Snapshot before the queryset delete: afterwards there is nothing left
        # to describe which policies went. One entry each, so a bulk delete is
        # traceable policy by policy rather than as a single count. The
        # instances are kept alongside their details because a queryset delete
        # leaves the in-memory objects' pks intact, so each entry can still
        # carry a target rather than only a reference string.
        doomed = [
            (policy, policy_details(policy))
            for policy in Policy.objects.filter(id__in=ids)
        ]
        count, dict = Policy.objects.filter(id__in=ids).delete()
        if count == 0:
            messages.error(request, _("Policies Not Found"))
        else:
            for policy, details in doomed:
                emp_audit(request, "Policy deleted", target=policy, changes=details)
            messages.success(request, "Policies deleted")
    except ValueError:
        messages.error(request, _("Policies Not Found"))
    return redirect(view_policies)


@login_required
@permission_required("employee.add_policymultiplefile")
def add_attachment(request):
    """
    This method is used to add attachment to policy
    """
    files = request.FILES.getlist("files")
    policy_id = request.GET["policy_id"]
    attachments = []
    for file in files:
        attachment = PolicyMultipleFile()
        attachment.attachment = file
        attachment.save()
        attachments.append(attachment)
    policy = Policy.objects.get(id=policy_id)
    policy.attachments.add(*attachments)
    details = policy_details(policy)
    details["Attachments added"] = ", ".join(
        attachment.attachment.name.rsplit("/", 1)[-1] for attachment in attachments
    )
    emp_audit(request, "Policy attachment added", target=policy, changes=details)
    messages.success(request, "Attachments added")
    return render(request, "policies/attachments.html", {"policy": policy})


@login_required
@permission_required("employee.delete_policymultiplefile")
def remove_attachment(request):
    """
    This method is used to remove the attachments
    """
    ids = request.GET.getlist("ids")
    policy_id = request.GET["policy_id"]
    policy = Policy.objects.get(id=policy_id)
    # Names captured before the delete — afterwards the rows are gone and the
    # entry could only say "some attachments were removed".
    removed = [
        attachment.attachment.name.rsplit("/", 1)[-1]
        for attachment in PolicyMultipleFile.objects.filter(id__in=ids)
    ]
    PolicyMultipleFile.objects.filter(id__in=ids).delete()
    if removed:
        details = policy_details(policy)
        details["Attachments removed"] = ", ".join(removed)
        emp_audit(request, "Policy attachment deleted", target=policy, changes=details)
    return render(request, "policies/attachments.html", {"policy": policy})


@login_required
def get_attachments(request):
    """
    This method is used to view all the attachments inside the policy
    """
    policy = request.GET["policy_id"]
    policy = Policy.objects.get(id=policy)
    return render(request, "policies/attachments.html", {"policy": policy})


@login_required
def disciplinary_actions(request):
    """
    This method is used to view all Disciplinaryaction
    """
    employee = Employee.objects.filter(employee_user_id=request.user).first()
    if request.user.has_perm("employee.view_disciplinaryaction"):
        dis_actions = DisciplinaryAction.objects.all()
    else:
        dis_actions = filtersubordinates(
            request, DisciplinaryAction.objects.all(), "base.add_disciplinaryaction"
        ).distinct()
        dis_actions = (
            dis_actions
            | DisciplinaryAction.objects.filter(employee_id=employee).distinct()
        )

    form = DisciplinaryActionFilter(request.GET, queryset=dis_actions)
    page_number = request.GET.get("page")
    page_obj = paginator_qry(form.qs, page_number)
    previous_data = request.GET.urlencode()

    return render(
        request,
        "disciplinary_actions/disciplinary_nav.html",
        {
            "data": page_obj,
            "pd": previous_data,
            "f": form,
        },
    )


def get_action_type(action_id):
    """
    This function is used to get the action type by the selection of title in the form.
    """
    action = Actiontype.objects.get(title=action_id["action"])
    return action.action_type


def get_action_type_delete(action_id):
    """
    This function is used to get the action type by the selection of title in the form.
    """
    action = Actiontype.objects.get(title=action_id)
    return action.action_type


def get_action_type(action_id):
    """
    This function is used to get the action type by the selection of title in the form.
    """
    action = Actiontype.objects.get(title=action_id["action"])
    return action.action_type


def get_action_type_delete(action_id):
    """
    This function is used to get the action type by the selection of title in the form.
    """
    action = Actiontype.objects.get(title=action_id)
    return action.action_type


def employee_account_block_unblock(emp_id, result):

    employee = get_object_or_404(Employee, id=emp_id)
    if not employee:
        return redirect(disciplinary_actions)
    user = get_object_or_404(User, id=employee.employee_user_id.id)
    if not user:
        return redirect(disciplinary_actions)
    user.is_active = result
    user.save()
    return HttpResponse("<script>window.location.reload()</script>")


@login_required
@hx_request_required
@permission_required("employee.add_disciplinaryaction")
def create_actions(request):
    """
    Method is used to create Disciplinaryaction
    """
    form = DisciplinaryActionForm()
    employees = []
    dynamic = (
        request.GET.get("dynamic") if request.GET.get("dynamic") != "None" else None
    )
    if request.GET:
        form = DisciplinaryActionForm(request.GET)

    if request.method == "POST":
        form = DisciplinaryActionForm(request.POST, request.FILES)
        if form.is_valid():
            employee_ids = form.cleaned_data["employee_id"]

            for employee in employee_ids:
                user = employee.employee_user_id
                employees.append(user)

            form.save()
            messages.success(request, _("Disciplinary action taken."))
            notify.send(
                request.user.employee_get,
                recipient=employees,
                verb="Disciplinary action is taken on you.",
                verb_ar="تم اتخاذ إجراء disziplinarisch ضدك.",
                verb_de="Disziplinarische Maßnahmen wurden gegen Sie ergriffen.",
                verb_es="Se ha tomado acción disciplinaria en tu contra.",
                verb_fr="Des mesures disciplinaires ont été prises à votre encontre.",
                redirect="/employee/disciplinary-actions/",
                icon="chatbox-ellipses",
            )
        dis = DisciplinaryAction.objects.all()
        if len(dis) == 1:
            return HttpResponse("<script>window.location.reload()</script>")

    return render(
        request, "disciplinary_actions/form.html", {"form": form, "dynamic": dynamic}
    )


@login_required
@hx_request_required
@permission_required("employee.change_disciplinaryaction")
def update_actions(request, action_id):
    """
    Method is used to update Disciplinaryaction
    """

    action = DisciplinaryAction.objects.get(id=action_id)
    form = DisciplinaryActionForm(instance=action)
    employees = []
    if request.method == "POST":
        form = DisciplinaryActionForm(request.POST, request.FILES, instance=action)

        if form.is_valid():
            employee_ids = form.cleaned_data["employee_id"]

            for employee in employee_ids:
                name = employee.employee_user_id
                employees.append(name)

            form.save()
            messages.success(request, _("Disciplinary action updated."))

            notify.send(
                request.user.employee_get,
                recipient=employees,
                verb="Disciplinary action is taken on you.",
                verb_ar="تم اتخاذ إجراء disziplinarisch ضدك.",
                verb_de="Disziplinarische Maßnahmen wurden gegen Sie ergriffen.",
                verb_es="Se ha tomado acción disciplinaria en tu contra.",
                verb_fr="Des mesures disciplinaires ont été prises à votre encontre.",
                redirect="/employee/disciplinary-actions/",
                icon="chatbox-ellipses",
            )
    return render(request, "disciplinary_actions/update_form.html", {"form": form})


@login_required
@hx_request_required
@permission_required("employee.change_disciplinaryaction")
def remove_employee_disciplinary_action(request, action_id, emp_id):
    dis_action = DisciplinaryAction.objects.get(id=action_id)
    employee = Employee.objects.get(id=emp_id)

    action_type = get_action_type_delete(dis_action.action)

    if action_type == "dismissal" or action_type == "suspension":
        emp = get_object_or_404(Employee, id=emp_id)
        user = get_object_or_404(User, id=emp.employee_user_id.id)
        if user.is_active:
            pass
        else:
            messages.warning(
                request, _("Employees login credentials will be unblocked.")
            )
            user.is_active = True
            user.save()

    dis_action.employee_id.remove(employee)

    employees = len(dis_action.employee_id.all())

    if employees == 0:
        dis_action.delete()

    messages.success(
        request, _("Employee removed from disciplinary action successfully.")
    )
    return redirect(f"/employee/disciplinary-filter-view?click_id={dis_action.id}")


@login_required
@hx_request_required
@permission_required("employee.delete_disciplinaryaction")
def delete_actions(request, action_id):
    """
    This method is used to delete Disciplinary action
    """

    dis = DisciplinaryAction.objects.get(id=action_id)

    action_type = get_action_type_delete(dis.action)

    for dis_emp in dis.employee_id.all():

        if action_type == "dismissal" or action_type == "suspension":
            employee = get_object_or_404(Employee, id=dis_emp.id)
            user = get_object_or_404(User, id=employee.employee_user_id.id)
            if user.is_active:
                pass
            else:
                messages.warning(
                    request, _("Employees login credentials will be unblocked.")
                )
                user.is_active = True
                user.save()

    dis.delete()
    messages.success(request, _("Disciplinary action deleted."))
    dis_actions = DisciplinaryAction.objects.all()

    if dis_actions.exists():
        return redirect(disciplinary_filter_view)
    return HttpResponse("<script>window.location.reload()</script>")


@login_required
def action_type_details(request):
    """
    This method is used to get the action type by the selection of title in the form.
    """
    action_id = request.POST["action_type"]
    action = Actiontype.objects.get(id=action_id)
    action_type = action.action_type
    return JsonResponse({"action_type": action_type})


@login_required
def action_type_name(request):
    """
    This method is used to get the action type name by the selection of type in the form.
    """
    action_type = request.POST["action_type"]
    return JsonResponse({"action_type": action_type})


@login_required
@hx_request_required
def disciplinary_filter_view(request):
    """
    This method is used to filter Disciplinary Action.
    """

    previous_data = request.GET.urlencode()
    action_id = request.GET.get("click_id") if request.GET.get("click_id") else None
    employee = Employee.objects.filter(employee_user_id=request.user).first()
    if request.user.has_perm("employee.view_disciplinaryaction"):
        dis_actions = DisciplinaryAction.objects.all()
    else:
        dis_actions = filtersubordinates(
            request, DisciplinaryAction.objects.all(), "base.add_disciplinaryaction"
        ).distinct()
        dis_actions = (
            dis_actions
            | DisciplinaryAction.objects.filter(employee_id=employee).distinct()
        )
    dis_filter = DisciplinaryActionFilter(request.GET, queryset=dis_actions).qs
    page_number = request.GET.get("page")
    page_obj = paginator_qry(dis_filter, page_number)
    data_dict = parse_qs(previous_data)
    get_key_instances(DisciplinaryAction, data_dict)
    return render(
        request,
        "disciplinary_actions/disciplinary_records.html",
        {
            "data": page_obj,
            "pd": previous_data,
            "filter_dict": data_dict,
            "dashboard": request.GET.get("dashboard"),
            "action_id": action_id,
        },
    )


@login_required
def search_disciplinary(request):
    """
    This method is used to search in Disciplinary Actions
    """
    disciplinary = DisciplinaryActionFilter(request.GET).qs
    return render(
        request,
        "disciplinary_actions/disciplinary_records.html",
        {
            "data": paginator_qry(disciplinary, request.GET.get("page")),
            "pd": request.GET.urlencode(),
        },
    )
