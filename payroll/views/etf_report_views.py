"""
payroll/views/etf_report_views.py

Views for generating the ETF Form II Return (Sri Lanka Employees' Trust Fund Board)
bi-annual statutory submission.

The workbook follows the official ETF format and contains three sheet types:
    1. Form II            - member contribution detail sheet (max 10 members per
                            sheet; additional members roll onto "Form II (2)", ...)
    2. Reconciliation sheet - Details of Payments + Summary of Return
    3. Summary sheet      - page totals by month with a grand total

Two distinct half-year periods are supported:
    * H1  -> January to June
    * H2  -> July to December
"""

from calendar import monthrange
from datetime import date

from django.utils.translation import gettext as _
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter

from base.models import Company
from payroll.models.models import Payslip

# Statutory ETF contribution rate (3% of gross earnings, paid by the employer).
ETF_RATE = 0.03
# Maximum number of member records allowed per Form II sheet/page.
RECORDS_PER_PAGE = 10

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
# Display labels used in the spreadsheet month headers.
MONTH_DISPLAY = [
    "Jan", "Feb", "March", "April", "May", "June",
    "July", "Aug", "Sept", "Oct", "Nov", "Dec",
]
MONTH_ABBR = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

PERIOD_CHOICES = {
    "H1": {"label": _("January - June"), "months": [1, 2, 3, 4, 5, 6]},
    "H2": {"label": _("July - December"), "months": [7, 8, 9, 10, 11, 12]},
}

# Statutory ETF employer details that are NOT stored on the Company model
# (employer registration number, telephone and fax). Provide per-company
# overrides keyed by the exact Company name; when a company is not listed the
# defaults below are used. ``name`` / ``address_lines`` overrides are optional
# and, when omitted, fall back to the Company record.
ETF_EMPLOYER_OVERRIDES = {
}
DEFAULT_ETF_REGISTRATION_NO = "51114/B"
DEFAULT_ETF_TELEPHONE = "0112818104 / 0777064068"
DEFAULT_ETF_FAX = ""
DEFAULT_ETF_EMPLOYER_NAME = "Wire Apps Pvt Ltd"
DEFAULT_ETF_ADDRESS_LINES = ["321/1, Colombo Road,", "Divulpitiya, Boralesgamuwa"]
# Bank/branch shown against each monthly payment on the reconciliation sheet.
DEFAULT_ETF_BANK = "Sampath Bank (Online)"



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_selected_company(request):
    """Return the Company instance for the currently selected company (or None)."""
    selected_company = request.session.get("selected_company")
    if selected_company and selected_company != "all":
        return Company.objects.filter(id=selected_company).first()
    return Company.objects.filter(hq=True).first() or Company.objects.first()


def _period_bounds(year, months):
    """Return (period_start, period_end) date objects for the given months list."""
    start_month = months[0]
    end_month = months[-1]
    period_start = date(year, start_month, 1)
    period_end = date(year, end_month, monthrange(year, end_month)[1])
    return period_start, period_end


def _split_rs_cts(value):
    """Split a monetary value into (rupees, cents) integers."""
    value = round(float(value or 0), 2)
    rs = int(value)
    cts = int(round((value - rs) * 100))
    if cts == 100:
        rs += 1
        cts = 0
    return rs, cts


def _employer_details(company, member_numbers):
    """
    Build the employer detail block. The statutory employer details
    (registration number, name, address, telephone, fax) are not stored on the
    Company model, so they come from per-company overrides when present and
    otherwise fall back to the hardcoded defaults. This keeps the footer of
    every ETF sheet consistent with the official form.
    """
    name = company.company if company else ""

    # Statutory employer details are hardcoded (not stored on the Company
    # model). Per-company overrides take precedence; otherwise the hardcoded
    # defaults below are always used so the form is fully populated.
    overrides = ETF_EMPLOYER_OVERRIDES.get(name, {}) if name else {}
    registration_no = (
        overrides.get("registration_no")
        or DEFAULT_ETF_REGISTRATION_NO
    )
    telephone = overrides.get("telephone") or DEFAULT_ETF_TELEPHONE
    fax = overrides.get("fax", DEFAULT_ETF_FAX)
    name = overrides.get("name") or DEFAULT_ETF_EMPLOYER_NAME
    address_lines = overrides.get("address_lines") or DEFAULT_ETF_ADDRESS_LINES

    return {
        "registration_no": registration_no,
        "name": name,
        "address_lines": address_lines,
        "address": ", ".join(address_lines) if address_lines else "-",
        "telephone": telephone or "-",
        "fax": fax or "-",
    }


