"""
time_tracker/urls.py

URL configuration for the Time Tracker app.
"""

from django.urls import path

from time_tracker.cbv import management as mgmt_views
from time_tracker.cbv import reports as report_views
from time_tracker.cbv import timesheet as ts_views
from time_tracker.cbv import tracker as tracker_views

app_name = "time_tracker"

urlpatterns = [
    # -----------------------------------------------------------------------
    # Tracker page
    # -----------------------------------------------------------------------
    path(
        "tracker/",
        tracker_views.TrackerPageView.as_view(),
        name="tracker-page",
    ),
    # -----------------------------------------------------------------------
    # Timer controls
    # -----------------------------------------------------------------------
    path(
        "timer/start/",
        tracker_views.timer_start,
        name="tt-timer-start",
    ),
    path(
        "timer/stop/",
        tracker_views.timer_stop,
        name="tt-timer-stop",
    ),
    path(
        "timer/state/",
        tracker_views.timer_state,
        name="tt-timer-state",
    ),
    path(
        "timer/heartbeat/",
        tracker_views.timer_heartbeat,
        name="tt-timer-heartbeat",
    ),
    path(
        "timer/widget/",
        tracker_views.timer_widget,
        name="tt-timer-widget",
    ),
    # -----------------------------------------------------------------------
    # Time entries
    # -----------------------------------------------------------------------
    path(
        "entries/",
        tracker_views.TimeEntryListView.as_view(),
        name="time-entry-list",
    ),
    path(
        "entries/create/",
        tracker_views.time_entry_create,
        name="time-entry-create",
    ),
    path(
        "entries/<int:pk>/row/",
        tracker_views.time_entry_row,
        name="time-entry-row",
    ),
    path(
        "entries/<int:pk>/edit-row/",
        tracker_views.time_entry_edit_row,
        name="time-entry-edit-row",
    ),
    path(
        "entries/<int:pk>/update/",
        tracker_views.time_entry_update,
        name="time-entry-update",
    ),
    path(
        "entries/<int:pk>/delete/",
        tracker_views.time_entry_delete,
        name="time-entry-delete",
    ),
    path(
        "entries/bulk-delete/",
        tracker_views.time_entry_bulk_delete,
        name="time-entry-bulk-delete",
    ),
    # -----------------------------------------------------------------------
    # Timesheet
    # -----------------------------------------------------------------------
    path(
        "timesheet/",
        ts_views.TimesheetPageView.as_view(),
        name="timesheet-page",
    ),
    path(
        "timesheet/week/",
        ts_views.timesheet_week,
        name="timesheet-week",
    ),
    # -----------------------------------------------------------------------
    # Reports
    # -----------------------------------------------------------------------
    path(
        "reports/",
        report_views.ReportsDashboard.as_view(),
        name="reports-dashboard",
    ),
    path(
        "reports/summary/",
        report_views.summary_report,
        name="reports-summary",
    ),
    path(
        "reports/export/",
        report_views.report_export,
        name="reports-export",
    ),
    # -----------------------------------------------------------------------
    # Clients
    # -----------------------------------------------------------------------
    path(
        "clients/",
        mgmt_views.ClientListView.as_view(),
        name="client-list",
    ),
    path(
        "clients/create/",
        mgmt_views.client_create,
        name="client-create",
    ),
    path(
        "clients/<int:pk>/update/",
        mgmt_views.client_update,
        name="client-update",
    ),
    path(
        "clients/<int:pk>/delete/",
        mgmt_views.client_delete,
        name="client-delete",
    ),
    # -----------------------------------------------------------------------
    # Tags
    # -----------------------------------------------------------------------
    path(
        "tags/",
        mgmt_views.TagListView.as_view(),
        name="tag-list",
    ),
    path(
        "tags/create/",
        mgmt_views.tag_create,
        name="tag-create",
    ),
    path(
        "tags/<int:pk>/update/",
        mgmt_views.tag_update,
        name="tag-update",
    ),
    path(
        "tags/<int:pk>/delete/",
        mgmt_views.tag_delete,
        name="tag-delete",
    ),
    # -----------------------------------------------------------------------
    # Management / Settings
    # -----------------------------------------------------------------------
    path(
        "manage/settings/",
        mgmt_views.tracker_settings,
        name="tracker-settings",
    ),
]
