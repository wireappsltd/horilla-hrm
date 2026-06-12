"""
time_tracker/sidebar.py

Sidebar menu definition for the Time Tracker app.
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
        "menu": trans("Calendar"),
        "redirect": "/time-tracker/calendar/day/",
    },
    {
        "menu": trans("Timesheet"),
        "redirect": "/time-tracker/timesheet/",
    },
    {
        "menu": trans("Month Overview"),
        "redirect": "/time-tracker/timesheet/month/",
    },
    {
        "menu": trans("Approvals"),
        "redirect": "/time-tracker/approvals/",
    },
    {
        "menu": trans("Reports"),
        "redirect": "/time-tracker/reports/",
    },
    {
        "menu": trans("Team Activity"),
        "redirect": "/time-tracker/reports/team/",
    },
    {
        "menu": trans("Detailed Report"),
        "redirect": "/time-tracker/reports/detailed/",
    },
]