def _is_active_in_period(employee, period_start, period_end):
    """
    An employee is included when they were active during the half-year period.
    Terminated employees are included for months they were active (i.e. up to
    their contract end date). The contribution lookup per month naturally
    excludes months without a payslip.
    """
    work_info = getattr(employee, "employee_work_info", None)
    join_date = getattr(work_info, "date_joining", None) if work_info else None
    end_date = getattr(work_info, "contract_end_date", None) if work_info else None

    if join_date and join_date > period_end:
        return False
    if end_date and end_date < period_start:
        return False
    return True


def _payslip_etf_values(payslip):
    """
    Return ``(earnings, contribution)`` for a payslip, matching the amounts
    shown on the payslip itself.

    The contribution is taken directly from the payslip's stored
    ``pay_head_data`` (the exact figure printed on the payslip), falling back
    to the ``employer_etf_amount`` model field and finally to the computed
    ``earnings x 3%``. Earnings are the ETF base, i.e. basic pay less loss of
    pay, so that ``earnings x 3%`` lines up with the contribution column.
    """
    pay_head_data = payslip.pay_head_data or {}
    loss_of_pay = float(pay_head_data.get("loss_of_pay") or 0)
    earnings = float(payslip.basic_pay or payslip.contract_wage or 0) - loss_of_pay
    try:
        contribution = float(pay_head_data.get("employer_etf_amount") or 0)
    except (TypeError, ValueError):
        contribution = 0
    if not contribution:
        contribution = float(payslip.employer_etf_amount or 0)
    if not contribution:
        # Fallback for legacy payslips generated before the ETF amount was
        # stored on the payslip.
        contribution = round(earnings * ETF_RATE, 2)
    return round(earnings, 2), round(contribution, 2)


def _build_report_context(request, year, period_key):
    """Thin request-based wrapper around :func:`build_etf_form_ii_context`."""
    company = _get_selected_company(request)
    return build_etf_form_ii_context(year, period_key, company)


