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

# Asset lookups route to the "Assets" audit tab so they sit beside the asset
# lifecycle events instead of the generic Configuration tab.
_ASSET_MODULE = "asset"

# Offboarding lookups/settings route to the "Offboarding" audit tab beside the
# offboarding lifecycle events instead of the generic Configuration tab.
_OFFBOARDING_MODULE = "offboarding"

# A model's audit tab follows the nav menu its edit screen lives under, so a
# reviewer looks in the same place they made the change. "Leave Types" is the
# only leave-domain screen under the Leave menu (leave/sidebar.py); Holidays,
# Company Leaves and Restrict Leaves sit under the Configuration menu
# (templates/sidebar.html), and the compensatory-leave and past-leave-restriction
# toggles sit on the Settings page (templates/settings.html), so all of those
# stay in the Configuration tab.
_LEAVE_MODULE = "leave"

# (app_label, model_name, module_slug, friendly_label)
CONFIG_MODELS = [
    # Leave — see the _LEAVE_MODULE note above for how the tab is chosen.
    ("leave", "LeaveType", _LEAVE_MODULE, "Leave Type"),
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
    # Org structure - shift / work-type definitions
    ("base", "RotatingWorkType", _CONFIG_MODULE, "Rotating Work Type"),
    ("base", "RotatingShift", _CONFIG_MODULE, "Rotating Shift"),
    ("base", "EmployeeShiftSchedule", _CONFIG_MODULE, "Shift Schedule"),
    ("base", "DynamicEmailConfiguration", _CONFIG_MODULE, "Mail Server"),
    ("employee", "EmployeeTag", _CONFIG_MODULE, "Employee Tag"),
    # Attendance settings
    (
        "attendance",
        "AttendanceValidationCondition",
        _CONFIG_MODULE,
        "Attendance Break-point Setting",
    ),
    (
        "attendance",
        "AttendanceGeneralSetting",
        _CONFIG_MODULE,
        "Check-In/Check-Out Setting",
    ),
    ("attendance", "GraceTime", _CONFIG_MODULE, "Grace Time"),
    # Leave settings — edited from the Settings page, so Configuration tab.
    (
        "leave",
        "EmployeePastLeaveRestrict",
        _CONFIG_MODULE,
        "Past Leave Restriction Setting",
    ),
    # Payroll settings
    ("payroll", "PayslipAutoGenerate", _CONFIG_MODULE, "Auto Payslip Setting"),
    # Performance (PMS) settings
    ("pms", "BonusPointSetting", _CONFIG_MODULE, "Bonus Point Setting"),
    # Recruitment definitions / settings
    ("recruitment", "Skill", _CONFIG_MODULE, "Skill"),
    ("recruitment", "RejectReason", _CONFIG_MODULE, "Candidate Reject Reason"),
    (
        "recruitment",
        "RecruitmentGeneralSetting",
        _CONFIG_MODULE,
        "Candidate Self-Tracking Setting",
    ),
    ("recruitment", "LinkedInAccount", _CONFIG_MODULE, "LinkedIn Integration Setting"),
    # Integrations
    ("horilla_ldap", "LDAPSettings", _CONFIG_MODULE, "LDAP Setting"),
    ("horilla_backup", "GoogleDriveBackup", _CONFIG_MODULE, "Google Drive Backup Setting"),
    ("outlook_auth", "AzureApi", _CONFIG_MODULE, "Outlook/Azure Mail Server"),
    # Data retention policies
    ("horilla_retention", "DataRetentionPolicy", _CONFIG_MODULE, "Data Retention Policy"),
    # Time tracker settings
    ("time_tracker", "RequiredFieldConfig", _CONFIG_MODULE, "Time Tracker Setting"),
    ("time_tracker", "Client", _CONFIG_MODULE, "Time Tracker Client"),
    ("time_tracker", "Tag", _CONFIG_MODULE, "Time Tracker Tag"),
    # Accessibility settings
    ("accessibility", "DefaultAccessibility", _CONFIG_MODULE, "Accessibility Setting"),
    # Asset lookups — categories and batches/lots. These land in the dedicated
    # "Assets" tab (not Configuration) alongside the asset lifecycle events that
    # asset/views.py logs explicitly. create/update/delete + company_id M2M are
    # tracked automatically here.
    ("asset", "AssetCategory", _ASSET_MODULE, "Asset Category"),
    ("asset", "AssetLot", _ASSET_MODULE, "Asset Batch/Lot"),
    # Offboarding lookups/settings — resignation-reason table and the resignation
    # feature toggle. These land in the "Offboarding" tab alongside the lifecycle
    # events that offboarding/views.py logs explicitly. Signal tracking covers all
    # CRUD paths (create/edit/delete views + the enable/disable settings toggle).
    ("offboarding", "ExitReason", _OFFBOARDING_MODULE, "Resignation Reason"),
    (
        "offboarding",
        "OffboardingGeneralSetting",
        _OFFBOARDING_MODULE,
        "Offboarding Setting",
    ),
]


_MODEL_CONFIG = {}  # model class -> (module, friendly_label)

