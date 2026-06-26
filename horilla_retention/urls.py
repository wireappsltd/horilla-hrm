"""URL routes for the retention dashboard."""

from django.urls import path

from horilla_retention import views

urlpatterns = [
    path("", views.dashboard, name="retention-dashboard"),
    path("policies/", views.policy_list, name="retention-policies"),
    path("policies/new/", views.policy_create, name="retention-policy-create"),
    path(
        "policies/<int:policy_id>/edit/",
        views.policy_update,
        name="retention-policy-update",
    ),
    path(
        "policies/<int:policy_id>/delete/",
        views.policy_delete,
        name="retention-policy-delete",
    ),
    path("actions/", views.action_list, name="retention-actions"),
    path(
        "actions/<int:action_id>/approve/",
        views.action_approve,
        name="retention-action-approve",
    ),
    path(
        "actions/<int:action_id>/defer/",
        views.action_defer,
        name="retention-action-defer",
    ),
    path(
        "actions/<int:action_id>/cancel/",
        views.action_cancel,
        name="retention-action-cancel",
    ),
    path("run/", views.trigger_run, name="retention-trigger-run"),
    path("run-log/", views.run_log, name="retention-run-log"),
    path("audit/", views.audit_log, name="retention-audit-log"),
]
