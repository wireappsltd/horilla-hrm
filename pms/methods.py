"""
pms/methods.py

Service/helper functions for the pms (Performance Management) app.
"""

from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from django.db import transaction

from employee.models import Employee
from pms.models import (
    PROBATION_REVIEW_CRITERIA_DEFINITIONS,
    ProbationReview,
    ProbationReviewCriterion,
)


def active_probation_reviews():
    """
    Return the queryset of "active" probation reviews.

    An active review is one that is active (``is_active=True``) and not yet
    completed. This is the single shared definition of an "active review"
    used by both generate_probation_review() (idempotency) and
    get_probation_eligible_employees() (exclusion), so the two stay
    consistent.
    """
    return ProbationReview.objects.filter(is_active=True).exclude(status="completed")


def probation_due_date(work_info):
    """
    Compute the probationary review due date for an EmployeeWorkInformation,
    together with the source the date came from.

    Rule (confirmed, unchanged): prefer the explicit ``probation_end_date``
    when it is populated; otherwise fall back to ``date_joining + 6 months``.

    Returns:
        A ``(due_date, source)`` tuple:
            - ``source == "probation_end_date"`` when the real
              ``probation_end_date`` field was used.
            - ``source == "computed"`` when the ``date_joining + 6 months``
              fallback was used.
        Returns ``(None, None)`` when neither can be determined (missing work
        info / null dates), which callers treat as "not eligible / cannot
        compute".
    """
    if work_info is None:
        return (None, None)
    if getattr(work_info, "probation_end_date", None):
        return (work_info.probation_end_date, "probation_end_date")
    if getattr(work_info, "date_joining", None):
        return (work_info.date_joining + relativedelta(months=6), "computed")
    return (None, None)


def generate_probation_review(employee, created_by=None, reviewers=None):
    """
    Generate a blank probationary review for the given employee.

    The review-time capture fields (job title, joining date, immediate
    supervisor) are populated from the employee's work information. The
    review is created in ``in_progress`` status together with the 11 unscored
    criterion rows defined in ``PROBATION_REVIEW_CRITERIA_DEFINITIONS``.

    Idempotency: if the employee already has an active (non-completed)
    review, that existing review is returned instead of creating a
    duplicate. When ``reviewers`` is supplied for an already-existing
    review, the reviewer set is updated on that review (so HR can adjust who
    is allowed to edit without creating a duplicate).

    Note: this function performs no eligibility checking. It builds a
    review for whatever employee it is given.

    Args:
        employee: The Employee instance the review is for.
        created_by: Optional User to set as the creator on the review and
            its criteria (audit fields).
        reviewers: Optional iterable of Employee instances (or ids) allowed
            to view and edit the review. HR/Admin with change permission can
            always edit regardless of this set.

    Returns:
        The ProbationReview instance (existing or newly created).
    """
    # Idempotency: an "existing review" is an active, non-completed review
    # for this employee. Completed / inactive reviews do not block a new one.
    # Reuses the shared active-review definition (active_probation_reviews).
    existing_review = active_probation_reviews().filter(employee_id=employee).first()
    if existing_review:
        # Allow HR to (re)assign reviewers on the existing review.
        if reviewers is not None:
            existing_review.reviewers.set(reviewers)
        return existing_review

    # Source review-time capture fields from the employee's work information.
    # The Employee helpers and getattr fallbacks handle a missing
    # employee_work_info or null fields without erroring.
    work_info = getattr(employee, "employee_work_info", None)
    job_position = employee.get_job_position()
    reporting_manager = employee.get_reporting_manager()

    with transaction.atomic():
        review = ProbationReview(
            employee_id=employee,
            job_title=str(job_position) if job_position else None,
            date_of_join=getattr(work_info, "date_joining", None),
            immediate_supervisor=reporting_manager,
            status="in_progress",
        )
        if created_by is not None:
            review.created_by = created_by
        review.save()

        if reviewers is not None:
            review.reviewers.set(reviewers)

        criteria = [
            ProbationReviewCriterion(
                probation_review_id=review,
                section=definition["section"],
                title=definition["title"],
                description=definition["description"],
                marks=None,
                created_by=created_by,
            )
            for definition in PROBATION_REVIEW_CRITERIA_DEFINITIONS
        ]
        ProbationReviewCriterion.objects.bulk_create(criteria)

    return review


