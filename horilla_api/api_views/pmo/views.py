"""
PMO (Project Management Office) integration API.

Exposes a read-only employee directory authenticated via a static API key
(instead of admin user credentials), so that the external PMO tool
(Meridian, hosted on Vercel) can create users from Horilla employees, run a
daily sync of department / job title / active status, and deactivate leavers.

Authentication
--------------
Clients must send the API key in either of these headers:

    X-API-Key: <key>
    Authorization: Api-Key <key>

The accepted key(s) are read from the ``PMO_API_KEY`` environment variable
(falls back to ``settings.PMO_API_KEY`` if defined). Several keys may be
configured as a comma-separated list so that a key can be rotated without
downtime: add the new key, let the client switch, then remove the old one.

Endpoints
---------
GET /api/pmo/employees/?search=<term>&include_inactive=<bool>&limit=<n>&offset=<n>
    Returns a paginated list of employees. Only active employees are
    returned unless ``include_inactive=true``. ``limit`` defaults to 20 and
    is clamped to a maximum of 100. ``offset`` must be non-negative.
    ``updated_since`` is not supported (HTTP 400); clients should run a
    full paged sync.

GET /api/pmo/employees/<id>/
    Returns a single employee's directory record, active or inactive
    (``is_active`` tells them apart). 404 only when the ID does not exist.
"""

from __future__ import annotations

import hmac
import os
from datetime import date, datetime

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from employee.models import Employee

# Pagination bounds shared by all PMO list endpoints.
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100

TRUTHY_VALUES = {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Authentication / authorization
# ---------------------------------------------------------------------------


def _get_expected_api_keys():
    """Return the list of configured PMO API keys (may be empty).

    ``PMO_API_KEY`` may hold a single key or a comma-separated list of keys
    (used for zero-downtime key rotation).
    """
    raw = os.environ.get("PMO_API_KEY") or getattr(settings, "PMO_API_KEY", None)
    if not raw:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _extract_api_key(request):
    """Pull the API key from supported headers."""
    key = request.META.get("HTTP_X_API_KEY")
    if key:
        return key.strip()

    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if auth:
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() in {"api-key", "apikey", "bearer"}:
            return parts[1].strip()
    return None


def _is_valid_api_key(provided, expected_keys):
    """Constant-time check of ``provided`` against every configured key.

    Every key is compared (no early exit) so timing does not reveal which
    configured key, if any, matched.
    """
    if not provided or not expected_keys:
        return False
    provided_bytes = provided.encode()
    matched = False
    for key in expected_keys:
        matched |= hmac.compare_digest(provided_bytes, key.encode())
    return matched


class APIKeyAuthentication(BaseAuthentication):
    """DRF authentication backend that validates a shared API key."""

    keyword = "Api-Key"

    def authenticate(self, request):
        provided = _extract_api_key(request)
        if not provided:
            return None

        expected_keys = _get_expected_api_keys()
        if not expected_keys:
            raise AuthenticationFailed("PMO API key is not configured on the server.")

        if not _is_valid_api_key(provided, expected_keys):
            raise AuthenticationFailed("Invalid PMO API key.")

        return (None, provided)

    def authenticate_header(self, request):
        return self.keyword


class HasValidPMOAPIKey(BasePermission):
    """Permission that strictly requires a valid PMO API key."""

    message = "A valid PMO API key is required."

    def has_permission(self, request, view):
        return _is_valid_api_key(_extract_api_key(request), _get_expected_api_keys())


class PMOAPIView(APIView):
    """Base view for every PMO endpoint: API-key auth only."""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [HasValidPMOAPIKey]


# ---------------------------------------------------------------------------
# Query-parameter / formatting helpers (shared with leave_views.py)
# ---------------------------------------------------------------------------


def _bad_request(message):
    return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)


def _parse_bounded_int(value, *, default, minimum, maximum=None):
    """Parse an int query parameter; return (value, error_message)."""
    if value is None or value == "":
        return default, None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, "must be an integer"
    if parsed < minimum:
        return None, f"must be >= {minimum}"
    if maximum is not None and parsed > maximum:
        parsed = maximum
    return parsed, None


def _parse_bool(value):
    return str(value or "").strip().lower() in TRUTHY_VALUES


def _parse_date(value, name):
    """Parse a ``YYYY-MM-DD`` query parameter; return (date, error_message)."""
    if value is None or value == "":
        return None, None
    try:
        parsed = parse_date(value)
    except ValueError:
        parsed = None
    if parsed is None:
        return None, f"Invalid '{name}': expected a date in YYYY-MM-DD format."
    return parsed, None


