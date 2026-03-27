from django.contrib.auth.models import Group, User
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from helpdesk.models import ISO_GROUP_NAME, cleanup_iso_user_password_reset_assignments


def _cleanup_users(users):
    for user in users:
        cleanup_iso_user_password_reset_assignments(user)


@receiver(m2m_changed, sender=User.groups.through)
def track_iso_group_pre_clear(sender, instance, action, reverse, **kwargs):
    """Cache users affected by clear() so post_clear can process ISO removals."""
    if action != "pre_clear":
        return

    if reverse:
        if isinstance(instance, Group) and instance.name == ISO_GROUP_NAME:
            instance._cached_iso_user_ids_before_clear = list(
                instance.user_set.values_list("id", flat=True)
            )
    elif instance.groups.filter(name=ISO_GROUP_NAME).exists():
        instance._cached_iso_membership_before_clear = True


@receiver(m2m_changed, sender=User.groups.through)
def sync_password_reset_on_iso_group_change(
    sender, instance, action, reverse, model, pk_set, **kwargs
):
    """Sync pending/request routing when users are removed from the ISO group."""
    if action not in {"post_remove", "post_clear"}:
        return

    if reverse:
        if not isinstance(instance, Group) or instance.name != ISO_GROUP_NAME:
            return

        if action == "post_remove":
            removed_users = model.objects.filter(pk__in=(pk_set or set()))
        else:
            user_ids = getattr(instance, "_cached_iso_user_ids_before_clear", [])
            removed_users = model.objects.filter(pk__in=user_ids)
            if hasattr(instance, "_cached_iso_user_ids_before_clear"):
                delattr(instance, "_cached_iso_user_ids_before_clear")

        _cleanup_users(removed_users)
        return

    if action == "post_remove":
        if not Group.objects.filter(pk__in=(pk_set or set()), name=ISO_GROUP_NAME).exists():
            return
        _cleanup_users([instance])
        return

    if getattr(instance, "_cached_iso_membership_before_clear", False):
        _cleanup_users([instance])
        delattr(instance, "_cached_iso_membership_before_clear")

