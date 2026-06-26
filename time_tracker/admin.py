"""
time_tracker/admin.py

Register all Time Tracker models with Django admin.
"""

from django.contrib import admin

from .models import ActiveTimer, Break, Client, RequiredFieldConfig, Tag, TimeEntry


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "color", "company_id", "is_active"]
    list_filter = ["company_id", "is_active"]
    search_fields = ["name"]


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "currency", "company_id", "is_active"]
    list_filter = ["company_id", "is_active"]
    search_fields = ["name", "email"]


@admin.register(TimeEntry)
class TimeEntryAdmin(admin.ModelAdmin):
    list_display = [
        "employee_id",
        "date",
        "project_id",
        "task_id",
        "duration_display",
        "status",
        "is_billable",
        "is_locked",
    ]
    list_filter = ["status", "is_billable", "is_locked", "date", "company_id"]
    search_fields = [
        "employee_id__employee_first_name",
        "employee_id__employee_last_name",
        "description",
    ]
    date_hierarchy = "date"
    readonly_fields = ["duration_seconds", "duration_display"]

    def duration_display(self, obj):
        return obj.duration_display

    duration_display.short_description = "Duration"


@admin.register(ActiveTimer)
class ActiveTimerAdmin(admin.ModelAdmin):
    list_display = ["employee_id", "started_at", "last_heartbeat", "project_id"]
    search_fields = [
        "employee_id__employee_first_name",
        "employee_id__employee_last_name",
    ]


@admin.register(Break)
class BreakAdmin(admin.ModelAdmin):
    list_display = [
        "time_entry_id",
        "active_timer",
        "start_time",
        "end_time",
        "break_type",
        "duration_seconds",
    ]
    list_filter = ["break_type"]


@admin.register(RequiredFieldConfig)
class RequiredFieldConfigAdmin(admin.ModelAdmin):
    list_display = [
        "company_id",
        "require_project",
        "require_task",
        "require_client",
        "require_description",
        "require_tags",
        "force_timer_only",
    ]
    list_filter = ["company_id"]
