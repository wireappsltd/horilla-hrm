from django.apps import AppConfig


class TimeTrackerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "time_tracker"

    def ready(self):
        from django.urls import include, path

        from horilla.horilla_settings import APP_URLS, APPS
        from horilla.urls import urlpatterns

        APPS.append("time_tracker")
        urlpatterns.append(
            path("time-tracker/", include("time_tracker.urls")),
        )
        APP_URLS.append("time_tracker.urls")
        super().ready()
