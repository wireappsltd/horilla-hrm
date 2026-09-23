"""
horilla/db_utils.py

Database helpers for code that runs outside the request/response cycle.
"""

from functools import wraps

from django.db import close_old_connections


def db_safe_job(func):
    """
    Wrap a scheduled job so its worker thread does not hold a database
    connection open between runs.

    Django database connections are thread-local and are normally released by
    the ``request_started`` / ``request_finished`` signals. Those signals never
    fire inside an APScheduler worker thread, so without this wrapper the
    connection opened by a job's first run stays open for the life of the
    process -- which is how idle sessions accumulate on the database server.

    ``close_old_connections`` is called both before and after the job: before,
    so a connection left unusable since the previous run is discarded rather
    than reused; after (in ``finally``), so the connection is released even
    when the job raises.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        close_old_connections()
        try:
            return func(*args, **kwargs)
        finally:
            close_old_connections()

    return wrapper
