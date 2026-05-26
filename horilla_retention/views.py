"""
IT-Admin dashboard views.

Three pages:
  - dashboard: last run summary, pending actions count, quick links
  - policies:  list/create/update/delete DataRetentionPolicy rows
  - actions:   pending RetentionAction list with approve / defer / cancel
  - run-log:   RetentionRunLog history
  - audit:     RetentionAuditEntry stream
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from horilla_retention import audit
from horilla_retention.anonymizers import run_anonymizer
from horilla_retention.engine import run_daily
from horilla_retention.forms import DataRetentionPolicyForm, DeferActionForm
from horilla_retention.models import (
    DataRetentionPolicy,
    RetentionAction,
    RetentionAuditEntry,
    RetentionRunLog,
)
from horilla_retention.constants import (
    STATUS_ANONYMIZED,
    STATUS_CANCELLED,
    STATUS_DEFERRED,
    STATUS_PENDING_REVIEW,
)


VIEW_PERM = "horilla_retention.view_retention_dashboard"
RUN_PERM = "horilla_retention.run_retention_engine"
APPROVE_PERM = "horilla_retention.approve_retention_action"


@login_required
@permission_required(VIEW_PERM, raise_exception=True)
def dashboard(request):
    last_run = RetentionRunLog.objects.order_by("-started_at").first()
    pending_qs = RetentionAction.objects.filter(status=STATUS_PENDING_REVIEW)
    overdue_qs = pending_qs.filter(
        auto_anonymize_after__lte=timezone.localdate()
    )
    failed_qs = RetentionAction.objects.filter(status="failed")
    policy_count = DataRetentionPolicy.objects.filter(is_enabled=True).count()

    ctx = {
        "active_tab": "dashboard",
        "last_run": last_run,
        "pending_count": pending_qs.count(),
        "overdue_count": overdue_qs.count(),
        "failed_count": failed_qs.count(),
        "policy_count": policy_count,
        "can_run": request.user.has_perm(RUN_PERM),
        "can_approve": request.user.has_perm(APPROVE_PERM),
    }
    return render(request, "horilla_retention/dashboard.html", ctx)


@login_required
@permission_required(VIEW_PERM, raise_exception=True)
def policy_list(request):
    policies = DataRetentionPolicy.objects.select_related("company_id").order_by(
        "company_id__company", "category"
    )
    return render(
        request,
        "horilla_retention/policy_list.html",
        {"policies": policies, "active_tab": "policies"},
    )


@login_required
@permission_required(APPROVE_PERM, raise_exception=True)
def policy_create(request):
    if request.method == "POST":
        form = DataRetentionPolicyForm(request.POST)
        if form.is_valid():
            policy = form.save()
            audit.write(
                event=RetentionAuditEntry.EVENT_POLICY_CREATED,
                message=f"Policy created: {policy}",
                actor=request.user,
                category=policy.category,
                payload={
                    "policy_id": policy.id,
                    "company_id": policy.company_id_id,
                    "retention_years": policy.retention_years,
                    "grace_period_days": policy.grace_period_days,
                },
            )
            messages.success(request, _("Retention policy created."))
            return redirect("retention-policies")
    else:
        form = DataRetentionPolicyForm()
    return render(
        request,
        "horilla_retention/policy_form.html",
        {"form": form, "title": _("New retention policy"), "active_tab": "policies"},
    )


@login_required
@permission_required(APPROVE_PERM, raise_exception=True)
def policy_update(request, policy_id):
    policy = get_object_or_404(DataRetentionPolicy, pk=policy_id)
    if request.method == "POST":
        form = DataRetentionPolicyForm(request.POST, instance=policy)
        if form.is_valid():
            changed = form.changed_data
            policy = form.save()
            audit.write(
                event=RetentionAuditEntry.EVENT_POLICY_UPDATED,
                message=f"Policy updated: {policy} (fields={','.join(changed)})",
                actor=request.user,
                category=policy.category,
                payload={"policy_id": policy.id, "changed_fields": changed},
            )
            messages.success(request, _("Retention policy updated."))
            return redirect("retention-policies")
    else:
        form = DataRetentionPolicyForm(instance=policy)
    return render(
        request,
        "horilla_retention/policy_form.html",
        {"form": form, "title": _("Edit retention policy"), "active_tab": "policies"},
    )


@login_required
@permission_required(APPROVE_PERM, raise_exception=True)
@require_POST
def policy_delete(request, policy_id):
    policy = get_object_or_404(DataRetentionPolicy, pk=policy_id)
    label = str(policy)
    policy_id_copy = policy.id
    category = policy.category
    policy.delete()
    audit.write(
        event=RetentionAuditEntry.EVENT_POLICY_DELETED,
        message=f"Policy deleted: {label}",
        actor=request.user,
        category=category,
        payload={"policy_id": policy_id_copy},
    )
    messages.success(request, _("Retention policy deleted."))
    return redirect("retention-policies")


@login_required
@permission_required(VIEW_PERM, raise_exception=True)
def action_list(request):
    status = request.GET.get("status", STATUS_PENDING_REVIEW)
    qs = RetentionAction.objects.select_related(
        "employee_id", "policy", "policy__company_id"
    )
    if status != "all":
        qs = qs.filter(status=status)
    return render(
        request,
        "horilla_retention/action_list.html",
        {
            "actions": qs.order_by("auto_anonymize_after"),
            "status": status,
            "today": timezone.localdate(),
            "can_approve": request.user.has_perm(APPROVE_PERM),
            "pending_statuses": (STATUS_PENDING_REVIEW, STATUS_DEFERRED),
            "active_tab": "actions",
        },
    )


@login_required
@permission_required(APPROVE_PERM, raise_exception=True)
@require_POST
def action_approve(request, action_id):
    """Immediately anonymize, bypassing remaining grace period."""
    action = get_object_or_404(RetentionAction, pk=action_id)
    if action.status not in (STATUS_PENDING_REVIEW, STATUS_DEFERRED):
        messages.error(request, _("This action is no longer pending."))
        return redirect("retention-actions")

    affected, error = run_anonymizer(action.category, action.employee_id)
    if error:
        action.status = "failed"
        action.last_error = error
        action.records_affected = affected
        action.save()
        audit.write(
            event=RetentionAuditEntry.EVENT_ERROR,
            message=f"Manual anonymization failed: {error}",
            actor=request.user,
            employee=action.employee_id,
            category=action.category,
            action=action,
        )
        messages.error(request, _("Anonymization failed: %s") % error)
    else:
        action.status = STATUS_ANONYMIZED
        action.actioned_on = timezone.now()
        action.actioned_by = request.user
        action.records_affected = affected
        action.last_error = None
        action.save()
        audit.write(
            event=RetentionAuditEntry.EVENT_ANONYMIZED,
            message=(
                f"Manually anonymized employee {action.employee_id_id} "
                f"category={action.category}; {affected} record(s) affected."
            ),
            actor=request.user,
            employee=action.employee_id,
            category=action.category,
            action=action,
            payload={"records_affected": affected},
        )
        messages.success(
            request, _("Records anonymized (%d row(s)).") % affected
        )
    return redirect("retention-actions")


@login_required
@permission_required(APPROVE_PERM, raise_exception=True)
def action_defer(request, action_id):
    action = get_object_or_404(RetentionAction, pk=action_id)
    if action.status not in (STATUS_PENDING_REVIEW, STATUS_DEFERRED):
        messages.error(request, _("This action is no longer pending."))
        return redirect("retention-actions")

    if request.method == "POST":
        form = DeferActionForm(request.POST)
        if form.is_valid():
            action.deferred_until = form.cleaned_data["deferred_until"]
            action.deferral_reason = form.cleaned_data["deferral_reason"]
            action.status = STATUS_DEFERRED
            action.save()
            audit.write(
                event=RetentionAuditEntry.EVENT_DEFERRED,
                message=(
                    f"Deferred until {action.deferred_until.isoformat()} "
                    f"(reason: {action.deferral_reason[:200]})"
                ),
                actor=request.user,
                employee=action.employee_id,
                category=action.category,
                action=action,
            )
            messages.success(request, _("Action deferred."))
            return redirect("retention-actions")
    else:
        form = DeferActionForm(
            initial={
                "deferred_until": action.deferred_until or action.auto_anonymize_after
            }
        )
    return render(
        request,
        "horilla_retention/action_defer.html",
        {"form": form, "action": action, "active_tab": "actions"},
    )


@login_required
@permission_required(APPROVE_PERM, raise_exception=True)
@require_POST
def action_cancel(request, action_id):
    """Cancel a pending action — typically because the policy itself was a
    misconfiguration. Does NOT touch records."""
    action = get_object_or_404(RetentionAction, pk=action_id)
    if action.status not in (STATUS_PENDING_REVIEW, STATUS_DEFERRED):
        messages.error(request, _("This action is no longer pending."))
        return redirect("retention-actions")
    action.status = STATUS_CANCELLED
    action.actioned_on = timezone.now()
    action.actioned_by = request.user
    action.save()
    audit.write(
        event=RetentionAuditEntry.EVENT_CANCELLED,
        message="Action cancelled by admin.",
        actor=request.user,
        employee=action.employee_id,
        category=action.category,
        action=action,
    )
    messages.success(request, _("Action cancelled."))
    return redirect("retention-actions")


@login_required
@permission_required(RUN_PERM, raise_exception=True)
@require_POST
def trigger_run(request):
    dry_run = request.POST.get("dry_run") == "on"
    run = run_daily(triggered_by=request.user, is_scheduled=False, dry_run=dry_run)
    messages.success(
        request,
        _("Run #%(id)s completed: %(outcome)s") % {"id": run.id, "outcome": run.outcome},
    )
    return redirect("retention-run-log")


@login_required
@permission_required(VIEW_PERM, raise_exception=True)
def run_log(request):
    runs = RetentionRunLog.objects.order_by("-started_at")[:200]
    return render(
        request,
        "horilla_retention/run_log.html",
        {"runs": runs, "can_run": request.user.has_perm(RUN_PERM), "active_tab": "runs"},
    )


@login_required
@permission_required(VIEW_PERM, raise_exception=True)
def audit_log(request):
    entries = RetentionAuditEntry.objects.select_related(
        "actor", "employee_id", "action", "run"
    ).order_by("-timestamp")[:500]
    return render(
        request,
        "horilla_retention/audit_log.html",
        {"entries": entries, "active_tab": "audit"},
    )
