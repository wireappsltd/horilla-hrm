"""
apit_t10_views.py

Views used to list employees and generate the APIT T10 (Advance Personal
Income Tax) "Certificate of Income Tax Deductions" PDF prescribed under
Section 87 of the Sri Lanka Inland Revenue Act.

The certificate is generated per employee per year of assessment (1st April
to 31st March) using the employee's confirmed payroll (payslip) data.
"""

import logging
from datetime import date
from io import BytesIO

from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils.text import get_valid_filename
from django.utils.translation import gettext_lazy as _
from xhtml2pdf import pisa

from employee.models import Employee
from horilla.decorators import (
    hx_request_required,
    login_required,
    permission_required,
)
from payroll.methods.methods import paginator_qry
from payroll.models.models import ApitT10Log, Payslip

logger = logging.getLogger(__name__)


# Hard coded employer TIN (Taxpayer Identification Number) per company. Keys
# are the company names exactly as stored on the Company record. Add an entry
# here for each company that issues APIT T10 certificates.
APIT_EMPLOYER_TINS = {
    "Wireapps Pvt Ltd": "103185815",
}
# Used when the employee's company is not present in the mapping above.
DEFAULT_APIT_EMPLOYER_TIN = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_assessment_year_choices():
    """
    Return the selectable years of assessment (e.g. ``"2025/2026"``) derived
    from existing payslip periods. A Sri Lankan year of assessment runs from
    1st April to 31st March. Always includes the current year of assessment.
    """
    years = set()
    today = date.today()
    current_start = today.year if today.month >= 4 else today.year - 1
    years.add(current_start)
    boundaries = Payslip.objects.order_by("start_date").values_list(
        "start_date", flat=True
    )
    first = boundaries.first()
    if first:
        first_start = first.year if first.month >= 4 else first.year - 1
        for year in range(first_start, current_start + 1):
            years.add(year)
    return [f"{year}/{year + 1}" for year in sorted(years, reverse=True)]


def _parse_assessment_year(assessment_year):
    """
    Parse an assessment year label like ``"2025/2026"`` and return the period
    ``(start_date, end_date)`` -> (1st April, 31st March). Returns ``None``
    when the value is invalid.
    """
    try:
        start_year = int(str(assessment_year).split("/")[0])
        return date(start_year, 4, 1), date(start_year + 1, 3, 31)
    except (ValueError, IndexError):
        return None


def _get_employer_tin(employee):
    """
    Return the employer TIN for an employee's company (hard coded mapping).
    """
    company = getattr(
        getattr(employee, "employee_work_info", None), "company_id", None
    )
    company_name = getattr(company, "company", None)
    if company_name and company_name in APIT_EMPLOYER_TINS:
        return APIT_EMPLOYER_TINS[company_name]
    return DEFAULT_APIT_EMPLOYER_TIN