def build_etf_form_ii_context(year, period_key, company=None):
    """
    Gather all the data required to render/export the ETF Form II return.

    ``company`` is a :class:`base.models.Company` instance (or ``None`` for all
    companies).

    Members are paginated into Form II pages of at most ``RECORDS_PER_PAGE``.
    The Summary sheet lists one row per page; Reconciliation aggregates all
    pages.
    """
    months = PERIOD_CHOICES[period_key]["months"]
    period_start, period_end = _period_bounds(year, months)

    payslips = Payslip.objects.filter(
        end_date__range=(period_start, period_end)
    ).select_related("employee_id", "employee_id__employee_work_info")

    if company is not None:
        payslips = payslips.filter(
            employee_id__employee_work_info__company_id=company
        )

    # Group payslips by employee and month index (0..5 within the half-year).
    employees_map = {}
    for payslip in payslips:
        employee = payslip.employee_id
        if not _is_active_in_period(employee, period_start, period_end):
            continue
        if payslip.end_date.month not in months:
            continue
        month_index = months.index(payslip.end_date.month)

        record = employees_map.setdefault(
            employee.id,
            {
                "employee": employee,
                "months": [{"earnings": 0.0, "contribution": 0.0} for _m in months],
            },
        )
        # Earnings/contribution are taken from the payslip itself so the
        # Form II figures always match the issued payslips (and the monthly
        # ETF contribution report).
        earnings, contribution = _payslip_etf_values(payslip)
        record["months"][month_index]["earnings"] += earnings
        record["months"][month_index]["contribution"] += contribution

    # Build the ordered list of member rows.
    rows = []
    for record in employees_map.values():
        employee = record["employee"]
        total_earnings = round(sum(m["earnings"] for m in record["months"]), 2)
        total_contribution = round(sum(m["contribution"] for m in record["months"]), 2)
        rows.append(
            {
                "member_no": employee.etf_epf_number or "",
                "name": employee.get_full_name(),
                "nic": employee.nic or "-",
                "months": record["months"],
                "total_earnings": total_earnings,
                "total_contribution": total_contribution,
            }
        )

    rows.sort(key=lambda r: (r["member_no"] or "", r["name"]))

    # Paginate members into Form II pages of RECORDS_PER_PAGE.
    chunks = [
        rows[i : i + RECORDS_PER_PAGE] for i in range(0, len(rows), RECORDS_PER_PAGE)
    ] or [[]]

    num_months = len(months)
    pages = []
    for page_no, page_rows in enumerate(chunks, start=1):
        earn_totals = [0.0] * num_months
        contrib_totals = [0.0] * num_months
        for row in page_rows:
            for idx, month in enumerate(row["months"]):
                earn_totals[idx] += month["earnings"]
                contrib_totals[idx] += month["contribution"]
        earn_totals = [round(v, 2) for v in earn_totals]
        contrib_totals = [round(v, 2) for v in contrib_totals]
        pages.append(
            {
                "page_no": page_no,
                "rows": page_rows,
                "start_serial": (page_no - 1) * RECORDS_PER_PAGE,
                "earn_totals": earn_totals,
                "contrib_totals": contrib_totals,
                "total_earnings": round(sum(earn_totals), 2),
                "total_contribution": round(sum(contrib_totals), 2),
            }
        )

    # Grand totals across all pages.
    grand_earn = [0.0] * num_months
    grand_contrib = [0.0] * num_months
    for page in pages:
        for idx in range(num_months):
            grand_earn[idx] += page["earn_totals"][idx]
            grand_contrib[idx] += page["contrib_totals"][idx]
    grand_earn = [round(v, 2) for v in grand_earn]
    grand_contrib = [round(v, 2) for v in grand_contrib]
    grand = {
        "earn_totals": grand_earn,
        "contrib_totals": grand_contrib,
        "total_earnings": round(sum(grand_earn), 2),
        "total_contribution": round(sum(grand_contrib), 2),
    }

    # Reconciliation (Details of Payments): payable vs remitted per month.
    reconciliation = []
    for idx, month in enumerate(months):
        payable = grand_contrib[idx]
        reconciliation.append(
            {
                "month_label": MONTH_DISPLAY[month - 1],
                "payable": payable,
                "remitted": payable,
                "over_under": 0.0,
            }
        )

    member_numbers = [r["member_no"] for r in rows]
    employer = _employer_details(company, member_numbers)

    last_month = months[-1]
    half_period_label = f"{MONTH_ABBR[last_month - 1]}-{year % 100:02d}"
    month_display = [MONTH_DISPLAY[m - 1] for m in months]

    return {
        "year": year,
        "period_key": period_key,
        "period_label": PERIOD_CHOICES[period_key]["label"],
        "half_period_label": half_period_label,
        "month_display": month_display,
        "pages": pages,
        "grand": grand,
        "reconciliation": reconciliation,
        "reconciliation_total": {
            "payable": grand["total_contribution"],
            "remitted": grand["total_contribution"],
            "over_under": 0.0,
        },
        "no_of_members": len(rows),
        "no_of_pages": len(pages),
        "total_contribution": grand["total_contribution"],
        "employer": employer,
        "company": company,
        "generated_on": date.today(),
        "etf_rate": ETF_RATE,
    }


# ---------------------------------------------------------------------------
# XLSX styling helpers
# ---------------------------------------------------------------------------
THIN = Side(style="thin", color="000000")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
RIGHT = Alignment(horizontal="right", vertical="center", wrap_text=True)
RS_FMT = "#,##0"
CTS_FMT = "0"
AMT_FMT = "#,##0.00"


def _cell(
    ws,
    row,
    col,
    value=None,
    *,
    bold=False,
    align="center",
    border=True,
    number_format=None,
    size=None,
    wrap=True,
):
    cell = ws.cell(row=row, column=col, value=value)
    font_kwargs = {}
    if bold:
        font_kwargs["bold"] = True
    if size:
        font_kwargs["size"] = size
    if font_kwargs:
        cell.font = Font(**font_kwargs)
    if align == "left":
        cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=wrap)
    elif align == "right":
        cell.alignment = Alignment(horizontal="right", vertical="center", wrap_text=wrap)
    else:
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=wrap)
    if border:
        cell.border = BORDER
    if number_format:
        cell.number_format = number_format
    return cell


def _border_range(ws, r1, c1, r2, c2):
    """Apply a thin border to every cell in the given range."""
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            ws.cell(row=r, column=c).border = BORDER


def _merge(ws, r1, c1, r2, c2):
    ws.merge_cells(start_row=r1, start_column=c1, end_row=r2, end_column=c2)


