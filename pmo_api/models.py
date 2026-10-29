from django.db import models


class PMODeletedRecord(models.Model):
    """
    Tombstone written when an employee or leave request is hard-deleted, so
    Meridian's incremental sync can learn about deletions via
    ``GET /api/pmo/v1/deleted/?deleted_since=``.

    ``record_id`` is never reused (auto-increment primary keys).
    """

    RESOURCES = [("employee", "Employee"), ("leave", "Leave")]

    resource = models.CharField(max_length=20, choices=RESOURCES)
    record_id = models.BigIntegerField()
    deleted_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["resource", "record_id"], name="pmo_unique_tombstone"
            )
        ]
        indexes = [models.Index(fields=["resource", "deleted_at"])]


class PMOEmployeeDeactivation(models.Model):
    """
    Date on which an employee's ``is_active`` flag was switched to False.
    Used as the ``leaving_date`` fallback so every inactive employee has one.
    """

    employee_id = models.IntegerField(unique=True)
    deactivated_on = models.DateField()

