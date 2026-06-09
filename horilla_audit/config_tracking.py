"""Mirror configuration-model changes into ActivityLog so they show in the
Audit Logs UI, alongside the action-event hooks elsewhere in the codebase.

How it works:
- A curated registry maps (app_label, model_name) -> (module, friendly label).
- Django's pre_save / post_save / post_delete signals are connected per model.
- pre_save snapshots the existing field values; post_save diffs against them.
- The acting user comes from auditlog's ContextVar populated by
  AuditlogMiddleware (already in MIDDLEWARE).

If the actor is None (migrations, fixtures, management commands, scheduler
jobs) we skip — those aren't user-driven config changes.
"""

import logging

from django.apps import apps
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save

from horilla_audit.methods import log_activity

logger = logging.getLogger(__name__)


# All configuration changes land in the dedicated "Configuration" tab so
# compliance reviewers can scan them together rather than walking every domain tab.
_CONFIG_MODULE = "configuration"

# (app_label, model_name, module_slug, friendly_label)
CONFIG_MODELS = [
    # Leave settings
    ("leave", "LeaveType", _CONFIG_MODULE, "Leave Type"),
    ("leave", "Holiday", _CONFIG_MODULE, "Holiday"),
    ("leave", "CompanyLeave", _CONFIG_MODULE, "Company Leave"),
    ("leave", "LeaveGeneralSetting", _CONFIG_MODULE, "Leave General Setting"),
    ("leave", "RestrictLeave", _CONFIG_MODULE, "Restricted Leave"),
    ("base", "Holidays", _CONFIG_MODULE, "Holiday"),
    ("base", "CompanyLeaves", _CONFIG_MODULE, "Company Leave"),
    # Payroll configurations
    ("payroll", "PayrollSettings", _CONFIG_MODULE, "Tax Setting"),
    ("payroll", "PayrollGeneralSetting", _CONFIG_MODULE, "Payroll General Setting"),
    ("payroll", "EncashmentGeneralSettings", _CONFIG_MODULE, "Encashment Setting"),
    # Roles & Permissions
    ("auth", "Group", _CONFIG_MODULE, "User Group"),
    # Org structure
    ("base", "Company", _CONFIG_MODULE, "Company"),
    ("base", "Department", _CONFIG_MODULE, "Department"),
    ("base", "JobPosition", _CONFIG_MODULE, "Job Position"),
    ("base", "JobRole", _CONFIG_MODULE, "Job Role"),
    ("base", "WorkType", _CONFIG_MODULE, "Work Type"),
    ("base", "EmployeeType", _CONFIG_MODULE, "Employee Type"),
    ("base", "EmployeeShift", _CONFIG_MODULE, "Employee Shift"),
    ("base", "MultipleApprovalCondition", _CONFIG_MODULE, "Approval Condition"),
    # Configuration submenu
    ("base", "HorillaMailTemplate", _CONFIG_MODULE, "Mail Template"),
    ("horilla_automations", "MailAutomation", _CONFIG_MODULE, "Mail Automation"),
    # Tags & Audit Tags
    ("base", "Tags", _CONFIG_MODULE, "Tag"),
    ("horilla_audit", "AuditTag", _CONFIG_MODULE, "Audit Tag"),
    # Attendance config
    ("base", "TrackLateComeEarlyOut", _CONFIG_MODULE, "Late-Come / Early-Out Tracker"),
    ("base", "AttendanceAllowedIP", _CONFIG_MODULE, "Attendance Allowed IP"),
    ("base", "BiometricAttendance", _CONFIG_MODULE, "Biometric Attendance Setting"),
    # System policies
    ("horilla_audit", "HistoryTrackingFields", _CONFIG_MODULE, "History Tracking Setting"),
    ("horilla_audit", "AccountBlockUnblock", _CONFIG_MODULE, "Account Block/Unblock Setting"),
    ("employee", "Actiontype", _CONFIG_MODULE, "Action Type"),
    ("base", "PenaltyAccounts", _CONFIG_MODULE, "Penalty Account"),
]


_MODEL_CONFIG = {}  # model class -> (module, friendly_label)

# Fields we never include in diffs — internal bookkeeping that flips on every save.
_EXCLUDE_FIELDS = {
    "id",
    "created_at",
    "modified_at",
    "created_by",
    "modified_by",
    "history_id",
    "history_date",
    "history_change_reason",
    "history_user_id",
    "history_type",
}


def _get_current_actor():
    """Read auditlog's ContextVar set by AuditlogMiddleware."""
    try:
        from auditlog.context import auditlog_value

        data = auditlog_value.get(None)
        if data:
            return data.get("actor")
    except Exception:
        pass
    return None


