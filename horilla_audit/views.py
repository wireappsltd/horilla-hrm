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
    ("helpdesk", "ISO Forms"),
    ("password_reset", "Password Reset Logs"),
    ("configuration", "Configuration"),
    ("logins", "Logins"),
)
DEFAULT_MODULE = AUDIT_MODULES[0][0]
_VALID_MODULES = {slug for slug, _label in AUDIT_MODULES}

# Page-size options offered in the audit log UI. First entry is the default.
PAGE_SIZE_OPTIONS = (20, 50, 100)
DEFAULT_PAGE_SIZE = PAGE_SIZE_OPTIONS[0]


def _resolve_page_size(request):
    """Return a validated page size from ?per_page, or the default.

    Only values from the fixed option list are honoured, so a crafted
    ``?per_page=100000`` can't force an unbounded query on a large table.
    """
    try:
        size = int(request.GET.get("per_page", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return size if size in PAGE_SIZE_OPTIONS else DEFAULT_PAGE_SIZE


def _paginate(request, queryset):
    """Paginate ``queryset`` for the current request.

    Returns ``(page, page_size, page_range)`` where ``page_range`` is an elided
    list of page numbers (with ``Paginator.ELLIPSIS`` gaps) so we render a few
    numbered links instead of hundreds on large datasets.
    """
    page_size = _resolve_page_size(request)
    paginator = Paginator(queryset, page_size)
    page = paginator.get_page(request.GET.get("page"))
    page_range = list(
        paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1)
    )
    return page, page_size, page_range


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
        # -id tiebreaker keeps rows with identical timestamps in a stable order
        # so entries don't drift between pages.
        login_logs = login_logs.order_by("-timestamp", "-id")
        page, page_size, page_range = _paginate(request, login_logs)
        return render(
            request,
            "horilla_audit/audit_logs.html",
            {
                "modules": AUDIT_MODULES,
                "module_labels": dict(AUDIT_MODULES),
                "active_module": module,
                "active_module_label": dict(AUDIT_MODULES).get(module, module),
                "page_obj": page,
                "page_range": page_range,
                "page_size": page_size,
                "page_size_options": PAGE_SIZE_OPTIONS,
                "is_login_log": True,
                "status_filter": status_filter,
            },
        )

    logs = (
        ActivityLog.objects.filter(module=module)
        .select_related("user")
        .order_by("-timestamp", "-id")
    )
    page, page_size, page_range = _paginate(request, logs)

    return render(
        request,
        "horilla_audit/audit_logs.html",
        {
            "modules": AUDIT_MODULES,
            "module_labels": dict(AUDIT_MODULES),
            "active_module": module,
            "active_module_label": dict(AUDIT_MODULES).get(module, module),
            "page_obj": page,
            "page_range": page_range,
            "page_size": page_size,
            "page_size_options": PAGE_SIZE_OPTIONS,
            "is_login_log": False,
        },
    )
