"""
time_tracker/filters.py

FilterSet classes for Time Tracker models.
"""

import django_filters
from django import forms
from django.utils.translation import gettext_lazy as _

from employee.models import Employee
from horilla.filters import FilterSet
from project.models import Project

from .models import TimeEntry


class TimeEntryFilter(FilterSet):
    """
    FilterSet for TimeEntry model.
    Supports filtering by date range, employee, project, billable flag, status.
    """

    date__gte = django_filters.DateFilter(
        field_name="date",
        lookup_expr="gte",
        label=_("Date From"),
        widget=forms.DateInput(attrs={"type": "date", "class": "oh-input w-100"}),
    )
    date__lte = django_filters.DateFilter(
        field_name="date",
        lookup_expr="lte",
        label=_("Date To"),
        widget=forms.DateInput(attrs={"type": "date", "class": "oh-input w-100"}),
    )
    employee_id = django_filters.ModelMultipleChoiceFilter(
        queryset=Employee.objects.all(),
        label=_("Employee"),
        widget=forms.SelectMultiple(attrs={"class": "oh-select oh-select-2 w-100"}),
    )
    project_id = django_filters.ModelMultipleChoiceFilter(
        queryset=Project.objects.all(),
        label=_("Project"),
        widget=forms.SelectMultiple(attrs={"class": "oh-select oh-select-2 w-100"}),
    )
    is_billable = django_filters.BooleanFilter(
        label=_("Billable"),
        widget=forms.Select(
            choices=[("", "---------"), (True, _("Yes")), (False, _("No"))],
            attrs={"class": "oh-select w-100"},
        ),
    )
    status = django_filters.MultipleChoiceFilter(
        choices=TimeEntry.ENTRY_STATUS,
        label=_("Status"),
        widget=forms.SelectMultiple(attrs={"class": "oh-select oh-select-2 w-100"}),
    )

    class Meta:
        model = TimeEntry
        fields = [
            "date__gte",
            "date__lte",
            "employee_id",
            "project_id",
            "is_billable",
            "status",
        ]
