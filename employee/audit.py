"""Audit helpers for the Employee module.

The employee views are split between ``views.py`` and ``policies.py``, so the
per-app audit wrapper that other single-file modules keep at the top of their
``views.py`` lives here instead and is imported by both — the same arrangement
``attendance/audit.py`` uses.

Everything written here lands in the "Employee" tab of Audit Trails.

Only create, update and delete are recorded. Reads are deliberately not logged:
opening a profile or paging the directory would write a row per page load and
bury the change history that this trail exists to show.

Employee *settings* (general settings, profile-edit feature toggles) are not
logged from these views — they are tracked by ``horilla_audit.config_tracking``
and surface on the Configuration tab, matching the Settings screens they are
edited from. Logging them here as well would double every entry.
"""

from horilla_audit.methods import log_activity, log_form_changes

MODULE = "employee"

# Bank fields whose before/after values are masked to their last 4 characters
# in audit diffs. An audit trail should show that an account number changed
# without becoming a place to read account numbers out of.
BANK_MASK_FIELDS = ("account_number", "swift_code")


def client_ip(request):
    """Best-effort client IP: first X-Forwarded-For hop, else REMOTE_ADDR."""
    meta = getattr(request, "META", None) or {}
    forwarded = meta.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return meta.get("REMOTE_ADDR", "")


def emp_audit(request, action, target=None, changes=None):
    """Write an employee-module ActivityLog entry for a lifecycle event.

    Thin wrapper over ``log_activity`` so every employee event lands in the
    "Employee" audit tab with a consistent actor and (where available) the
    client IP stamped into ``changes``. Never blocks the request — the
    underlying helper swallows and logs its own failures.
    """
    changes = dict(changes or {})
    ip = client_ip(request)
    if ip:
        changes.setdefault("IP", ip)
    log_activity(
        getattr(request, "user", None),
        module=MODULE,
        action=action,
        target=target,
        changes=changes or None,
    )


def emp_form_audit(request, action, form, target=None, mask_fields=None, extra=None):
    """Write a field-level before/after entry from a saved ModelForm.

    Delegates the diffing to ``log_form_changes`` so update entries render
    old → new per field, and adds the identifying context this module's ticket
    asks for: the record identifier and the client IP. Nothing is written when
    no field actually changed.
    """
    details = dict(extra or {})
    ip = client_ip(request)
    if ip:
        details.setdefault("IP", ip)
    log_form_changes(
        getattr(request, "user", None),
        module=MODULE,
        action=action,
        form=form,
        target=target,
        mask_fields=mask_fields,
        extra=details or None,
    )


def drop_empty(details):
    """Strip keys whose value is None so audit entries stay readable."""
    return {key: value for key, value in details.items() if value is not None}


def employee_details(employee):
    """Standard identifying fields for an employee audit entry.

    ``Employee.__str__`` already renders "First Last (BADGE)", but the badge is
    repeated on its own so the record identifier is filterable rather than only
    embedded in a display string.
    """
    if employee is None:
        return {}
    return drop_empty(
        {
            "Employee": str(employee),
            "Employee ID": getattr(employee, "badge_id", None) or None,
            "Email": getattr(employee, "email", None) or None,
            "Status": "Active" if getattr(employee, "is_active", False) else "Inactive",
        }
    )


def work_info_details(work_info):
    """Standard identifying fields for a work-information audit entry."""
    if work_info is None:
        return {}
    return drop_empty(
        {
            "Employee": str(work_info.employee_id)
            if getattr(work_info, "employee_id", None)
            else None,
            "Job position": str(getattr(work_info, "job_position_id", "") or "") or None,
            "Department": str(getattr(work_info, "department_id", "") or "") or None,
            "Reporting manager": str(
                getattr(work_info, "reporting_manager_id", "") or ""
            )
            or None,
            "Company": str(getattr(work_info, "company_id", "") or "") or None,
        }
    )


def policy_details(policy):
    """Standard identifying fields for a policy audit entry."""
    if policy is None:
        return {}
    return drop_empty(
        {
            "Policy": str(getattr(policy, "title", "") or "") or None,
            "Reference": f"POL-{policy.pk}" if policy.pk else None,
            "Visible to all": "Yes"
            if getattr(policy, "is_visible_to_all", False)
            else "No",
        }
    )
