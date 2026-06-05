"""
methods.py

This module is used to write methods related to the history
"""

import logging

from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import models
from django.shortcuts import render

from horilla.decorators import apply_decorators

logger = logging.getLogger(__name__)

# Groups whose members may view audit logs.
AUDIT_ROLE_GROUPS = ("HR", "ISO")


def user_can_view_audit(user):
    """HR Admin, ISO/Compliance Officer, or superuser."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=AUDIT_ROLE_GROUPS).exists()


def log_activity(user, module, action, target=None, changes=None):
    """Record an action-based audit log entry.

    Failures are swallowed and logged — never block the originating request
    because we couldn't write an audit row.
    """
    from horilla_audit.models import ActivityLog

    try:
        ActivityLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            module=module,
            action=action,
            target_type=type(target).__name__ if target is not None else "",
            target_id=str(getattr(target, "pk", "")) if target is not None else "",
            changes=changes,
        )
    except Exception:
        logger.exception("Failed to write ActivityLog entry")


class Bot:
    def __init__(self) -> None:
        self.__str__()

    def __str__(self) -> str:
        return "Horilla Bot"

    def get_avatar(self):
        return "https://ui-avatars.com/api/?name=Horilla+Bot&background=random"


def _check_and_delete(entry1, entry2, dry_run=False):
    delta = entry1.diff_against(entry2)
    if not delta.changed_fields:
        if not dry_run:
            entry1.delete()
        return 1
    return 0


def remove_duplicate_history(instance):
    """
    This method is used to remove duplicate entries
    """
    o_qs = instance.history_set.all()
    entries_deleted = 0
    # ordering is ('-history_date', '-history_id') so this is ok
    f1 = o_qs.first()
    if not f1:
        return
    for f2 in o_qs[1:]:
        entries_deleted += _check_and_delete(
            f1,
            f2,
        )
        f1 = f2


def get_field_label(model_class, field_name):
    # Check if the field exists in the model class
    if hasattr(model_class, field_name):
        field = model_class._meta.get_field(field_name)
        return field.verbose_name.capitalize()
    # Return None if the field does not exist
    return None


def filter_history(histories, track_fields):
    filtered_histories = []
    for history in histories:
        changes = history.get("changes", [])
        filtered_changes = [
            change for change in changes if change.get("field_name", "") in track_fields
        ]
        if filtered_changes:
            history["changes"] = filtered_changes
            filtered_histories.append(history)
    histories = filtered_histories
    return histories


def _format_m2m_values(rows, related_model):
    """
    Convert M2M through-table row dicts to a readable comma-separated string.
    Each row is a dict like {'user_id': 5, 'passwordresetrequest_id': 3} or
    {'user': 5, 'passwordresetrequest': 3} depending on how the values were
    extracted from the through model. We resolve the FK pointing to the
    related_model to get display names.

    Returns an empty string when ``rows`` is empty/None so callers can use a
    truthiness check to detect "no rows" without conflating it with the
    literal string "None" (which would be truthy and prevent skipping
    entries where both old and new are empty).
    """
    if not rows:
        return ""
    names = []
    # Determine which key in the row dicts corresponds to the related model
    # by matching the model name. simple_history's diff_against pulls rows via
    # ``QuerySet.values(*field_names)`` using the through-model FK *field*
    # names (e.g. ``user``), not the underlying ``<field>_id`` column names,
    # so we must look up both forms.
    model_name = related_model.__name__.lower()
    candidate_keys = (model_name, f"{model_name}_id")

    skip_keys = {"id", "m2m_history_id", "history", "history_id"}

    for row in rows:
        pk_value = None
        # Prefer keys that match the related model name
        for key in candidate_keys:
            if key in row and row[key] is not None:
                pk_value = row[key]
                break
        if pk_value is None:
            # Fallback: try any non-housekeeping key that resolves to the
            # related model (handles arbitrary through-model field names).
            for key, value in row.items():
                if not value or key in skip_keys:
                    continue
                # Skip the source-model FK (e.g. ``passwordresetrequest`` /
                # ``passwordresetrequest_id``) so we resolve the correct side
                # of the M2M relationship.
                stripped = key[:-3] if key.endswith("_id") else key
                if stripped == model_name or stripped.endswith(model_name):
                    pk_value = value
                    break
        if pk_value is None:
            continue
        # ``foreign_keys_are_objs`` mode in simple_history may already give us
        # a model instance instead of a raw pk.
        if isinstance(pk_value, related_model):
            obj = pk_value
        else:
            try:
                obj = related_model.objects.get(pk=pk_value)
            except Exception:
                names.append(str(pk_value))
                continue
        # Try employee display name first (for User -> Employee chain)
        if hasattr(obj, "employee_get"):
            try:
                full_name = obj.employee_get.get_full_name()
                if full_name:
                    names.append(full_name)
                    continue
            except Exception:
                pass
        if hasattr(obj, "get_full_name"):
            full_name = obj.get_full_name()
            if full_name:
                names.append(full_name)
                continue
        names.append(str(obj))
    return ", ".join(names) if names else ""


def _normalize_m2m_pks(rows, related_model):
    """
    Extract the set of related-model PKs from a list of through-table row
    dicts. Used to detect when an M2M change reported by ``diff_against``
    has identical "before" and "after" sides at the relationship level
    (which can happen for edge cases such as duplicate historical rows).
    """
    if not rows:
        return frozenset()
    model_name = related_model.__name__.lower()
    candidate_keys = (model_name, f"{model_name}_id")
    skip_keys = {"id", "m2m_history_id", "history", "history_id"}
    pks = set()
    for row in rows:
        pk_value = None
        for key in candidate_keys:
            if key in row and row[key] is not None:
                pk_value = row[key]
                break
        if pk_value is None:
            for key, value in row.items():
                if not value or key in skip_keys:
                    continue
                stripped = key[:-3] if key.endswith("_id") else key
                if stripped == model_name or stripped.endswith(model_name):
                    pk_value = value
                    break
        if pk_value is None:
            continue
        if isinstance(pk_value, related_model):
            pk_value = pk_value.pk
        pks.add(pk_value)
    return frozenset(pks)


def get_diff(instance):
    """
    This method is used to find the differences in the history
    """
    remove_duplicate_history(instance)
    history = instance.history_set.all()
    history_list = list(history)
    pairs = [
        [history_list[i], history_list[i + 1]] for i in range(len(history_list) - 1)
    ]
    delta_changes = []
    create_history = history.filter(history_type="+").first()
    for pair in pairs:
        suppress_initial_m2m = (
            create_history is not None
            and pair[1].pk == create_history.pk
            and pair[1].history_type == "+"
        )
        delta = pair[0].diff_against(pair[1])
        diffs = []
        class_name = pair[0].instance.__class__
        for change in delta.changes:
            old = change.old
            new = change.new
            field = instance._meta.get_field(change.field)
            is_fk = False
            if isinstance(field, models.ManyToManyField):
                # M2M changes: old/new are lists of through-table dicts
                # Convert to readable display names. Skip the change entry
                # entirely when the underlying relationship sets are
                # identical (e.g. simple_history reported a diff because of
                # row PK ordering / DO_NOTHING-orphaned through rows) – this
                # prevents misleading "<field> from None to None" entries.
                related_model = field.related_model
                old_pks = _normalize_m2m_pks(old, related_model)
                new_pks = _normalize_m2m_pks(new, related_model)
                if old_pks == new_pks:
                    continue
                if suppress_initial_m2m and not old_pks:
                    continue
                old_display = _format_m2m_values(old, related_model)
                new_display = _format_m2m_values(new, related_model)
                # Render empty side as the literal "None" for readability,
                # but only when the *other* side has values (otherwise both
                # would say "None" and the entry is meaningless).
                old = old_display or "None"
                new = new_display or "None"
            elif (
                isinstance(field, models.fields.CharField)
                and field.choices
                and old
                and new
            ):
                choices = dict(field.choices)
                old = choices.get(old, old)
                new = choices.get(new, new)
            elif isinstance(field, models.ForeignKey):
                is_fk = True
                # old = getattr(pair[0], change.field)
                # new = getattr(pair[1], change.field)
            # Skip changes where both old and new are empty/None
            if not old and not new:
                continue
            diffs.append(
                {
                    "field": get_field_label(class_name, change.field),
                    "field_name": change.field,
                    "is_fk": is_fk,
                    "old": old,
                    "new": new,
                }
            )
        updated_by = (
            User.objects.get(id=pair[0].history_user.id).employee_get
            if pair[0].history_user
            else Bot()
        )
        delta_changes.append(
            {
                "type": "Changes",
                "pair": pair,
                "changes": diffs,
                "updated_by": updated_by,
            }
        )
    if create_history:
        try:
            updated_by = create_history.history_user.employee_get
        except:
            updated_by = Bot()
        delta_changes.append(
            {
                "type": f"{create_history.instance.__class__._meta.verbose_name.capitalize()} created",
                "pair": (create_history, create_history),
                "updated_by": updated_by,
            }
        )
    if instance._meta.model_name == "employeeworkinformation":
        from .models import HistoryTrackingFields

        history_tracking_instance = HistoryTrackingFields.objects.first()
        if history_tracking_instance and history_tracking_instance.tracking_fields:
            track_fields = history_tracking_instance.tracking_fields["tracking_fields"]
            if track_fields:
                delta_changes = filter_history(delta_changes, track_fields)
    return delta_changes


def history_tracking(request, obj_id, **kwargs):
    model = kwargs.get("model")
    decorator_strings = kwargs.get("decorators", [])

    @apply_decorators(decorator_strings)
    def _history_tracking(request, obj_id, model):
        instance = model.objects.get(pk=obj_id)
        histories = instance.horilla_history.all()
        page_number = request.GET.get("page", 1)
        paginator = Paginator(histories, 4)
        page_obj = paginator.get_page(page_number)
        context = {
            "histories": page_obj,
            "model_name": model,
        }
        return render(
            request,
            "horilla_audit/history_tracking.html",
            context,
        )

    return _history_tracking(request, obj_id, model)
