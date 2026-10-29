from datetime import timezone as dt_timezone

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from employee.models import Employee

from .leaving import effective_is_active, resolve_leaving_date

_HEALTH_KEYWORDS = ("sick", "medical", "maternity", "paternity", "health")


def public_leave_type_name(name):
    """
    GDPR (B4): health-related leave types are exposed under a generic name
    (``settings.PMO_MASKED_LEAVE_NAME``, default "Leave"). Disable with
    ``PMO_MASK_HEALTH_LEAVE=False``.
    """
    if not getattr(settings, "PMO_MASK_HEALTH_LEAVE", True):
        return name
    lowered = (name or "").lower()
    if any(word in lowered for word in _HEALTH_KEYWORDS):
        return getattr(settings, "PMO_MASKED_LEAVE_NAME", "Leave")
    return name


def _work_info(employee):
    """Return the employee's work info or None (reverse one-to-one may be missing)."""
    try:
        return employee.employee_work_info
    except Exception:  # RelatedObjectDoesNotExist
        return None


class PMOEmployeeSerializer(serializers.ModelSerializer):
    """
    Read-only Meridian representation of a Horilla employee.

    Dates are ISO 8601 (YYYY-MM-DD); ``updated_at`` is an ISO 8601 timestamp
    with a UTC offset.
    """

    id = serializers.IntegerField(read_only=True)
    name = serializers.SerializerMethodField()
    first_name = serializers.CharField(source="employee_first_name", read_only=True)
    last_name = serializers.SerializerMethodField()
    email = serializers.EmailField(read_only=True)
    department = serializers.SerializerMethodField()
    job_title = serializers.SerializerMethodField()
    is_active = serializers.SerializerMethodField()
    date_joined = serializers.SerializerMethodField()
    leaving_date = serializers.SerializerMethodField()
    updated_at = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = [
            "id",
            "name",
            "first_name",
            "last_name",
            "email",
            "department",
            "job_title",
            "is_active",
            "date_joined",
            "leaving_date",
            "updated_at",
        ]
        read_only_fields = fields

    def get_name(self, obj):
        return obj.get_full_name()

    def get_last_name(self, obj):
        return obj.employee_last_name or ""

    def get_department(self, obj):
        wi = _work_info(obj)
        if wi and wi.department_id:
            return wi.department_id.department
        return None

    def get_job_title(self, obj):
        wi = _work_info(obj)
        if wi and wi.job_position_id:
            return wi.job_position_id.job_position
        return None

    def get_date_joined(self, obj):
        wi = _work_info(obj)
        if wi and wi.date_joining:
            return wi.date_joining.isoformat()
        return None

    def get_is_active(self, obj):
        """False only on/after the leaving date (see ``pmo_api.leaving``)."""
        return effective_is_active(obj, self._leaving(obj))

    def _leaving(self, obj):
        if not hasattr(obj, "_pmo_leaving"):
            obj._pmo_leaving = resolve_leaving_date(obj)
        return obj._pmo_leaving

    def get_leaving_date(self, obj):
        """Always set for inactive employees; see ``pmo_api.leaving``."""
        date = self._leaving(obj)
        return date.isoformat() if date else None

    def get_updated_at(self, obj):
        value = obj.updated_at
        if not value:
            return None
        if timezone.is_naive(value):
            value = timezone.make_aware(value, dt_timezone.utc)
        return timezone.localtime(value).isoformat()


class PMOLeaveSerializer(serializers.Serializer):
    """
    Read-only Meridian representation of a Horilla LeaveRequest.

    Horilla's breakdown values (full_day / first_half / second_half) already
    match Meridian's enums, so they are passed through.
    """

    id = serializers.IntegerField(read_only=True)
    employee_id = serializers.IntegerField(source="employee_id_id", read_only=True)
    leave_type_id = serializers.IntegerField(
        source="leave_type_id_id", read_only=True
    )
    leave_type = serializers.SerializerMethodField()
    start_date = serializers.DateField(format="%Y-%m-%d", read_only=True)
    end_date = serializers.SerializerMethodField()
    start_breakdown = serializers.SerializerMethodField()
    end_breakdown = serializers.SerializerMethodField()
    requested_days = serializers.FloatField(read_only=True)
    status = serializers.CharField(read_only=True)
    updated_at = serializers.SerializerMethodField()

    _BREAKDOWNS = {"full_day", "first_half", "second_half"}

    def get_leave_type(self, obj):
        return public_leave_type_name(obj.leave_type_id.name)

    def get_end_date(self, obj):
        # end_date is nullable in Horilla; a single-day request ends on start.
        return (obj.end_date or obj.start_date).isoformat()

    def _breakdown(self, value):
        return value if value in self._BREAKDOWNS else "full_day"

    def get_start_breakdown(self, obj):
        return self._breakdown(obj.start_date_breakdown)

    def get_end_breakdown(self, obj):
        return self._breakdown(obj.end_date_breakdown)

    def get_updated_at(self, obj):
        value = obj.updated_at
        if not value:
            return None
        if timezone.is_naive(value):
            value = timezone.make_aware(value, dt_timezone.utc)
        return timezone.localtime(value).isoformat()


class PMOLeaveTypeSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.SerializerMethodField()
    is_paid = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(read_only=True)

    def get_name(self, obj):
        return public_leave_type_name(obj.name)

    def get_is_paid(self, obj):
        return obj.payment == "paid"


class PMOHolidaySerializer(serializers.Serializer):
    """
    When the serializer context carries a ``year`` and the holiday is
    recurring, ``start_date`` / ``end_date`` are projected onto that year.
    """

    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    start_date = serializers.SerializerMethodField()
    end_date = serializers.SerializerMethodField()
    recurring = serializers.BooleanField(read_only=True)
    applies_to = serializers.SerializerMethodField()

    @staticmethod
    def _shift_year(value, new_year):
        """Return ``value`` moved to ``new_year`` (29 Feb -> 28 Feb if needed)."""
        try:
            return value.replace(year=new_year)
        except ValueError:
            return value.replace(year=new_year, day=28)

    def _projected(self, obj):
        """Return (start, end) dates, projected to the requested year if recurring."""
        start = obj.start_date
        end = obj.end_date or obj.start_date
        year = self.context.get("year")
        if obj.recurring and year:
            span_years = end.year - start.year
            start = self._shift_year(start, year)
            end = self._shift_year(end, year + span_years)
        return start, end

    def get_start_date(self, obj):
        return self._projected(obj)[0].isoformat()

    def get_end_date(self, obj):
        return self._projected(obj)[1].isoformat()

    def get_applies_to(self, obj):
        """Company name for company-specific holidays, otherwise "all"."""
        company = obj.company_id
        return company.company if company else "all"


