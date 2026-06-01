"""
Thin wrapper around RetentionAuditEntry for consistent, structured event
writing. Every retention-related action (flag, defer, anonymize, error, policy
change, engine run start/stop) MUST go through one of these helpers so the
ISO 27001 A.12 evidence stream is uniform.
"""

from typing import Any, Optional

from django.contrib.auth.models import User

from employee.models import Employee

from horilla_retention.models import (
    RetentionAction,
    RetentionAuditEntry,
    RetentionRunLog,
)


def write(
    event: str,
    message: str,
    actor: Optional[User] = None,
    employee: Optional[Employee] = None,
    category: Optional[str] = None,
    action: Optional[RetentionAction] = None,
    run: Optional[RetentionRunLog] = None,
    payload: Optional[dict[str, Any]] = None,
) -> RetentionAuditEntry:
    """Create an audit entry. Never raises — audit failures are logged but
    do not abort the calling business action.
    """
    try:
        return RetentionAuditEntry.objects.create(
            event=event,
            message=message,
            actor=actor,
            employee_id=employee,
            category=category,
            action=action,
            run=run,
            payload=payload,
        )
    except Exception as exc:
        import logging

        logging.getLogger("horilla_retention").exception(
            "Failed to write audit entry (%s): %s", event, exc
        )
        return None
