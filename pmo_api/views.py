import re
from datetime import datetime, time

from django.conf import settings
from django.db.models import DateField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce, ExtractDay, ExtractMonth
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from base.models import Holidays
from employee.models import Employee
from leave.models import LeaveRequest, LeaveType
from offboarding.models import ResignationLetter

from .authentication import APIKeyAuthentication, HasValidAPIKey, PMORateThrottle
from .errors import pmo_exception_handler
from .models import PMODeletedRecord, PMOEmployeeDeactivation
from .serializers import (
    PMOEmployeeSerializer,
    PMOHolidaySerializer,
    PMOLeaveSerializer,
    PMOLeaveTypeSerializer,
)


class PMOBaseAPIView(APIView):
    """
    Base class for all Meridian endpoints: API-key auth, rate limiting, and
    JSON-only errors (401/403/404/405/429/5xx).
    """

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [HasValidAPIKey]
    throttle_classes = [PMORateThrottle]

    def get_exception_handler(self):
        return pmo_exception_handler


class PMOLimitOffsetPagination(LimitOffsetPagination):
    """Returns ``{count, limit, offset, results}`` with a hard max of 100."""

    default_limit = 20
    max_limit = 100

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.count,
                "limit": self.limit,
                "offset": self.offset,
                "results": data,
            }
        )

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
                "results": schema,
            },
        }


class PMOOptInPagination(PMOLimitOffsetPagination):
    """Paginates only when ``limit`` or ``offset`` is supplied (keeps the
    legacy plain-list response for existing clients)."""

    def paginate_queryset(self, queryset, request, view=None):
        params = request.query_params
        if "limit" not in params and "offset" not in params:
            return None
        return super().paginate_queryset(queryset, request, view)


def _company_name_q(prefix):
    """Q matching the server-configured company (never client/session driven)."""
    return Q(**{f"{prefix}__company__icontains": settings.PMO_COMPANY_NAME})


def employee_base_queryset():
    """
    Shared queryset: includes inactive employees. Annotates, in SQL (no N+1):
    ``pmo_planned_leaving`` (offboarding last working date / notice end, else
    latest approved resignation date) and ``pmo_deactivated_on``.
    """
    latest_resignation = (
        ResignationLetter.objects.filter(
            employee_id=OuterRef("pk"), status="approved"
        )
        .order_by("-planned_to_leave_on")
        .values("planned_to_leave_on")[:1]
    )
    deactivated_on = PMOEmployeeDeactivation.objects.filter(
        employee_id=OuterRef("pk")
    ).values("deactivated_on")[:1]
    return (
        Employee.objects.filter(_company_name_q("employee_work_info__company_id"))
        .select_related(
            "employee_work_info__department_id",
            "employee_work_info__job_position_id",
        )
        .annotate(
            pmo_planned_leaving=Coalesce(
                "offboardingemployee__last_working_date",
                "offboardingemployee__notice_period_ends",
                Subquery(latest_resignation, output_field=DateField()),
                output_field=DateField(),
            ),
            pmo_deactivated_on=Subquery(deactivated_on, output_field=DateField()),
        )
    )


def _parse_timestamp(raw):
    """Parse an ISO 8601 date/datetime into an aware datetime, or raise 400."""
    # A literal "+" in a query string is decoded as a space; restore it.
    value = raw.strip().replace(" ", "+")
    parsed = parse_datetime(value)
    if parsed is None:
        day = parse_date(value)
        if day is not None:
            parsed = datetime.combine(day, time.min)
    if parsed is None:
        raise ValidationError(
            {"updated_since": "Invalid timestamp. Use ISO 8601, e.g. 2026-10-08T09:15:00+05:30."}
        )
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


class PMOEmployeeListView(PMOBaseAPIView, generics.ListAPIView):
    """
    GET /api/pmo/v1/employees/

    Query params:
      include_inactive=true  include inactive employees (default: active only)
      updated_since=<ISO8601> only employees with updated_at >= value
      search=<text>          case-insensitive match on name or email
      limit, offset          pagination (max limit 100)
    """

    serializer_class = PMOEmployeeSerializer
    pagination_class = PMOLimitOffsetPagination

    def get_queryset(self):
        qs = employee_base_queryset()
        params = self.request.query_params

        if params.get("include_inactive", "").strip().lower() not in (
            "true",
            "1",
            "yes",
        ):
            # Same rule as ``leaving.effective_is_active``.
            today = timezone.localdate()
            qs = qs.filter(
                Q(pmo_planned_leaving__gt=today)
                | Q(pmo_planned_leaving__isnull=True, is_active=True)
            )

        updated_since = params.get("updated_since")
        if updated_since:
            qs = qs.filter(updated_at__gte=_parse_timestamp(updated_since))

        search = (params.get("search") or "").strip()
        if search:
            cond = Q()
            for term in search.split():
                cond &= (
                    Q(employee_first_name__icontains=term)
                    | Q(employee_last_name__icontains=term)
                    | Q(email__icontains=term)
                )
            qs = qs.filter(cond)

        return qs.order_by("id")


class PMOEmployeeDetailView(PMOBaseAPIView, generics.RetrieveAPIView):
    """
    GET /api/pmo/v1/employees/<id>/

    Includes inactive employees (200 with ``is_active: false``); 404 only
    when the ID does not exist.
    """

    serializer_class = PMOEmployeeSerializer
    lookup_field = "pk"

    def get_queryset(self):
        return employee_base_queryset()


# ---------------------------------------------------------------------------
# Phase 2: leaves, leave types, holidays
# ---------------------------------------------------------------------------

LEAVE_STATUSES = {"requested", "approved", "cancelled", "rejected"}


