from django import template
from django.conf import settings
from django.template.defaultfilters import register

from leave.models import LeaveGeneralSetting

register = template.Library()


@register.filter(name="is_compensatory")
def is_compensatory(user):
    if LeaveGeneralSetting.objects.exists():
        return LeaveGeneralSetting.objects.first().compensatory_leave
    else:
        return False


@register.simple_tag(name="settings_debug")
def settings_debug():
    return bool(settings.DEBUG)
