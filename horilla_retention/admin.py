"""Django admin wiring."""

from django.contrib import admin

from horilla_retention.models import (
    DataRetentionPolicy,
    RetentionAction,
    RetentionAuditEntry,
    RetentionRunLog,
)


@admin.register(DataRetentionPolicy)
class DataRetentionPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "company_id",
        "category",
        "retention_years",
        "grace_period_days",
        "is_enabled",
    )
    list_filter = ("is_enabled", "category")
    search_fields = ("company_id__company", "notes")


@admin.register(RetentionAction)
class RetentionActionAdmin(admin.ModelAdmin):
    list_display = (
        "employee_id",
        "category",
        "status",
        "flagged_on",
        "auto_anonymize_after",
        "actioned_on",
    )
    list_filter = ("status", "category")
    search_fields = (
        "employee_id__employee_first_name",
        "employee_id__employee_last_name",
        "employee_id__badge_id",
    )
    readonly_fields = (
        "employee_id",
        "policy",
        "category",
        "flagged_on",
        "auto_anonymize_after",
        "actioned_on",
        "actioned_by",
        "records_affected",
        "last_error",
    )


@admin.register(RetentionRunLog)
class RetentionRunLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "started_at",
        "finished_at",
        "outcome",
        "is_scheduled",
        "is_dry_run",
        "employees_scanned",
        "actions_flagged",
        "actions_anonymized",
        "actions_failed",
    )
    list_filter = ("outcome", "is_scheduled", "is_dry_run")
    readonly_fields = [f.name for f in RetentionRunLog._meta.fields]


@admin.register(RetentionAuditEntry)
class RetentionAuditEntryAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "event", "actor", "employee_id", "category")
    list_filter = ("event", "category")
    search_fields = ("message", "employee_id__badge_id")
    readonly_fields = [f.name for f in RetentionAuditEntry._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False