def _parse_date_param(name, raw):
    parsed = parse_date(raw.strip())
    if parsed is None:
        raise ValidationError({name: "Invalid date. Use YYYY-MM-DD."})
    return parsed


class PMOLeaveListView(PMOBaseAPIView, generics.ListAPIView):
    """
    GET /api/pmo/v1/leaves/

    Query params:
      employee_id=<int>
      status=requested|approved|cancelled|rejected
      updated_since=<ISO8601>   updated_at >= value
      start_date=YYYY-MM-DD, end_date=YYYY-MM-DD
          overlap filter: leave.start <= end_date AND leave.end >= start_date

    """

    serializer_class = PMOLeaveSerializer
    pagination_class = PMOOptInPagination

    def get_queryset(self):
        qs = (
            LeaveRequest.objects.filter(
                _company_name_q("employee_id__employee_work_info__company_id")
            )
            .select_related("leave_type_id")
            .annotate(
                pmo_end_date=Coalesce("end_date", "start_date", output_field=DateField()),
            )
        )
        params = self.request.query_params

        employee_id = params.get("employee_id")
        if employee_id:
            try:
                qs = qs.filter(employee_id_id=int(employee_id))
            except ValueError:
                raise ValidationError({"employee_id": "Must be an integer."})

        status_value = params.get("status")
        if status_value:
            status_value = status_value.strip().lower()
            if status_value not in LEAVE_STATUSES:
                raise ValidationError(
                    {"status": f"Must be one of: {', '.join(sorted(LEAVE_STATUSES))}."}
                )
            qs = qs.filter(status=status_value)

        updated_since = params.get("updated_since")
        if updated_since:
            qs = qs.filter(updated_at__gte=_parse_timestamp(updated_since))

        req_start = params.get("start_date")
        req_end = params.get("end_date")
        start = _parse_date_param("start_date", req_start) if req_start else None
        end = _parse_date_param("end_date", req_end) if req_end else None
        if start and end and start > end:
            raise ValidationError({"end_date": "Must be on or after start_date."})
        if end:
            qs = qs.filter(start_date__lte=end)
        if start:
            qs = qs.filter(pmo_end_date__gte=start)

        return qs.order_by("id")


class PMOLeaveTypeListView(PMOBaseAPIView, generics.ListAPIView):
    """
    GET /api/pmo/v1/leave-types/

    Active types only by default; ``include_inactive=true`` includes the rest
    (each row carries ``is_active``).
    """

    serializer_class = PMOLeaveTypeSerializer
    pagination_class = PMOLimitOffsetPagination

    def get_queryset(self):
        # Global leave types (no company) are permitted, like holidays.
        qs = LeaveType.objects.filter(
            Q(company_id__isnull=True) | _company_name_q("company_id")
        )
        include_inactive = self.request.query_params.get(
            "include_inactive", ""
        ).strip().lower() in ("true", "1", "yes")
        if not include_inactive:
            qs = qs.filter(is_active=True)
        return qs.order_by("id")


class PMOHolidayListView(PMOBaseAPIView, generics.ListAPIView):
    """
    GET /api/pmo/v1/holidays/?year=YYYY

    ``applies_to`` is the company name, or "all" for global holidays. Only
    active holidays that are global (no company) or belong to a company whose
    name contains ``settings.PMO_COMPANY_NAME`` are returned.

    ``year`` returns holidays starting in that year, plus recurring holidays
    that started in that year or earlier. Recurring holidays have their
    ``start_date`` / ``end_date`` projected onto the requested year.
    """

    serializer_class = PMOHolidaySerializer
    pagination_class = PMOLimitOffsetPagination

    def get_serializer_context(self):
        context = super().get_serializer_context()
        year = self.request.query_params.get("year", "")
        if year.isdigit() and len(year) == 4:
            context["year"] = int(year)
        return context

    def get_queryset(self):
        qs = Holidays.objects.select_related("company_id").filter(
            Q(company_id__isnull=True) | _company_name_q("company_id"),
            is_active=True,
        )
        year = self.request.query_params.get("year")
        if year:
            if not (year.isdigit() and len(year) == 4):
                raise ValidationError({"year": "Must be a 4-digit year (YYYY)."})
            year = int(year)
            qs = qs.filter(
                Q(start_date__year=year)
                | Q(recurring=True, start_date__year__lte=year)
            )
            # Recurring dates are projected onto ``year``, so order by month/day.
            return qs.order_by(
                ExtractMonth("start_date"), ExtractDay("start_date"), "id"
            )
        return qs.order_by("start_date", "id")


class PMODeletedListView(PMOBaseAPIView):
    """
    GET /api/pmo/v1/deleted/?resource=employee|leave&deleted_since=<ISO8601>

    Tombstones for hard-deleted records, so incremental sync can remove them.
    Response: ``{count, limit, offset, results: [{resource, id, deleted_at}]}``.
    """

    def get(self, request):
        params = request.query_params
        qs = PMODeletedRecord.objects.all()

        resource = params.get("resource")
        if resource:
            if resource not in ("employee", "leave"):
                raise ValidationError({"resource": "Must be 'employee' or 'leave'."})
            qs = qs.filter(resource=resource)

        deleted_since = params.get("deleted_since")
        if deleted_since:
            qs = qs.filter(deleted_at__gte=_parse_timestamp(deleted_since))

        qs = qs.order_by("id")
        paginator = PMOLimitOffsetPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = [
            {
                "resource": row.resource,
                "id": row.record_id,
                "deleted_at": timezone.localtime(row.deleted_at).isoformat(),
            }
            for row in page
        ]
        return paginator.get_paginated_response(data)