# ---------------------------------------------------------------------------
# Form II sheet
# ---------------------------------------------------------------------------
def _build_form_ii_sheet(wb, context, page, sheet_name):
    months = context["month_display"]
    num_months = len(months)
    last_col = 4 + num_months * 2  # P (16) for 6 months
    ws = wb.create_sheet(title=sheet_name)

    hdr = 4

    # Title band (rows 1-3, no borders).
    _cell(ws, 1, 1, "EMPLOYEES' TRUST FUND BOARD", bold=True, size=16, border=False, wrap=False)
    _merge(ws, 1, 1, 1, last_col)
    _cell(ws, 2, 1, "FORM II RETURN", bold=True, size=12, border=False, wrap=False)
    _merge(ws, 2, 1, 2, last_col)
    half_end = (
        f"30TH JUNE {context['year']}"
        if context["period_key"] == "H1"
        else f"31ST DECEMBER {context['year']}"
    )
    _cell(
        ws,
        3,
        1,
        f"RETURN FOR THE HALF - YEAR ENDING  {half_end}",
        bold=True,
        align="left",
        border=False,
        wrap=False,
    )
    _cell(ws, 3, 14, "Page No", bold=True, align="left", border=False, wrap=False)
    _cell(ws, 3, last_col, page["page_no"], bold=True, border=False)

    # Column-number row (row 4).
    for idx, label in enumerate(["1", "2", "3", "4"], start=1):
        _cell(ws, hdr, idx, label, bold=True)
    _cell(ws, hdr, 5, "5. TOTAL GROSS WAGES AND CONTRIBUTION", bold=True)
    _merge(ws, hdr, 5, hdr, last_col)

    # Fixed columns (rows 5-7 merged vertically).
    _cell(ws, hdr + 1, 1, "NAME OF MEMBER\n(surname first followed by initial)", bold=True)
    _merge(ws, hdr + 1, 1, hdr + 3, 1)
    _cell(ws, hdr + 1, 2, "MEMBER'S NUMBER", bold=True)
    _merge(ws, hdr + 1, 2, hdr + 3, 2)
    _cell(ws, hdr + 1, 3, "NATIONAL IDENTITY CARD NO", bold=True)
    _merge(ws, hdr + 1, 3, hdr + 3, 3)
    _cell(ws, hdr + 1, 4, "TOTAL", bold=True)
    _cell(ws, hdr + 2, 4, "CONTRIBUTIONS", bold=True)
    _cell(ws, hdr + 3, 4, "Rs.", bold=True)

    # Month group headers.
    for i in range(num_months):
        earn_col = 5 + i * 2
        con_col = earn_col + 1
        _cell(ws, hdr + 1, earn_col, months[i], bold=True)
        _merge(ws, hdr + 1, earn_col, hdr + 1, con_col)
        _cell(ws, hdr + 2, earn_col, "TOTAL EARNINGS", bold=True)
        _cell(ws, hdr + 2, con_col, "CONTRIBUTIONS", bold=True)
        _cell(ws, hdr + 3, earn_col, "Rs.", bold=True)
        _cell(ws, hdr + 3, con_col, "Rs.", bold=True)

    # Data rows.
    data_start = hdr + 4
    for offset, row in enumerate(page["rows"]):
        r = data_start + offset
        serial = page["start_serial"] + offset + 1
        _cell(ws, r, 1, row["name"], align="left")
        _cell(ws, r, 2, row["member_no"] or serial)
        _cell(ws, r, 3, row["nic"])
        _cell(ws, r, 4, row["total_contribution"], number_format=AMT_FMT, align="right")
        for i, month in enumerate(row["months"]):
            earn_col = 5 + i * 2
            _cell(ws, r, earn_col, round(month["earnings"], 2), number_format=AMT_FMT, align="right")
            _cell(ws, r, earn_col + 1, round(month["contribution"], 2), number_format=AMT_FMT, align="right")

    # Totals row (page total).
    total_row = data_start + len(page["rows"])
    _cell(ws, total_row, 1, "", border=True)
    _cell(ws, total_row, 2, "", border=True)
    _cell(ws, total_row, 3, "", border=True)
    _cell(ws, total_row, 4, page["total_contribution"], bold=True, number_format=AMT_FMT, align="right")
    for i in range(num_months):
        earn_col = 5 + i * 2
        _cell(ws, total_row, earn_col, page["earn_totals"][i], bold=True, number_format=AMT_FMT, align="right")
        _cell(ws, total_row, earn_col + 1, page["contrib_totals"][i], bold=True, number_format=AMT_FMT, align="right")

    _border_range(ws, hdr, 1, total_row, last_col)

    # ---- Notes & employer block (no borders, fixed offsets from totals row) ----
    employer = context["employer"]
    addr = employer["address_lines"]
    addr1 = addr[0] if len(addr) > 0 else ""
    addr2 = ", ".join(addr[1:]) if len(addr) > 1 else ""

    _cell(ws, total_row + 1, 1, "PAGE TOTAL TO BE TAKEN TO THE SUMMARY SHEET",
          bold=True, align="left", border=False, wrap=False)

    _cell(ws, total_row + 3, 1, "EMPLOYER'S REGISTRATION NO",
          bold=True, align="left", border=False, wrap=False)
    _cell(ws, total_row + 3, 4, employer["registration_no"],
          bold=True, align="left", border=False, wrap=False)

    _cell(ws, total_row + 5, 1, "NAME & ADDRESS OF EMPLOYER",
          bold=True, align="left", border=False, wrap=False)
    _cell(ws, total_row + 5, 4, employer["name"], bold=True, align="left", border=False, wrap=False)
    _cell(ws, total_row + 6, 4, addr1, bold=True, align="left", border=False, wrap=False)
    _cell(ws, total_row + 7, 4, addr2, bold=True, align="left", border=False, wrap=False)

    _cell(ws, total_row + 8, 1, "TELEPHONE NO", bold=True, align="left", border=False, wrap=False)
    _cell(ws, total_row + 8, 4, employer["telephone"], bold=True, align="left", border=False, wrap=False)

    _cell(ws, total_row + 10, 1, "FAX NO", bold=True, align="left", border=False, wrap=False)
    _cell(ws, total_row + 10, 4, "." * 40, align="left", border=False, wrap=False)

    # Right-side certification text.
    _cell(ws, total_row + 10, 9,
          "I Certify that all the particulars given above are correct and that no part of",
          align="left", border=False, wrap=False)
    _cell(ws, total_row + 11, 9,
          "the contributions that should be paid by us has been deducted from any",
          align="left", border=False, wrap=False)
    _cell(ws, total_row + 12, 9, "employees' earnings.", align="left", border=False, wrap=False)

    # Destination block (left).
    _cell(ws, total_row + 12, 1, "Duly completed returns should be Sent to :-",
          bold=True, align="left", border=False, wrap=False)
    for offset, text in enumerate(
        [
            "Manager - Memebr Accounts",
            "Employees' Trust Fund Board",
            "P.O. Box 807, 1st Floor",
            "Labour Secretariat, Colombo 05",
            "011-2369596",
        ],
        start=13,
    ):
        _cell(ws, total_row + offset, 1, text, align="left", border=False, wrap=False)

    # Signature lines (right).
    _cell(ws, total_row + 14, 9, "." * 28, align="left", border=False, wrap=False)
    _cell(ws, total_row + 14, 13, "." * 55, align="left", border=False, wrap=False)
    _cell(ws, total_row + 15, 9, "Date", bold=True, align="left", border=False, wrap=False)
    _cell(ws, total_row + 15, 13, "Signature of Employer and Rubber Stamp",
          bold=True, align="left", border=False, wrap=False)
    _cell(ws, total_row + 17, 9, "* Please notice that this is a specimen of Form II.",
          bold=True, align="left", border=False, wrap=False)

    # Column widths.
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 12
    ws.column_dimensions["C"].width = 16
    for c in range(4, last_col + 1):
        ws.column_dimensions[get_column_letter(c)].width = 12

    # Header row heights (allow wrapped header text to display fully).
    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 18
    ws.row_dimensions[3].height = 16
    ws.row_dimensions[hdr].height = 16
    ws.row_dimensions[hdr + 1].height = 16
    ws.row_dimensions[hdr + 2].height = 30
    ws.row_dimensions[hdr + 3].height = 14