# Per-model predicate: return True to SKIP logging an instance entirely. Used to
# drop auto-generated artifacts (e.g. loan-spawned allowances / installment
# deductions) that aren't user-facing salary components.
_SKIP_BY_MODEL = {}  # model class -> callable(instance) -> bool

# Per-model field names to drop from diffs, on top of _EXCLUDE_FIELDS — auto-managed
# links the user never edits directly (e.g. LoanAccount.allowance_id).
_EXTRA_EXCLUDE_BY_MODEL = {}  # model class -> set[str]

# Salary / payroll component models. These land in the existing "payroll" tab so
# salary changes are reviewable alongside payslip events.
_PAYROLL_MODULE = "payroll"
SALARY_MODELS = [
    ("payroll", "Contract", _PAYROLL_MODULE, "Contract"),
    ("payroll", "Allowance", _PAYROLL_MODULE, "Allowance"),
    ("payroll", "Deduction", _PAYROLL_MODULE, "Deduction"),
    ("payroll", "LoanAccount", _PAYROLL_MODULE, "Loan"),
    ("payroll", "Reimbursement", _PAYROLL_MODULE, "Reimbursement"),
    # Statutory report metadata (e.g. ETF Monthly Contribution). Records the
    # create/delete of a report definition; the file export itself is logged
    # explicitly in download_payroll_report (no DB write happens there).
    ("payroll", "PayrollReport", _PAYROLL_MODULE, "Payroll Report"),
]

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
    extra = _EXTRA_EXCLUDE_BY_MODEL.get(type(instance), ())
    snapshot = {}
    for field in instance._meta.concrete_fields:
        if field.name in _EXCLUDE_FIELDS or field.name in extra:
            continue
        try:
            snapshot[field.name] = getattr(instance, field.attname, None)
        except Exception:
            continue
    return snapshot


def _render_field(model, field_name, value):
    """Return a (human label, display string) pair for a raw field value.

    Resolves FK pks to their object string and choice values to their labels so
    the diff reads as "Basic salary: 1000 -> 2000" rather than raw column data.
    """
    try:
        field = model._meta.get_field(field_name)
    except Exception:
        field = None
    label = field_name
    if field is not None and getattr(field, "verbose_name", None):
        label = str(field.verbose_name).capitalize()
    if value is None or value == "":
        return label, None
    if field is not None:
        if field.is_relation and field.related_model is not None:
            try:
                return label, str(field.related_model.objects.get(pk=value))
            except Exception:
                return label, str(value)
        choices = getattr(field, "choices", None)
        if choices:
            try:
                mapping = dict(choices)
                if value in mapping:
                    return label, str(mapping[value])
            except (TypeError, ValueError):
                pass
    return label, str(value)


def _diff_values(model, old, new):
    """Return {label: {from, to}} for differing fields only."""
    diff = {}
    for key, new_value in new.items():
        old_value = old.get(key)
        if old_value == new_value:
            continue
        label, old_disp = _render_field(model, key, old_value)
        _, new_disp = _render_field(model, key, new_value)
        diff[label] = {"from": old_disp, "to": new_disp}
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
    skip = _SKIP_BY_MODEL.get(sender)
    if skip and skip(instance):
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
    diff = _diff_values(sender, old_values, new_values)
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
    skip = _SKIP_BY_MODEL.get(sender)
    if skip and skip(instance):
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
    skip = _SKIP_BY_MODEL.get(parent_model)
    if skip and skip(instance):
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


def _register_model(
    model, module, label, *, skip=None, extra_exclude=None, track_m2m=True
):
    """Connect audit signals for a single model. Idempotent via dispatch_uid."""
    _MODEL_CONFIG[model] = (module, label)
    if skip is not None:
        _SKIP_BY_MODEL[model] = skip
    if extra_exclude:
        _EXTRA_EXCLUDE_BY_MODEL[model] = set(extra_exclude)
    uid_base = f"audit_cfg_{model._meta.app_label}_{model._meta.model_name}"
    pre_save.connect(_pre_save_handler, sender=model, dispatch_uid=f"{uid_base}_pre")
    post_save.connect(_post_save_handler, sender=model, dispatch_uid=f"{uid_base}_post")
    post_delete.connect(
        _post_delete_handler, sender=model, dispatch_uid=f"{uid_base}_del"
    )
    if track_m2m:
        for field in model._meta.many_to_many:
            through = field.remote_field.through
            _M2M_FIELD_BY_THROUGH[(through, model)] = field.name
            m2m_changed.connect(
                _m2m_changed_handler,
                sender=through,
                dispatch_uid=f"{uid_base}_m2m_{field.name}",
            )


# --- Roles & permissions -----------------------------------------------------
# Granting someone a user group or a direct permission is the highest-impact
# configuration change in the system, so it is audited explicitly here.
#
# These are NOT covered by registering ``auth.User`` in CONFIG_MODELS: doing so
# would fire _post_save_handler on every User row save, including the
# ``last_login`` write that happens on every single login, drowning the
# Configuration tab in noise. Instead we hook only the two m2m through-tables.
#
# Group *definitions* and a group's *permission set* are already covered by the
# ``auth.Group`` entry in CONFIG_MODELS; what follows covers group membership
# and per-user permission grants, which hang off User and so were invisible.