def _parse_datetime(value, name):
    """Parse an ISO 8601 timestamp that carries a timezone.

    Returns (aware datetime, error_message). Naive timestamps are rejected
    because their meaning would depend on the server timezone.
    """
    if value is None or value == "":
        return None, None
    try:
        parsed = parse_datetime(value)
    except ValueError:
        parsed = None
    if parsed is None:
        return None, (
            f"Invalid '{name}': expected an ISO 8601 timestamp, "
            "e.g. 2026-10-08T09:15:00+05:30."
        )
    if timezone.is_naive(parsed):
        return None, f"Invalid '{name}': the timestamp must include a timezone offset."
    return parsed, None


def _iso(value):
    """Format a date / datetime for the API (datetimes in server-local tz)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _paginate(request, queryset, serialize):
    """Apply limit/offset pagination; return a Response.

    Response shape: ``{count, limit, offset, results}``.
    """
    limit, err = _parse_bounded_int(
        request.query_params.get("limit"),
        default=DEFAULT_PAGE_LIMIT,
        minimum=1,
        maximum=MAX_PAGE_LIMIT,
    )
    if err:
        return _bad_request(f"Invalid 'limit': {err}.")

    offset, err = _parse_bounded_int(
        request.query_params.get("offset"),
        default=0,
        minimum=0,
    )
    if err:
        return _bad_request(f"Invalid 'offset': {err}.")

    total = queryset.count()
    results = [serialize(obj) for obj in queryset[offset : offset + limit]]
    return Response(
        {
            "count": total,
            "limit": limit,
            "offset": offset,
            "results": results,
        }
    )


# ---------------------------------------------------------------------------
# Employee serialization
# ---------------------------------------------------------------------------


def _offboarding_installed():
    return apps.is_installed("offboarding")


def _employee_queryset():
    """Base employee queryset (active and inactive) with related rows loaded.

    Uses ``select_related`` on the manager rather than ``.all()``:
    ``HorillaCompanyManager.all()`` silently drops inactive employees.
    """
    related = [
        "employee_user_id",
        "employee_work_info",
        "employee_work_info__department_id",
        "employee_work_info__job_position_id",
    ]
    if _offboarding_installed():
        related.append("offboardingemployee")
    return Employee.objects.select_related(*related)


def _leaving_date(employee):
    """Last working date from offboarding, if the employee was offboarded."""
    if not _offboarding_installed():
        return None
    try:
        offboarding = employee.offboardingemployee
    except ObjectDoesNotExist:
        return None
    return getattr(offboarding, "last_working_date", None)


def _serialize_employee(employee):
    try:
        work_info = employee.employee_work_info
    except ObjectDoesNotExist:
        work_info = None

    department = None
    job_title = None
    date_joined = None
    if work_info is not None:
        if work_info.department_id is not None:
            department = work_info.department_id.department
        if work_info.job_position_id is not None:
            job_title = work_info.job_position_id.job_position
        date_joined = work_info.date_joining

    user = getattr(employee, "employee_user_id", None)
    user_email = getattr(user, "email", "") if user is not None else ""
    email = user_email or employee.email or ""

    first = employee.employee_first_name or ""
    last = employee.employee_last_name or ""
    full_name = (first + " " + last).strip() or first or last

    return {
        "id": employee.id,
        "name": full_name,
        "first_name": first,
        "last_name": last,
        "email": email,
        "department": department,
        "job_title": job_title,
        "is_active": bool(employee.is_active),
        "date_joined": _iso(date_joined),
        "leaving_date": _iso(_leaving_date(employee)),
        # Employees have no last-modified timestamp in Horilla; always null.
        "updated_at": None,
    }


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class PMOEmployeeListAPIView(PMOAPIView):
    """List employees, optionally filtered by a search term.

    Query parameters:
        search:            Case-insensitive match against first name, last
                           name, user email, department or job title.
        include_inactive:  ``true`` to also return inactive employees.
        limit:             Page size (default 20, clamped to a max of 100).
        offset:            Pagination offset (must be >= 0).
    """

    def get(self, request):
        if request.query_params.get("updated_since") not in (None, ""):
            return _bad_request(
                "'updated_since' is not supported on employees; perform a full sync."
            )

        search = (request.query_params.get("search") or "").strip()
        include_inactive = _parse_bool(request.query_params.get("include_inactive"))

        qs = _employee_queryset()
        if not include_inactive:
            qs = qs.filter(is_active=True)

        if search:
            qs = qs.filter(
                Q(employee_first_name__icontains=search)
                | Q(employee_last_name__icontains=search)
                | Q(employee_user_id__email__icontains=search)
                | Q(employee_work_info__department_id__department__icontains=search)
                | Q(employee_work_info__job_position_id__job_position__icontains=search)
            ).distinct()

        qs = qs.order_by("employee_first_name", "employee_last_name", "id")
        return _paginate(request, qs, _serialize_employee)


class PMOEmployeeDetailAPIView(PMOAPIView):
    """Return a single employee's directory record (active or inactive)."""

    def get(self, request, pk):
        try:
            employee = _employee_queryset().get(pk=pk)
        except Employee.DoesNotExist:
            return Response(
                {"detail": "Employee not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(_serialize_employee(employee))
