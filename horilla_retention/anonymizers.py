"""
Category-specific anonymization routines.

Design rules:
  - Each routine is IDEMPOTENT — running it twice must be a no-op.
  - We NEVER hard-delete an Employee row. Payroll/tax history may have a
    longer retention period than profile data, so the Employee row must
    survive (with PII blanked) until ALL categories have aged out. Once
    the final category anonymizes, the row remains as an empty husk linked
    to surviving payslip aggregates (which are the legal record).
  - For attendance/leave we DO hard-delete rows because those tables contain
    no statutory record obligation; the payroll register summarises what
    legal requires.
  - For payroll/tax categories we clear free-text PII fields on Payslip but
    keep amounts.

Return value of each routine: int (count of underlying rows touched).
"""

from typing import Tuple

from django.db import transaction

from employee.models import Employee

from horilla_retention.constants import (
    ANONYMIZED_EMAIL_DOMAIN,
    ANONYMIZED_TEXT,
    CATEGORY_ATTENDANCE_LEAVE,
    CATEGORY_GENERAL_HR,
    CATEGORY_PAYROLL,
    CATEGORY_TAX_EPF_ETF,
)


def _placeholder_email(employee_id: int) -> str:
    return f"anon-{employee_id}@{ANONYMIZED_EMAIL_DOMAIN}"


def _is_already_anonymized_employee(employee: Employee) -> bool:
    return (
        employee.employee_first_name == ANONYMIZED_TEXT
        and (employee.email or "").endswith("@" + ANONYMIZED_EMAIL_DOMAIN)
    )


@transaction.atomic
def anonymize_general_hr(employee: Employee) -> int:
    """
    Blank PII on Employee, EmployeeWorkInformation (free-text fields),
    EmployeeBankDetails, and delete personal document attachments.
    """
    count = 0

    if not _is_already_anonymized_employee(employee):
        employee.employee_first_name = ANONYMIZED_TEXT
        employee.employee_last_name = ANONYMIZED_TEXT
        employee.email = _placeholder_email(employee.id)
        employee.phone = ""
        employee.nic = None
        employee.passport = None
        employee.address = None
        employee.country = None
        employee.state = None
        employee.city = None
        employee.zip = None
        employee.dob = None
        employee.emergency_contact = None
        employee.emergency_contact_name = None
        employee.emergency_contact_relation = None
        employee.qualification = None
        employee.additional_info = None
        if employee.employee_profile:
            try:
                employee.employee_profile.delete(save=False)
            except Exception:
                pass
            employee.employee_profile = None
        employee.is_active = False
        employee.save()
        count += 1

    # EmployeeWorkInformation: keep org structure (company/department/job
    # position) for HR analytics, blank PII-ish fields.
    work_info = getattr(employee, "employee_work_info", None)
    if work_info is not None:
        dirty = False
        if work_info.email:
            work_info.email = None
            dirty = True
        if work_info.mobile:
            work_info.mobile = None
            dirty = True
        if work_info.location:
            work_info.location = None
            dirty = True
        if work_info.additional_info:
            work_info.additional_info = None
            dirty = True
        if dirty:
            work_info.skip_history = True
            work_info.save()
            count += 1

    # EmployeeBankDetails: blank financial PII.
    bank = getattr(employee, "employee_bank_details", None)
    if bank is not None:
        bank.bank_name = ANONYMIZED_TEXT
        bank.account_number = None
        bank.branch = None
        bank.swift_code = None
        bank.address = None
        bank.country = None
        bank.state = ""
        bank.city = ""
        bank.any_other_code1 = None
        bank.any_other_code2 = None
        bank.additional_info = None
        bank.save()
        count += 1

    # Personal documents (resume, NIC scan, etc.).
    try:
        from horilla_documents.models import Document

        for doc in Document.objects.filter(employee_id=employee):
            try:
                if doc.document:
                    doc.document.delete(save=False)
            except Exception:
                pass
            doc.delete()
            count += 1
    except Exception:
        pass

    return count


@transaction.atomic
def anonymize_attendance_leave(employee: Employee) -> int:
    """
    Hard-delete attendance and leave history for this employee. Aggregate
    payroll figures already capture what statutory law requires.
    """
    count = 0

    try:
        from attendance.models import (
            Attendance,
            AttendanceActivity,
            AttendanceLateComeEarlyOut,
            AttendanceOverTime,
        )

        count += AttendanceActivity.objects.filter(employee_id=employee).delete()[0]
        count += Attendance.objects.filter(employee_id=employee).delete()[0]
        count += AttendanceLateComeEarlyOut.objects.filter(
            employee_id=employee
        ).delete()[0]
        count += AttendanceOverTime.objects.filter(employee_id=employee).delete()[0]
    except Exception:
        pass

    try:
        from leave.models import AvailableLeave, LeaveAllocationRequest, LeaveRequest

        count += LeaveRequest.objects.filter(employee_id=employee).delete()[0]
        count += LeaveAllocationRequest.objects.filter(employee_id=employee).delete()[0]
        count += AvailableLeave.objects.filter(employee_id=employee).delete()[0]
    except Exception:
        pass

    return count


@transaction.atomic
def anonymize_payroll(employee: Employee) -> int:
    """
    Clear free-text PII on payroll registers; KEEP amounts and statutory
    figures. Payslip rows survive as the legal payroll register.
    """
    count = 0

    try:
        from payroll.models.models import Contract, Payslip

        for payslip in Payslip.objects.filter(employee_id=employee):
            dirty = False
            if payslip.pay_head_data:
                payslip.pay_head_data = {
                    k: v
                    for k, v in (payslip.pay_head_data or {}).items()
                    if isinstance(k, str) and not k.lower().startswith("note")
                }
                dirty = True
            if dirty:
                payslip.save()
                count += 1

        for contract in Contract.objects.filter(employee_id=employee):
            if contract.contract_document:
                try:
                    contract.contract_document.delete(save=False)
                except Exception:
                    pass
                contract.contract_document = None
                contract.save()
                count += 1
    except Exception:
        pass

    return count


@transaction.atomic
def anonymize_tax_epf_etf(employee: Employee) -> int:
    """
    Statutory tax retention is the LONGEST (5-6y EPF/ETF, 5y PAYE), so this
    runs after payroll has aged out. At this point we can clear remaining
    detail JSON on payslips while keeping the headline EPF/ETF/PAYE numbers
    required for tax audit.
    """
    count = 0

    try:
        from payroll.models.models import Payslip

        for payslip in Payslip.objects.filter(employee_id=employee):
            dirty = False
            if payslip.pay_head_data:
                payslip.pay_head_data = {}
                dirty = True
            if dirty:
                payslip.save()
                count += 1
    except Exception:
        pass

    return count


# Public dispatch table.
ANONYMIZERS = {
    CATEGORY_GENERAL_HR: anonymize_general_hr,
    CATEGORY_ATTENDANCE_LEAVE: anonymize_attendance_leave,
    CATEGORY_PAYROLL: anonymize_payroll,
    CATEGORY_TAX_EPF_ETF: anonymize_tax_epf_etf,
}


def run_anonymizer(category: str, employee: Employee) -> Tuple[int, str]:
    """Returns (records_affected, error_message). Never raises."""
    fn = ANONYMIZERS.get(category)
    if fn is None:
        return 0, f"No anonymizer registered for category '{category}'"
    try:
        return fn(employee), ""
    except Exception as exc:  # pragma: no cover - defensive
        return 0, str(exc)
