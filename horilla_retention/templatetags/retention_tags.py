"""Template helpers for retention pages."""

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


_CATEGORY_ICONS = {
    "general_hr": "person-circle-outline",
    "attendance_leave": "calendar-outline",
    "payroll": "wallet-outline",
    "tax_epf_etf": "receipt-outline",
}

_STATUS_CLASS = {
    "pending_review": "pending",
    "deferred": "deferred",
    "anonymized": "anonymized",
    "failed": "failed",
    "cancelled": "cancelled",
}

_OUTCOME_CLASS = {
    "success": "success",
    "partial": "partial",
    "failed": "failed",
}


@register.simple_tag
def category_pill(category, label):
    """Render a category pill with an icon."""
    icon = _CATEGORY_ICONS.get(category, "ellipse-outline")
    return mark_safe(
        f'<span class="retention-pill retention-pill--cat">'
        f'<ion-icon name="{icon}"></ion-icon>{label}</span>'
    )


@register.simple_tag
def status_pill(status, label):
    cls = _STATUS_CLASS.get(status, "cancelled")
    return mark_safe(
        f'<span class="retention-pill retention-pill--{cls}">{label}</span>'
    )


@register.simple_tag
def outcome_pill(outcome, label):
    if not outcome:
        return mark_safe('<span class="text-muted">—</span>')
    cls = _OUTCOME_CLASS.get(outcome, "cancelled")
    return mark_safe(
        f'<span class="retention-pill retention-pill--{cls}">{label}</span>'
    )
