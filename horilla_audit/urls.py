"""
horilla_audit/urls.py
"""

from django.urls import path

from horilla_audit import views

urlpatterns = [
    path("audit-logs/", views.audit_logs, name="audit-logs"),
]