def can_edit_probation_review(user, review):
    """
    Return True when ``user`` is allowed to view/edit ``review``.

    Access is granted to:
        - HR/Admin: any user holding the ``pms.change_probationreview``
          permission (unchanged existing behaviour).
        - Assigned reviewers: a user whose linked Employee is in the
          review's ``reviewers`` set (the people HR selected in the Generate
          popup).

    Args:
        user: The Django ``User`` from the request.
        review: The ProbationReview being accessed.

    Returns:
        bool
    """
    if user.has_perm("pms.change_probationreview"):
        return True
    employee = getattr(user, "employee_get", None)
    if employee is None:
        return False
    return review.reviewers.filter(id=employee.id).exists()


def get_probation_eligible_employees(lookahead_days=30):
    """
    Return the active employees who are due (or approaching due) for a
    probationary review.

    An employee is eligible when their probation due date (see
    ``probation_due_date``: ``probation_end_date`` when populated, else
    ``date_joining + 6 months``) falls on or before ``today + lookahead_days``.

    Rules:
        - Only active employees with employee work information are considered.
        - Employees whose due date cannot be computed (missing work info /
          null dates) are excluded without erroring.
        - Employees who already have an active (non-completed) review are
          excluded, using the shared ``active_probation_reviews`` definition
          so this stays consistent with generate_probation_review().
        - The query is driven off the company-scoped ``Employee.objects``
          manager, mirroring the pms models' tenant boundary so HR only sees
          their own company's employees.

    Each returned Employee instance is annotated with:
        - ``probation_due_date``: the computed due date (a ``date``).
        - ``probation_due_source``: where that date came from, either
          ``"probation_end_date"`` (real HR-entered field) or ``"computed"``
          (the ``date_joining + 6 months`` fallback). This lets the list show
          HR which due dates are real vs computed fallbacks.

    Args:
        lookahead_days: How many days ahead of today to include as
            "approaching". Defaults to 30.

    Returns:
        A list of Employee instances (annotated with ``probation_due_date``
        and ``probation_due_source``).
    """
    cutoff = date.today() + timedelta(days=lookahead_days)

    # Employees who already have an active (non-completed) review — excluded.
    reviewed_employee_ids = active_probation_reviews().values_list(
        "employee_id", flat=True
    )

    # Company-scoped active employees that have work information. Using
    # Employee.objects (HorillaCompanyManager) keeps the tenant boundary.
    candidates = (
        Employee.objects.filter(is_active=True, employee_work_info__isnull=False)
        .exclude(id__in=reviewed_employee_ids)
        .select_related("employee_work_info")
    )

    eligible_employees = []
    for employee in candidates:
        work_info = getattr(employee, "employee_work_info", None)
        due_date, due_source = probation_due_date(work_info)
        # Null / uncomputable due dates are excluded.
        if due_date and due_date <= cutoff:
            employee.probation_due_date = due_date
            employee.probation_due_source = due_source
            eligible_employees.append(employee)

    return eligible_employees


