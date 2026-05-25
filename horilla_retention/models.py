"""
Retention models.

  - DataRetentionPolicy : per-company, per-category configuration
  - RetentionAction     : one row per (employee, category) once a retention
                          period elapses; tracks the flag-and-anonymize lifecycle
  - RetentionRunLog     : engine run history (visible to IT Admin)
  - RetentionAuditEntry : immutable audit trail of every retention-related action
                          (ISO 27001 A.12 evidence)
"""

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from base.models import Company
from employee.models import Employee
from horilla.models import HorillaModel

from horilla_retention.constants import (
    ACTION_STATUSES,
    RETENTION_CATEGORIES,
    RUN_OUTCOMES,
    STATUS_PENDING_REVIEW,
)


class DataRetentionPolicy(HorillaModel):
    """
    Retention configuration for one (company, category) pair.

    `retention_years` counts from the employee's `last_working_date`. When that
    period elapses, the engine creates a RetentionAction (status=pending_review).
    If no admin acts within `grace_period_days`, the engine auto-anonymizes.
    """

    company_id = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="retention_policies",
        verbose_name=_("Company"),
    )
    category = models.CharField(
        max_length=32,
        choices=RETENTION_CATEGORIES,
        verbose_name=_("Record category"),
    )
    retention_years = models.PositiveSmallIntegerField(
        verbose_name=_("Retention period (years)"),
        help_text=_(
            "Years after the employee's last working date for which records of "
            "this category must be kept."
        ),
    )
    grace_period_days = models.PositiveSmallIntegerField(
        default=30,
        verbose_name=_("Grace period (days)"),
        help_text=_(
            "Days after a record is flagged before the engine will auto-anonymize "
            "if no admin action is taken."
        ),
    )
    is_enabled = models.BooleanField(
        default=True,
        verbose_name=_("Enabled"),
        help_text=_("If unchecked, the engine ignores this policy."),
    )
    notes = models.TextField(
        blank=True,
        null=True,
        verbose_name=_("Notes"),
        help_text=_("Legal basis or audit reference."),
    )

    class Meta:
        verbose_name = _("Data Retention Policy")
        verbose_name_plural = _("Data Retention Policies")
        unique_together = (("company_id", "category"),)
        ordering = ("company_id", "category")

    def __str__(self):
        return f"{self.company_id} — {self.get_category_display()} ({self.retention_years}y)"


class RetentionAction(HorillaModel):
    """
    One pending or completed retention action against an employee's records
    for a single category.
    """

    employee_id = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="retention_actions",
        verbose_name=_("Employee"),
    )
    policy = models.ForeignKey(
        DataRetentionPolicy,
        on_delete=models.PROTECT,
        related_name="actions",
        verbose_name=_("Policy"),
    )
    category = models.CharField(
        max_length=32,
        choices=RETENTION_CATEGORIES,
        verbose_name=_("Record category"),
    )
    status = models.CharField(
        max_length=20,
        choices=ACTION_STATUSES,
        default=STATUS_PENDING_REVIEW,
        verbose_name=_("Status"),
    )
    flagged_on = models.DateTimeField(
        default=timezone.now,
        verbose_name=_("Flagged on"),
    )
    auto_anonymize_after = models.DateField(
        verbose_name=_("Auto-anonymize after"),
        help_text=_(
            "Date after which the engine will auto-anonymize unless deferred."
        ),
    )
    deferred_until = models.DateField(
        blank=True,
        null=True,
        verbose_name=_("Deferred until"),
    )
    deferral_reason = models.TextField(
        blank=True,
        null=True,
        verbose_name=_("Reason for deferral"),
    )
    actioned_on = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name=_("Actioned on"),
    )
    actioned_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="retention_actions_taken",
        verbose_name=_("Actioned by"),
    )
    records_affected = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Records affected"),
        help_text=_("Count of underlying rows anonymized or removed."),
    )
    last_error = models.TextField(
        blank=True,
        null=True,
        verbose_name=_("Last error"),
    )

    class Meta:
        verbose_name = _("Retention Action")
        verbose_name_plural = _("Retention Actions")
        unique_together = (("employee_id", "category"),)
        ordering = ("-flagged_on",)

    def __str__(self):
        return f"{self.employee_id} / {self.get_category_display()} — {self.get_status_display()}"


