import calendar
from datetime import datetime, timedelta

from django.utils.timezone import now
from django.apps import apps
from django.db.models import Q

from base.methods import is_mercantile_or_poya_holiday
from employee.models import Employee
from horilla.methods import get_horilla_model_class


def get_selected_company(request):
    """
    Return the specific company id currently selected (logged in) in the
    session, or ``None`` when no specific company context is active
    (i.e. nothing selected or "All Company" is selected).
    """
    selected_company = None
    if request is not None:
        selected_company = request.session.get("selected_company")
    if not selected_company or selected_company == "all":
        return None
    return selected_company


def company_selection_required(request):
    """
    Determine whether the current user must select (log in to) a specific
    company before they are allowed to submit a leave request.

    Special privileged users who are associated with multiple companies (i.e.
    users who are allowed to switch between companies via the company
    selector) must have an active/specific company context. This ensures the
    leave request is applied under the correct company.

    """
    from base.models import Company

    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return False

    # Only privileged users who can switch between multiple companies are
    # affected by this requirement.
    is_multi_company_user = user.has_perm("base.change_company") and (
        Company.objects.count() > 1
    )
    if not is_multi_company_user:
        return False

    return get_selected_company(request) is None


def calculate_requested_days(
    start_date, end_date, start_date_breakdown, end_date_breakdown
):
    if start_date == end_date:
        return (
            1
            if start_date_breakdown == "full_day" and end_date_breakdown == "full_day"
            else 0.5
        )

    # Count full days between the two dates, excluding start and end
    middle_days = (end_date - start_date).days - 1

    # Count start and end days
    start_day_value = 1 if start_date_breakdown == "full_day" else 0.5
    end_day_value = 1 if end_date_breakdown == "full_day" else 0.5

    return middle_days + start_day_value + end_day_value


def holiday_dates_list(holidays):
    """
    :return: This function returns a list of all holiday dates.
    """
    holiday_dates = []
    for holiday in holidays:
        holiday_start_date = holiday.start_date
        holiday_end_date = holiday.end_date or holiday_start_date
        holiday_dates.extend(
            holiday_start_date + timedelta(i)
            for i in range((holiday_end_date - holiday_start_date).days + 1)
        )
    return holiday_dates


def company_leave_dates_list(company_leaves, start_date):
    """
    :return: This function returns a list of all company leave dates
    """
    company_leave_dates = set()
    year = start_date.year
    for company_leave in company_leaves:
        based_on_week = company_leave.based_on_week
        based_on_week_day = company_leave.based_on_week_day

        for month in range(1, 13):
            month_calendar = calendar.monthcalendar(year, month)

            if based_on_week is not None:
                # Set Sunday as the first day of the week
                calendar.setfirstweekday(6)
                try:
                    week_days = [
                        day for day in month_calendar[int(based_on_week)] if day != 0
                    ]
                    for day in week_days:
                        date = datetime(year, month, day)
                        if date.weekday() == int(based_on_week_day):
                            company_leave_dates.add(date.date())
                except IndexError:
                    pass
            else:
                # Set Monday as the first day of the week
                calendar.setfirstweekday(0)
                for week in month_calendar:
                    if week[int(based_on_week_day)] != 0:
                        date = datetime(year, month, week[int(based_on_week_day)])
                        company_leave_dates.add(date.date())

    return list(company_leave_dates)


def get_leave_day_attendance(employee, comp_id=None):
    """
    This function returns a queryset of attendance on leave dates
    """
    Attendance = get_horilla_model_class(app_label="attendance", model="attendance")
    from leave.models import CompensatoryLeaveRequest

    attendances_to_exclude = Attendance.objects.none()  # Empty queryset to start with
    # Check for compensatory leave requests that are not rejected and not the current one
    if (
        CompensatoryLeaveRequest.objects.filter(employee_id=employee)
        .exclude(Q(id=comp_id) | Q(status="rejected"))
        .exists()
    ):
        comp_leave_reqs = CompensatoryLeaveRequest.objects.filter(
            employee_id=employee
        ).exclude(Q(id=comp_id) | Q(status="rejected"))
        for req in comp_leave_reqs:
            attendances_to_exclude |= req.attendance_id.all()
    # Filter holiday attendance excluding the attendances in attendances_to_exclude
    holiday_attendance = Attendance.objects.filter(
        is_holiday=True, employee_id=employee, attendance_validated=True
    ).exclude(id__in=attendances_to_exclude.values_list("id", flat=True))
    return holiday_attendance


