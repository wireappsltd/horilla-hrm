"""
PMO (Project Management Office) integration API.

Exposes a read-only employee directory endpoint authenticated via a static
API key (instead of admin user credentials), so that the external PMO tool
hosted on Vercel can populate its "user creation" autocomplete dropdown
with employees from Horilla.

Required fields exposed: id, name, department, job_title, email.

Authentication
--------------
Clients must send the API key in either of these headers:

    X-API-Key: <key>
    Authorization: Api-Key <key>

The expected key value is read from the ``PMO_API_KEY`` environment
variable (falls back to ``settings.PMO_API_KEY`` if defined).

Endpoints
---------
GET /api/pmo/employees/?search=<term>&limit=<n>&offset=<n>
    Returns a paginated list of active employees. Used to power the
    autocomplete dropdown on the PMO "create user" forms.
    ``limit`` defaults to 20 and is clamped to a maximum of 100.
    ``offset`` must be non-negative.

GET /api/pmo/employees/<id>/
    Returns a single employee's directory record.
"""

from __future__ import annotations

import hmac
import os

from django.conf import settings
from django.db.models import Q
from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from employee.models import Employee

# Pagination bounds for the employee directory endpoint.
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100


# ---------------------------------------------------------------------------
# Authentication / authorization
# ---------------------------------------------------------------------------


def _get_expected_api_key():
    """Return the configured PMO API key, or ``None`` if not configured."""
    key = os.environ.get("PMO_API_KEY") or getattr(settings, "PMO_API_KEY", None)
    if key:
        key = str(key).strip()
    return key or None


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


class APIKeyAuthentication(BaseAuthentication):
    """DRF authentication backend that validates a shared API key."""

    keyword = "Api-Key"

    def authenticate(self, request):
        provided = _extract_api_key(request)
        if not provided:
            return None

        expected = _get_expected_api_key()
        if not expected:
            raise AuthenticationFailed(
                "PMO API key is not configured on the server."
            )

        if not hmac.compare_digest(provided, expected):
            raise AuthenticationFailed("Invalid PMO API key.")

        return (None, provided)

    def authenticate_header(self, request):
        return self.keyword


class HasValidPMOAPIKey(BasePermission):
    """Permission that strictly requires a valid PMO API key."""

    message = "A valid PMO API key is required."

    def has_permission(self, request, view):
        provided = _extract_api_key(request)
        expected = _get_expected_api_key()
        if not provided or not expected:
            return False
        return hmac.compare_digest(provided, expected)


# ---------------------------------------------------------------------------
# Serialization helper
# ---------------------------------------------------------------------------


def _serialize_employee(employee):
    work_info = getattr(employee, "employee_work_info", None)

    department = None
    job_title = None
    if work_info is not None:
        if work_info.department_id is not None:
            department = work_info.department_id.department
        if work_info.job_position_id is not None:
            job_title = work_info.job_position_id.job_position


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
    }


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


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


class PMOEmployeeListAPIView(APIView):
    """List active employees, optionally filtered by a search term.

    Query parameters:
        search:  Case-insensitive match against first name, last name,
                 user email, department or job title.
        limit:   Page size (default 20, clamped to a max of 100).
        offset:  Pagination offset (must be >= 0).
    """

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [HasValidPMOAPIKey]

    def get(self, request):
        search = (request.query_params.get("search") or "").strip()

        limit, err = _parse_bounded_int(
            request.query_params.get("limit"),
            default=DEFAULT_PAGE_LIMIT,
            minimum=1,
            maximum=MAX_PAGE_LIMIT,
        )
        if err:
            return Response(
                {"detail": f"Invalid 'limit': {err}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        offset, err = _parse_bounded_int(
            request.query_params.get("offset"),
            default=0,
            minimum=0,
        )
        if err:
            return Response(
                {"detail": f"Invalid 'offset': {err}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = (
            Employee.objects.filter(is_active=True)
            .select_related(
                "employee_user_id",
                "employee_work_info",
                "employee_work_info__department_id",
                "employee_work_info__job_position_id",
            )
            .order_by("employee_first_name", "employee_last_name", "id")
        )

        if search:
            qs = qs.filter(
                Q(employee_first_name__icontains=search)
                | Q(employee_last_name__icontains=search)
                | Q(employee_user_id__email__icontains=search)
                | Q(employee_work_info__department_id__department__icontains=search)
                | Q(
                    employee_work_info__job_position_id__job_position__icontains=search
                )
            ).distinct()

        total = qs.count()
        results = [_serialize_employee(e) for e in qs[offset : offset + limit]]

        return Response(
            {
                "count": total,
                "limit": limit,
                "offset": offset,
                "results": results,
            }
        )


class PMOEmployeeDetailAPIView(APIView):
    """Return a single employee's directory record."""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [HasValidPMOAPIKey]

    def get(self, request, pk):
        try:
            employee = (
                Employee.objects.select_related(
                    "employee_user_id",
                    "employee_work_info",
                    "employee_work_info__department_id",
                    "employee_work_info__job_position_id",
                )
                .filter(is_active=True)
                .get(pk=pk)
            )
        except Employee.DoesNotExist:
            return Response(
                {"detail": "Employee not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(_serialize_employee(employee))

