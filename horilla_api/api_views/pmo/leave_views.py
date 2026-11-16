"""
PMO (Meridian) integration API: leaves, leave types and public holidays.

Read-only endpoints authenticated with the PMO API key (see ``views.py``).

GET /api/pmo/leaves/?start_date=&end_date=&employee_id=&status=&updated_since=
    Paginated leave requests overlapping [start_date, end_date] (both
    required, YYYY-MM-DD). ``status`` may be one value or a comma-separated
    list of: requested, approved, cancelled, rejected. ``updated_since`` is
    an ISO 8601 timestamp with timezone. Leaves of inactive employees are
    included.

GET /api/pmo/leave-types/
    All leave types with ``is_paid``.

GET /api/pmo/holidays/?year=YYYY
    Public holidays falling in ``year``. Recurring holidays are projected
    into the requested year. ``company_id`` / ``company`` tell which
    company's employees a holiday applies to (null = all companies).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django.db.models import DateTimeField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone
from rest_framework.response import Response

from base.models import Holidays
from leave.models import LEAVE_STATUS, LeaveRequest, LeaveType

from .views import (
    PMOAPIView,
    _bad_request,
    _iso,
    _paginate,
    _parse_bounded_int,
    _parse_date,
    _parse_datetime,
)

VALID_LEAVE_STATUSES = {value for value, _label in LEAVE_STATUS}

# Sentinel used so GREATEST() never sees NULL (SQLite returns NULL if any
# argument is NULL); mapped back to None when serialising.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Leaves
# ---------------------------------------------------------------------------


def _leave_queryset():
    """Leave requests annotated with a derived ``pmo_updated_at``.

    LeaveRequest has no last-modified column, so the value is the latest of:
    created_at, reviewed_at, cancellation_reviewed_at and the newest
    audit-history entry (django-simple-history records every save).

    ``.entire()`` is used instead of ``.all()`` because
    ``HorillaCompanyManager.all()`` hides leaves of inactive employees.
    """
    history_model = LeaveRequest.history.model
    last_history = Subquery(
        history_model.objects.filter(history_relation=OuterRef("pk"))
        .order_by("-history_date")
        .values("history_date")[:1],
        output_field=DateTimeField(),
    )
    epoch = Value(_EPOCH, output_field=DateTimeField())
    return (
        LeaveRequest.objects.entire()
        .select_related("leave_type_id")
        .annotate(
            pmo_updated_at=Greatest(
                Coalesce("created_at", epoch),
                Coalesce("reviewed_at", epoch),
                Coalesce("cancellation_reviewed_at", epoch),
                Coalesce(last_history, epoch),
            )
        )
    )


def _serialize_leave(leave):
    updated_at = leave.pmo_updated_at
    if updated_at is not None and updated_at <= _EPOCH:
        updated_at = None
    leave_type = leave.leave_type_id
    return {
        "id": leave.id,
        "employee_id": leave.employee_id_id,
        "leave_type_id": leave.leave_type_id_id,
        "leave_type": leave_type.name if leave_type is not None else None,
        "start_date": _iso(leave.start_date),
        "end_date": _iso(leave.end_date or leave.start_date),
        "start_breakdown": leave.start_date_breakdown,
        "end_breakdown": leave.end_date_breakdown,
        "requested_days": leave.requested_days,
        "status": leave.status,
        "cancellation_status": leave.cancellation_status,
        "updated_at": _iso(updated_at),
    }


class PMOLeaveListAPIView(PMOAPIView):
    """Leave requests overlapping a date range."""

    def get(self, request):
        params = request.query_params

        start, err = _parse_date(params.get("start_date"), "start_date")
        if err:
            return _bad_request(err)
        end, err = _parse_date(params.get("end_date"), "end_date")
        if err:
            return _bad_request(err)
        if start is None or end is None:
            return _bad_request("'start_date' and 'end_date' are required.")
        if end < start:
            return _bad_request("'end_date' must be on or after 'start_date'.")

        qs = _leave_queryset().filter(
            Q(start_date__lte=end)
            & (Q(end_date__gte=start) | Q(end_date__isnull=True, start_date__gte=start))
        )

        employee_id = params.get("employee_id")
        if employee_id not in (None, ""):
            employee_id, err = _parse_bounded_int(employee_id, default=None, minimum=1)
            if err:
                return _bad_request(f"Invalid 'employee_id': {err}.")
            qs = qs.filter(employee_id_id=employee_id)

        status_param = params.get("status")
        if status_param not in (None, ""):
            statuses = {s.strip().lower() for s in status_param.split(",") if s.strip()}
            invalid = statuses - VALID_LEAVE_STATUSES
            if invalid or not statuses:
                return _bad_request(
                    "Invalid 'status'. Allowed values: "
                    + ", ".join(sorted(VALID_LEAVE_STATUSES))
                    + "."
                )
            qs = qs.filter(status__in=statuses)

        updated_since, err = _parse_datetime(
            params.get("updated_since"), "updated_since"
        )
        if err:
            return _bad_request(err)
        if updated_since is not None:
            qs = qs.filter(pmo_updated_at__gt=updated_since)

        return _paginate(request, qs.order_by("id"), _serialize_leave)


# ---------------------------------------------------------------------------
# Leave types
# ---------------------------------------------------------------------------


class PMOLeaveTypeListAPIView(PMOAPIView):
    """All leave types (small table, not paginated)."""

    def get(self, request):
        leave_types = LeaveType.objects.entire().order_by("name", "id")
        return Response(
            {
                "results": [
                    {
                        "id": lt.id,
                        "name": lt.name,
                        "is_paid": lt.payment == "paid",
                        "is_active": bool(lt.is_active),
                    }
                    for lt in leave_types
                ]
            }
        )


# ---------------------------------------------------------------------------
# Holidays
# ---------------------------------------------------------------------------


def _shift_to_year(value: date, year: int) -> date:
    """Move ``value`` to ``year``; 29 Feb becomes 28 Feb in non-leap years."""
    try:
        return value.replace(year=year)
    except ValueError:
        return value.replace(year=year, day=28)


def _serialize_holiday(holiday, start, end):
    company = holiday.company_id
    return {
        "id": holiday.id,
        "name": holiday.name,
        "start_date": _iso(start),
        "end_date": _iso(end),
        "recurring": bool(holiday.recurring),
        "original_start_date": _iso(holiday.start_date),
        "company_id": company.id if company is not None else None,
        "company": company.company if company is not None else None,
        "is_poya_holiday": bool(holiday.is_poya_holiday),
        "is_mercantile_holiday": bool(holiday.is_mercantile_holiday),
    }


class PMOHolidayListAPIView(PMOAPIView):
    """Public holidays in a given year (recurring ones projected)."""

    def get(self, request):
        raw_year = request.query_params.get("year")
        if raw_year in (None, ""):
            return _bad_request("'year' is required.")
        year, err = _parse_bounded_int(
            raw_year, default=None, minimum=1900, maximum=9999
        )
        if err or year > 2999:
            return _bad_request("Invalid 'year': must be a four-digit year.")

        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)
        base_qs = Holidays.objects.entire().select_related("company_id")

        results = []

        one_off = base_qs.filter(recurring=False).filter(
            Q(start_date__lte=year_end)
            & (
                Q(end_date__gte=year_start)
                | Q(end_date__isnull=True, start_date__gte=year_start)
            )
        )
        for holiday in one_off:
            results.append(
                _serialize_holiday(
                    holiday, holiday.start_date, holiday.end_date or holiday.start_date
                )
            )

        # Recurring holidays apply every year from their first occurrence on.
        recurring = base_qs.filter(recurring=True, start_date__year__lte=year)
        for holiday in recurring:
            span = (holiday.end_date or holiday.start_date) - holiday.start_date
            start = _shift_to_year(holiday.start_date, year)
            end = start + max(span, timedelta(0))
            results.append(_serialize_holiday(holiday, start, end))

        results.sort(key=lambda h: (h["start_date"], h["id"]))
        return Response({"year": year, "results": results})
