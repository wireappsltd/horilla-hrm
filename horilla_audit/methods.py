"""
methods.py

This module is used to write methods related to the history
"""

from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import models
from django.shortcuts import render

from horilla.decorators import apply_decorators


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
    """
    if not rows:
        return "None"
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
    return ", ".join(names) if names else "None"


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
                # Convert to readable display names
                related_model = field.related_model
                old = _format_m2m_values(old, related_model)
                new = _format_m2m_values(new, related_model)
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
