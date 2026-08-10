"""Audit helpers for the Attendance module.

The attendance views are split across several modules (``views/views.py``,
``views/requests.py``, ``views/clock_in_out.py`` …), so the per-app audit
wrapper other modules keep at the top of their single ``views.py`` lives here
instead and is imported where needed.

Everything written here lands in the "Attendance" tab of Audit Trails.
Attendance *settings* (validation conditions, check-in/out settings, grace
time, allowed IPs, biometric and late-come trackers) are not logged from the
views: they are tracked automatically by ``horilla_audit.config_tracking`` and
surface on the Configuration tab, matching the Settings screens they are edited
from. Logging them again here would double every entry.
"""

from horilla_audit.methods import log_activity

MODULE = "attendance"


def client_ip(request):
    """Best-effort client IP: first X-Forwarded-For hop, else REMOTE_ADDR."""
    meta = getattr(request, "META", None) or {}
    forwarded = meta.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return meta.get("REMOTE_ADDR", "")


def att_audit(request, action, target=None, changes=None):
    """Write an attendance-module ActivityLog entry for a lifecycle event.

    Thin wrapper over ``log_activity`` so every attendance event lands in the
    "Attendance" audit tab with a consistent actor and (where available) the
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


def drop_empty(details):
    """Strip keys whose value is None so audit entries stay readable."""
    return {key: value for key, value in details.items() if value is not None}


def attendance_details(attendance):
    """Standard identifying fields for an attendance record audit entry.

    Gives every attendance event the same shape in the audit tab: who it is
    for, the date, the clock in/out pair and the worked/overtime totals.
    ``Employee.__str__`` already carries the badge id, so the employee name and
    ID travel together in one field.
    """
    return drop_empty(
        {
            "Reference": f"ATT-{attendance.pk}" if attendance.pk else None,
            "Employee": str(attendance.employee_id)
            if getattr(attendance, "employee_id", None)
            else None,
            "Attendance date": str(attendance.attendance_date or "") or None,
            "Clock in": str(attendance.attendance_clock_in or "") or None,
            "Clock out": str(attendance.attendance_clock_out or "") or None,
            "Worked hours": str(attendance.attendance_worked_hour or "") or None,
            "Overtime": str(attendance.attendance_overtime or "") or None,
            "Validated": "Yes" if attendance.attendance_validated else "No",
        }
    )


def overtime_details(account):
    """Standard identifying fields for an overtime/attendance-account entry."""
    return drop_empty(
        {
            "Employee": str(account.employee_id)
            if getattr(account, "employee_id", None)
            else None,
            "Month": str(getattr(account, "month", "") or "") or None,
            "Year": str(getattr(account, "year", "") or "") or None,
            "Worked hours": str(getattr(account, "worked_hours", "") or "") or None,
            "Overtime": str(getattr(account, "overtime", "") or "") or None,
        }
    )


def activity_details(activity):
    """Standard identifying fields for an attendance-activity audit entry."""
    return drop_empty(
        {
            "Employee": str(activity.employee_id)
            if getattr(activity, "employee_id", None)
            else None,
            "Attendance date": str(getattr(activity, "attendance_date", "") or "")
            or None,
            "Clock in": str(getattr(activity, "clock_in", "") or "") or None,
            "Clock out": str(getattr(activity, "clock_out", "") or "") or None,
        }
    )


def status_change(before, after):
    """Render a before/after pair in the diff shape the audit UI uses."""
    return {"Status": {"from": before, "to": after}}