# === PERFORMANCE MODULE DISABLED (HRMOD-541): commented out; restore when reworking PMS ===
# from django.contrib import messages
# from django.http import HttpResponse
# from django.shortcuts import render
# from pyexpat.errors import messages
#
# from employee.models import EmployeeWorkInformation
# from pms.models import AnonymousFeedback, EmployeeObjective, Objective
#
# decorator_with_arguments = (
#     lambda decorator: lambda *args, **kwargs: lambda func: decorator(
#         func, *args, **kwargs
#     )
# )
#
#
# @decorator_with_arguments
# def pms_manager_can_enter(function, perm):
#     """
#     This method is used to check permission to employee for enter to the function if the employee
#     do not have permission also checks, has reporting manager or manager of respective objective.
#     """
#
#     def _function(request, *args, **kwargs):
#         user = request.user
#         employee = user.employee_get
#         is_manager = EmployeeWorkInformation.objects.filter(
#             reporting_manager_id=employee
#         ).exists()
#         is_objective_manager = Objective.objects.filter(managers=employee).exists()
#         if user.has_perm(perm) or is_manager or is_objective_manager:
#             return function(request, *args, **kwargs)
#         else:
#             messages.info(request, "You dont have permission.")
#             previous_url = request.META.get("HTTP_REFERER", "/")
#             script = f'<script>window.location.href = "{previous_url}"</script>'
#             key = "HTTP_HX_REQUEST"
#             if key in request.META.keys():
#                 return render(request, "decorator_404.html")
#             return HttpResponse(script)
#
#     return _function
#
#
# @decorator_with_arguments
# def pms_owner_and_manager_can_enter(function, perm):
#     """
#     This method is used to check permission to employee for enter to the function if the employee
#     do not have permission also checks, has reporting manager or manager of respective objective.
#     """
#
#     def _function(request, *args, **kwargs):
#         user = request.user
#         employee = user.employee_get
#         is_manager = EmployeeWorkInformation.objects.filter(
#             reporting_manager_id=employee
#         ).exists()
#         is_objective_owner = EmployeeObjective.objects.filter(
#             employee_id=employee
#         ).exists()
#         is_objective_manager = Objective.objects.filter(managers=employee).exists()
#         if (
#             user.has_perm(perm)
#             or is_manager
#             or is_objective_manager
#             or is_objective_owner
#         ):
#             return function(request, *args, **kwargs)
#         else:
#             messages.info(request, "You dont have permission.")
#             previous_url = request.META.get("HTTP_REFERER", "/")
#             script = f'<script>window.location.href = "{previous_url}"</script>'
#             key = "HTTP_HX_REQUEST"
#             if key in request.META.keys():
#                 return render(request, "decorator_404.html")
#             return HttpResponse(script)
#
#     return _function
#
#
# def check_permission_feedback_detailed_view(request, feedback, perm):
#     """
#     Checks if the user has permission to view the detailed view of feedback.
#
#     The user is allowed if they:
#     - Have the required permission
#     - Are the owner of the feedback
#     - Are the reporting manager of the feedback owner
#     - Are the feedback manager
#
#     Args:
#         request: The HTTP request object containing the user.
#         feedback: The feedback object being accessed.
#         perm: The specific permission required.
#
#     Returns:
#         bool: True if the user has permission, False otherwise.
#     """
#     user = request.user
#     employee = user.employee_get
#
#     # Check if the user is the reporting manager of the feedback owner
#     is_manager = EmployeeWorkInformation.objects.filter(
#         reporting_manager_id=employee, employee_id=feedback.employee_id
#     ).exists()
#
#     # Check for permission, if the user is the feedback manager, reporting manager, or the feedback owner
#     has_permission = (
#         user.has_perm(perm)
#         or feedback.manager_id == employee
#         or is_manager
#         or feedback.employee_id == employee
#     )
#
#     return has_permission
#
#
# def get_anonymous_feedbacks(employee):
#     department = employee.get_department()
#     job_position = employee.get_job_position()
#     anonymous_feedbacks = (
#         AnonymousFeedback.objects.filter(department_id=department)
#         | AnonymousFeedback.objects.filter(job_position_id=job_position)
#         | AnonymousFeedback.objects.filter(employee_id=employee)
#     )
#     return anonymous_feedbacks
#
#
# def check_duplication(feedback, other_employees):
#     """Remove already existing employee from feedback request and return updated employees"""
#     req_employees = set(feedback.subordinate_id.all())
#     req_employees.update(feedback.colleague_id.all())
#     if feedback.manager_id:
#         req_employees.add(feedback.manager_id)
#     if feedback.employee_id:
#         req_employees.add(feedback.employee_id)
#     # Remove already requested employees from others_id
#     updated_employees = [emp for emp in other_employees if emp not in req_employees]
#     return updated_employees
#