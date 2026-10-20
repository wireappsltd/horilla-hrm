from django.apps import AppConfig


class PmoApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pmo_api"

    def ready(self):
        from . import signals  # noqa: F401  (registers signal handlers)

