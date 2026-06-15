"""
horilla_audit/sidebar.py
"""

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from horilla_audit.methods import user_can_view_audit

MENU = _("Audit Logs")
IMG_SRC = "images/ui/report.svg"
ACCESSIBILITY = "horilla_audit.sidebar.audit_accessibility"


SUBMENUS = [
    {
        "menu": _("Audit Logs"),
        "redirect": reverse("audit-logs"),
        "accessibility": "horilla_audit.sidebar.audit_accessibility",
    },
]


def audit_accessibility(request, *args, **kwargs):
    return user_can_view_audit(request.user)
