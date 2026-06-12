from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import render

from horilla_audit.methods import user_can_view_audit
from horilla_audit.models import ActivityLog, LoginLog

# UI label -> internal module slug. Defines tab order shown to user.
AUDIT_MODULES = (
    ("recruitment", "Recruitment"),
    ("onboarding", "Onboarding"),
    ("employee", "Employee"),
    ("attendance", "Attendance"),
    ("leave", "Leave"),
    ("payroll", "Payroll"),
    ("pms", "Performance"),
    ("offboarding", "Offboarding"),
    ("asset", "Assets"),
    ("helpdesk", "Help Desk"),
    ("configuration", "Configuration"),
    ("logins", "Logins"),
)
DEFAULT_MODULE = AUDIT_MODULES[0][0]
_VALID_MODULES = {slug for slug, _label in AUDIT_MODULES}


@login_required
def audit_logs(request):
    if not user_can_view_audit(request.user):
        raise PermissionDenied

    module = request.GET.get("module") or DEFAULT_MODULE
    if module not in _VALID_MODULES:
        module = DEFAULT_MODULE

    if module == "logins":
        status_filter = request.GET.get("status", "")
        login_logs = LoginLog.objects.select_related("user")
        if status_filter in ("failed", "success"):
            login_logs = login_logs.filter(status=status_filter)
        page = Paginator(login_logs, 25).get_page(request.GET.get("page"))
        return render(
            request,
            "horilla_audit/audit_logs.html",
            {
                "modules": AUDIT_MODULES,
                "module_labels": dict(AUDIT_MODULES),
                "active_module": module,
                "active_module_label": dict(AUDIT_MODULES).get(module, module),
                "page_obj": page,
                "is_login_log": True,
                "status_filter": status_filter,
            },
        )

    logs = ActivityLog.objects.filter(module=module).select_related("user")
    page = Paginator(logs, 25).get_page(request.GET.get("page"))

    return render(
        request,
        "horilla_audit/audit_logs.html",
        {
            "modules": AUDIT_MODULES,
            "module_labels": dict(AUDIT_MODULES),
            "active_module": module,
            "active_module_label": dict(AUDIT_MODULES).get(module, module),
            "page_obj": page,
            "is_login_log": False,
        },
    )