def attendance_days(employee, attendances):
    """
    This function returns count of workrecord from the attendance
    """
    attendance_days = 0
    if apps.is_installed("attendance"):
        from attendance.models import WorkRecords

        for attendance in attendances:
            if WorkRecords.objects.filter(
                employee_id=employee, date=attendance.attendance_date
            ).exists():
                work_record_type = (
                    WorkRecords.objects.filter(
                        employee_id=employee, date=attendance.attendance_date
                    )
                    .first()
                    .work_record_type
                )
                if work_record_type == "HDP":
                    attendance_days += 0.5
                elif work_record_type == "FDP":
                    attendance_days += 1
    return attendance_days


def filter_conditional_leave_request(request):
    """
    Filters and returns LeaveRequest objects that have been conditionally approved by the previous sequence of approvals.
    """
    approval_manager = Employee.objects.filter(employee_user_id=request.user).first()
    leave_request_ids = []
    if apps.is_installed("leave"):
        from leave.models import LeaveRequest, LeaveRequestConditionApproval

        multiple_approval_requests = LeaveRequestConditionApproval.objects.filter(
            manager_id=approval_manager
        )
    else:
        multiple_approval_requests = None
    for instance in multiple_approval_requests:
        if instance.sequence > 1:
            pre_sequence = instance.sequence - 1
            leave_request_id = instance.leave_request_id
            instance = LeaveRequestConditionApproval.objects.filter(
                leave_request_id=leave_request_id, sequence=pre_sequence
            ).first()
            if instance and instance.is_approved:
                leave_request_ids.append(instance.leave_request_id.id)
        else:
            leave_request_ids.append(instance.leave_request_id.id)
    return LeaveRequest.objects.filter(pk__in=leave_request_ids)




def is_carryforward_valid(leave_type, leave_start_date):
    if leave_type.carryforward_expire_date:
        return leave_start_date <= leave_type.carryforward_expire_date

    if (
        leave_type.carryforward_expire_in
        and leave_type.carryforward_expire_period
    ):
        today = now().date()

        if leave_type.carryforward_expire_period == "day":
            expiry_date = today + timedelta(days=leave_type.carryforward_expire_in)

        elif leave_type.carryforward_expire_period == "month":
            expiry_date = today.replace(
                month=min(today.month + leave_type.carryforward_expire_in, 12)
            )

        else:
            return False

        return leave_start_date <= expiry_date

    return False


def calculate_max_mercantile_leave_days_based_on_holidays(company_id=None):
    """
    This function calculates the total number of mercantile days
    and updates the compensatory leave type's total_days accordingly.

    If ``company_id`` is provided, the calculation and update are scoped to that
    company. If ``company_id`` is ``None``, the calculation is performed across
    all companies (backwards-compatible behavior).
    """
    from base.models import Holidays
    from leave.models import LeaveType

    current_year = now().year
    holidays_filter = {
        "is_mercantile_holiday": True,
        "start_date__year": current_year,
    }
    if company_id is not None:
        holidays_filter["company_id"] = company_id

    holidays = Holidays.objects.filter(**holidays_filter)

    holiday_dates = set(holiday_dates_list(holidays))
    total_days = len(holiday_dates)

    leave_type_filter = {"is_compensatory_leave": True}
    if company_id is not None:
        leave_type_filter["company_id"] = company_id

    LeaveType.objects.filter(**leave_type_filter).update(
        total_days=total_days,
        count=total_days
    )
    return total_days