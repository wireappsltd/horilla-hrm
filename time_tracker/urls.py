"""
time_tracker/urls.py

URL configuration for the Time Tracker app (Phase 1 + Phase 2).
"""

from django.urls import path

from time_tracker.cbv import approvals as approval_views
from time_tracker.cbv import calendar as cal_views
from time_tracker.cbv import management as mgmt_views
from time_tracker.cbv import reports as report_views
from time_tracker.cbv import timesheet as ts_views
from time_tracker.cbv import tracker as tracker_views

app_name = "time_tracker"

urlpatterns = [
    # -----------------------------------------------------------------------
    # Tracker page
    # -----------------------------------------------------------------------
    path("tracker/", tracker_views.TrackerPageView.as_view(), name="tracker-page"),
    # -----------------------------------------------------------------------
    # Timer controls
    # -----------------------------------------------------------------------
    path("timer/start/", tracker_views.timer_start, name="tt-timer-start"),
    path("timer/stop/", tracker_views.timer_stop, name="tt-timer-stop"),
    path("timer/state/", tracker_views.timer_state, name="tt-timer-state"),
    path("timer/heartbeat/", tracker_views.timer_heartbeat, name="tt-timer-heartbeat"),
    path("timer/widget/", tracker_views.timer_widget, name="tt-timer-widget"),
    path("descriptions/suggest/", tracker_views.description_suggestions, name="tt-desc-suggest"),
    path("manual/add/", tracker_views.manual_entry_create, name="tt-manual-add"),
    # -----------------------------------------------------------------------
    # Breaks (Phase 2)
    # -----------------------------------------------------------------------
    path("breaks/start/", approval_views.break_start, name="tt-break-start"),
    path("breaks/stop/", approval_views.break_stop, name="tt-break-stop"),
    path("breaks/state/", approval_views.break_state, name="tt-break-state"),
    # -----------------------------------------------------------------------
    # Time entries
    # -----------------------------------------------------------------------
    path("entries/", tracker_views.TimeEntryListView.as_view(), name="time-entry-list"),
    path("entries/create/", tracker_views.time_entry_create, name="time-entry-create"),
    path("entries/<int:pk>/row/", tracker_views.time_entry_row, name="time-entry-row"),
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
    # Timesheet — week + month (Phase 2)
    # -----------------------------------------------------------------------
    path("timesheet/", ts_views.TimesheetPageView.as_view(), name="timesheet-page"),
    path("timesheet/week/", ts_views.timesheet_week, name="timesheet-week"),
    path("timesheet/month/", ts_views.timesheet_month, name="timesheet-month"),
    path("timesheet/submit/", approval_views.timesheet_submit, name="timesheet-submit"),
    # -----------------------------------------------------------------------
    # Calendar (Phase 2)
    # -----------------------------------------------------------------------
    path("calendar/day/", cal_views.calendar_day, name="calendar-day"),
    path("calendar/week/", cal_views.calendar_week, name="calendar-week"),
    path("calendar/entry/<int:pk>/", cal_views.cal_entry_panel, name="calendar-entry-panel"),
    # -----------------------------------------------------------------------
    # Approval workflow (Phase 2)
    # -----------------------------------------------------------------------
    path(
        "approvals/",
        approval_views.ApprovalListView.as_view(),
        name="approval-list",
    ),
    path(
        "approvals/<int:pk>/",
        approval_views.approval_detail,
        name="approval-detail",
    ),
    # -----------------------------------------------------------------------
    # Locks (Phase 2)
    # -----------------------------------------------------------------------
    path("manage/locks/", approval_views.LockListView.as_view(), name="lock-list"),
    path("manage/locks/create/", approval_views.lock_create, name="lock-create"),
    path("manage/locks/<int:pk>/delete/", approval_views.lock_delete, name="lock-delete"),
    # -----------------------------------------------------------------------
    # Favourites (Phase 2)
    # -----------------------------------------------------------------------
    path("favourites/", approval_views.favourites_list, name="favourites-list"),
    path("favourites/create/", approval_views.favourite_create, name="favourite-create"),
    path(
        "favourites/<int:pk>/delete/",
        approval_views.favourite_delete,
        name="favourite-delete",
    ),
    path(
        "favourites/<int:pk>/apply/",
        approval_views.favourite_apply,
        name="favourite-apply",
    ),
    # -----------------------------------------------------------------------
    # Reports
    # -----------------------------------------------------------------------
    path("reports/", report_views.ReportsDashboard.as_view(), name="reports-dashboard"),
    path("reports/summary/", report_views.summary_report, name="reports-summary"),
    path("reports/detailed/", report_views.detailed_report, name="reports-detailed"),
    path("reports/team/", report_views.team_report, name="reports-team"),
    path("reports/export/", report_views.report_export, name="reports-export"),
    # -----------------------------------------------------------------------
    # Clients
    # -----------------------------------------------------------------------
    path("clients/", mgmt_views.ClientListView.as_view(), name="client-list"),
    path("clients/create/", mgmt_views.client_create, name="client-create"),
    path("clients/<int:pk>/update/", mgmt_views.client_update, name="client-update"),
    path("clients/<int:pk>/delete/", mgmt_views.client_delete, name="client-delete"),
    # -----------------------------------------------------------------------
    # Tags
    # -----------------------------------------------------------------------
    path("tags/", mgmt_views.TagListView.as_view(), name="tag-list"),
    path("tags/create/", mgmt_views.tag_create, name="tag-create"),
    path("tags/<int:pk>/update/", mgmt_views.tag_update, name="tag-update"),
    path("tags/<int:pk>/delete/", mgmt_views.tag_delete, name="tag-delete"),
    # -----------------------------------------------------------------------
    # Management / Settings
    # -----------------------------------------------------------------------
    path("manage/settings/", mgmt_views.tracker_settings, name="tracker-settings"),
]

