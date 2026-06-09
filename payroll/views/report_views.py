"""
report_views.py

This module contains the views used to generate and download statutory payroll
reports (e.g. the ETF Monthly Contribution file submitted to the ETF board).
"""

import logging
import os
from io import BytesIO

from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

try:
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
except Exception:  # pragma: no cover - Pillow may not be available
    XLImage = None
    AnchorMarker = None
    TwoCellAnchor = None

from horilla.decorators import (
    hx_request_required,
    login_required,
    permission_required,
)
from payroll.forms.component_forms import PayrollReportForm
from payroll.methods.methods import paginator_qry
from payroll.models.models import PayrollReport, Payslip

logger = logging.getLogger(__name__)


# Banner image embedded across the top of the generated ETF file.
# Drop the banner image (Create File button + Sampath Bank logo + title) here.
ETF_REPORT_BANNER_PATH = os.path.join(
    settings.BASE_DIR,
    "payroll",
    "static",
    "payroll",
    "images",
    "etf_banner.png",
)

# Fallback title (used only when the banner image above is not available).
ETF_REPORT_TITLE = "ETF Contribution File Generator"

# Hard coded ETF "Employer Number" (statutory employer registration number) per
# company. Keys are the company names exactly as stored on the Company record.
# Add an entry here for each company that submits ETF returns.
ETF_EMPLOYER_NUMBERS = {
    "Wireapps Pvt Ltd": "B 051114",
}
# Used when the employee's company is not present in the mapping above.
DEFAULT_ETF_EMPLOYER_NUMBER = "B 051114"


# ETF Monthly Contribution file column headers (matching the ETF submission format).
ETF_REPORT_HEADERS = [
    "NIC/Passport Number",
    "Surname",
    "Initials",
    "Member Number",
    "Total Contribution",
    "Employer Number",
    "Contribution From Period (YYYY/MM/DD)",
    "Contribution To Period (YYYY/MM/DD)",
]

# Column widths (Excel "character" units) for the 8 report columns.
ETF_COLUMN_WIDTHS = [22, 22, 14, 18, 18, 18, 22, 22]



def _excel_width_to_px(width):
    """Approximate pixel width of an Excel column (Calibri 11, 7px digit)."""
    return int(round(width * 7)) + 5


def _px_to_points(pixels):
    """Convert pixels to Excel points (96 DPI => 1px = 0.75pt)."""
    return pixels * 0.75


def _get_employer_number(employee, report):
    """
    Return the company-level ETF "Employer Number" for an employee.

    The value is hard coded per company (statutory employer registration number).
    Resolves the employee's own company first, then the report's company, and
    falls back to the default when the company is not in the mapping.
    """
    company = getattr(
        getattr(employee, "employee_work_info", None), "company_id", None
    )
    if company is None:
        company = report.company_id
    company_name = getattr(company, "company", None)
    if company_name and company_name in ETF_EMPLOYER_NUMBERS:
        return ETF_EMPLOYER_NUMBERS[company_name]
    return DEFAULT_ETF_EMPLOYER_NUMBER


@login_required
@permission_required("payroll.view_payslip")
def view_payroll_reports(request):
    """
    Render the payroll reports page listing all generated reports.
    """
    reports = PayrollReport.objects.all()
    reports = paginator_qry(reports, request.GET.get("page"))
    return render(
        request,
        "payroll/reports/view_reports.html",
        {"reports": reports},
    )


@login_required
@hx_request_required
@permission_required("payroll.view_payslip")
def filter_payroll_reports(request):
    """
    Return the reports list partial (used to refresh the table after changes).
    """
    reports = PayrollReport.objects.all()
    reports = paginator_qry(reports, request.GET.get("page"))
    return render(
        request,
        "payroll/reports/list_reports.html",
        {"reports": reports},
    )


