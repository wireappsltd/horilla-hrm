"""
time_tracker/forms.py

Django ModelForms for Time Tracker models.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm

from .models import Client, Favourite, RequiredFieldConfig, Tag, TimeEntry, TimesheetLock


class TimeEntryForm(ModelForm):
    """
    Form for creating and editing TimeEntry records.
    """

    class Meta:
        model = TimeEntry
        fields = [
            "employee_id",
            "project_id",
            "task_id",
            "client_id",
            "tag_ids",
            "description",
            "date",
            "start_time",
            "end_time",
            "is_billable",
            "billable_rate",
            "currency",
            "status",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date", "class": "oh-input w-100"}),
            "start_time": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": "oh-input w-100"}
            ),
            "end_time": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": "oh-input w-100"}
            ),
            "description": forms.Textarea(
                attrs={"class": "oh-input w-100", "rows": 3}
            ),
            "project_id": forms.Select(
                attrs={"class": "oh-select oh-select-2 w-100", "id": "id_project_id"}
            ),
            "task_id": forms.Select(
                attrs={"class": "oh-select oh-select-2 w-100", "id": "id_task_id"}
            ),
            "client_id": forms.Select(
                attrs={"class": "oh-select oh-select-2 w-100"}
            ),
            "tag_ids": forms.SelectMultiple(
                attrs={"class": "oh-select oh-select-2 w-100"}
            ),
            "employee_id": forms.Select(
                attrs={"class": "oh-select oh-select-2 w-100"}
            ),
            "currency": forms.TextInput(attrs={"class": "oh-input w-100"}),
            "billable_rate": forms.NumberInput(attrs={"class": "oh-input w-100"}),
            "status": forms.Select(attrs={"class": "oh-select w-100"}),
            "is_billable": forms.CheckboxInput(attrs={"class": "oh-switch__checkbox"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Allow filtering tasks by project via JavaScript / HTMX
        if self.initial.get("project_id") or (
            self.data and self.data.get("project_id")
        ):
            project_id = self.initial.get("project_id") or self.data.get("project_id")
            try:
                from project.models import Task

                self.fields["task_id"].queryset = Task.objects.filter(
                    project_id=project_id
                )
            except Exception:
                pass


class TagForm(ModelForm):
    """
    Form for creating and editing Tag records.
    """

    class Meta:
        model = Tag
        fields = ["name", "color"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "oh-input w-100"}),
            "color": forms.TextInput(
                attrs={"type": "color", "class": "oh-input w-100"}
            ),
        }


class ClientForm(ModelForm):
    """
    Form for creating and editing Client records.
    """

    class Meta:
        model = Client
        fields = [
            "name",
            "email",
            "address",
            "currency",
            "default_rate",
            "color",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "oh-input w-100"}),
            "email": forms.EmailInput(attrs={"class": "oh-input w-100"}),
            "address": forms.Textarea(
                attrs={"class": "oh-input w-100", "rows": 3}
            ),
            "currency": forms.TextInput(attrs={"class": "oh-input w-100"}),
            "default_rate": forms.NumberInput(attrs={"class": "oh-input w-100"}),
            "color": forms.TextInput(
                attrs={"type": "color", "class": "oh-input w-100"}
            ),
        }


class FavouriteForm(ModelForm):
    """Form for creating and editing Favourite (saved entry template) records."""

    class Meta:
        model = Favourite
        fields = [
            "name",
            "project_id",
            "task_id",
            "client_id",
            "description",
            "is_billable",
            "tag_ids",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "oh-input w-100"}),
            "project_id": forms.Select(attrs={"class": "oh-select oh-select-2 w-100"}),
            "task_id": forms.Select(attrs={"class": "oh-select oh-select-2 w-100"}),
            "client_id": forms.Select(attrs={"class": "oh-select oh-select-2 w-100"}),
            "description": forms.Textarea(attrs={"class": "oh-input w-100", "rows": 2}),
            "is_billable": forms.CheckboxInput(attrs={"class": "oh-switch__checkbox"}),
            "tag_ids": forms.SelectMultiple(attrs={"class": "oh-select oh-select-2 w-100"}),
        }


class TimesheetLockForm(ModelForm):
    """Form for creating a TimesheetLock."""

    class Meta:
        model = TimesheetLock
        fields = ["employee_id", "date_from", "date_to", "reason"]
        widgets = {
            "employee_id": forms.Select(attrs={"class": "oh-select oh-select-2 w-100"}),
            "date_from": forms.DateInput(attrs={"type": "date", "class": "oh-input w-100"}),
            "date_to": forms.DateInput(attrs={"type": "date", "class": "oh-input w-100"}),
            "reason": forms.TextInput(attrs={"class": "oh-input w-100"}),
        }


class RequiredFieldConfigForm(ModelForm):
    """
    Form for managing per-company RequiredFieldConfig.
    """

    class Meta:
        model = RequiredFieldConfig
        fields = [
            "require_project",
            "require_task",
            "require_client",
            "require_description",
            "require_tags",
            "force_timer_only",
            "idle_timeout_minutes",
        ]
        widgets = {
            "require_project": forms.CheckboxInput(
                attrs={"class": "oh-switch__checkbox"}
            ),
            "require_task": forms.CheckboxInput(
                attrs={"class": "oh-switch__checkbox"}
            ),
            "require_client": forms.CheckboxInput(
                attrs={"class": "oh-switch__checkbox"}
            ),
            "require_description": forms.CheckboxInput(
                attrs={"class": "oh-switch__checkbox"}
            ),
            "require_tags": forms.CheckboxInput(
                attrs={"class": "oh-switch__checkbox"}
            ),
            "force_timer_only": forms.CheckboxInput(
                attrs={"class": "oh-switch__checkbox"}
            ),
            "idle_timeout_minutes": forms.NumberInput(
                attrs={"class": "oh-input w-100", "min": "1", "max": "120"}
            ),
        }