def _amount_in_words(amount):
    """
    Convert a numeric amount into words, e.g. ``1250.50`` ->
    ``"Rupees One Thousand Two Hundred Fifty and Cents Fifty Only"``.
    """
    ones = [
        "",
        "One",
        "Two",
        "Three",
        "Four",
        "Five",
        "Six",
        "Seven",
        "Eight",
        "Nine",
        "Ten",
        "Eleven",
        "Twelve",
        "Thirteen",
        "Fourteen",
        "Fifteen",
        "Sixteen",
        "Seventeen",
        "Eighteen",
        "Nineteen",
    ]
    tens = [
        "",
        "",
        "Twenty",
        "Thirty",
        "Forty",
        "Fifty",
        "Sixty",
        "Seventy",
        "Eighty",
        "Ninety",
    ]

    def two_digits(number):
        if number < 20:
            return ones[number]
        word = tens[number // 10]
        if number % 10:
            word += f" {ones[number % 10]}"
        return word

    def three_digits(number):
        word = ""
        if number >= 100:
            word = f"{ones[number // 100]} Hundred"
            number %= 100
            if number:
                word += " "
        if number:
            word += two_digits(number)
        return word

    def integer_to_words(number):
        if number == 0:
            return "Zero"
        parts = []
        for divisor, label in (
            (10**9, "Billion"),
            (10**6, "Million"),
            (10**3, "Thousand"),
        ):
            if number >= divisor:
                parts.append(f"{three_digits(number // divisor)} {label}")
                number %= divisor
        if number:
            parts.append(three_digits(number))
        return " ".join(parts)

    amount = round(float(amount or 0), 2)
    rupees = int(amount)
    cents = int(round((amount - rupees) * 100))
    words = f"Rupees {integer_to_words(rupees)}"
    if cents:
        words += f" and Cents {integer_to_words(cents)}"
    return f"{words} Only"


PAYE_DEDUCTION_TITLES = ("paye tax", "paye", "apit", "apit tax")

PAYE_DEDUCTION_KEYS = (
    "pretax_deductions",
    "post_tax_deductions",
    "tax_deductions",
    "net_deductions",
)


def _get_paye_tax_amount(payslip):
    """
    Return the total APIT/PAYE tax deducted on a payslip. The amount is stored
    inside ``pay_head_data`` as a deduction titled "PAYE Tax"/"APIT" (in any of
    the deduction buckets, depending on how the component is configured), with
    any computed federal tax added on top.
    """
    data = payslip.pay_head_data or {}
    total = 0.0
    for key in PAYE_DEDUCTION_KEYS:
        for deduction in data.get(key) or []:
            title = str(deduction.get("title", "")).strip().lower()
            if title in PAYE_DEDUCTION_TITLES:
                total += float(deduction.get("amount") or 0)
    total += float(data.get("federal_tax") or 0)
    return total


def _collect_certificate_data(employee, assessment_year):
    """
    Aggregate the employee's payslip data for the assessment year and return
    the context required to render the APIT T10 certificate.

    Returns ``(data, error)`` - ``error`` is a human readable message when the
    payroll data is missing/incomplete, in which case ``data`` is ``None``.
    """
    period = _parse_assessment_year(assessment_year)
    if not period:
        return None, _("Invalid year of assessment selected.")
    period_start, period_end = period

    # Only confirmed payroll runs are certified - draft / review payslips
    # do not represent remuneration actually paid.
    payslips = Payslip.objects.filter(
        employee_id=employee,
        status__in=["confirmed", "paid"],
        start_date__gte=period_start,
        start_date__lte=period_end,
    ).order_by("start_date")
    if not payslips.exists():
        return None, _(
            "No confirmed payroll data found for %(employee)s for the "
            "%(year)s year of assessment."
        ) % {"employee": employee.get_full_name(), "year": assessment_year}

    gross_remuneration = 0.0
    cash_benefits = 0.0
    non_cash_benefits = 0.0
    excluded_benefits = 0.0
    total_tax_deducted = 0.0

    for payslip in payslips:
        data = payslip.pay_head_data or {}
        # Prefer the stored payslip figure; fall back to the computed pay
        # head data for older payslips where the model field was not set.
        gross_pay = payslip.gross_pay or data.get("gross_pay") or 0
        gross_remuneration += float(gross_pay)
        total_tax_deducted += _get_paye_tax_amount(payslip)
        for allowance in data.get("allowances") or []:
            amount = float(allowance.get("amount") or 0)
            cash_benefits += amount
            if allowance.get("is_taxable") is False:
                excluded_benefits += amount

    if gross_remuneration <= 0:
        return None, _(
            "Payroll data for %(employee)s is incomplete for the %(year)s "
            "year of assessment (no gross remuneration recorded)."
        ) % {"employee": employee.get_full_name(), "year": assessment_year}

    work_info = getattr(employee, "employee_work_info", None)
    company = getattr(work_info, "company_id", None)
    employer_address = ""
    if company:
        employer_address = ", ".join(
            part
            for part in [
                company.address,
                company.city,
                company.zip,
                company.country,
            ]
            if part
        )

    service_from = max(payslips.first().start_date, period_start)
    service_to = min(payslips.last().end_date, period_end)

    return (
        {
            "employee": employee,
            "assessment_year": assessment_year,
            "employer_tin": _get_employer_tin(employee),
            "employment_type": "primary",
            "employee_full_name": employee.get_full_name(),
            "pay_sheet_serial_number": employee.badge_id or "",
            "nic_number": employee.nic or "",
            "service_from": service_from,
            "service_to": service_to,
            "gross_remuneration": round(gross_remuneration, 2),
            "cash_benefits": round(cash_benefits, 2),
            "non_cash_benefits": round(non_cash_benefits, 2),
            "excluded_benefits": round(excluded_benefits, 2),
            "total_tax_deducted": round(total_tax_deducted, 2),
            "total_tax_deducted_words": _amount_in_words(total_tax_deducted),
            "total_remitted": round(total_tax_deducted, 2),
            "employer_name": getattr(company, "company", "") or "",
            "employer_address": employer_address,
            "generated_on": date.today(),
        },
        None,
    )


def get_apit_employees(request):
    """
    Return the active employees filtered by the request's search/department
    query parameters.
    """
    employees = Employee.objects.filter(is_active=True).select_related(
        "employee_work_info",
        "employee_work_info__department_id",
        "employee_work_info__company_id",
    )
    search = request.GET.get("search", "").strip()
    if search:
        employees = employees.filter(
            Q(employee_first_name__icontains=search)
            | Q(employee_last_name__icontains=search)
            | Q(badge_id__icontains=search)
            | Q(nic__icontains=search)
        )
    department_id = request.GET.get("department")
    if department_id:
        employees = employees.filter(
            employee_work_info__department_id=department_id
        )
    return employees.order_by("employee_first_name")


def get_selected_assessment_year(request):
    """
    Return the assessment year selected on the request (falls back to the
    current year of assessment).
    """
    year_choices = _get_assessment_year_choices()
    selected_year = request.GET.get("assessment_year")
    if selected_year not in year_choices:
        selected_year = year_choices[0] if year_choices else ""
    return selected_year, year_choices


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@login_required
@permission_required("payroll.view_payslip")
def view_apit_t10(request):
    """
    Render the APIT T10 page listing employees with year of assessment and
    department filters.
    """
    from base.models import Department

    employees = get_apit_employees(request)
    employees = paginator_qry(employees, request.GET.get("apit_page"))
    selected_year, year_choices = get_selected_assessment_year(request)
    return render(
        request,
        "payroll/apit_t10/view_apit_t10.html",
        {
            "employees": employees,
            "year_choices": year_choices,
            "selected_year": selected_year,
            "departments": Department.objects.all(),
            "selected_department": request.GET.get("department", ""),
            "search": request.GET.get("search", ""),
        },
    )


@login_required
@hx_request_required
@permission_required("payroll.view_payslip")
def filter_apit_t10(request):
    """
    Return the APIT T10 employee list partial (used by the filters and
    pagination on the APIT T10 page).
    """
    employees = get_apit_employees(request)
    employees = paginator_qry(employees, request.GET.get("apit_page"))
    selected_year, _year_choices = get_selected_assessment_year(request)
    return render(
        request,
        "payroll/apit_t10/list_apit_t10.html",
        {
            "employees": employees,
            "selected_year": selected_year,
            "selected_department": request.GET.get("department", ""),
            "search": request.GET.get("search", ""),
        },
    )


@login_required
@permission_required("payroll.view_payslip")
def download_apit_t10(request, employee_id):
    """
    Generate and download the populated APIT T10 certificate PDF for the
    employee and the selected year of assessment. Shows an error message
    instead of generating a blank/partial certificate when the payroll data
    is incomplete.
    """
    employee = get_object_or_404(Employee, id=employee_id)
    selected_year, _year_choices = get_selected_assessment_year(request)

    data, error = _collect_certificate_data(employee, selected_year)
    if error:
        messages.error(request, error)
        return redirect("view-apit-t10")

    html = render_to_string("payroll/apit_t10/apit_t10_pdf.html", data)
    result = BytesIO()
    pdf_status = pisa.CreatePDF(src=html, dest=result)
    if pdf_status.err:
        logger.error(
            "Failed to generate APIT T10 PDF for employee %s", employee_id
        )
        messages.error(request, _("Could not generate the APIT T10 PDF."))
        return redirect("view-apit-t10")

    # Audit log: employee, generated by (created_by) and timestamp.
    ApitT10Log.objects.create(
        employee_id=employee, assessment_year=selected_year
    )

    year_label = selected_year.replace("/", "-")
    file_name = get_valid_filename(
        f"APIT T10 - {employee.get_full_name()} - {year_label}.pdf"
    )
    response = HttpResponse(result.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{file_name}"'
    return response


