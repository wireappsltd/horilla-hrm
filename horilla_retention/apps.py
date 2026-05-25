"""
apps.py for horilla_retention
"""

from django.apps import AppConfig


class HorillaRetentionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "horilla_retention"
    verbose_name = "Data Retention"

    def ready(self):
        from django.urls import include, path

        from horilla.horilla_settings import APPS
        from horilla.urls import urlpatterns

        if "horilla_retention" not in APPS:
            APPS.append("horilla_retention")

        urlpatterns.append(
            path("retention/", include("horilla_retention.urls")),
        )

        # Start the daily scheduler. Guarded internally against admin/migration
        # invocations so it doesn't fire during makemigrations etc.
        from horilla_retention import scheduler

        scheduler.start()