class RetentionRunLog(HorillaModel):
    """
    Engine run history. One row per scheduled or manual run.

    Visible to IT Admin to satisfy 'last run, records processed, errors'
    acceptance criterion.
    """

    started_at = models.DateTimeField(
        default=timezone.now,
        verbose_name=_("Started at"),
    )
    finished_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name=_("Finished at"),
    )
    triggered_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="retention_runs_triggered",
        verbose_name=_("Triggered by"),
        help_text=_("Blank for scheduled runs."),
    )
    is_scheduled = models.BooleanField(
        default=True,
        verbose_name=_("Scheduled run"),
    )
    is_dry_run = models.BooleanField(
        default=False,
        verbose_name=_("Dry run"),
    )
    outcome = models.CharField(
        max_length=16,
        choices=RUN_OUTCOMES,
        blank=True,
        null=True,
        verbose_name=_("Outcome"),
    )
    employees_scanned = models.PositiveIntegerField(default=0)
    actions_flagged = models.PositiveIntegerField(default=0)
    actions_anonymized = models.PositiveIntegerField(default=0)
    actions_failed = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = _("Retention Run Log")
        verbose_name_plural = _("Retention Run Logs")
        ordering = ("-started_at",)

    def __str__(self):
        return f"Run {self.id} @ {self.started_at:%Y-%m-%d %H:%M}"


class RetentionAuditEntry(models.Model):
    """
    Immutable audit trail. Append-only.

    This is the primary ISO 27001 A.12 evidence stream for retention actions:
    every flag, defer, anonymize, error, and policy change writes a row here.

    Deliberately NOT a HorillaModel: we do not want `is_active` toggling,
    `modified_by` auto-updates, or AuditlogHistoryField recursion on the audit
    log itself. Once written, rows are read-only.
    """

    EVENT_POLICY_CREATED = "policy_created"
    EVENT_POLICY_UPDATED = "policy_updated"
    EVENT_POLICY_DELETED = "policy_deleted"
    EVENT_FLAGGED = "flagged"
    EVENT_DEFERRED = "deferred"
    EVENT_ANONYMIZED = "anonymized"
    EVENT_CANCELLED = "cancelled"
    EVENT_RUN_STARTED = "run_started"
    EVENT_RUN_COMPLETED = "run_completed"
    EVENT_ERROR = "error"

    EVENT_CHOICES = [
        (EVENT_POLICY_CREATED, _("Policy created")),
        (EVENT_POLICY_UPDATED, _("Policy updated")),
        (EVENT_POLICY_DELETED, _("Policy deleted")),
        (EVENT_FLAGGED, _("Records flagged")),
        (EVENT_DEFERRED, _("Action deferred")),
        (EVENT_ANONYMIZED, _("Records anonymized")),
        (EVENT_CANCELLED, _("Action cancelled")),
        (EVENT_RUN_STARTED, _("Engine run started")),
        (EVENT_RUN_COMPLETED, _("Engine run completed")),
        (EVENT_ERROR, _("Error")),
    ]

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    event = models.CharField(max_length=32, choices=EVENT_CHOICES, db_index=True)
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="retention_audit_entries",
        help_text=_("Blank for system-initiated actions."),
    )
    employee_id = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="retention_audit_entries",
    )
    category = models.CharField(
        max_length=32, choices=RETENTION_CATEGORIES, blank=True, null=True
    )
    action = models.ForeignKey(
        RetentionAction,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="audit_entries",
    )
    run = models.ForeignKey(
        RetentionRunLog,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="audit_entries",
    )
    message = models.TextField()
    payload = models.JSONField(blank=True, null=True)

    class Meta:
        verbose_name = _("Retention Audit Entry")
        verbose_name_plural = _("Retention Audit Entries")
        ordering = ("-timestamp",)
        # Required Django permission for IT Admin role.
        permissions = (
            ("view_retention_dashboard", "View retention dashboard"),
            ("run_retention_engine", "Run retention engine manually"),
            ("approve_retention_action", "Approve retention action"),
        )

    def __str__(self):
        return f"[{self.timestamp:%Y-%m-%d %H:%M}] {self.get_event_display()}"
