"""
horilla_api/api_urls/time_tracker/urls.py

API URL patterns for the Time Tracker app.
"""

from django.urls import path

from horilla_api.api_views.time_tracker.views import (
    TimeEntryAPIView,
    TimeEntryDetailAPIView,
    TimerStartAPIView,
    TimerStateAPIView,
    TimerStopAPIView,
)

urlpatterns = [
    # Timer
    path("timer/start/", TimerStartAPIView.as_view(), name="api-tt-timer-start"),
    path("timer/stop/", TimerStopAPIView.as_view(), name="api-tt-timer-stop"),
    path("timer/state/", TimerStateAPIView.as_view(), name="api-tt-timer-state"),
    # Time Entries
    path("entries/", TimeEntryAPIView.as_view(), name="api-tt-entries"),
    path("entries/<int:pk>/", TimeEntryDetailAPIView.as_view(), name="api-tt-entry-detail"),
]
