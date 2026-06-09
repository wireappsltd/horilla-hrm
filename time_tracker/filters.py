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
        widget=forms.DateInput(attrs={"type": "date", "class": "tt-filter-input"}),
    )
    date__lte = django_filters.DateFilter(
        field_name="date",
        lookup_expr="lte",
        label=_("Date To"),
        widget=forms.DateInput(attrs={"type": "date", "class": "tt-filter-input"}),
    )
    employee_id = django_filters.ModelChoiceFilter(
        queryset=Employee.objects.all(),
        label=_("Employee"),
        empty_label=_("All employees"),
        widget=forms.Select(attrs={"class": "tt-filter-input"}),
    )
    project_id = django_filters.ModelChoiceFilter(
        queryset=Project.objects.all(),
        label=_("Project"),
        empty_label=_("All projects"),
        widget=forms.Select(attrs={"class": "tt-filter-input"}),
    )
    is_billable = django_filters.BooleanFilter(
        label=_("Billable"),
        widget=forms.Select(
            choices=[("", _("All")), (True, _("Billable")), (False, _("Non-billable"))],
            attrs={"class": "tt-filter-input"},
        ),
    )
    status = django_filters.ChoiceFilter(
        choices=TimeEntry.ENTRY_STATUS,
        label=_("Status"),
        empty_label=_("All statuses"),
        widget=forms.Select(attrs={"class": "tt-filter-input"}),
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
