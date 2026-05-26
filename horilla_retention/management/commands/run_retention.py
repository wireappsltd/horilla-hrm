"""
manage.py run_retention [--dry-run]

Manually invokes the retention engine. Useful for OS-level cron, CI smoke
tests, or one-off ISO audit evidence runs.
"""

from django.core.management.base import BaseCommand

from horilla_retention.engine import run_daily


class Command(BaseCommand):
    help = (
        "Run the data-retention engine once. Flags expired records and "
        "auto-anonymizes any whose grace period has elapsed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would happen without writing changes.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        self.stdout.write(
            self.style.NOTICE(
                f"Starting retention engine (dry_run={dry_run})..."
            )
        )
        run = run_daily(triggered_by=None, is_scheduled=False, dry_run=dry_run)
        self.stdout.write(
            self.style.SUCCESS(
                f"Run #{run.id} outcome={run.outcome} "
                f"scanned={run.employees_scanned} "
                f"flagged={run.actions_flagged} "
                f"anonymized={run.actions_anonymized} "
                f"failed={run.actions_failed}"
            )
        )
        if run.error_summary:
            self.stdout.write(self.style.WARNING(run.error_summary))