@login_required
@permission_required("payroll.add_payslip")
def create_payroll_report(request):
    """
    Display the report creation form and create a new payroll report record.
    """
    form = PayrollReportForm()
    if request.method == "POST":
        form = PayrollReportForm(request.POST)
        if form.is_valid():
            report = PayrollReport()
            report.report_type = form.cleaned_data["report_type"]
            report.start_date = form.cleaned_data["start_date"]
            report.end_date = form.cleaned_data["end_date"]
            selected_company = request.session.get("selected_company")
            if selected_company and selected_company != "all":
                from base.models import Company

                report.company_id = Company.objects.filter(
                    id=selected_company
                ).first()
            report.save()
            messages.success(request, _("Report generated successfully."))
            return HttpResponse("<script>window.location.reload();</script>")
    return render(
        request,
        "payroll/reports/report_form.html",
        {"form": form},
    )


@login_required
@hx_request_required
@permission_required("payroll.delete_payslip")
def delete_payroll_report(request, report_id):
    """
    Delete a generated payroll report record.
    """
    report = PayrollReport.objects.filter(id=report_id).first()
    if report:
        report.delete()
        messages.success(request, _("Report deleted successfully."))
    else:
        messages.error(request, _("Report not found."))
    return redirect("filter-payroll-reports")


@login_required
@permission_required("payroll.view_payslip")
def download_payroll_report(request, report_id):
    """
    Generate and download the report's ``.xlsx`` file on demand.
    """
    report = get_object_or_404(PayrollReport, id=report_id)
    if report.report_type == PayrollReport.REPORT_ETF_MONTHLY:
        return _build_etf_monthly_contribution_file(report)
    messages.error(request, _("Unsupported report type."))
    return redirect("view-payroll-reports")


def _get_payslips_for_period(report):
    """
    Return the payslips that overlap the report period for the report company.
    """
    payslips = Payslip.objects.filter(
        Q(start_date__lte=report.end_date) & Q(end_date__gte=report.start_date)
    )
    if report.company_id:
        payslips = payslips.filter(
            employee_id__employee_work_info__company_id=report.company_id
        )
    return payslips.select_related(
        "employee_id",
        "employee_id__employee_work_info",
        "employee_id__employee_work_info__company_id",
    ).order_by("employee_id__employee_first_name")



def _get_banner_natural_size():
    """
    Return the banner image's natural ``(width, height)`` in pixels, or ``None``
    when no usable banner image is available.
    """
    if XLImage is None or not os.path.exists(ETF_REPORT_BANNER_PATH):
        if XLImage is None:
            logger.warning(
                "openpyxl image support unavailable (Pillow not installed)."
            )
        else:
            logger.info("ETF report banner not found at %s", ETF_REPORT_BANNER_PATH)
        return None
    try:
        banner = XLImage(ETF_REPORT_BANNER_PATH)
        if banner.width and banner.height:
            return (banner.width, banner.height)
    except Exception as error:  # pragma: no cover - defensive
        logger.warning("Could not read ETF report banner: %s", error)
    return None


def _add_banner(sheet, end_col_idx, end_row_idx):
    """
    Embed the ETF banner image (button + Sampath Bank logo + title) so that it
    spans exactly from cell A1 to the top-left corner of ``(end_col_idx,
    end_row_idx)`` (0-indexed). Using a two-cell anchor pins the image to the
    actual column/row boundaries, so it fits the table width precisely with no
    right-edge gap and no overlap.

    Returns ``True`` when the banner was added. The export must never break
    because of a missing/unreadable image, so any error is logged and ignored.
    """
    if XLImage is None or TwoCellAnchor is None or AnchorMarker is None:
        return False
    if not os.path.exists(ETF_REPORT_BANNER_PATH):
        return False
    try:
        banner = XLImage(ETF_REPORT_BANNER_PATH)
        marker_from = AnchorMarker(col=0, colOff=0, row=0, rowOff=0)
        marker_to = AnchorMarker(
            col=end_col_idx, colOff=0, row=end_row_idx, rowOff=0
        )
        banner.anchor = TwoCellAnchor(
            editAs="twoCell", _from=marker_from, to=marker_to
        )
        sheet.add_image(banner)
        return True
    except Exception as error:  # pragma: no cover - defensive
        logger.warning("Could not embed ETF report banner: %s", error)
        return False


