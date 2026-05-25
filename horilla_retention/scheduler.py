"""
APScheduler wiring for the retention engine.

A single daily job at 02:00 server time. The guard against management
subcommands matches base/scheduler.py so makemigrations/migrate don't trigger
the engine.
"""

import logging
import sys

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger("horilla_retention")

_started = False


def _run_daily_safe():
    """Wrapper so any exception from engine.run_daily is logged and never
    kills the scheduler thread."""
    try:
        from horilla_retention.engine import run_daily

        run = run_daily(triggered_by=None, is_scheduled=True, dry_run=False)
        logger.info(
            "Retention engine daily run #%s finished with outcome=%s",
            run.id,
            run.outcome,
        )
    except Exception:
        logger.exception("Retention engine daily run crashed")


def start():
    """Idempotent. Skips if invoked during a Django management subcommand that
    shouldn't trigger background work."""
    global _started
    if _started:
        return

    skip_cmds = {
        "makemigrations",
        "migrate",
        "compilemessages",
        "flush",
        "shell",
        "collectstatic",
        "loaddata",
        "test",
    }
    if any(arg in skip_cmds for arg in sys.argv):
        return

    scheduler = BackgroundScheduler()
    try:
        scheduler.add_job(
            _run_daily_safe,
            trigger="cron",
            hour=2,
            minute=0,
            id="retention_engine_daily",
            replace_existing=True,
        )
        scheduler.start()
        _started = True
        logger.info("Retention engine scheduler started (daily @ 02:00).")
    except Exception:
        logger.exception("Failed to start retention engine scheduler")
