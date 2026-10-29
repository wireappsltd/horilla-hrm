from django.apps import AppConfig
from django.apps import apps as django_apps
from django.db import connections
from django.db.models.signals import post_migrate

# (app label, model, resource name) for the tombstone triggers below.
_TRIGGER_TARGETS = [
    ("employee", "Employee", "employee"),
    ("leave", "LeaveRequest", "leave"),
]


def install_triggers(using="default", **kwargs):
    """
    Install database-level tombstone triggers so deletes that bypass the ORM
    (raw SQL, shells, DB maintenance) still show up in ``/deleted/``.

    Called from ``post_migrate`` (not a migration) so it survives deployments
    that generate migrations on the fly. Idempotent. SQLite and PostgreSQL
    only; other backends rely on the Django signals in ``signals.py``.
    """
    connection = connections[using]
    vendor = connection.vendor
    if vendor not in ("sqlite", "postgresql"):
        return
    qn = connection.ops.quote_name
    existing_tables = set(connection.introspection.table_names())
    if "pmo_api_pmodeletedrecord" not in existing_tables:
        return

    with connection.cursor() as cur:
        for app, model, resource in _TRIGGER_TARGETS:
            table = django_apps.get_model(app, model)._meta.db_table
            if table not in existing_tables:
                continue
            name = f"pmo_tombstone_{resource}"
            if vendor == "sqlite":
                cur.execute(f"DROP TRIGGER IF EXISTS {name}")
                cur.execute(
                    f"CREATE TRIGGER {name} AFTER DELETE ON {qn(table)} BEGIN "
                    "INSERT OR IGNORE INTO pmo_api_pmodeletedrecord "
                    "(resource, record_id, deleted_at) VALUES "
                    f"('{resource}', OLD.id, strftime('%Y-%m-%d %H:%M:%f','now')); END"
                )
            else:
                cur.execute(
                    f"CREATE OR REPLACE FUNCTION {name}_fn() RETURNS trigger AS $$ "
                    "BEGIN INSERT INTO pmo_api_pmodeletedrecord "
                    "(resource, record_id, deleted_at) VALUES "
                    f"('{resource}', OLD.id, NOW()) ON CONFLICT DO NOTHING; "
                    "RETURN OLD; END; $$ LANGUAGE plpgsql"
                )
                cur.execute(f"DROP TRIGGER IF EXISTS {name} ON {qn(table)}")
                cur.execute(
                    f"CREATE TRIGGER {name} AFTER DELETE ON {qn(table)} "
                    f"FOR EACH ROW EXECUTE FUNCTION {name}_fn()"
                )


class PmoApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pmo_api"

    def ready(self):
        from . import signals  # noqa: F401  (registers signal handlers)

        # post_migrate fires after all migrations finish; idempotent.
        post_migrate.connect(
            lambda sender, using="default", **kw: install_triggers(using),
            sender=self,
            dispatch_uid="pmo_api.install_triggers",
            weak=False,
        )

