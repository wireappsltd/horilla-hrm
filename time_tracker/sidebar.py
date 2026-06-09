"""
time_tracker/sidebar.py

Sidebar menu definition for the Time Tracker app.
Management lives under the gear-icon General Settings page (see templates/settings.html).
"""

from django.utils.translation import gettext_lazy as trans

MENU = trans("Time Tracker")
IMG_SRC = "images/ui/time_tracker.svg"

SUBMENUS = [
    {
        "menu": trans("Tracker"),
        "redirect": "/time-tracker/tracker/",
    },
    {
        "menu": trans("Timesheet"),
        "redirect": "/time-tracker/timesheet/",
    },
    {
        "menu": trans("Reports"),
        "redirect": "/time-tracker/reports/",
    },
]
