import calendar
import datetime as dt
import sys
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from dateutil.relativedelta import relativedelta


def leave_reset():
    from leave.models import LeaveType

    today = datetime.now()
    today_date = today.date()
    leave_types = LeaveType.objects.filter(reset=True)
    # Looping through filtered leave types with reset is true
    for leave_type in leave_types:
        # Looping through all available leaves
        available_leaves = leave_type.employee_available_leave.all()

        for available_leave in available_leaves:
            reset_date = available_leave.reset_date
            expired_date = available_leave.expired_date
            if reset_date == today_date:
                available_leave.update_carryforward()
                # new_reset_date = available_leave.set_reset_date(assigned_date=today_date,available_leave = available_leave)
                new_reset_date = available_leave.set_reset_date(
                    assigned_date=today_date, available_leave=available_leave
                )
                available_leave.reset_date = new_reset_date
                # Mark the start of the new period so leave_taken/pending_leaves
                # exclude pre-reset approvals. Without this, total_leaves keeps
                # adding pre-reset approved days into the new period's stats.
                available_leave.last_reset_date = today_date.replace(month=1, day=1)
                available_leave.save()
            if expired_date and expired_date <= today_date:
                new_expired_date = available_leave.set_expired_date(
                    available_leave=available_leave, assigned_date=today_date
                )
                available_leave.expired_date = new_expired_date
                available_leave.save()

        if (
            leave_type.carryforward_expire_date
            and leave_type.carryforward_expire_date <= today_date
        ):
            # Zero out CF days on every employee's AvailableLeave for this
            # leave type — bumping the expire date alone leaves stale CF
            # showing in leave statistics indefinitely. Capture the value
            # into expired_carryforward_days first so the expired total
            # remains visible as a stat after the wipe.
            for available_leave in leave_type.employee_available_leave.all():
                # `> 0` rather than truthy: a corrupted negative balance
                # would otherwise be captured as a negative expired stat.
                if available_leave.carryforward_days > 0:
                    available_leave.expired_carryforward_days = (
                        available_leave.carryforward_days
                    )
                    available_leave.carryforward_days = 0
                    available_leave.save()
            leave_type.carryforward_expire_date = leave_type.set_expired_date(
                today_date
            )
            leave_type.save()


def leave_approval_reminder():
    """
    For every leave request that is still in the "requested" state, remind the
    employee that their request is awaiting approval and send the approver(s)
    (reporting manager or the designated conditional-approval managers) a daily
    reminder to action it, until the request is approved in the system.
    """
    from leave.models import LeaveRequest
    from leave.threading import LeaveMailSendThread

    pending_requests = LeaveRequest.objects.filter(status="requested")
    for leave_request in pending_requests:
        try:
            LeaveMailSendThread(None, leave_request, type="reminder").start()
        except Exception:
            pass


if not any(
    cmd in sys.argv
    for cmd in ["makemigrations", "migrate", "compilemessages", "flush", "shell"]
):
    """
    Initializes and starts background tasks using APScheduler when the server is running.
    """
    scheduler = BackgroundScheduler()
    scheduler.add_job(leave_reset, "interval", seconds=20)
    scheduler.add_job(leave_approval_reminder, "cron", hour=8, minute=0)

    scheduler.start()