# ---------------------------------------------------------------------------
# Reconciliation sheet
# ---------------------------------------------------------------------------
def _build_reconciliation_sheet(wb, context):
    ws = wb.create_sheet(title="Reconciliation sheet")
    last_col = 12  # A..L

    # Title band (rows 1-3, no borders).
    half_end = (
        f"30TH JUNE {context['year']}"
        if context["period_key"] == "H1"
        else f"31ST DECEMBER {context['year']}"
    )
    _cell(ws, 1, 1, "EMPLOYEES' TRUST FUND BOARD", bold=True, size=16, border=False, wrap=False)
    _merge(ws, 1, 1, 1, last_col)
    _cell(ws, 2, 1, f"RETURN FOR THE HALF - YEAR ENDING  {half_end}", bold=True, size=12, border=False, wrap=False)
    _merge(ws, 2, 1, 2, last_col)
    _cell(ws, 3, 1, "CONTRIBUTION'S RECONCILIATION STATEMENT", bold=True, size=12, border=False, wrap=False)
    _merge(ws, 3, 1, 3, last_col)

    _cell(ws, 5, 1, "01.", bold=True, align="left", border=False)
    _cell(ws, 5, 2, "DETAILS OF PAYMENTS", bold=True, align="left", border=False)

    # Header rows 6-7.
    _cell(ws, 6, 1, "Month", bold=True)
    _merge(ws, 6, 1, 7, 1)
    _cell(ws, 6, 2, "Total Monthly Contributions payble as per Form II return", bold=True)
    _merge(ws, 6, 2, 6, 3)
    _cell(ws, 6, 4, "Amount remitted monthly", bold=True)
    _merge(ws, 6, 4, 6, 5)
    _cell(ws, 6, 6, "Over/Under", bold=True)
    _merge(ws, 6, 6, 6, 7)
    _cell(ws, 6, 8, "Cheque No", bold=True)
    _merge(ws, 6, 8, 7, 8)
    _cell(ws, 6, 9, "Cheque Amount", bold=True)
    _merge(ws, 6, 9, 6, 10)
    _cell(ws, 6, 11, "Name & branch of Bank where payment was made (If paid by cash)", bold=True)
    _merge(ws, 6, 11, 7, 11)
    _cell(ws, 6, 12, "Date of Payment", bold=True)
    _merge(ws, 6, 12, 7, 12)

    for col in (2, 4, 6, 9):
        _cell(ws, 7, col, "Rs", bold=True)
        _cell(ws, 7, col + 1, "Cts", bold=True)

    # Month rows.
    r = 8
    for item in context["reconciliation"]:
        p_rs, p_cts = _split_rs_cts(item["payable"])
        m_rs, m_cts = _split_rs_cts(item["remitted"])
        _cell(ws, r, 1, item["month_label"], align="left")
        _cell(ws, r, 2, p_rs, number_format=RS_FMT, align="right")
        _cell(ws, r, 3, p_cts, number_format=CTS_FMT)
        _cell(ws, r, 4, m_rs, number_format=RS_FMT, align="right")
        _cell(ws, r, 5, m_cts, number_format=CTS_FMT)
        for c in range(6, last_col + 1):
            _cell(ws, r, c, "")
        _cell(ws, r, 11, DEFAULT_ETF_BANK, align="left")
        r += 1

    # Total row.
    t_rs, t_cts = _split_rs_cts(context["reconciliation_total"]["payable"])
    _cell(ws, r, 1, "Total", bold=True, align="left")
    _cell(ws, r, 2, t_rs, bold=True, number_format=RS_FMT, align="right")
    _cell(ws, r, 3, t_cts, bold=True, number_format=CTS_FMT)
    _cell(ws, r, 4, t_rs, bold=True, number_format=RS_FMT, align="right")
    _cell(ws, r, 5, t_cts, bold=True, number_format=CTS_FMT)
    for c in range(6, last_col + 1):
        _cell(ws, r, c, "")
    _border_range(ws, 6, 1, r, last_col)

    # 02. SUMMARY OF RETURN.
    employer = context["employer"]
    r += 2
    _cell(ws, r, 1, "02.", bold=True, align="left", border=False)
    _cell(ws, r, 2, "SUMMARY OF RETURN", bold=True, align="left", border=False)
    summary_rows = [
        ("Employer's Registration No", employer["registration_no"]),
        ("Half Year / Period", context["half_period_label"]),
        ("No of Members", context["no_of_members"]),
        ("Total Contribution of six Months", f"{context['total_contribution']:,.2f}"),
        ("No of Pages", context["no_of_pages"]),
    ]
    start = r + 1
    for offset, (label, value) in enumerate(summary_rows):
        rr = start + offset
        _cell(ws, rr, 1, label, bold=True, align="left")
        _merge(ws, rr, 1, rr, 3)
        _cell(ws, rr, 4, value, align="left")
        _merge(ws, rr, 4, rr, 5)
    _border_range(ws, start, 1, start + len(summary_rows) - 1, 5)

    # Employer footer (fixed layout, matching the form).
    base = start + len(summary_rows) + 1
    addr = employer["address_lines"]
    addr1 = addr[0] if len(addr) > 0 else ""
    addr2 = ", ".join(addr[1:]) if len(addr) > 1 else ""

    _cell(ws, base, 1, "EPF/PPF No", bold=True, align="left", border=False, wrap=False)
    _cell(ws, base, 3, employer["registration_no"], align="left", border=False, wrap=False)

    _cell(ws, base + 1, 1, "Name & Address of Employer", bold=True, align="left", border=False, wrap=False)
    _cell(ws, base + 1, 3, employer["name"], bold=True, align="left", border=False, wrap=False)
    _cell(ws, base + 1, 9, "I certify that all the particulars given above are true &",
          align="left", border=False, wrap=False)

    _cell(ws, base + 2, 3, addr1, bold=True, align="left", border=False, wrap=False)
    _cell(ws, base + 2, 9, "correct", align="left", border=False, wrap=False)

    _cell(ws, base + 3, 3, addr2, bold=True, align="left", border=False, wrap=False)

    _cell(ws, base + 4, 9, "." * 55, align="left", border=False, wrap=False)

    _cell(ws, base + 5, 1, "Te. No", bold=True, align="left", border=False, wrap=False)
    _cell(ws, base + 5, 3, employer["telephone"], align="left", border=False, wrap=False)
    _cell(ws, base + 5, 9, "Signature of Employer and Official Seal", align="left", border=False, wrap=False)

    _cell(ws, base + 6, 1, "Fax No", bold=True, align="left", border=False, wrap=False)
    _cell(ws, base + 6, 3, ":" + "." * 40, align="left", border=False, wrap=False)
    _cell(ws, base + 6, 9, "Date : " + "." * 20, align="left", border=False, wrap=False)

    widths = {
        "A": 12, "B": 14, "C": 8, "D": 14, "E": 8, "F": 8, "G": 8,
        "H": 10, "I": 12, "J": 8, "K": 26, "L": 14,
    }
    for col, width in widths.items():
        ws.column_dimensions[col].width = width

    # Header row heights for wrapped header text.
    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 18
    ws.row_dimensions[3].height = 18
    ws.row_dimensions[6].height = 60
    ws.row_dimensions[7].height = 15