def _build_etf_monthly_contribution_file(report):
    """
    Build the ETF Monthly Contribution ``.xlsx`` file for the given report and
    return it as a downloadable HTTP response.
    """
    period_label = report.start_date.strftime("%B %Y")
    # Period stored as YYYYMM (confirmed sample format e.g. 202603)
    from_period = report.start_date.strftime("%Y%m")
    to_period = report.end_date.strftime("%Y%m")

    workbook = Workbook()
    sheet = workbook.active
    # Excel sheet names are limited to 31 characters.
    sheet.title = period_label[:31]

    # ---- Styles ----
    header_fill = PatternFill(
        start_color="E8A56B", end_color="E8A56B", fill_type="solid"
    )
    header_font = Font(bold=True, color="000000")
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center")
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    total_columns = len(ETF_REPORT_HEADERS)

    # ---- Column widths (set first so the banner can match the table width) ----
    for col_index, width in enumerate(ETF_COLUMN_WIDTHS, start=1):
        sheet.column_dimensions[get_column_letter(col_index)].width = width
    table_width_px = sum(_excel_width_to_px(w) for w in ETF_COLUMN_WIDTHS)

    # ---- Banner area ----
    # Embed the full banner image (button + logo + title) so it spans exactly
    # columns A..H using a two-cell anchor (no right-edge gap, no overlap).
    banner_size = _get_banner_natural_size()

    if banner_size:
        natural_width, natural_height = banner_size
        ratio = natural_height / natural_width if natural_width else 0
        # Height the banner would need to keep its aspect ratio at table width.
        banner_height_px = table_width_px * ratio if ratio else 0
        banner_total_pt = _px_to_points(banner_height_px)
        banner_rows = max(1, int(round(banner_total_pt / 15))) if banner_total_pt else 6
        per_row_pt = (banner_total_pt / banner_rows) if banner_total_pt else 15
        for banner_row in range(1, banner_rows + 1):
            sheet.row_dimensions[banner_row].height = per_row_pt
        header_row = banner_rows + 1
        # Anchor: from top-left of A1 to top-left of the cell just past column H
        # at the header row -> bottom-right pinned to H's right edge & banner end.
        _add_banner(sheet, end_col_idx=total_columns, end_row_idx=banner_rows)
    else:
        # Fallback: render a styled text title when no banner image is present.
        title_font = Font(bold=True, size=26, name="Arial Black")
        sheet.merge_cells(
            start_row=1, start_column=1, end_row=2, end_column=total_columns
        )
        title_cell = sheet.cell(row=1, column=1)
        title_cell.value = ETF_REPORT_TITLE
        title_cell.font = title_font
        title_cell.alignment = center
        sheet.row_dimensions[1].height = 30
        sheet.row_dimensions[2].height = 30
        header_row = 4

    # ---- Header row ----
    for col_index, header in enumerate(ETF_REPORT_HEADERS, start=1):
        cell = sheet.cell(row=header_row, column=col_index)
        cell.value = header
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center
        cell.border = border
    sheet.row_dimensions[header_row].height = 45

    # ---- Data rows ----
    payslips = _get_payslips_for_period(report)
    row_index = header_row + 1
    for payslip in payslips:
        employee = payslip.employee_id
        nic_passport = employee.nic or employee.passport or ""
        total_contribution = float(payslip.employer_etf_amount or 0)

        values = [
            nic_passport,
            employee.employee_last_name or "",
            employee.initials or "",
            employee.etf_epf_number or "",
            round(total_contribution, 2),
            _get_employer_number(employee, report),
            from_period,
            to_period,
        ]
        for col_index, value in enumerate(values, start=1):
            cell = sheet.cell(row=row_index, column=col_index)
            cell.value = value
            cell.border = border
            if col_index == 5:
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(horizontal="right", vertical="center")
            else:
                cell.alignment = left
        row_index += 1


    # ---- Build response ----
    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    file_name = f"ETF Monthly Contribution - {period_label}.xlsx"
    response = HttpResponse(
        buffer.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
    response["Content-Disposition"] = f'attachment; filename="{file_name}"'
    return response