def _capture_field_values(instance):
    """Snapshot of comparable field values for diffing."""
    snapshot = {}
    for field in instance._meta.concrete_fields:
        if field.name in _EXCLUDE_FIELDS:
            continue
        try:
            snapshot[field.name] = getattr(instance, field.attname, None)
        except Exception:
            continue
    return snapshot


def _diff_values(old, new):
    """Return {field: {from, to}} for differing fields only."""
    diff = {}
    for key, new_value in new.items():
        old_value = old.get(key)
        if old_value != new_value:
            diff[key] = {
                "from": str(old_value) if old_value is not None else None,
                "to": str(new_value) if new_value is not None else None,
            }
    return diff


def _pre_save_handler(sender, instance, **kwargs):
    if instance.pk is None:
        instance._audit_pre_save_values = None
        return
    try:
        existing = sender.objects.get(pk=instance.pk)
        instance._audit_pre_save_values = _capture_field_values(existing)
    except sender.DoesNotExist:
        instance._audit_pre_save_values = None


def _post_save_handler(sender, instance, created, **kwargs):
    config = _MODEL_CONFIG.get(sender)
    if not config:
        return
    user = _get_current_actor()
    if user is None or not getattr(user, "is_authenticated", False):
        # Skip non-user-driven saves (migrations, fixtures, schedulers).
        return
    module, label = config

    if created:
        log_activity(
            user,
            module=module,
            action=f"{label} created",
            target=instance,
            changes={"new": str(instance)[:200]},
        )
        return

    old_values = getattr(instance, "_audit_pre_save_values", None)
    if not old_values:
        return
    new_values = _capture_field_values(instance)
    diff = _diff_values(old_values, new_values)
    if not diff:
        return
    log_activity(
        user,
        module=module,
        action=f"{label} updated",
        target=instance,
        changes=diff,
    )


def _post_delete_handler(sender, instance, **kwargs):
    config = _MODEL_CONFIG.get(sender)
    if not config:
        return
    user = _get_current_actor()
    if user is None or not getattr(user, "is_authenticated", False):
        return
    module, label = config
    log_activity(
        user,
        module=module,
        action=f"{label} deleted",
        changes={
            "deleted": str(instance)[:200],
            "id": str(instance.pk) if instance.pk is not None else "",
        },
    )


# Map (through_model, parent_model) -> field name for m2m signal resolution.
_M2M_FIELD_BY_THROUGH = {}


def _resolve_items(model, pk_set):
    if not pk_set:
        return []
    try:
        return [str(obj) for obj in model.objects.filter(pk__in=pk_set)]
    except Exception:
        return [str(pk) for pk in pk_set]


def _m2m_changed_handler(sender, instance, action, reverse, model, pk_set, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    parent_model = type(instance)
    config = _MODEL_CONFIG.get(parent_model)
    if not config:
        return
    user = _get_current_actor()
    if user is None or not getattr(user, "is_authenticated", False):
        return
    module, label = config

    field_name = _M2M_FIELD_BY_THROUGH.get((sender, parent_model), "items")

    if action == "post_add":
        change_payload = {"added": _resolve_items(model, pk_set)}
    elif action == "post_remove":
        change_payload = {"removed": _resolve_items(model, pk_set)}
    else:  # post_clear
        change_payload = {"cleared": True}

    log_activity(
        user,
        module=module,
        action=f"{label} updated",
        target=instance,
        changes={field_name: change_payload},
    )


def register_config_tracking():
    """Connect signals for every model in CONFIG_MODELS. Idempotent via dispatch_uid."""
    for app_label, model_name, module, label in CONFIG_MODELS:
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            logger.info(
                "config_tracking: model %s.%s not found; skipping",
                app_label,
                model_name,
            )
            continue
        _MODEL_CONFIG[model] = (module, label)
        uid_base = f"audit_cfg_{app_label}_{model_name}"
        pre_save.connect(
            _pre_save_handler, sender=model, dispatch_uid=f"{uid_base}_pre"
        )
        post_save.connect(
            _post_save_handler, sender=model, dispatch_uid=f"{uid_base}_post"
        )
        post_delete.connect(
            _post_delete_handler, sender=model, dispatch_uid=f"{uid_base}_del"
        )
        for field in model._meta.many_to_many:
            through = field.remote_field.through
            _M2M_FIELD_BY_THROUGH[(through, model)] = field.name
            m2m_changed.connect(
                _m2m_changed_handler,
                sender=through,
                dispatch_uid=f"{uid_base}_m2m_{field.name}",
            )
