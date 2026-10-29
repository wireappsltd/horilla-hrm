"""
Leaving-date / deactivation policy for the Meridian API.

Policy (A1):
* ``is_active`` is reported False only on or after the leaving date
  (``today >= leaving_date``); it never flips early because of an HR flag
  alone when a future leaving date exists.
* Every inactive employee has a ``leaving_date``. Sources, in order:
  1. OffboardingEmployee.last_working_date, then notice_period_ends
  2. latest approved ResignationLetter.planned_to_leave_on
  3. the date ``is_active`` was switched off (PMOEmployeeDeactivation)
  4. ``updated_at`` date (legacy rows deactivated before tracking existed)
* The ``pmo_apply_leavers`` management command persists the policy to the
  database daily so ``Employee.updated_at`` moves when the flip happens.
"""

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from .models import PMOEmployeeDeactivation


def planned_leaving_date(employee):
    """Leaving date from offboarding / resignation records, or None."""
    if hasattr(employee, "pmo_planned_leaving"):  # SQL annotation (no queries)
        return employee.pmo_planned_leaving

    try:
        off = employee.offboardingemployee
    except ObjectDoesNotExist:
        off = None
    if off:
        date = off.last_working_date or off.notice_period_ends
        if date:
            return date

    letters = getattr(employee, "pmo_approved_letters", None)
    if letters is None:
        from offboarding.models import ResignationLetter

        letters = list(
            ResignationLetter.objects.filter(
                employee_id=employee, status="approved"
            ).order_by("-planned_to_leave_on")[:1]
        )
    if letters and letters[0].planned_to_leave_on:
        return letters[0].planned_to_leave_on
    return None


def _deactivation_date(employee):
    cache = getattr(employee, "pmo_deactivated_on", False)
    if cache is not False:
        return cache
    return (
        PMOEmployeeDeactivation.objects.filter(employee_id=employee.pk)
        .values_list("deactivated_on", flat=True)
        .first()
    )


def resolve_leaving_date(employee):
    """Leaving date, guaranteed non-None for employees with is_active=False."""
    planned = planned_leaving_date(employee)
    if planned:
        return planned
    if not employee.is_active:
        fallback = _deactivation_date(employee)
        if fallback:
            return fallback
        if employee.updated_at:
            return timezone.localtime(employee.updated_at).date()
        return timezone.localdate()
    return None


def effective_is_active(employee, leaving_date=None, today=None):
    today = today or timezone.localdate()
    leaving_date = leaving_date or resolve_leaving_date(employee)
    if leaving_date is None:
        return bool(employee.is_active)
    # Planned (future) leaving date keeps the employee active until it arrives.
    return leaving_date > today

