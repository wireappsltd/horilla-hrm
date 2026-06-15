from django.apps import AppConfig


class HorillaAuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "horilla_audit"

    def ready(self):
        from horilla_audit.config_tracking import (
            register_config_tracking,
            register_salary_tracking,
        )

        register_config_tracking()
        register_salary_tracking()
