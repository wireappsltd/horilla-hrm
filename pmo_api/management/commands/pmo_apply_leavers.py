"""
Apply the leaving-date policy (see ``pmo_api.leaving``). Run daily (cron):

    python manage.py pmo_apply_leavers

For every employee whose leaving date is today or earlier:
* set ``is_active=False`` if still active, and
* bump ``updated_at`` so Meridian's ``updated_since`` sync sees the flip.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from employee.models import Employee
from pmo_api.leaving import planned_leaving_date
from pmo_api.views import employee_base_queryset


class Command(BaseCommand):
    help = "Deactivate employees on/after their leaving date and bump updated_at."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        today = timezone.localdate()
        candidates = employee_base_queryset().filter(pmo_planned_leaving__lte=today)
        changed = 0
        for employee in candidates:
            leaving = planned_leaving_date(employee)
            if not leaving or leaving > today:
                continue
            needs_flip = employee.is_active
            # Not yet re-stamped since the leaving date arrived.
            unsignalled = (
                employee.updated_at is None
                or timezone.localtime(employee.updated_at).date() < leaving
            )
            if not (needs_flip or unsignalled):
                continue
            changed += 1
            if options["dry_run"]:
                self.stdout.write(f"would update employee {employee.pk} ({leaving})")
                continue
            updates = {"updated_at": timezone.now()}
            if needs_flip:
                updates["is_active"] = False
            Employee._base_manager.filter(pk=employee.pk).update(**updates)
        self.stdout.write(self.style.SUCCESS(f"{changed} employee(s) processed."))

