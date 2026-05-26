"""
Retention engine.

Daily run does two passes:

  Pass 1 — flag_expired_records:
    For every enabled DataRetentionPolicy, find resigned employees whose
    last_working_date elapsed retention_years years ago. If no
    RetentionAction exists for that (employee, category), create one with
    status=pending_review and auto_anonymize_after = today + grace_period_days.

  Pass 2 — execute_grace_expirations:
    Find RetentionAction.status=pending_review whose
    auto_anonymize_after <= today AND not deferred past today.
    Run the category anonymizer, mark status=anonymized.

Both passes are wrapped in a RetentionRunLog and every event is mirrored to
RetentionAuditEntry for ISO 27001 A.12.

Refuses to act if NO policies are configured for the company — the user story
explicitly required "configurable only — no seeded defaults", so a missing
policy is a safe no-op (never an implicit retention period).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from django.contrib.auth.models import User
from django.utils import timezone

from employee.models import Employee

from horilla_retention import audit
from horilla_retention.anonymizers import run_anonymizer
from horilla_retention.constants import (
    RUN_OUTCOME_FAILED,
    RUN_OUTCOME_PARTIAL,
    RUN_OUTCOME_SUCCESS,
    STATUS_ANONYMIZED,
    STATUS_FAILED,
    STATUS_PENDING_REVIEW,
)
from horilla_retention.models import (
    DataRetentionPolicy,
    RetentionAction,
    RetentionAuditEntry,
    RetentionRunLog,
)


@dataclass
class RunResult:
    employees_scanned: int = 0
    actions_flagged: int = 0
    actions_anonymized: int = 0
    actions_failed: int = 0
    errors: list = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


def _years_ago(years: int) -> date:
    today = timezone.localdate()
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        # 29 Feb edge case.
        return today.replace(year=today.year - years, day=28)


def _eligible_employees_for(policy: DataRetentionPolicy):
    """Resigned employees in this company whose last_working_date elapsed
    `policy.retention_years` years ago."""
    cutoff = _years_ago(policy.retention_years)
    return Employee.objects.filter(
        employee_work_info__company_id=policy.company_id,
        offboardingemployee__last_working_date__isnull=False,
        offboardingemployee__last_working_date__lte=cutoff,
    ).distinct()


def flag_expired_records(run: RetentionRunLog, dry_run: bool = False) -> RunResult:
    """Pass 1."""
    result = RunResult()
    today = timezone.localdate()

    for policy in DataRetentionPolicy.objects.filter(is_enabled=True).select_related(
        "company_id"
    ):
        eligible = _eligible_employees_for(policy)
        for employee in eligible:
            result.employees_scanned += 1

            existing = RetentionAction.objects.filter(
                employee_id=employee, category=policy.category
            ).first()
            if existing is not None:
                continue

            if dry_run:
                result.actions_flagged += 1
                continue

            action = RetentionAction.objects.create(
                employee_id=employee,
                policy=policy,
                category=policy.category,
                status=STATUS_PENDING_REVIEW,
                auto_anonymize_after=today + timedelta(days=policy.grace_period_days),
            )
            result.actions_flagged += 1

            audit.write(
                event=RetentionAuditEntry.EVENT_FLAGGED,
                message=(
                    f"Flagged employee {employee.id} category={policy.category} "
                    f"for review. Will auto-anonymize on "
                    f"{action.auto_anonymize_after.isoformat()}."
                ),
                employee=employee,
                category=policy.category,
                action=action,
                run=run,
                payload={
                    "policy_id": policy.id,
                    "retention_years": policy.retention_years,
                    "grace_period_days": policy.grace_period_days,
                },
            )

    return result


def execute_grace_expirations(
    run: RetentionRunLog, dry_run: bool = False
) -> RunResult:
    """Pass 2."""
    result = RunResult()
    today = timezone.localdate()

    pending = RetentionAction.objects.filter(
        status=STATUS_PENDING_REVIEW, auto_anonymize_after__lte=today
    ).select_related("employee_id", "policy")

    for action in pending:
        # If admin has deferred this action beyond today, skip.
        if action.deferred_until and action.deferred_until > today:
            continue

        if dry_run:
            result.actions_anonymized += 1
            continue

        affected, error = run_anonymizer(action.category, action.employee_id)

        if error:
            action.status = STATUS_FAILED
            action.last_error = error
            action.records_affected = affected
            action.save()
            result.actions_failed += 1
            result.errors.append(
                f"emp={action.employee_id_id} cat={action.category}: {error}"
            )
            audit.write(
                event=RetentionAuditEntry.EVENT_ERROR,
                message=f"Anonymization failed: {error}",
                employee=action.employee_id,
                category=action.category,
                action=action,
                run=run,
            )
            continue

        action.status = STATUS_ANONYMIZED
        action.actioned_on = timezone.now()
        action.records_affected = affected
        action.last_error = None
        action.save()
        result.actions_anonymized += 1
        audit.write(
            event=RetentionAuditEntry.EVENT_ANONYMIZED,
            message=(
                f"Auto-anonymized employee {action.employee_id_id} "
                f"category={action.category}; {affected} record(s) affected."
            ),
            employee=action.employee_id,
            category=action.category,
            action=action,
            run=run,
            payload={"records_affected": affected},
        )

    return result


def run_daily(
    triggered_by: Optional[User] = None,
    is_scheduled: bool = True,
    dry_run: bool = False,
) -> RetentionRunLog:
    """Top-level entry point. Always creates a RetentionRunLog, even on
    failure, so the IT Admin dashboard can show 'last run' + errors."""
    run = RetentionRunLog.objects.create(
        triggered_by=triggered_by,
        is_scheduled=is_scheduled,
        is_dry_run=dry_run,
    )
    audit.write(
        event=RetentionAuditEntry.EVENT_RUN_STARTED,
        message=(
            f"Retention engine run started "
            f"(scheduled={is_scheduled}, dry_run={dry_run})."
        ),
        actor=triggered_by,
        run=run,
    )

    aggregate = RunResult()

    try:
        pass1 = flag_expired_records(run, dry_run=dry_run)
        aggregate.employees_scanned += pass1.employees_scanned
        aggregate.actions_flagged += pass1.actions_flagged

        pass2 = execute_grace_expirations(run, dry_run=dry_run)
        aggregate.actions_anonymized += pass2.actions_anonymized
        aggregate.actions_failed += pass2.actions_failed
        aggregate.errors.extend(pass2.errors)
    except Exception as exc:  # pragma: no cover - defensive
        run.outcome = RUN_OUTCOME_FAILED
        run.finished_at = timezone.now()
        run.error_summary = str(exc)
        run.save()
        audit.write(
            event=RetentionAuditEntry.EVENT_ERROR,
            message=f"Retention engine crashed: {exc}",
            actor=triggered_by,
            run=run,
        )
        return run

    run.employees_scanned = aggregate.employees_scanned
    run.actions_flagged = aggregate.actions_flagged
    run.actions_anonymized = aggregate.actions_anonymized
    run.actions_failed = aggregate.actions_failed
    run.finished_at = timezone.now()
    if aggregate.actions_failed and aggregate.actions_anonymized:
        run.outcome = RUN_OUTCOME_PARTIAL
    elif aggregate.actions_failed:
        run.outcome = RUN_OUTCOME_FAILED
    else:
        run.outcome = RUN_OUTCOME_SUCCESS
    if aggregate.errors:
        run.error_summary = "\n".join(aggregate.errors[:50])
    run.save()

    audit.write(
        event=RetentionAuditEntry.EVENT_RUN_COMPLETED,
        message=(
            f"Run completed: scanned={run.employees_scanned} "
            f"flagged={run.actions_flagged} "
            f"anonymized={run.actions_anonymized} "
            f"failed={run.actions_failed}"
        ),
        actor=triggered_by,
        run=run,
        payload={
            "employees_scanned": run.employees_scanned,
            "actions_flagged": run.actions_flagged,
            "actions_anonymized": run.actions_anonymized,
            "actions_failed": run.actions_failed,
        },
    )

    return run
