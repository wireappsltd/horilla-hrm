"""
Constants for the retention engine.

Categories cover the four record groupings called out in the HR Data Retention
and Disposal Procedure V1 and the ISO 27001 user story:
  - general HR (employee profile, work info, documents)
  - attendance / leave
  - payroll register (payslips, contracts, deductions)
  - statutory tax (EPF, ETF, PAYE)
"""

from django.utils.translation import gettext_lazy as _


CATEGORY_GENERAL_HR = "general_hr"
CATEGORY_ATTENDANCE_LEAVE = "attendance_leave"
CATEGORY_PAYROLL = "payroll"
CATEGORY_TAX_EPF_ETF = "tax_epf_etf"

RETENTION_CATEGORIES = [
    (CATEGORY_GENERAL_HR, _("General HR (profile, work info, documents)")),
    (CATEGORY_ATTENDANCE_LEAVE, _("Attendance and Leave")),
    (CATEGORY_PAYROLL, _("Payroll register (payslips, contracts)")),
    (CATEGORY_TAX_EPF_ETF, _("Statutory tax (EPF / ETF / PAYE)")),
]


# Action lifecycle.
STATUS_PENDING_REVIEW = "pending_review"
STATUS_DEFERRED = "deferred"
STATUS_ANONYMIZED = "anonymized"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

ACTION_STATUSES = [
    (STATUS_PENDING_REVIEW, _("Pending review")),
    (STATUS_DEFERRED, _("Deferred")),
    (STATUS_ANONYMIZED, _("Anonymized")),
    (STATUS_FAILED, _("Failed")),
    (STATUS_CANCELLED, _("Cancelled")),
]


# Run log outcomes.
RUN_OUTCOME_SUCCESS = "success"
RUN_OUTCOME_PARTIAL = "partial"
RUN_OUTCOME_FAILED = "failed"

RUN_OUTCOMES = [
    (RUN_OUTCOME_SUCCESS, _("Success")),
    (RUN_OUTCOME_PARTIAL, _("Partial success")),
    (RUN_OUTCOME_FAILED, _("Failed")),
]


# Sentinel value written into anonymized PII fields. Avoids accidentally
# matching real-looking data while remaining recognisable to auditors.
ANONYMIZED_TEXT = "[ANONYMIZED]"
ANONYMIZED_EMAIL_DOMAIN = "anonymized.invalid"