# ---------------------------------------------------------------------------
# Summary sheet
# ---------------------------------------------------------------------------
def _build_summary_sheet(wb, context):
    ws = wb.create_sheet(title="Summary sheet")
    months = context["month_display"]
    num_months = len(months)
    # A | (Page Total Rs,Cts) | per-month (Rs,Cts)
    last_col = 1 + 2 + num_months * 2  # 15 -> O for 6 months

    # Title band (rows 1-3, no borders), matching the other ETF sheets.
    half_end = (
        f"30TH JUNE {context['year']}"
        if context["period_key"] == "H1"
        else f"31ST DECEMBER {context['year']}"
    )
    _cell(ws, 1, 1, "EMPLOYEES' TRUST FUND BOARD", bold=True, size=16, border=False, wrap=False)
    _merge(ws, 1, 1, 1, last_col)
    _cell(
        ws,
        2,
        1,
        f"RETURN FOR THE HALF - YEAR ENDING  {half_end}",
        bold=True,
        size=12,
        border=False,
        wrap=False,
    )
    _merge(ws, 2, 1, 2, last_col)
    _cell(ws, 3, 1, "SUMMARY", bold=True, size=14, border=False)
    _merge(ws, 3, 1, 3, last_col)

    # Header rows 5-6.
    _cell(ws, 5, 1, "Page NO", bold=True)
    _merge(ws, 5, 1, 6, 1)
    _cell(ws, 5, 2, "Page Total", bold=True)
    _merge(ws, 5, 2, 5, 3)
    for i in range(num_months):
        rs_col = 4 + i * 2
        _cell(ws, 5, rs_col, months[i], bold=True)
        _merge(ws, 5, rs_col, 5, rs_col + 1)
    # Rs./Cts. sub-headers.
    for col in [2] + [4 + i * 2 for i in range(num_months)]:
        _cell(ws, 6, col, "Rs.", bold=True)
        _cell(ws, 6, col + 1, "Cts.", bold=True)

    # Page rows.
    r = 7
    for page in context["pages"]:
        _cell(ws, r, 1, page["page_no"])
        pt_rs, pt_cts = _split_rs_cts(page["total_contribution"])
        _cell(ws, r, 2, pt_rs, number_format=RS_FMT, align="right")
        _cell(ws, r, 3, pt_cts, number_format=CTS_FMT)
        for i in range(num_months):
            rs_col = 4 + i * 2
            m_rs, m_cts = _split_rs_cts(page["contrib_totals"][i])
            _cell(ws, r, rs_col, m_rs, number_format=RS_FMT, align="right")
            _cell(ws, r, rs_col + 1, m_cts, number_format=CTS_FMT)
        r += 1

    # Pad to keep a tidy grid (mirror the printed form's blank rows 7-16).
    min_rows = 10
    filled = len(context["pages"])
    for _pad in range(filled, min_rows):
        for c in range(1, last_col + 1):
            _cell(ws, r, c, "")
        r += 1

    # Grand Total row.
    g_rs, g_cts = _split_rs_cts(context["grand"]["total_contribution"])
    _cell(ws, r, 1, "Grand Total", bold=True)
    _cell(ws, r, 2, g_rs, bold=True, number_format=RS_FMT, align="right")
    _cell(ws, r, 3, g_cts, bold=True, number_format=CTS_FMT)
    for i in range(num_months):
        rs_col = 4 + i * 2
        m_rs, m_cts = _split_rs_cts(context["grand"]["contrib_totals"][i])
        _cell(ws, r, rs_col, m_rs, bold=True, number_format=RS_FMT, align="right")
        _cell(ws, r, rs_col + 1, m_cts, bold=True, number_format=CTS_FMT)
    _border_range(ws, 5, 1, r, last_col)

    # Employer footer.
    employer = context["employer"]
    r += 2
    _cell(ws, r, 1, "Employer's Registration No", bold=True, align="left", border=False)
    _cell(ws, r, 4, employer["registration_no"], align="left", border=False)
    r += 1
    _cell(ws, r, 1, "Name & Address of Employer", bold=True, align="left", border=False)
    _cell(ws, r, 4, employer["name"], bold=True, align="left", border=False)
    for line in employer["address_lines"]:
        r += 1
        _cell(ws, r, 4, line, bold=True, align="left", border=False)
    # Dotted signature line above the signature label.
    r += 1
    _cell(ws, r, 9, "." * 55, align="left", border=False, wrap=False)
    # Te. No row carries the signature label.
    r += 1
    _cell(ws, r, 1, "Te. No", bold=True, align="left", border=False, wrap=False)
    _cell(ws, r, 4, employer["telephone"], align="left", border=False, wrap=False)
    _cell(ws, r, 9, "Signature of Employer and Official Seal", align="left", border=False, wrap=False)
    # Fax No row carries the date label.
    r += 1
    _cell(ws, r, 1, "Fax No", bold=True, align="left", border=False, wrap=False)
    _cell(ws, r, 4, ":" + "." * 40, align="left", border=False, wrap=False)
    _cell(ws, r, 9, "Date : " + "." * 20, align="left", border=False, wrap=False)

    # Page NO column, then alternating Rs. (wide) / Cts. (narrow) columns to
    # mirror the printed SUMMARY form: col 2 = Rs, col 3 = Cts, col 4 = Rs, ...
    ws.column_dimensions["A"].width = 10
    for c in range(2, last_col + 1):
        # Even columns (2, 4, 6, ...) hold Rs. values; odd columns hold Cts.
        width = 12 if c % 2 == 0 else 7
        ws.column_dimensions[get_column_letter(c)].width = width

    # Header row heights.
    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 18
    ws.row_dimensions[3].height = 20
    ws.row_dimensions[5].height = 18
    ws.row_dimensions[6].height = 15


def _build_workbook(context):
    wb = Workbook()
    wb.remove(wb.active)  # remove default empty sheet
    for page in context["pages"]:
        name = "Form II" if page["page_no"] == 1 else f"Form II ({page['page_no']})"
        _build_form_ii_sheet(wb, context, page, name)
    _build_reconciliation_sheet(wb, context)
    _build_summary_sheet(wb, context)
    return wb