# through model -> (User-side field name, noun used in the change payload)
_ROLE_M2M_SPECS = {}


def _describe_user(user):
    """Readable identity for an auth User in a role-change audit entry."""
    employee = None
    try:
        employee = user.employee_get
    except Exception:
        employee = None
    if employee is not None:
        return str(employee)
    return user.get_username()


def _resolve_names(model, pk_set):
    """Resolve a set of pks to display names, newest lookup errors tolerated."""
    if not pk_set:
        return []
    try:
        return sorted(str(obj) for obj in model.objects.filter(pk__in=pk_set))
    except Exception:
        return sorted(str(pk) for pk in pk_set)


def _log_role_change(actor, user, noun, verb, names):
    """Write one Configuration-tab entry for a role/permission change."""
    if not names:
        return
    log_activity(
        actor,
        module=_CONFIG_MODULE,
        action=f"{noun} {verb}",
        target=user,
        changes={
            "User": _describe_user(user),
            "Username": user.get_username(),
            noun: ", ".join(names),
        },
    )


def _role_m2m_changed_handler(
    sender, instance, action, reverse, model, pk_set, **kwargs
):
    """Audit User<->Group and User<->Permission membership changes.

    Handles both directions: ``user.groups.add(group)`` (instance is the User)
    and ``group.user_set.add(user)`` (instance is the Group). ``clear()`` is
    snapshotted on ``pre_clear`` because by ``post_clear`` the rows are gone.
    """
    spec = _ROLE_M2M_SPECS.get(sender)
    if spec is None:
        return
    field_name, noun = spec

    if action == "pre_clear":
        # Snapshot what is about to be removed; logged on post_clear, by which
        # point the through-rows no longer exist.
        related = instance.user_set if reverse else getattr(instance, field_name)
        try:
            instance._audit_cleared_roles = [str(obj) for obj in related.all()]
        except Exception:
            instance._audit_cleared_roles = []
        return

    if action not in ("post_add", "post_remove", "post_clear"):
        return

    actor = _get_current_actor()
    if actor is None or not getattr(actor, "is_authenticated", False):
        # Skip non-user-driven changes (migrations, fixtures, shell, scheduler).
        return

    verb = {
        "post_add": "granted",
        "post_remove": "revoked",
        "post_clear": "cleared",
    }[action]
    cleared = getattr(instance, "_audit_cleared_roles", []) or []

    if reverse:
        # instance is the Group/Permission; the users sit on the other side.
        if action == "post_clear":
            # pk_set is None here, so fall back to the pre_clear snapshot and
            # emit a single entry naming everyone who was removed.
            if cleared:
                log_activity(
                    actor,
                    module=_CONFIG_MODULE,
                    action=f"{noun} cleared",
                    target=instance,
                    changes={noun: str(instance), "Users": ", ".join(cleared)},
                )
            return
        # Otherwise emit one entry per affected user, keeping entries
        # user-centric and consistent with the forward direction.
        for user in model.objects.filter(pk__in=pk_set or []):
            _log_role_change(actor, user, noun, verb, [str(instance)])
        return

    names = cleared if action == "post_clear" else _resolve_names(model, pk_set)
    _log_role_change(actor, instance, noun, verb, names)


def register_role_tracking():
    """Connect audit signals for group membership and per-user permissions."""
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    targets = (
        ("groups", "User groups"),
        ("user_permissions", "User permissions"),
    )
    for field_name, noun in targets:
        through = user_model._meta.get_field(field_name).remote_field.through
        _ROLE_M2M_SPECS[through] = (field_name, noun)
        m2m_changed.connect(
            _role_m2m_changed_handler,
            sender=through,
            dispatch_uid=f"audit_role_{through._meta.model_name}",
        )


def register_config_tracking():
    """Connect signals for every model in CONFIG_MODELS."""
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
        _register_model(model, module, label)


def register_salary_tracking():
    """Connect signals for salary component models (-> the payroll tab).

    M2M tracking is intentionally disabled: the only m2m on these models are
    auto-managed links (loan installment deductions, attachments), which would
    be noise. Loan-spawned allowance/installment artifacts are filtered via the
    per-model skip predicates.
    """
    skips = {
        "Allowance": lambda i: bool(getattr(i, "is_loan", False)),
        "Deduction": lambda i: bool(getattr(i, "is_installment", False)),
    }
    extra_excludes = {
        "LoanAccount": {"allowance_id", "rate", "is_fixed", "apply_on", "asset_id"},
        "Reimbursement": {"allowance_id"},
    }
    for app_label, model_name, module, label in SALARY_MODELS:
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            logger.info(
                "salary_tracking: model %s.%s not found; skipping",
                app_label,
                model_name,
            )
            continue
        _register_model(
            model,
            module,
            label,
            skip=skips.get(model_name),
            extra_exclude=extra_excludes.get(model_name),
            track_m2m=False,
        )
