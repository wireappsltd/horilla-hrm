import json
import logging
import os
from datetime import datetime, timedelta
from distutils.util import strtobool
from operator import itemgetter
from urllib.parse import parse_qs

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import Group, User
from django.core import serializers
from django.db.models import ProtectedError, Q
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, strip_tags
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from base.forms import TagsForm
from base.methods import (
    filtersubordinates,
    get_key_instances,
    is_reportingmanager,
    paginator_qry,
    sortby,
)
from base.models import Department, JobPosition, Tags
from employee.models import Employee
from employee.views import get_content_type
from helpdesk.decorators import ticket_owner_can_enter
from helpdesk.filter import (
    FAQCategoryFilter,
    FAQFilter,
    FaqSearch,
    TicketFilter,
    TicketReGroup,
)
from helpdesk.forms import (
    AttachmentForm,
    AccessRequestForm,
    AdminAccessRequestForm,
    ChangeImplementerForm,
    ChangeReleaseForm,
    ChangeRequesterForm,
    CommentForm,
    DepartmentManagerCreateForm,
    ExceptionRequestForm,
    FAQCategoryForm,
    FAQForm,
    IncidentPostReviewForm,
    IncidentReportForm,
    IncidentTransitionForm,
    ISOAcknowledgementForm,
    ISOCommentTransitionForm,
    ISOEvaluationForm,
    ISCApprovalForm,
    ISOReviewForm,
    PasswordResetRequestForm,
    TicketAssigneesForm,
    TicketForm,
    TicketRaisedOnForm,
    TicketTagForm,
    TicketTypeForm,
)
from helpdesk.methods import is_department_manager
from helpdesk.models import (
    FAQ,
    ISO_GROUP_NAME,
    ISC_GROUP_NAME,
    ACCESS_REQUEST_STATUS_CHOICES,
    EXCEPTION_REQUEST_STATUS_CHOICES,
    ADMIN_ACCESS_REQUEST_STATUS_CHOICES,
    INCIDENT_REPORT_STATUS_CHOICES,
    CHANGE_REQUEST_STATUS_CHOICES,
    ISO_STATUS_CHOICES,
    TICKET_STATUS,
    AccessRequest,
    AdminAccessRequest,
    Attachment,
    ChangeRequest,
    ClaimRequest,
    Comment,
    DepartmentManager,
    ExceptionRequest,
    FAQCategory,
    IncidentReport,
    PasswordResetRequest,
    Ticket,
    TicketType,
)
from helpdesk.threading import (
    AddAssigneeThread,
    PasswordResetMailThread,
    RemoveAssigneeThread,
    TicketSendThread,
)
from horilla.decorators import (
    hx_request_required,
    login_required,
    manager_can_enter,
    permission_required,
)
from horilla.group_by import group_by_queryset
from horilla_audit.methods import log_activity, log_form_changes
from notifications.signals import notify

logger = logging.getLogger(__name__)

# Create your views here.


def _helpdesk_client_ip(request):
    """Best-effort client IP for audit logs."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _ticket_ref(ticket):
    """Human ticket reference like 'IT001' (prefix + zero-padded id)."""
    try:
        return f"{ticket.ticket_type.prefix}{ticket.id:03d}"
    except Exception:
        return str(getattr(ticket, "id", ""))


def _helpdesk_audit(request, action, ticket=None, changes=None):
    """Write a Help Desk audit entry. Captures ticket ref, requester and IP.

    `changes` is an optional dict of extra detail (e.g. {"status": {"from":..,"to":..}}).
    Never raises (log_activity swallows errors) so it is safe in any view.
    """
    detail = {}
    if ticket is not None:
        detail["ticket"] = _ticket_ref(ticket)
        try:
            detail["requester"] = ticket.employee_id.get_full_name()
        except Exception:
            pass
    if changes:
        detail.update(changes)
    ip = _helpdesk_client_ip(request)
    if ip:
        detail["ip_address"] = ip
    log_activity(
        getattr(request, "user", None),
        module="helpdesk",
        action=action,
        target=ticket,
        changes=detail or None,
    )


@login_required
def faq_category_view(request):
    """
    This function is responsible for rendering the FAQ category view.

    Parameters:
        request (HttpRequest): The HTTP request object.
    """

    faq_categories = FAQCategory.objects.all()
    questions = FAQ.objects.values_list("question", flat=True)
    context = {
        "faq_categories": faq_categories,
        "questions": list(questions),
    }

    return render(request, "helpdesk/faq/faq_view.html", context=context)


@login_required
@hx_request_required
@permission_required("helpdesk.add_faqcategory")
def faq_category_create(request):
    """
    This function is responsible for creating the FAQ Category.

    Parameters:
        request (HttpRequest): The HTTP request object.

    Returns:
    GET : return faq category create form template
    POST : return faq category view
    """

    form = FAQCategoryForm()
    if request.method == "POST":
        form = FAQCategoryForm(request.POST)
        if form.is_valid():
            faq_category = form.save()
            _helpdesk_audit(
                request,
                "FAQ category created",
                None,
                {"category": faq_category.title},
            )
            messages.success(request, _("The FAQ Category created successfully."))
            form = FAQCategoryForm()

    context = {
        "form": form,
    }
    return render(request, "helpdesk/faq/faq_category_create.html", context)


@login_required
@hx_request_required
@permission_required("helpdesk.change_faqcategory")
def faq_category_update(request, id):
    """
    This function is responsible for updating the FAQ.

    Parameters:
        request (HttpRequest): The HTTP request object.
        id : id of the faq to update.

    Returns:
    GET : return faq create form template
    POST : return faq view
    """

    faq_category = FAQCategory.objects.get(id=id)
    form = FAQCategoryForm(instance=faq_category)
    if request.method == "POST":
        form = FAQCategoryForm(request.POST, instance=faq_category)
        if form.is_valid():
            form.save()
            log_form_changes(
                request.user,
                "helpdesk",
                "FAQ category updated",
                form,
                target=faq_category,
            )
            messages.success(request, _("The FAQ category updated successfully."))

    context = {
        "form": form,
        "faq_category": faq_category,
    }
    return render(request, "helpdesk/faq/faq_category_create.html", context)


@login_required
@permission_required("helpdesk.delete_faqcategory")
def faq_category_delete(request, id):
    try:
        faq = FAQCategory.objects.get(id=id)
        category_title = faq.title
        faq.delete()
        _helpdesk_audit(
            request,
            "FAQ category deleted",
            None,
            {"category": category_title},
        )
        messages.success(request, _("The FAQ category has been deleted successfully."))
        return HttpResponse("")
    except ProtectedError:
        messages.error(request, _("You cannot delete this FAQ category."))
    return HttpResponse("<script>window.location.reload()</script>")


@login_required
@hx_request_required
def faq_category_search(request):
    """
    This function is responsible for search and filter the FAQ.

    Parameters:
        request (HttpRequest): The HTTP request object.

    Returns:
    GET : return faq filter form template
    POST : return faq view
    """

    previous_data = request.GET.urlencode()
    faq_categories = FAQCategoryFilter(request.GET).qs
    data_dict = parse_qs(previous_data)
    get_key_instances(FAQCategory, data_dict)
    context = {
        "faq_categories": faq_categories,
        "f": FAQCategoryFilter(request.GET),
        "pd": previous_data,
        "filter_dict": data_dict,
    }
    return render(request, "helpdesk/faq/faq_category_list.html", context)


@login_required
def faq_view(request, obj_id, **kwargs):
    """
    This function is responsible for rendering the FAQ view.

    Parameters:
        request (HttpRequest): The HTTP request object.
        obj_id (int): The id of the the faq category.
    """

    faqs = FAQ.objects.filter(category=obj_id)
    faq_category = FAQCategory.objects.filter(id=obj_id)
    if not faq_category:
        messages.info(request, _("No FAQ found for the given category."))
        return redirect(faq_category_view)
    context = {
        "faqs": faqs,
        "f": FAQFilter(request.GET),
        "cat_id": obj_id,
        "create_tag_f": TagsForm(),
    }

    return render(request, "helpdesk/faq/faq_list_view.html", context=context)


@login_required
@hx_request_required
@permission_required("helpdesk.add_faq")
def create_faq(request, obj_id):
    """
    This function is responsible for creating the FAQ.

    Parameters:
        request (HttpRequest): The HTTP request object.

    Returns:
    GET : return faq create form template
    POST : return faq view
    """

    form = FAQForm(initial={"category": obj_id})
    if request.method == "POST":
        form = FAQForm(request.POST)
        if form.is_valid():
            faq = form.save()
            _helpdesk_audit(
                request,
                "FAQ created",
                None,
                {
                    "question": faq.question,
                    "category": str(faq.category),
                },
            )
            messages.success(request, _("The FAQ created successfully."))

    context = {
        "form": form,
        "cat_id": obj_id,
    }
    return render(request, "helpdesk/faq/faq_create.html", context)


@login_required
@hx_request_required
@permission_required("helpdesk.change_faq")
def faq_update(request, obj_id):
    """
    This function is responsible for updating the FAQ.

    Parameters:
        request (HttpRequest): The HTTP request object.
        id : id of the faq to update.

    Returns:
    GET : return faq create form template
    POST : return faq view
    """

    faq = FAQ.objects.get(id=obj_id)
    form = FAQForm(instance=faq)
    if request.method == "POST":
        form = FAQForm(request.POST, instance=faq)
        if form.is_valid():
            form.save()
            log_form_changes(
                request.user,
                "helpdesk",
                "FAQ updated",
                form,
                target=faq,
            )
            messages.success(request, _("The FAQ updated successfully."))
    context = {
        "form": form,
        "faq": faq,
        "cat_id": faq.category.id,
    }
    return render(request, "helpdesk/faq/faq_create.html", context)


@login_required
@hx_request_required
def faq_search(request):
    """
    This function is responsible for search and filter the FAQ.

    Parameters:
        request (HttpRequest): The HTTP request object.

    Returns:
    GET : return faq filter form template
    POST : return faq view
    """
    id = request.GET.get("cat_id", "")
    category = request.GET.get("category", "")
    previous_data = request.GET.urlencode()
    query = request.GET.get("search", "")
    data_dict = parse_qs(previous_data)
    get_key_instances(FAQ, data_dict)

    if query:
        faqs = FaqSearch(request.GET).qs

    else:
        faqs = FAQ.objects.filter(is_active=True)
        if category:
            return redirect(faq_category_search)

    if id:
        data_dict.pop("cat_id")
        faqs = faqs.filter(category=id)
    if category:
        data_dict.pop("category")

    context = {
        "faqs": faqs,
        "f": FAQFilter(request.GET),
        "pd": previous_data,
        "filter_dict": data_dict,
        "query": query,
    }
    return render(request, "helpdesk/faq/faq_list.html", context)


@login_required
@hx_request_required
def faq_filter(request, id):
    """
    This function is responsible for filter the FAQ.

    Parameters:
        request (HttpRequest): The HTTP request object.

    Returns:
    GET : return faq filter form template
    POST : return faq view
    """

    previous_data = request.GET.urlencode()
    faqs = FAQFilter(request.GET).qs
    faqs = faqs.filter(category=id)
    data_dict = parse_qs(previous_data)
    get_key_instances(FAQ, data_dict)
    context = {
        "faqs": faqs,
        "f": FAQFilter(request.GET),
        "pd": previous_data,
        "filter_dict": data_dict,
    }
    return render(request, "helpdesk/faq/faq_list.html", context)


@login_required
def faq_suggestion(request):
    faqs = FAQFilter(request.GET).qs
    data_list = list(faqs.values())
    response = {
        "faqs": data_list,
    }
    return JsonResponse(response)


@login_required
@permission_required("helpdesk.delete_faq")
def faq_delete(request, id):
    try:
        faq = FAQ.objects.get(id=id)
        cat_id = faq.category.id
        faq_question = faq.question
        faq_category = str(faq.category)
        faq.delete()
        _helpdesk_audit(
            request,
            "FAQ deleted",
            None,
            {"question": faq_question, "category": faq_category},
        )
        messages.success(
            request, _('The FAQ "{}" has been deleted successfully.').format(faq)
        )
        return HttpResponse("")
    except ProtectedError:
        messages.error(request, _("You cannot delete this FAQ."))
    return HttpResponse("<script>window.location.reload()</script>")


@login_required
def ticket_view(request):
    """
    This function is responsible for rendering the Ticket view.

    Parameters:
        request (HttpRequest): The HTTP request object.
    """
    tickets = Ticket.objects.filter(is_active=True)
    view = request.GET.get("view") if request.GET.get("view") else "list"
    employee = request.user.employee_get
    previous_data = request.GET.urlencode()
    my_page_number = request.GET.get("my_page")
    all_page_number = request.GET.get("all_page")

    my_tickets = tickets.filter(employee_id=employee) | tickets.filter(
        created_by=request.user
    )
    all_tickets = []
    if is_reportingmanager(request):
        all_tickets = filtersubordinates(request, tickets, "helpdesk.view_ticket")
    if request.user.has_perm("helpdesk.view_ticket") or _is_helpdesk_admin(
        request.user
    ):
        all_tickets = tickets

    data_dict = parse_qs(previous_data)
    get_key_instances(Ticket, data_dict)
    template = "helpdesk/ticket/ticket_view.html"
    context = {
        "my_tickets": paginator_qry(my_tickets, my_page_number),
        "all_tickets": paginator_qry(all_tickets, all_page_number),
        "f": TicketFilter(request.GET),
        "gp_fields": TicketReGroup.fields,
        "ticket_status": TICKET_STATUS,
        "view": view,
        "today": datetime.today().date(),
        "filter_dict": data_dict,
        "is_helpdesk_admin": _is_helpdesk_admin(request.user),
    }

    return render(request, template, context=context)


@login_required
@hx_request_required
def ticket_create(request):
    """
    This function is responsible for creating the Ticket.

    Parameters:
        request (HttpRequest): The HTTP request object.

    Returns:
    GET : return Ticket create form template
    POST : return Ticket view
    """

    form = TicketForm()
    if request.GET.get("status"):
        status = request.GET.get("status")
        form = TicketForm(
            initial={
                "status": status,
            }
        )
    if request.method == "POST":
        form = TicketForm(request.POST, request.FILES)
        if form.is_valid():
            ticket = form.save()
            attachments = form.files.getlist("attachment")
            for attachment in attachments:
                attachment_instance = Attachment(file=attachment, ticket=ticket)
                attachment_instance.save()
            _helpdesk_audit(
                request,
                "Ticket created",
                ticket,
                {
                    "title": ticket.title,
                    "priority": ticket.get_priority_display(),
                    "status": ticket.get_status_display(),
                    "type": str(ticket.ticket_type),
                },
            )
            mail_thread = TicketSendThread(request, ticket, type="create")
            mail_thread.start()
            messages.success(request, _("The Ticket created successfully."))
            employees = ticket.assigned_to.all()
            assignees = [employee.employee_user_id for employee in employees]
            assignees.append(ticket.employee_id.employee_user_id)
            if hasattr(ticket.get_raised_on_object(), "dept_manager"):
                if ticket.get_raised_on_object().dept_manager.all():
                    manager = (
                        ticket.get_raised_on_object().dept_manager.all().first().manager
                    )
                    assignees.append(manager.employee_user_id)
            notify.send(
                request.user.employee_get,
                recipient=assignees,
                verb="You have been assigned to a new Ticket",
                verb_ar="لقد تم تعيينك لتذكرة جديدة",
                verb_de="Ihnen wurde ein neues Ticket zugewiesen",
                verb_es="Se te ha asignado un nuevo ticket",
                verb_fr="Un nouveau ticket vous a été attribué",
                icon="infinite",
                redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
            )
            return HttpResponse("<script>window.location.reload()</script>")
    context = {
        "form": form,
        "t_type_form": TicketTypeForm(),
    }
    return render(request, "helpdesk/ticket/ticket_form.html", context)


@login_required
@hx_request_required
def ticket_update(request, ticket_id):
    """
    This function is responsible for updating the Ticket.

    Parameters:
        request (HttpRequest): The HTTP request object.
        ticket_id : id of the ticket to update.
    Returns:
    GET : return Ticket update form template
    POST : return Ticket view
    """

    ticket = Ticket.objects.get(id=ticket_id)

    # Block editing if this ticket has a password reset request that has been reviewed
    pr_request = getattr(ticket, "password_reset_request", None)
    if pr_request and pr_request.iso_status != "PENDING" and not request.user.is_superuser:
        messages.info(
            request,
            _("This ticket is linked to a password reset request that has already been reviewed and cannot be edited."),
        )
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        return HttpResponse(
            f'<script>window.location.href = "{request.META.get("HTTP_REFERER", "/")}"</script>'
        )

    if (
        request.user.has_perm("helpdesk.change_ticket")
        or is_department_manager(request, ticket)
        or request.user.employee_get == ticket.employee_id
        or request.user.employee_get in ticket.assigned_to.all()
        or _is_helpdesk_admin(request.user)
    ):
        form = TicketForm(instance=ticket)
        if request.method == "POST":
            form = TicketForm(request.POST, request.FILES, instance=ticket)
            # Snapshot BEFORE is_valid(): a ModelForm's full_clean mutates
            # form.instance (== ticket) with the submitted values, so capturing
            # after validation would read the new values and the diff would be empty.
            pre = {
                "title": ticket.title,
                "priority": ticket.get_priority_display(),
                "status": ticket.get_status_display(),
                "deadline": str(ticket.deadline),
            }
            if form.is_valid():
                ticket = form.save()
                attachments = form.files.getlist("attachment")
                for attachment in attachments:
                    attachment_instance = Attachment(file=attachment, ticket=ticket)
                    attachment_instance.save()
                post = {
                    "title": ticket.title,
                    "priority": ticket.get_priority_display(),
                    "status": ticket.get_status_display(),
                    "deadline": str(ticket.deadline),
                }
                diff = {
                    field: {"from": pre[field], "to": post[field]}
                    for field in pre
                    if pre[field] != post[field]
                }
                if diff:
                    _helpdesk_audit(request, "Ticket updated", ticket, diff)
                messages.success(request, _("The Ticket updated successfully."))
                return HttpResponse("<script>window.location.reload()</script>")
        context = {
            "form": form,
            "ticket_id": ticket_id,
            "t_type_form": TicketTypeForm(),
        }
        return render(request, "helpdesk/ticket/ticket_form.html", context)
    else:
        messages.info(request, _("You don't have permission."))

        previous_url = request.META.get("HTTP_REFERER", "/")

        # Handle request for HTMX if needed
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        else:
            return HttpResponse(
                f'<script>window.location.href = "{previous_url}"</script>'
            )


@login_required
def ticket_archive(request, ticket_id):
    """
    This function is responsible for archiving the Ticket.

    Parameters:
        request (HttpRequest): The HTTP request object.
        ticket_id : id of the ticket to update.
    Returns:
        return Ticket view
    """

    ticket = Ticket.objects.get(id=ticket_id)

    # Check if the user has permission or is the employee or their reporting manager
    if (
        request.user.has_perm("helpdesk.change_ticket")
        or ticket.employee_id == request.user.employee_get
        or is_department_manager(request, ticket)
        or _is_helpdesk_admin(request.user)
    ):

        # Toggle the ticket's active state
        ticket.is_active = not ticket.is_active
        ticket.save()

        _helpdesk_audit(
            request,
            "Ticket un-archived" if ticket.is_active else "Ticket archived",
            ticket,
        )

        if ticket.is_active:
            messages.success(request, _("The Ticket un-archived successfully."))
        else:
            messages.success(request, _("The Ticket archived successfully."))

        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    else:
        messages.info(request, _("You don't have permission."))

        previous_url = request.META.get("HTTP_REFERER", "/")

        # Handle request for HTMX if needed
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        else:
            return HttpResponse(
                f'<script>window.location.href = "{previous_url}"</script>'
            )


@login_required
# @ticket_owner_can_enter(perm="helpdesk.change_ticket", model=Ticket)
def change_ticket_status(request, ticket_id):
    """
    This function is responsible for changing the Ticket status.

    Parameters:
    request (HttpRequest): The HTTP request object.
    ticket_id (int): The ID of the Ticket

    Returns:
        return Ticket view
    """
    ticket = Ticket.objects.get(id=ticket_id)

    # Block status change if this ticket has a reviewed password reset request
    pr_request = getattr(ticket, "password_reset_request", None)
    if pr_request and pr_request.iso_status != "PENDING" and not request.user.is_superuser:
        return JsonResponse(
            {
                "type": "danger",
                "message": str(
                    _(
                        "This ticket is linked to a password reset request that has "
                        "already been reviewed. Status cannot be changed."
                    )
                ),
                "errors": "noChange",
            }
        )

    pre_status = ticket.get_status_display()
    status = request.POST.get("status")
    user = request.user.employee_get
    # Default response so the view never raises a NameError when the submitted
    # status equals the current one (neither branch below would assign it).
    response = {
        "type": "info",
        "message": _("The ticket status is unchanged."),
    }
    if ticket.status != status:
        if (
            user == ticket.employee_id
            or user in ticket.assigned_to.all()
            or request.user.has_perm("helpdesk.change_ticket")
            or _is_helpdesk_admin(request.user)
        ):
            ticket.status = status
            ticket.save()
            cur_status_display = ticket.get_status_display()
            if status == "resolved":
                status_action = "Ticket resolved"
            elif status == "closed":
                status_action = "Ticket closed"
            elif pre_status in (
                dict(TICKET_STATUS).get("resolved"),
                dict(TICKET_STATUS).get("closed"),
            ) and status in ("new", "in_progress", "on_hold"):
                status_action = "Ticket reopened"
            else:
                status_action = "Ticket status changed"
            status_changes = {"status": {"from": pre_status, "to": cur_status_display}}
            # SLA impact: when resolving/closing, record the deadline and flag a
            # breach if the ticket is being closed out after its deadline.
            if status in ("resolved", "closed") and ticket.deadline:
                resolved_on = datetime.now().date()
                status_changes["sla_deadline"] = str(ticket.deadline)
                status_changes["resolved_on"] = str(resolved_on)
                status_changes["sla_breached"] = resolved_on > ticket.deadline
            _helpdesk_audit(
                request,
                status_action,
                ticket,
                status_changes,
            )
            time = datetime.now()
            time = time.strftime("%b. %d, %Y, %I:%M %p")
            response = {
                "type": "success",
                "message": _("The Ticket status updated successfully."),
                "user": user.get_full_name(),
                "pre_status": pre_status,
                "cur_status": ticket.get_status_display(),
                "time": time,
            }
            employees = ticket.assigned_to.all()
            assignees = [employee.employee_user_id for employee in employees]
            assignees.append(ticket.employee_id.employee_user_id)
            if hasattr(ticket.get_raised_on_object(), "dept_manager"):
                if ticket.get_raised_on_object().dept_manager.all():
                    manager = (
                        ticket.get_raised_on_object().dept_manager.all().first().manager
                    )
                    assignees.append(manager.employee_user_id)
            notify.send(
                request.user.employee_get,
                recipient=assignees,
                verb=f"The status of the ticket has been changed to {ticket.status}.",
                verb_ar="تم تغيير حالة التذكرة.",
                verb_de="Der Status des Tickets wurde geändert.",
                verb_es="El estado del ticket ha sido cambiado.",
                verb_fr="Le statut du ticket a été modifié.",
                icon="infinite",
                redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
            )
            mail_thread = TicketSendThread(
                request,
                ticket,
                type="status_change",
            )
            mail_thread.start()
        else:
            response = {
                "type": "danger",
                "message": _("You Don't have the permission."),
            }

    if ticket.status == "resolved":
        ticket.resolved_date = datetime.today()
    return JsonResponse(response)


@login_required
@ticket_owner_can_enter(perm="helpdesk.delete_ticket", model=Ticket)
def ticket_delete(request, ticket_id):
    """
    This function is responsible for deleting the Ticket.

    Parameters:
    request (HttpRequest): The HTTP request object.
    ticket_id (int): The ID of the Ticket

    Returns:
    return Ticket view
    """
    try:
        ticket = Ticket.objects.get(id=ticket_id)
        if ticket.status == "new":
            mail_thread = TicketSendThread(
                request,
                ticket,
                type="delete",
            )
            mail_thread.start()
            employees = ticket.assigned_to.all()
            assignees = [employee.employee_user_id for employee in employees]
            assignees.append(ticket.employee_id.employee_user_id)
            if hasattr(ticket.get_raised_on_object(), "dept_manager"):
                if ticket.get_raised_on_object().dept_manager.all():
                    manager = (
                        ticket.get_raised_on_object().dept_manager.all().first().manager
                    )
                    assignees.append(manager.employee_user_id)
            notify.send(
                request.user.employee_get,
                recipient=assignees,
                verb=f"The ticket has been deleted.",
                verb_ar="تم حذف التذكرة.",
                verb_de="Das Ticket wurde gelöscht",
                verb_es="El billete ha sido eliminado.",
                verb_fr="Le ticket a été supprimé.",
                icon="infinite",
                redirect=reverse("ticket-view"),
            )
            ref = _ticket_ref(ticket)
            try:
                requester = ticket.employee_id.get_full_name()
            except Exception:
                requester = ""
            pre_status_display = ticket.get_status_display()
            ticket.delete()
            log_activity(
                request.user,
                module="helpdesk",
                action="Ticket deleted",
                changes={
                    "ticket": ref,
                    "requester": requester,
                    "status": pre_status_display,
                    "ip_address": _helpdesk_client_ip(request),
                },
            )
            messages.success(
                request,
                _('The Ticket "{}" has been deleted successfully.').format(ticket),
            )
        else:
            messages.error(request, _('The ticket is not in the "New" status'))
    except ProtectedError:
        messages.error(request, _("You cannot delete this Ticket."))
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))



@login_required
@hx_request_required
def ticket_filter(request):
    """
    This function is responsible for search and filter the Ticket.

    Parameters:
        request (HttpRequest): The HTTP request object.

    Returns:
    GET : return ticket filter form template
    POST : return ticket view
    """
    previous_data = request.GET.urlencode()
    tickets = TicketFilter(request.GET).qs
    my_page_number = request.GET.get("my_page")
    all_page_number = request.GET.get("all_page")

    my_tickets = tickets.filter(employee_id=request.user.employee_get) | tickets.filter(
        created_by=request.user
    )

    all_tickets = tickets.filter(is_active=True)
    all_tickets = filtersubordinates(request, tickets, "helpdesk.add_tickets")
    if request.user.has_perm("helpdesk.view_ticket") or _is_helpdesk_admin(
        request.user
    ):
        all_tickets = tickets

    template = "helpdesk/ticket/ticket_list.html"

    if request.GET.get("view") == "card":
        template = "helpdesk/ticket/ticket_card.html"
    if request.GET.get("sortby"):
        all_tickets = sortby(request, all_tickets, "sortby")
        my_tickets = sortby(request, my_tickets, "sortby")

    field = request.GET.get("field")
    if field != "" and field is not None:
        my_tickets = group_by_queryset(
            my_tickets, field, request.GET.get("my_page"), "my_page"
        )
        all_tickets = group_by_queryset(
            all_tickets, field, request.GET.get("all_page"), "all_page"
        )
        template = "helpdesk/ticket/ticket_group.html"
    else:
        my_tickets = paginator_qry(my_tickets, my_page_number)
        all_tickets = paginator_qry(all_tickets, all_page_number)

    data_dict = parse_qs(previous_data)
    get_key_instances(Ticket, data_dict)
    context = {
        "my_tickets": my_tickets,
        "all_tickets": all_tickets,
        "f": TicketFilter(request.GET),
        "pd": previous_data,
        "ticket_status": TICKET_STATUS,
        "filter_dict": data_dict,
        "field": field,
        "today": datetime.today().date(),
    }

    return render(request, template, context)


def _suppress_initial_set_changes(trackings):
    for history in trackings:
        changes = history.get("changes")
        if not changes:
            continue
        history["changes"] = [
            change for change in changes if change.get("old") not in (None, "")
        ]
    return trackings


@login_required
def ticket_detail(request, ticket_id, **kwargs):
    ticket = Ticket.objects.entire().filter(id=ticket_id).first()
    if ticket is None:
        raise Http404("Ticket not found")
    # A ticket can be "forwarded" to one or more individuals by storing their
    # employee ids in ``raised_on`` (the model labels this field "Forward To").
    # Anyone the ticket is forwarded to must be able to open it, including ISO
    # officers and IS Council members who receive forwarded requests.
    is_forwarded_recipient = False
    if ticket.assigning_type == "individual":
        current_employee = getattr(request.user, "employee_get", None)
        if current_employee is not None:
            is_forwarded_recipient = str(current_employee.id) in (
                ticket._parse_raised_on_ids()
            )
    # Allow ISO officers to view password reset tickets
    is_iso = request.user.is_superuser or _is_iso_officer(request.user)
    has_pr = hasattr(ticket, "password_reset_request")
    # Check if the user is a forward_to recipient for a password reset request
    pr = getattr(ticket, "password_reset_request", None)
    is_forward_to_user = (
        pr is not None and pr.forward_to.filter(pk=request.user.pk).exists()
    )
    access_request = getattr(ticket, "access_request", None)
    has_ar = access_request is not None
    is_ar_forward_to_user = (
        has_ar and access_request.forward_to.filter(pk=request.user.pk).exists()
    )
    # Exception request visibility: ISO officers (Stage 1), IS Council members
    # (Stage 2), and forward_to recipients must be able to open the ticket.
    is_isc = request.user.is_superuser or _is_isc_member(request.user)
    exception_request = getattr(ticket, "exception_request", None)
    has_er = exception_request is not None
    is_er_forward_to_user = (
        has_er and exception_request.forward_to.filter(pk=request.user.pk).exists()
    )
    admin_access_request = getattr(ticket, "admin_access_request", None)
    has_aar = admin_access_request is not None
    is_aar_forward_to_user = (
        has_aar and admin_access_request.forward_to.filter(pk=request.user.pk).exists()
    )
    # Incident Report visibility: IS Council members drive the workflow and
    # forward_to recipients must be able to open the ticket.
    incident_report = getattr(ticket, "incident_report", None)
    has_inc = incident_report is not None
    is_inc_forward_to_user = (
        has_inc and incident_report.forward_to.filter(pk=request.user.pk).exists()
    )
    # Change Request visibility: ISO officers (Stage 2) and ISC members
    # (Stage 1 Divisional Head + Stage 3) drive the workflow; forward_to
    # recipients must also be able to open the ticket.
    change_request = getattr(ticket, "change_request", None)
    has_cr = change_request is not None
    is_cr_forward_to_user = (
        has_cr and change_request.forward_to.filter(pk=request.user.pk).exists()
    )
    # ISO officers and IS Council members can open a ticket only when that
    # ticket carries an ISO workflow request that requires their feedback
    # (e.g. they are the relevant reviewer) or it was explicitly forwarded to
    # them. They are intentionally NOT granted blanket access to every ticket:
    # only their own tickets plus the requests forwarded to them are visible.
    if (
        request.user.has_perm("helpdesk.view_ticket")
        or ticket.employee_id.get_reporting_manager() == request.user.employee_get
        or is_department_manager(request, ticket)
        or request.user.employee_get == ticket.employee_id
        or request.user.employee_get in ticket.assigned_to.all()
        or is_forwarded_recipient
        or _is_helpdesk_admin(request.user)
        or (has_pr and is_iso)
        or is_forward_to_user
        or (has_ar and (is_iso or is_isc))
        or is_ar_forward_to_user
        or (has_er and (is_iso or is_isc))
        or is_er_forward_to_user
        or (has_aar and (is_iso or is_isc))
        or is_aar_forward_to_user
        or (has_inc and is_isc)
        or is_inc_forward_to_user
        or (has_cr and (is_iso or is_isc))
        or is_cr_forward_to_user
    ):
        today = datetime.now().date()
        c_form = CommentForm()
        f_form = AttachmentForm()
        attachments = ticket.ticket_attachment.all()

        activity_list = []
        comments = ticket.comment.all()
        trackings = ticket.tracking()

        # Determine if this ticket has an ISO-reviewed Password Reset request
        # so we can suppress redundant audit log entries that duplicate the
        # dedicated ISO review activity entry / approval comment.
        _pr_for_audit = getattr(ticket, "password_reset_request", None)
        _has_iso_review = bool(
            _pr_for_audit
            and _pr_for_audit.reviewed_at
            and _pr_for_audit.iso_status in ("APPROVED", "REJECTED")
        )

        if _pr_for_audit:
            for h in trackings:
                changes = h.get("changes") or []
                h["changes"] = [
                    c for c in changes if c.get("field_name") != "status"
                ]

        trackings = _suppress_initial_set_changes(trackings)

        # Filter out history entries that have no visible changes
        trackings = [
            h for h in trackings
            if h.get("type", "").endswith("created") or h.get("changes")
        ]
        for comment in comments:
            activity_list.append(
                {"type": "comment", "comment": comment, "date": comment.date}
            )
        for history in trackings:
            activity_list.append(
                {
                    "type": "history",
                    "history": history,
                    "date": history["pair"][0].history_date,
                }
            )

        # Include PasswordResetRequest audit history if one exists
        password_reset_request = getattr(ticket, "password_reset_request", None)
        if password_reset_request:
            pr_trackings = password_reset_request.tracking()
            _iso_review_fields = {
                "iso_status",
                "iso_feedback",
                "reviewed_by",
                "reviewed_at",
            }
            if _has_iso_review:
                for h in pr_trackings:
                    changes = h.get("changes") or []
                    changes = [
                        c for c in changes
                        if c.get("field_name") not in _iso_review_fields
                    ]
                    h["changes"] = changes
            pr_trackings = _suppress_initial_set_changes(pr_trackings)
            # Filter out history entries that have no visible changes
            pr_trackings = [
                h for h in pr_trackings
                if h.get("changes")  # exclude "created" entry (already shown by ticket history)
            ]
            for history in pr_trackings:
                activity_list.append(
                    {
                        "type": "history",
                        "history": history,
                        "date": history["pair"][0].history_date,
                    }
                )

            # Include ISO review (approve/reject) as a timeline entry so the
            # reviewer's comment/feedback is visible when the ticket is clicked.
            if (
                password_reset_request.reviewed_at
                and password_reset_request.iso_status in ("APPROVED", "REJECTED")
            ):
                activity_list.append(
                    {
                        "type": "iso_review",
                        "pr": password_reset_request,
                        "date": password_reset_request.reviewed_at,
                    }
                )

        sorted_activity_list = sorted(activity_list, key=itemgetter("date"))

        color = "success"
        if ticket.deadline:
            remaining_days = ticket.deadline - today
            remaining = f"Due in {remaining_days.days} days"
            if remaining_days.days < 0:
                remaining = f"{abs(remaining_days.days)} days overdue"
                color = "danger"
            elif remaining_days.days == 0:
                remaining = "Due Today"
                color = "warning"
        else:
            remaining = "No deadline"

        rating = ""
        if ticket.priority == "low":
            rating = "1"
        elif ticket.priority == "medium":
            rating = "2"
        else:
            rating = "3"

        # Fetch password reset request if it exists for this ticket
        password_reset_request = getattr(ticket, "password_reset_request", None)
        iso_review_form = ISOReviewForm() if password_reset_request else None

        # Fetch access request if it exists for this ticket. Mirrors the
        # password-reset accept/reject workflow, but uses the two-stage
        # (ISO Officer → IS Council) approval procedure.
        access_request = getattr(ticket, "access_request", None)
        access_review_form = ISOReviewForm() if access_request else None

        # Fetch exception request if it exists for this ticket.
        exception_request = getattr(ticket, "exception_request", None)

        # Fetch admin access request if it exists for this ticket.
        admin_access_request = getattr(ticket, "admin_access_request", None)

        # Fetch incident report if it exists for this ticket.
        incident_report = getattr(ticket, "incident_report", None)
        incident_post_review_form = (
            IncidentPostReviewForm(instance=incident_report)
            if incident_report
            else None
        )

        # Fetch change request if it exists for this ticket. Its four-section,
        # multi-stage workflow renders section forms inline on the detail view.
        change_request = getattr(ticket, "change_request", None)
        change_implementer_form = (
            ChangeImplementerForm(instance=change_request, request=request)
            if change_request
            else None
        )
        change_iso_form = (
            ISOEvaluationForm(instance=change_request) if change_request else None
        )
        change_isc_form = (
            ISCApprovalForm(instance=change_request) if change_request else None
        )
        change_release_form = (
            ChangeReleaseForm(instance=change_request) if change_request else None
        )
        change_request_can_edit = (
            _change_request_has_edit_access(request, change_request)
            if change_request
            else False
        )

        context = {
            "ticket": ticket,
            "display_description": _get_ticket_display_description(ticket),
            "c_form": c_form,
            "f_form": f_form,
            "attachments": attachments,
            "ticket_status": TICKET_STATUS,
            "iso_status_choices": ISO_STATUS_CHOICES,
            "access_request_status_choices": ACCESS_REQUEST_STATUS_CHOICES,
            "exception_request_status_choices": EXCEPTION_REQUEST_STATUS_CHOICES,
            "admin_access_request_status_choices": ADMIN_ACCESS_REQUEST_STATUS_CHOICES,
            "incident_report_status_choices": INCIDENT_REPORT_STATUS_CHOICES,
            "change_request_status_choices": CHANGE_REQUEST_STATUS_CHOICES,
            "tag_form": TicketTagForm(instance=ticket),
            "sorted_activity_list": sorted_activity_list,
            "create_tag_f": TagsForm(),
            "color": color,
            "remaining": remaining,
            "rating": rating,
            "password_reset_request": password_reset_request,
            "iso_review_form": iso_review_form,
            "access_request": access_request,
            "access_review_form": access_review_form,
            "exception_request": exception_request,
            "admin_access_request": admin_access_request,
            "incident_report": incident_report,
            "incident_post_review_form": incident_post_review_form,
            "change_request": change_request,
            "change_implementer_form": change_implementer_form,
            "change_iso_form": change_iso_form,
            "change_isc_form": change_isc_form,
            "change_release_form": change_release_form,
            "change_request_can_edit": change_request_can_edit,
            "is_iso_officer": request.user.is_superuser or _is_iso_officer(request.user),
            "is_isc_member": request.user.is_superuser
            or _is_isc_member(request.user),
            # (Divisional Head / IS Council) and Stage 2 (ISO Officer) approvals
            # remain strictly sequential and role-separated: an ISO Officer can
            # only act AFTER the Divisional Head has approved the request.
            "access_is_divisional_head": _is_isc_member(request.user),
            "access_is_iso_officer": _is_iso_officer(request.user),
        }
        return render(request, "helpdesk/ticket/ticket_detail.html", context=context)
    else:
        messages.info(request, _("You don't have permission."))
        previous_url = request.META.get("HTTP_REFERER", "/")

        # Handle request for HTMX if needed
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        else:
            return HttpResponse(
                f'<script>window.location.href = "{previous_url}"</script>'
            )


@login_required
def ticket_individual_view(request, ticket_id):
    ticket = Ticket.objects.filter(id=ticket_id).first()
    context = {
        "ticket": ticket,
        "display_description": _get_ticket_display_description(ticket) if ticket else "",
    }
    return render(
        request, "helpdesk/ticket/ticket_individual_view.html", context=context
    )


@login_required
def view_ticket_claim_request(request, ticket_id):
    ticket = Ticket.objects.filter(id=ticket_id).first()
    if (
        request.user.has_perm("helpdesk.change_claimrequest")
        or request.user.has_perm("helpdesk.change_ticket")
        or is_department_manager(request, ticket)
    ):
        claim_requests = ticket.claimrequest_set.all()
        context = {
            "claim_requests": claim_requests,
        }
        return render(request, "helpdesk/ticket/ticket_claim_requests.html", context)
    else:
        messages.info(request, _("You don't have permission."))
        previous_url = request.META.get("HTTP_REFERER", "/")

        # Handle request for HTMX if needed
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        else:
            return HttpResponse(
                f'<script>window.location.href = "{previous_url}"</script>'
            )


@login_required
def ticket_update_tag(request):
    """
    method to update the tags of ticket
    """
    data = request.GET
    ticket = Ticket.objects.get(id=data["ticketId"])

    # Block tag change if this ticket has a reviewed password reset request
    pr_request = getattr(ticket, "password_reset_request", None)
    if pr_request and pr_request.iso_status != "PENDING" and not request.user.is_superuser:
        return JsonResponse(
            {
                "type": "danger",
                "message": str(
                    _(
                        "This ticket is linked to a password reset request that has "
                        "already been reviewed. Tags cannot be changed."
                    )
                ),
            }
        )

    if (
        request.user.has_perm("helpdesk.view_ticket")
        or request.user.employee_get == ticket.employee_id
        or is_department_manager(request, ticket)
        or _is_helpdesk_admin(request.user)
    ):
        tagids = data.getlist("selectedValues[]")
        old_tags = list(ticket.tags.values_list("title", flat=True))
        ticket.tags.clear()
        for tagId in tagids:
            tag = Tags.objects.get(id=tagId)
            ticket.tags.add(tag)
        new_tags = list(ticket.tags.values_list("title", flat=True))
        if old_tags != new_tags:
            _helpdesk_audit(
                request,
                "Ticket tags changed",
                ticket,
                {"tags": {"from": old_tags, "to": new_tags}},
            )
        response = {
            "type": "success",
            "message": _("The Ticket tag updated successfully."),
        }
        return JsonResponse(response)
    else:
        messages.info(request, _("You don't have permission."))
        previous_url = request.META.get("HTTP_REFERER", "/")

        # Handle request for HTMX if needed
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        else:
            return HttpResponse(
                f'<script>window.location.href = "{previous_url}"</script>'
            )


@login_required
@hx_request_required
def ticket_change_raised_on(request, ticket_id):
    ticket = Ticket.objects.get(id=ticket_id)

    # Block raised-on change if this ticket has a reviewed password reset request
    pr_request = getattr(ticket, "password_reset_request", None)
    if pr_request and pr_request.iso_status != "PENDING" and not request.user.is_superuser:
        messages.info(
            request,
            _("This ticket is linked to a password reset request that has already been reviewed and cannot be edited."),
        )
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        return HttpResponse(
            f'<script>window.location.href = "{request.META.get("HTTP_REFERER", "/")}"</script>'
        )

    if (
        request.user.has_perm("helpdesk.view_ticket")
        or request.user.employee_get == ticket.employee_id
        or _is_helpdesk_admin(request.user)
    ):
        form = TicketRaisedOnForm(instance=ticket)
        if request.method == "POST":
            form = TicketRaisedOnForm(request.POST, instance=ticket)
            # Capture BEFORE is_valid(): full_clean mutates form.instance
            # (== ticket).raised_on with the submitted value, so reading after
            # validation would yield the new value and the diff would be empty.
            old_raised_on = ticket.get_raised_on()
            if form.is_valid():
                form.save()
                new_raised_on = ticket.get_raised_on()
                if old_raised_on != new_raised_on:
                    _helpdesk_audit(
                        request,
                        "Ticket forwarded-to changed",
                        ticket,
                        {"forwarded_to": {"from": old_raised_on, "to": new_raised_on}},
                    )
                # Sync forward_to M2M for password reset requests
                if pr_request:
                    raised_ids = ticket._parse_raised_on_ids()
                    if raised_ids:
                        forward_users = User.objects.filter(
                            employee_get__id__in=raised_ids,
                            groups__name=ISO_GROUP_NAME,
                            is_active=True,
                        ).distinct()
                        pr_request.forward_to.set(forward_users)
                    else:
                        pr_request.forward_to.clear()
                messages.success(request, _("Responsibility updated for the Ticket"))
                return HttpResponse("<script>window.location.reload()</script>")
        return render(
            request,
            "helpdesk/ticket/forms/change_raised_on.html",
            {"form": form, "ticket_id": ticket_id},
        )
    else:
        messages.info(request, _("You don't have permission."))
        previous_url = request.META.get("HTTP_REFERER", "/")

        # Handle request for HTMX if needed
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        else:
            return HttpResponse(
                f'<script>window.location.href = "{previous_url}"</script>'
            )


@login_required
@hx_request_required
def ticket_change_assignees(request, ticket_id):
    ticket = Ticket.objects.get(id=ticket_id)

    # Block assignee change if this ticket has a reviewed password reset request
    pr_request = getattr(ticket, "password_reset_request", None)
    if pr_request and pr_request.iso_status != "PENDING" and not request.user.is_superuser:
        messages.info(
            request,
            _("This ticket is linked to a password reset request that has already been reviewed and cannot be edited."),
        )
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        return HttpResponse(
            f'<script>window.location.href = "{request.META.get("HTTP_REFERER", "/")}"</script>'
        )

    if request.user.has_perm("helpdesk.change_ticket") or is_department_manager(
        request, ticket
    ) or _is_helpdesk_admin(request.user):
        prev_assignee_ids = ticket.assigned_to.values_list("id", flat=True)
        form = TicketAssigneesForm(instance=ticket)
        if request.method == "POST":
            form = TicketAssigneesForm(request.POST, instance=ticket)
            if form.is_valid():
                selected_assignees = form.cleaned_data["assigned_to"]

                # Password reset tickets must be owned by exactly one assignee.
                if pr_request and selected_assignees.count() != 1:
                    form.add_error(
                        "assigned_to",
                        _("Password reset requests must be assigned to exactly one user."),
                    )
                    return render(
                        request,
                        "helpdesk/ticket/forms/change_assinees.html",
                        {"form": form, "ticket_id": ticket_id},
                    )

                new_assignee_ids = selected_assignees.values_list("id", flat=True)
                added_assignee_ids = set(new_assignee_ids) - set(prev_assignee_ids)
                removed_assignee_ids = set(prev_assignee_ids) - set(new_assignee_ids)
                added_assignees = Employee.objects.filter(id__in=added_assignee_ids)
                removed_assignees = Employee.objects.filter(id__in=removed_assignee_ids)

                prev_assignee_names = [
                    emp.get_full_name()
                    for emp in Employee.objects.filter(id__in=list(prev_assignee_ids))
                ]

                form.save()

                new_assignee_names = [
                    emp.get_full_name() for emp in ticket.assigned_to.all()
                ]
                if prev_assignee_names != new_assignee_names:
                    _helpdesk_audit(
                        request,
                        "Ticket assignees changed",
                        ticket,
                        {
                            "assignees": {
                                "from": prev_assignee_names,
                                "to": new_assignee_names,
                            }
                        },
                    )

                # For password reset tickets, sync the new assignee to
                # ticket.employee_id and PasswordResetRequest.user_id so
                # the old assignee loses access and the dashboard reflects
                # the change.
                if pr_request and selected_assignees.count() == 1:
                    new_employee = selected_assignees.first()
                    old_employee = ticket.employee_id

                    if new_employee != old_employee:
                        ticket.employee_id = new_employee
                        request_type_display = pr_request.get_request_type_display()
                        user_display = _format_password_reset_user(new_employee)
                        ticket.description = (
                            f"<b>{request_type_display} Details:</b><br><br>"
                            f"<b>Type:</b> {request_type_display}<br>"
                            f"<b>Platform:</b> {pr_request.platform}<br>"
                            f"<b>User:</b> {user_display}<br>"
                            f"<b>Reason:</b> {pr_request.reason}"
                        )
                        ticket.save()

                        # Update the email stored on the PR request, mirroring
                        # the fallback chain used in PasswordResetRequestForm.save()
                        # so we never persist a blank email when a company
                        # email is not configured on the employee.
                        new_email = ""
                        try:
                            new_email = (
                                new_employee.employee_work_info.email or ""
                            )
                        except Exception:
                            new_email = ""
                        if not new_email:
                            try:
                                new_email = new_employee.employee_user_id.email or ""
                            except Exception:
                                pass
                        if not new_email:
                            try:
                                new_email = getattr(new_employee, "email", "") or ""
                            except Exception:
                                pass
                        if new_email:
                            pr_request.user_id = new_email
                            pr_request.save()

                        # Note: The owner/assignee change is already recorded
                        # automatically by horilla_audit history on ticket.save(),
                        # so we intentionally do not create an extra Comment here
                        # to avoid duplicate audit log entries on the request view.

                mail_thread = AddAssigneeThread(
                    request,
                    ticket,
                    added_assignees,
                )
                mail_thread.start()
                mail_thread = RemoveAssigneeThread(
                    request,
                    ticket,
                    removed_assignees,
                )
                mail_thread.start()

                messages.success(request, _("Assinees updated for the Ticket"))
                return HttpResponse("<script>window.location.reload()</script>")

        return render(
            request,
            "helpdesk/ticket/forms/change_assinees.html",
            {"form": form, "ticket_id": ticket_id},
        )
    else:
        messages.info(request, _("You don't have permission."))
        previous_url = request.META.get("HTTP_REFERER", "/")

        # Handle request for HTMX if needed
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        else:
            return HttpResponse(
                f'<script>window.location.href = "{previous_url}"</script>'
            )


@login_required
def create_tag(request):
    """
    This is an ajax method to return json response to create tag in the change tag form.
    """

    if request.method == "POST":
        form = TagsForm(request.POST)

        if form.is_valid():
            instance = form.save()
            _helpdesk_audit(request, "Ticket tag created", None, {"tag": instance.title})
            response = {
                "errors": "no_error",
                "tag_id": instance.id,
                "title": instance.title,
            }
            return JsonResponse(response)

        errors = form.errors.as_json()
        return JsonResponse({"errors": errors})


@login_required
def remove_tag(request):
    """
    This is an ajax method to  remove tag from a ticket.
    """

    data = request.GET
    ticket_id = data["ticket_id"]
    tag_id = data["tag_id"]
    try:
        ticket = Ticket.objects.get(id=ticket_id)
        tag = Tags.objects.get(id=tag_id)
        ticket.tags.remove(tag)
        _helpdesk_audit(request, "Ticket tag removed", ticket, {"tag": tag.title})
        # message = messages.success(request,_("Success"))
        message = _("success")
        type = "success"
    except:
        message = messages.error(request, _("Failed"))

    return JsonResponse({"message": message, "type": type})


@login_required
@hx_request_required
def view_ticket_document(request, doc_id):
    """
    This function used to view the uploaded document in the modal.
    Parameters:

    request (HttpRequest): The HTTP request object.
    id (int): The id of the document.

    Returns: return view_file template
    """

    document_obj = Attachment.objects.filter(id=doc_id).first()
    context = {
        "document": document_obj,
    }
    if document_obj.file:
        file_path = document_obj.file.path
        file_extension = os.path.splitext(file_path)[1][
            1:
        ].lower()  # Get the lowercase file extension
        content_type = get_content_type(file_extension)
        try:
            with open(file_path, "rb") as file:
                file_content = file.read()  # Decode the binary content for display
        except:
            file_content = None

        context["file_content"] = file_content
        context["file_extension"] = file_extension
        context["content_type"] = content_type
    return render(request, "helpdesk/ticket/ticket_document.html", context)


@login_required
@hx_request_required
def delete_ticket_document(request, doc_id):
    """
    This function used to delete the uploaded document in the modal.
    Parameters:

    request (HttpRequest): The HTTP request object.
    id (int): The id of the document.

    """
    attachment = Attachment.objects.get(id=doc_id)
    doc_ticket = attachment.ticket
    doc_name = os.path.basename(attachment.file.name) if attachment.file else ""
    attachment.delete()
    _helpdesk_audit(
        request,
        "Ticket attachment deleted",
        doc_ticket,
        {"attachment": doc_name} if doc_name else None,
    )
    messages.success(request, _("Document has been deleted."))
    return HttpResponse("", status=200)


@login_required
def comment_create(request, ticket_id):
    """ "
    This method is used to create comment to a ticket
    """
    if request.method == "POST":
        ticket = Ticket.objects.get(id=ticket_id)
        comment_text = request.POST.get("comment", "").strip()
        has_files = bool(request.FILES)

        # Validate files before processing
        if has_files:
            from helpdesk.forms import ALLOWED_FILE_EXTENSIONS, MAX_FILE_SIZE_MB

            files = request.FILES.getlist("file")
            max_size = MAX_FILE_SIZE_MB * 1024 * 1024
            for f in files:
                ext = os.path.splitext(f.name)[1].lower()
                if ext not in ALLOWED_FILE_EXTENSIONS:
                    messages.error(
                        request,
                        _("File '%(name)s' has an unsupported type '%(ext)s'. Allowed: %(allowed)s")
                        % {"name": f.name, "ext": ext, "allowed": ", ".join(ALLOWED_FILE_EXTENSIONS)},
                    )
                    return redirect(ticket_detail, ticket_id=ticket_id)
                if f.size > max_size:
                    messages.error(
                        request,
                        _("File '%(name)s' exceeds the maximum size of %(max_size)s MB.")
                        % {"name": f.name, "max_size": MAX_FILE_SIZE_MB},
                    )
                    return redirect(ticket_detail, ticket_id=ticket_id)

        if comment_text:
            c_form = CommentForm(request.POST)
            if c_form.is_valid():
                comment = c_form.save(commit=False)
                comment.employee_id = request.user.employee_get
                comment.ticket = ticket
                comment.save()
                if has_files:
                    files = request.FILES.getlist("file")
                    for file in files:
                        a_form = AttachmentForm(
                            {"file": file, "comment": comment, "ticket": ticket}
                        )
                        a_form.save()
                _helpdesk_audit(
                    request,
                    "Comment added",
                    ticket,
                    {"comment": (comment.comment or "")[:200]},
                )
                messages.success(request, _("A new comment has been created."))
        elif has_files:
            comment = Comment(
                employee_id=request.user.employee_get,
                ticket=ticket,
                is_auto_generated=True,
            )
            comment.save()
            files = request.FILES.getlist("file")
            file_names = ", ".join(f.name for f in files)
            comment.comment = _("Attached document(s): {}").format(file_names)
            comment.save()
            for file in files:
                a_form = AttachmentForm(
                    {"file": file, "comment": comment, "ticket": ticket}
                )
                a_form.save()
            _helpdesk_audit(
                request,
                "Comment added",
                ticket,
                {"comment": (comment.comment or "")[:200]},
            )
            messages.success(request, _("Document(s) uploaded successfully."))
    return redirect(ticket_detail, ticket_id=ticket_id)


@login_required
def comment_edit(request):
    comment_id = request.POST.get("comment_id")
    new_comment = request.POST.get("new_comment")
    comment = Comment.objects.filter(id=comment_id).first()
    if not comment:
        return JsonResponse({"errors": "not_found"}, status=404)

    employee = getattr(request.user, "employee_get", None)
    is_dept_manager = False
    try:
        if comment.ticket_id:
            is_dept_manager = is_department_manager(request, comment.ticket_id)
    except Exception:
        is_dept_manager = False

    if not (
        request.user.has_perm("helpdesk.change_comment")
        or comment.employee_id == employee
        or is_dept_manager
    ):
        return JsonResponse({"errors": "permission_denied"}, status=403)

    if new_comment and len(new_comment) > 1:
        comment.comment = new_comment
        comment.save()
        _helpdesk_audit(
            request,
            "Comment edited",
            comment.ticket,
            {"comment": (comment.comment or "")[:200]},
        )
        messages.success(request, _("The comment updated successfully."))
    else:
        messages.error(request, _("The comment needs to be at least 2 characters."))
    response = {
        "errors": "no_error",
    }
    return JsonResponse(response)


@login_required
def comment_delete(request, comment_id):
    comment = Comment.objects.filter(id=comment_id).first()
    if not comment:
        messages.error(request, _("Comment not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    employee = getattr(request.user, "employee_get", None)
    is_dept_manager = False
    try:
        if comment.ticket_id:
            is_dept_manager = is_department_manager(request, comment.ticket_id)
    except Exception:
        is_dept_manager = False

    if not (
        request.user.has_perm("helpdesk.delete_comment")
        or comment.employee_id == employee
        or is_dept_manager
    ):
        messages.error(request, _("You do not have permission to delete this comment."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    employee_name = comment.employee_id
    comment_ticket = comment.ticket
    comment_text = (comment.comment or "")[:200]
    comment.delete()
    _helpdesk_audit(
        request,
        "Comment deleted",
        comment_ticket,
        {"comment": comment_text} if comment_text else None,
    )
    messages.success(request, _("{}'s comment has been deleted successfully.").format(employee_name))
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def get_raised_on(request):
    """
    This is an ajax method to return list for raised on field.
    """

    data = request.GET
    assigning_type = data["assigning_type"]

    is_password_reset = False
    ticket_id = data.get("ticket_id")
    ticket_type_id = data.get("ticket_type_id")

    if ticket_id:
        try:
            ticket = Ticket.objects.select_related("ticket_type").get(id=ticket_id)
            if hasattr(ticket, "password_reset_request"):
                is_password_reset = True
        except Ticket.DoesNotExist:
            pass

    if not is_password_reset and ticket_type_id:
        try:
            tt = TicketType.objects.get(id=ticket_type_id)
            if tt.title == "Password Reset":
                is_password_reset = True
        except (TicketType.DoesNotExist, ValueError):
            pass

    raised_on = []

    if assigning_type == "department":
        # Retrieve data from the Department model and format it as a list of dictionaries
        departments = Department.objects.values("id", "department")
        raised_on = [
            {"id": dept["id"], "name": dept["department"]} for dept in departments
        ]
    elif assigning_type == "job_position":
        jobpositions = JobPosition.objects.values("id", "job_position")
        raised_on = [
            {"id": job["id"], "name": job["job_position"]} for job in jobpositions
        ]
    elif assigning_type == "individual":
        if is_password_reset:
            # Only show employees who belong to the ISO group
            iso_group = Group.objects.filter(name=ISO_GROUP_NAME).first()
            if iso_group:
                employees = Employee.objects.filter(
                    employee_user_id__groups=iso_group,
                    is_active=True,
                ).values("id", "employee_first_name", "employee_last_name")
            else:
                # Fallback: if the group doesn't exist, show only superusers
                employees = Employee.objects.filter(
                    employee_user_id__is_superuser=True,
                    is_active=True,
                ).values("id", "employee_first_name", "employee_last_name")
        else:
            employees = Employee.objects.values(
                "id", "employee_first_name", "employee_last_name"
            )
        raised_on = [
            {
                "id": employee["id"],
                "name": f"{employee['employee_first_name']} {employee['employee_last_name']}",
            }
            for employee in employees
        ]
    response = {"raised_on": list(raised_on)}
    return JsonResponse(response)


@login_required
def claim_ticket(request, id):
    """
    This is a function to create a claim request for requested employee
    """
    ticket = Ticket.objects.get(id=id)
    if not ClaimRequest.objects.filter(
        employee_id=request.user.employee_get, ticket_id=ticket
    ).exists():
        ClaimRequest(employee_id=request.user.employee_get, ticket_id=ticket).save()
        _helpdesk_audit(
            request,
            "Ticket claim requested",
            ticket,
            {"claimed_by": request.user.employee_get.get_full_name()},
        )
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def approve_claim_request(request, req_id):
    """
    Function for approve claim request and send notifications to the responsibles.
    """
    claim_request = ClaimRequest.objects.filter(id=req_id).first()
    if not claim_request:
        return HttpResponse("Invalid claim request", status=404)

    approve = strtobool(
        request.GET.get("approve", "False")
    )  # Safely convert to boolean

    ticket = claim_request.ticket_id
    employee = claim_request.employee_id
    refresh = False
    if approve:
        # message
        message = _("Claim request approved successfully.")
        refresh = True
        if employee not in ticket.assigned_to.all():
            ticket.assigned_to.add(employee)  # Approve and assign to employee
            try:
                # send notification
                notify.send(
                    request.user.employee_get,
                    recipient=employee.employee_user_id,
                    verb=f"You have been assigned to a new Ticket-{ticket}.",
                    verb_ar=f"لقد تم تعيينك لتذكرة جديدة {ticket}.",
                    verb_de=f"Ihnen wurde ein neues Ticket {ticket} zugewiesen.",
                    verb_es=f"Se te ha asignado un nuevo ticket {ticket}.",
                    verb_fr=f"Un nouveau ticket {ticket} vous a été attribué.",
                    icon="infinite",
                    redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
                )
            except Exception as e:
                logger.error(e)
            if not ticket.employee_id == ticket.created_by.employee_get:
                for emp in [ticket.created_by.employee_get, ticket.employee_id]:
                    try:
                        notify.send(
                            request.user.employee_get,
                            recipient=emp.employee_user_id,
                            verb=f"{employee} assigned to your ticket - {ticket}.",
                            verb_ar=f"تم تعيين {employee} إلى تذكرتك - {ticket}.",
                            verb_de=f"{employee} wurde Ihrem Ticket {ticket} zugewiesen.",
                            verb_es=f"{employee} ha sido asignado a tu ticket - {ticket}.",
                            verb_fr=f"{employee} a été attribué à votre ticket - {ticket}.",
                            icon="infinite",
                            redirect=reverse(
                                "ticket-detail", kwargs={"ticket_id": ticket.id}
                            ),
                        )
                    except Exception as e:
                        logger.error(e)
            try:
                notify.send(
                    request.user.employee_get,
                    recipient=ticket.employee_id.employee_user_id,
                    verb=f"{employee} assigned to your ticket - {ticket}.",
                    verb_ar=f"تم تعيين {employee} إلى تذكرتك - {ticket}.",
                    verb_de=f"{employee} wurde Ihrem Ticket {ticket} zugewiesen.",
                    verb_es=f"{employee} ha sido asignado a tu ticket - {ticket}.",
                    verb_fr=f"{employee} a été attribué à votre ticket - {ticket}.",
                    icon="infinite",
                    redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
                )
            except Exception as e:
                logger.error(e)
    else:
        message = _("Claim request rejected successfully.")
        refresh = True
        if employee in ticket.assigned_to.all():
            ticket.assigned_to.remove(employee)
            notify.send(
                request.user.employee_get,
                recipient=employee.employee_user_id,
                verb=f"Your claim request is rejected for Ticket-{ticket}",
                verb_ar=f"تم رفض طلبك للمطالبة بالتذكرة {ticket}.",
                verb_de=f"Ihre Anspruchsanfrage für Ticket-{ticket} wurde abgelehnt.",
                verb_es=f"Tu solicitud de reclamación ha sido rechazada para el ticket {ticket}.",
                verb_fr=f"Votre demande de réclamation pour le ticket {ticket} a été rejetée.",
                icon="infinite",
            )
    ticket.save()
    claim_request.is_approved = approve
    claim_request.is_rejected = not approve
    claim_request.save()
    _helpdesk_audit(
        request,
        "Ticket claim approved" if approve else "Ticket claim rejected",
        ticket,
        {"claimed_by": employee.get_full_name()},
    )
    html = render_to_string(
        "helpdesk/ticket/ticket_claim_requests.html",
        {"claim_requests": ticket.claimrequest_set.all(), "refresh": refresh},
    )
    messages.success(request, message)
    return HttpResponse(html)


@login_required
def tickets_select_filter(request):
    """
    This method is used to return all the ids of the filtered tickets
    """
    page_number = request.GET.get("page")
    filtered = request.GET.get("filter")
    filters = json.loads(filtered) if filtered else {}
    table = request.GET.get("tableName")
    user = request.user.employee_get

    tickets_filter = TicketFilter(
        filters, queryset=Ticket.objects.filter(is_active=True)
    )
    if page_number == "all":
        if table == "all":
            tickets_filter = TicketFilter(filters, queryset=Ticket.objects.all())
        elif table == "my":
            tickets_filter = TicketFilter(
                filters, queryset=Ticket.objects.filter(employee_id=user)
            )

        # Get the filtered queryset
        filtered_tickets = tickets_filter.qs

        ticket_ids = [str(ticket.id) for ticket in filtered_tickets]
        total_count = filtered_tickets.count()

        context = {"ticket_ids": ticket_ids, "total_count": total_count}

        return JsonResponse(context)


@login_required
@permission_required("helpdesk.change_ticket")
def tickets_bulk_archive(request):
    """
    This is a ajax method used to archive bulk of Ticket instances
    """
    ids = request.POST["ids"]
    ids = json.loads(ids)
    is_active = False
    if request.GET.get("is_active") == "True":
        is_active = True
    for ticket_id in ids:
        ticket = Ticket.objects.get(id=ticket_id)
        ticket.is_active = is_active
        ticket.save()
        _helpdesk_audit(
            request,
            "Ticket un-archived" if ticket.is_active else "Ticket archived",
            ticket,
        )
    messages.success(request, _("The Ticket updated successfully."))
    previous_url = request.META.get("HTTP_REFERER", "/")
    script = f'<script>window.location.href = "{previous_url}"</script>'
    return HttpResponse(script)


@login_required
# @ticket_owner_can_enter("perms.helpdesk.helpdesk_change_ticket", Ticket)
@permission_required("helpdesk.delete_ticket")
def tickets_bulk_delete(request):
    """
    This is a ajax method used to delete bulk of Ticket instances
    """
    ids = request.POST["ids"]
    ids = json.loads(ids)
    for ticket_id in ids:
        try:
            ticket = Ticket.objects.get(id=ticket_id)
            mail_thread = TicketSendThread(
                request,
                ticket,
                type="delete",
            )
            mail_thread.start()
            employees = ticket.assigned_to.all()
            assignees = [employee.employee_user_id for employee in employees]
            assignees.append(ticket.employee_id.employee_user_id)
            if hasattr(ticket.get_raised_on_object(), "dept_manager"):
                if ticket.get_raised_on_object().dept_manager.all():
                    manager = (
                        ticket.get_raised_on_object().dept_manager.all().first().manager
                    )
                    assignees.append(manager.employee_user_id)
            notify.send(
                request.user.employee_get,
                recipient=assignees,
                verb=f"The ticket has been deleted.",
                verb_ar="تم حذف التذكرة.",
                verb_de="Das Ticket wurde gelöscht",
                verb_es="El billete ha sido eliminado.",
                verb_fr="Le ticket a été supprimé.",
                icon="infinite",
                redirect=reverse("ticket-view"),
            )
            ref = _ticket_ref(ticket)
            try:
                requester = ticket.employee_id.get_full_name()
            except Exception:
                requester = ""
            pre_status_display = ticket.get_status_display()
            ticket.delete()
            log_activity(
                request.user,
                module="helpdesk",
                action="Ticket deleted",
                changes={
                    "ticket": ref,
                    "requester": requester,
                    "status": pre_status_display,
                    "ip_address": _helpdesk_client_ip(request),
                },
            )
            messages.success(
                request,
                _('The Ticket "{}" has been deleted successfully.').format(ticket),
            )
        except ProtectedError:
            messages.error(request, _("You cannot delete this Ticket."))
    previous_url = request.META.get("HTTP_REFERER", "/")
    script = f'<script>window.location.href = "{previous_url}"</script>'
    return HttpResponse(script)


@login_required
@hx_request_required
def create_department_manager(request):
    form = DepartmentManagerCreateForm()
    if request.method == "POST":
        form = DepartmentManagerCreateForm(request.POST, request.FILES)
        if form.is_valid():
            dept_manager = form.save()
            _helpdesk_audit(
                request,
                "Department manager added",
                None,
                {
                    "manager": dept_manager.manager.get_full_name(),
                    "department": str(dept_manager.department),
                },
            )
            messages.success(request, _("The department manager created successfully."))

            return HttpResponse("<script>window.location.reload()</script>")
    context = {
        "form": form,
    }
    return render(request, "department_managers/department_managers_form.html", context)


@login_required
@hx_request_required
def update_department_manager(request, dep_id):
    department_manager = DepartmentManager.objects.get(id=dep_id)
    form = DepartmentManagerCreateForm(instance=department_manager)
    if request.method == "POST":
        form = DepartmentManagerCreateForm(request.POST, instance=department_manager)
        if form.is_valid():
            form.save()
            log_form_changes(
                request.user,
                "helpdesk",
                "Department manager updated",
                form,
                target=department_manager,
            )
            messages.success(request, _("The department manager updated successfully."))
            return HttpResponse("<script>window.location.reload()</script>")
    context = {
        "form": form,
        "dep_id": dep_id,
    }
    return render(request, "department_managers/department_managers_form.html", context)


@login_required
@permission_required("helpdesk.delete_departmentmanager")
def delete_department_manager(request, dep_id):
    department_manager = DepartmentManager.objects.get(id=dep_id)
    manager_name = department_manager.manager.get_full_name()
    department_name = str(department_manager.department)
    department_manager.delete()
    _helpdesk_audit(
        request,
        "Department manager removed",
        None,
        {"manager": manager_name, "department": department_name},
    )
    messages.success(request, _("The department manager has been deleted successfully"))

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def update_priority(request, ticket_id):
    """
    This function is used to update the priority
    from the detailed view
    """
    ticket = Ticket.objects.get(id=ticket_id)

    # Block priority change if this ticket has a reviewed password reset request
    pr_request = getattr(ticket, "password_reset_request", None)
    if pr_request and pr_request.iso_status != "PENDING" and not request.user.is_superuser:
        messages.info(
            request,
            _("This ticket is linked to a password reset request that has already been reviewed and cannot be edited."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if (
        request.user.has_perm("helpdesk.view_ticket")
        or ticket.employee_id.get_reporting_manager() == request.user.employee_get
        or is_department_manager(request, ticket)
        or request.user.employee_get == ticket.employee_id
        or request.user.employee_get in ticket.assigned_to.all()
        or _is_helpdesk_admin(request.user)
    ):
        rating = request.POST.get("rating")

        old_priority_display = ticket.get_priority_display()
        if rating == "1":
            ticket.priority = "low"
        elif rating == "2":
            ticket.priority = "medium"
        else:
            ticket.priority = "high"
        ticket.save()
        new_priority_display = ticket.get_priority_display()
        if old_priority_display != new_priority_display:
            _helpdesk_audit(
                request,
                "Ticket priority changed",
                ticket,
                {
                    "priority": {
                        "from": old_priority_display,
                        "to": new_priority_display,
                    }
                },
            )
        messages.success(request, _("Priority updated successfully."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    else:
        messages.info(request, _("You don't have permission."))
        previous_url = request.META.get("HTTP_REFERER", "/")

        # Handle request for HTMX if needed
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        else:
            return HttpResponse(
                f'<script>window.location.href = "{previous_url}"</script>'
            )


@login_required
@permission_required("helpdesk.view_tickettype")
def ticket_type_view(request):
    """
    This method is used to show Ticket type
    """
    ticket_types = TicketType.objects.all()
    return render(
        request, "base/ticket_type/ticket_type.html", {"ticket_types": ticket_types}
    )


@login_required
# @hx_request_required
@permission_required("helpdesk.create_tickettype")
def ticket_type_create(request):
    """
    This method renders form and template to create Ticket type
    """
    form = TicketTypeForm()
    if request.method == "POST":
        form = TicketTypeForm(request.POST)
        if request.GET.get("ajax"):
            if form.is_valid():
                instance = form.save()
                _helpdesk_audit(
                    request,
                    "Ticket type created",
                    None,
                    {
                        "title": instance.title,
                        "prefix": instance.prefix,
                        "type": instance.get_type_display(),
                    },
                )
                response = {
                    "errors": "no_error",
                    "ticket_id": instance.id,
                    "title": instance.title,
                }
                return JsonResponse(response)

            errors = form.errors.as_json()
            return JsonResponse({"errors": errors})
        if form.is_valid():
            instance = form.save()
            _helpdesk_audit(
                request,
                "Ticket type created",
                None,
                {
                    "title": instance.title,
                    "prefix": instance.prefix,
                    "type": instance.get_type_display(),
                },
            )
            form = TicketTypeForm()
            messages.success(request, _("Ticket type has been created successfully!"))
            return HttpResponse("<script>window.location.reload()</script>")
    return render(
        request,
        "base/ticket_type/ticket_type_form.html",
        {
            "form": form,
        },
    )


@login_required
@hx_request_required
@permission_required("helpdesk.update_tickettype")
def ticket_type_update(request, t_type_id):
    """
    This method renders form and template to create Ticket type
    """
    ticket_type = TicketType.objects.get(id=t_type_id)
    form = TicketTypeForm(instance=ticket_type)
    if request.method == "POST":
        form = TicketTypeForm(request.POST, instance=ticket_type)
        if form.is_valid():
            form.save()
            log_form_changes(
                request.user,
                "helpdesk",
                "Ticket type updated",
                form,
                target=ticket_type,
            )
            form = TicketTypeForm()
            messages.success(request, _("Ticket type has been updated successfully!"))
            return HttpResponse("<script>window.location.reload()</script>")
    return render(
        request,
        "base/ticket_type/ticket_type_form.html",
        {"form": form, "t_type_id": t_type_id},
    )


@login_required
@require_http_methods(["POST", "DELETE"])
@permission_required("helpdesk.delete_tickettype")
def ticket_type_delete(request, t_type_id):
    ticket_type = TicketType.find(t_type_id)
    if ticket_type:
        tt_title = ticket_type.title
        tt_prefix = ticket_type.prefix
        ticket_type.delete()
        _helpdesk_audit(
            request,
            "Ticket type deleted",
            None,
            {"title": tt_title, "prefix": tt_prefix},
        )
        messages.success(request, _("Ticket type has been deleted successfully!"))
    else:
        messages.error(request, _("Ticket type not found"))
    return HttpResponse()


@login_required
@permission_required("helpdesk.view_departmentmanager")
def view_department_managers(request):
    model_class = DepartmentManager
    department_managers = model_class.objects.all()

    context = {
        "model": model_class,
        "department_managers": department_managers,
    }
    return render(request, "department_managers/department_managers.html", context)


@login_required
@permission_required("helpdesk.change_departmentmanager")
def get_department_employees(request):
    """
    Method to return employee in the department
    """
    department = (
        Department.objects.filter(id=request.GET.get("dep_id")).first()
        if request.GET.get("dep_id")
        else None
    )
    if department:
        employees_queryset = department.employeeworkinformation_set.all().values_list(
            "employee_id__id", "employee_id__employee_first_name"
        )
    else:
        employees_queryset = None
    employees = list(employees_queryset)
    context = {"employees": employees}
    employee_html = render_to_string("employee/employees_select.html", context)
    return HttpResponse(employee_html)


@login_required
def load_faqs(request):
    base_dir = settings.BASE_DIR
    faq_file = os.path.join(base_dir, "load_data", "faq.json")
    faq_category_file = os.path.join(base_dir, "load_data", "faq_category.json")
    tags_file = os.path.join(base_dir, "load_data", "tags.json")

    with open(faq_category_file, "r") as cats:
        faq_category_raw = json.load(cats)

    with open(tags_file, "r") as t:
        tags_raw = json.load(t)

    with open(faq_file, "r") as faqs:
        faq_raw = json.load(faqs)

    category_lookup = {item["pk"]: item["fields"]["title"] for item in faq_category_raw}

    tag_lookup = {item["pk"]: item["fields"]["title"] for item in tags_raw}

    if request.method == "POST":
        selected_ids = [int(k) for k in request.POST.keys() if k.isdigit()]
        selected_faqs = [a for a in faq_raw if a["pk"] in selected_ids]

        category_needed = [
            faq["fields"].get("category")
            for faq in selected_faqs
            if faq["fields"].get("category")
        ]

        tags_needed_list = [
            faq["fields"].get("tags")
            for faq in selected_faqs
            if faq["fields"].get("tags")
        ]
        tags_needed = [item for item_list in tags_needed_list for item in item_list]

        for category_json in faq_category_raw:
            if category_json["pk"] in category_needed:
                category_data = list(
                    serializers.deserialize("json", json.dumps([category_json]))
                )[0].object
                existing = FAQCategory.objects.filter(title=category_data.title).first()
                if not existing:
                    category_data.pk = None
                    category_data.save()

        for tag_json in tags_raw:
            if tag_json["pk"] in tags_needed:
                tag_data = list(
                    serializers.deserialize("json", json.dumps([tag_json]))
                )[0].object
                existing = Tags.objects.filter(title=tag_data.title).first()
                if not existing:
                    tag_data.pk = None
                    tag_data.save()

        for faq_json in selected_faqs:
            deserialized = list(
                serializers.deserialize("json", json.dumps([faq_json]))
            )[0]
            faq_obj = deserialized.object

            category_pk = faq_json["fields"].get("category")
            tag_pk = faq_json["fields"].get("tags")
            category_title = category_lookup.get(category_pk)
            tags_title = [tag_lookup.get(pk, "") for pk in tag_pk]
            category = FAQCategory.objects.filter(title=category_title).first()
            tags = Tags.objects.filter(title__in=tags_title)
            faq_obj.category = category

            if not FAQ.objects.filter(question=faq_obj.question).exists():
                faq_obj.pk = None
                faq_obj.save()
                faq_obj.tags.set(tags)

                messages.success(
                    request, f"Automation '{faq_obj.question}' created successfully."
                )
            else:
                messages.warning(
                    request, f"Automation '{faq_obj.question}' already exists."
                )

        script = """
            <script>
                $('.oh-modal--show').removeClass('oh-modal--show');
                $('#reloadMessagesButton').click();
                $('.filterButton').click();
            </script>
        """
        return HttpResponse(script)

    processed_faqs = []
    for faq in faq_raw:
        processed = faq.copy()

        category_pk = faq["fields"].get("category")
        tag_pk = faq["fields"].get("tags")
        processed["tags"] = [tag_lookup.get(pk, "") for pk in tag_pk]
        processed["category"] = category_lookup.get(category_pk, "")
        processed_faqs.append(processed)

    return render(
        request,
        "helpdesk/faq/load_faq.html",
        {
            "faqs": processed_faqs,
            "catagories": category_lookup,
        },
    )

def _is_iso_officer(user):
    """Return True if the user belongs to the ISO group."""
    return user.groups.filter(name=ISO_GROUP_NAME).exists()


def _is_password_reset_request_owner(user, pr_request):
    """Return True when the authenticated user owns the password reset request."""
    current_employee = getattr(user, "employee_get", None)
    ticket_employee = getattr(pr_request.ticket, "employee_id", None)
    return bool(current_employee and ticket_employee and current_employee == ticket_employee)


def _get_iso_officer_users():
    """
    Return a list of User objects who are ISO officers (members of the ISO
    group) OR superusers. Used for sending in-app notifications.
    """
    iso_users = User.objects.filter(
        Q(groups__name=ISO_GROUP_NAME) | Q(is_superuser=True),
        is_active=True,
    ).distinct()
    return list(iso_users)


def _is_access_request_owner(user, access_request):
    """Return True when the authenticated user owns the access request."""
    current_employee = getattr(user, "employee_get", None)
    ticket_employee = getattr(access_request.ticket, "employee_id", None)
    return bool(
        current_employee and ticket_employee and current_employee == ticket_employee
    )


def _is_isc_member(user):
    """Return True if the user belongs to the IS Council (ISC) group."""
    return user.groups.filter(name=ISC_GROUP_NAME).exists()


def _is_helpdesk_admin(user):
    """
    Return True for users who should have Administrator-level access to every
    helpdesk ticket (view and review/edit), regardless of ownership or
    assignment.

    This covers Django superusers as well as ISO officers and IS Council
    members, who provide information-security oversight across the whole
    helpdesk and therefore need the same blanket access as an Administrator so
    they can review any ticket.
    """
    return bool(
        user.is_superuser or _is_iso_officer(user) or _is_isc_member(user)
    )


def _get_isc_users():
    """
    Return a list of User objects who are IS Council members (members of the
    ISC group) OR superusers. Used for in-app notifications.
    """
    isc_users = User.objects.filter(
        Q(groups__name=ISC_GROUP_NAME) | Q(is_superuser=True),
        is_active=True,
    ).distinct()
    return list(isc_users)


def _is_exception_request_owner(user, exception_request):
    """Return True when the authenticated user owns the exception request."""
    current_employee = getattr(user, "employee_get", None)
    ticket_employee = getattr(exception_request.ticket, "employee_id", None)
    return bool(
        current_employee and ticket_employee and current_employee == ticket_employee
    )


def _is_admin_access_request_owner(user, admin_access_request):
    """Return True when the authenticated user owns the admin access request."""
    current_employee = getattr(user, "employee_get", None)
    ticket_employee = getattr(admin_access_request.ticket, "employee_id", None)
    return bool(
        current_employee and ticket_employee and current_employee == ticket_employee
    )


def _get_forward_employee_ids_and_employees(users):
    """Map selected auth users or employees to employee IDs for Ticket.forwarding compatibility."""
    employee_ids = []
    employees = []
    for item in users:
        try:
            # If item is already an Employee instance, use it directly.
            if isinstance(item, Employee):
                employee_ids.append(str(item.id))
                employees.append(item)
            else:
                # item is a User; resolve via reverse relation.
                employee = item.employee_get
                if employee:
                    employee_ids.append(str(employee.id))
                    employees.append(employee)
        except Employee.DoesNotExist:
            # User has no related Employee; skip silently to preserve existing behavior.
            continue
        except Exception:
            # Log unexpected exceptions so they can be investigated instead of being hidden.
            logger.exception(
                "Unexpected error while mapping user %s to Employee in "
                "_get_forward_employee_ids_and_employees",
                getattr(item, "pk", item),
            )
            continue
    return employee_ids, employees


@login_required
def iso_forms_home(request):
    """ISO Forms landing page showing password reset requests."""

    current_employee = getattr(request.user, "employee_get", None)
    queryset = PasswordResetRequest.objects.select_related(
        "ticket",
        "ticket__employee_id",
    ).order_by("-created_at")

    if not request.user.is_superuser and not _is_iso_officer(request.user):
        if current_employee:
            queryset = queryset.filter(
                Q(ticket__employee_id=current_employee)
                | Q(ticket__assigned_to=current_employee)
                | Q(ticket__raised_on__contains=str(current_employee.id))
                | Q(forward_to=request.user)
                | Q(reviewed_by=request.user)
            ).distinct()
        else:
            queryset = queryset.none()


    iso_form_options = [
        {
            "title": _("Password Reset Request"),
            "description": _("Request password reset access for internal systems."),
            "icon": "key-outline",
            "create_url": reverse("password-reset-request-create"),
            "target": "passwordResetModalTarget",
            "modal": "passwordResetModal",
        },
        {
            "title": _("Access Request & Deactivation"),
            "description": _(
                "Request or deactivate system access through a two-stage approval."
            ),
            "icon": "shield-checkmark-outline",
            "create_url": reverse("access-request-create"),
            "target": "accessRequestModalTarget",
            "modal": "accessRequestModal",
        },
        {
            "title": _("Exception Request"),
            "description": _(
                "Request an exception to an ISMS policy or procedure."
            ),
            "icon": "alert-circle-outline",
            "create_url": reverse("exception-request-create"),
            "target": "exceptionRequestModalTarget",
            "modal": "exceptionRequestModal",
        },
        {
            "title": _("Admin Access Request"),
            "description": _(
                "Request elevated admin privileges through a two-stage approval."
            ),
            "icon": "shield-outline",
            "create_url": reverse("admin-access-request-create"),
            "target": "adminAccessRequestModalTarget",
            "modal": "adminAccessRequestModal",
        },
        {
            "title": _("Incident Report"),
            "description": _(
                "Report a security incident for IS Council review."
            ),
            "icon": "warning-outline",
            "create_url": reverse("incident-report-create"),
            "target": "incidentReportModalTarget",
            "modal": "incidentReportModal",
        },
        {
            "title": _("Change Request"),
            "description": _(
                "Request a system or process change through the change workflow."
            ),
            "icon": "git-branch-outline",
            "create_url": reverse("change-request-create"),
            "target": "changeRequestModalTarget",
            "modal": "changeRequestModal",
        },
    ]

    # ── Access Requests visible to the current user ──
    access_qs = AccessRequest.objects.select_related(
        "ticket", "ticket__employee_id"
    ).order_by("-created_at")
    is_iso = request.user.is_superuser or _is_iso_officer(request.user)
    is_isc = request.user.is_superuser or _is_isc_member(request.user)
    if not is_iso and not is_isc:
        if current_employee:
            access_qs = access_qs.filter(
                Q(ticket__employee_id=current_employee)
                | Q(ticket__assigned_to=current_employee)
                | Q(forward_to=request.user)
            ).distinct()
        else:
            access_qs = access_qs.none()

    # ── Exception Requests visible to the current user ──
    exception_qs = ExceptionRequest.objects.select_related(
        "ticket", "ticket__employee_id"
    ).order_by("-created_at")
    if not is_iso and not is_isc:
        if current_employee:
            exception_qs = exception_qs.filter(
                Q(ticket__employee_id=current_employee)
                | Q(ticket__assigned_to=current_employee)
                | Q(forward_to=request.user)
            ).distinct()
        else:
            exception_qs = exception_qs.none()

    # ── Admin Access Requests visible to the current user ──
    admin_access_qs = AdminAccessRequest.objects.select_related(
        "ticket", "ticket__employee_id"
    ).order_by("-created_at")
    if not is_iso and not is_isc:
        if current_employee:
            admin_access_qs = admin_access_qs.filter(
                Q(ticket__employee_id=current_employee)
                | Q(ticket__assigned_to=current_employee)
                | Q(forward_to=request.user)
            ).distinct()
        else:
            admin_access_qs = admin_access_qs.none()

    # ── Incident Reports visible to the current user ──
    # The Incident Report workflow is IS Council–driven, so only ISC members
    # (and superusers) get blanket visibility; everyone else sees only their
    # own reports or ones forwarded to them.
    incident_qs = IncidentReport.objects.select_related(
        "ticket", "ticket__employee_id"
    ).order_by("-created_at")
    if not request.user.is_superuser and not is_isc:
        if current_employee:
            incident_qs = incident_qs.filter(
                Q(ticket__employee_id=current_employee)
                | Q(ticket__assigned_to=current_employee)
                | Q(forward_to=request.user)
            ).distinct()
        else:
            incident_qs = incident_qs.none()

    # ── Change Requests visible to the current user ──
    # Both ISO officers (Stage 2) and IS Council members (Stage 1 Divisional
    # Head + Stage 3) have oversight; everyone else sees only their own change
    # requests, ones forwarded to them, or ones they implement.
    change_qs = ChangeRequest.objects.select_related(
        "ticket", "ticket__employee_id"
    ).order_by("-created_at")
    if not is_iso and not is_isc:
        if current_employee:
            change_qs = change_qs.filter(
                Q(ticket__employee_id=current_employee)
                | Q(ticket__assigned_to=current_employee)
                | Q(forward_to=request.user)
                | Q(implementer=current_employee)
            ).distinct()
        else:
            change_qs = change_qs.none()

    context = {
        "password_reset_requests": queryset,
        "access_requests": access_qs,
        "exception_requests": exception_qs,
        "admin_access_requests": admin_access_qs,
        "incident_reports": incident_qs,
        "change_requests": change_qs,
        "current_employee": current_employee,
        "is_iso_officer": request.user.is_superuser or _is_iso_officer(request.user),
        "is_isc_member": is_isc,
        "iso_form_options": iso_form_options,
    }
    return render(request, "helpdesk/iso_forms/index.html", context)


# PASSWORD RESET REQUEST VIEWS

def _get_password_reset_ticket_type():
    """
    Returns the TicketType for Password Reset tickets, creating it if needed.
    """
    ticket_type, _ = TicketType.objects.get_or_create(
        title="Password Reset",
        defaults={"type": "service_request", "prefix": "PWR"},
    )
    return ticket_type


def _format_password_reset_user(employee):
    """Return a clean display string for the Password Reset user."""
    if not employee:
        return ""
    try:
        # Use get_full_name() which returns only "First Last" without badge.
        full_name = (employee.get_full_name() or "").strip()
    except Exception:
        full_name = ""

    badge = getattr(employee, "badge_id", "") or ""

    if full_name and badge:
        return f"{full_name} ({badge})"
    elif full_name:
        return full_name
    elif badge:
        return badge
    # Fallback to str(employee) which already includes badge.
    return str(employee).strip()


@login_required
@hx_request_required
def password_reset_request_create(request):
    """
    GET  → renders the Password Reset modal form.
    POST → creates Ticket + PasswordResetRequest, notifies admins.
    """
    form = PasswordResetRequestForm(request=request)

    if request.method == "POST":
        form = PasswordResetRequestForm(request.POST, request=request)
        if form.is_valid():
            ticket_type = _get_password_reset_ticket_type()
            priority = form.cleaned_data.get("priority", "medium")
            deadline = form.cleaned_data.get("deadline") or (timezone.now() + timedelta(days=7)).date()

            platform = form.cleaned_data["platform"]
            request_type_display = form.instance.get_request_type_display()
            selected_employee = form.cleaned_data["employee"]
            selected_forward_users = list(form.cleaned_data["forward_to"])
            reason = form.cleaned_data["reason"]

            assigning_type = "individual"
            # Map selected User objects to Employee IDs (forward_to uses User model)
            forward_employee_ids, forward_employees = _get_forward_employee_ids_and_employees(
                selected_forward_users
            )
            raised_on = ",".join(forward_employee_ids) or str(selected_employee.id)
            user_display = _format_password_reset_user(selected_employee)
            description = (
                f"<b>{request_type_display} Details:</b><br><br>"
                f"<b>Type:</b> {request_type_display}<br>"
                f"<b>Platform:</b> {platform}<br>"
                f"<b>User:</b> {user_display}<br>"
                f"<b>Reason:</b> {reason}"
            )

            # ticket owner is the selected employee, not the admin submitting
            ticket = Ticket(
                title=f"{request_type_display} – {platform}",
                employee_id=selected_employee,
                ticket_type=ticket_type,
                description=description,
                priority=priority,
                assigning_type=assigning_type,
                raised_on=raised_on,
                deadline=deadline,
                status="new",
            )
            ticket.save()

            ticket.assigned_to.add(selected_employee)

            pr_request = form.save(commit=False)
            pr_request.ticket = ticket
            pr_request.iso_status = "PENDING"
            pr_request.request_type = "password_reset"
            pr_request.save()
            # forward_to is M2M to User – selected_forward_users are already
            # User objects (from the ModelMultipleChoiceField), so set directly.
            pr_request.forward_to.set(selected_forward_users)
            # Fold the m2m_changed-triggered '~' history record into the '+'
            # create record so the timeline only shows "Created the ticket"
            # on first save (and not a spurious "changed Forward to from None
            # to <ISO officers>" entry). Subsequent forward_to edits remain
            # tracked normally.
            PasswordResetRequestForm._consolidate_create_history(pr_request)

            _helpdesk_audit(
                request,
                "Password reset request created",
                ticket,
                {
                    "platform": platform,
                    "forward_to": pr_request.get_forward_to_display() or None,
                    "status": pr_request.get_iso_status_display(),
                },
            )

            notification_actor = getattr(request.user, "employee_get", selected_employee)

            # In-app notification to selected ISO officers/admins (forward recipients)
            try:
                if selected_forward_users:
                    notify.send(
                        notification_actor,
                        recipient=selected_forward_users,
                        verb=f"New Password Reset request submitted for {selected_employee.get_full_name()} on {platform}.",
                        verb_ar="تم تقديم طلب إعادة تعيين كلمة المرور.",
                        verb_de="Eine neue Anfrage zum Zurücksetzen des Passworts wurde eingereicht.",
                        verb_es="Se ha enviado una nueva solicitud de restablecimiento de contraseña.",
                        verb_fr="Une nouvelle demande de réinitialisation de mot de passe a été soumise.",
                        icon="key",
                        redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
                    )
            except Exception as exc:
                logger.error("Password reset notify error: %s", exc)

            # In-app confirmation notification to the requester
            requestor_user = getattr(selected_employee, "employee_user_id", None)
            try:
                if requestor_user:
                    notify.send(
                        notification_actor,
                        recipient=requestor_user,
                        verb=f"Your password reset request for {platform} has been submitted and is pending ISO approval.",
                        icon="key",
                        redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
                    )
            except Exception as exc:
                logger.error("Password reset requester notify error: %s", exc)

            # Email notification to ISO officers and confirmation to requester
            try:
                mail_thread = PasswordResetMailThread(
                    request,
                    ticket,
                    type="new_request",
                    pr_request=pr_request,
                    iso_recipients=selected_forward_users,
                )
                mail_thread.start()
            except Exception as exc:
                logger.error("Password reset mail error: %s", exc)

            messages.success(
                request,
                _("Your password reset request has been submitted and is pending ISO approval."),
            )
            return HttpResponse("<script>window.location.reload()</script>")

    context = {"form": form}
    return render(request, "helpdesk/ticket/password_reset_form.html", context)


@login_required
@hx_request_required
def password_reset_request_update(request, pr_id):
    """
    Allow the ticket owner to edit their PENDING Password Reset request.
    """
    pr_request = PasswordResetRequest.objects.get(id=pr_id)
    ticket = pr_request.ticket

    current_employee = getattr(request.user, "employee_get", None)
    is_iso_officer = _is_iso_officer(request.user)
    has_access = request.user.is_superuser or is_iso_officer or current_employee == ticket.employee_id

    if not has_access:
        messages.info(request, _("You don't have permission."))
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        return HttpResponse(
            f'<script>window.location.href = "{request.META.get("HTTP_REFERER", "/")}"</script>'
        )

    if pr_request.iso_status != "PENDING":
        messages.info(request, _("This request has already been reviewed and cannot be edited."))
        return HttpResponse("<script>window.location.reload()</script>")

    form = PasswordResetRequestForm(instance=pr_request, request=request)
    if request.method == "POST":
        # Re-check status to handle race condition where ISO reviewed between
        # the employee loading the form and submitting it
        pr_request.refresh_from_db()
        if pr_request.iso_status != "PENDING":
            messages.info(request, _("This request has already been reviewed and cannot be edited."))
            return HttpResponse("<script>window.location.reload()</script>")
        form = PasswordResetRequestForm(request.POST, instance=pr_request, request=request)
        if form.is_valid():
            platform = form.cleaned_data["platform"]
            request_type_display = pr_request.get_request_type_display()
            selected_employee = form.cleaned_data["employee"]
            selected_forward_users = list(form.cleaned_data["forward_to"])
            reason = form.cleaned_data["reason"]
            user_display = _format_password_reset_user(selected_employee)

            # Map selected Employee objects to Employee IDs
            forward_employee_ids, forward_employees = _get_forward_employee_ids_and_employees(
                selected_forward_users
            )

            # Update the ticket FIRST so the owner (employee_id) is always
            # reassigned together with the description and other fields.
            # Re-fetch the ticket fresh from DB to avoid stale reference
            ticket = Ticket.objects.get(pk=pr_request.ticket_id)

            ticket.employee_id = selected_employee
            ticket.priority = form.cleaned_data.get("priority")
            ticket.deadline = form.cleaned_data.get("deadline")
            ticket.title = f"{request_type_display} – {platform}"
            ticket.description = (
                f"<b>{request_type_display} Details:</b><br><br>"
                f"<b>Type:</b> {request_type_display}<br>"
                f"<b>Platform:</b> {platform}<br>"
                f"<b>User:</b> {user_display}<br>"
                f"<b>Reason:</b> {reason}"
            )
            ticket.raised_on = ",".join(forward_employee_ids) or str(selected_employee.id)
            ticket.save()

            # Refresh assigned_to: only the ticket owner should be assigned
            ticket.assigned_to.clear()
            ticket.assigned_to.add(selected_employee)

            # Now save the PasswordResetRequest (user_id, platform, reason, forward_to)
            pr_request = form.save(commit=False)
            pr_request.request_type = "password_reset"
            pr_request.save()

            # Set forward_to M2M after saving the PR request –
            # selected_forward_users are already User objects.
            pr_request.forward_to.set(selected_forward_users)

            log_form_changes(
                request.user,
                "helpdesk",
                "Password reset request updated",
                form,
                target=ticket,
            )

            messages.success(request, _("Password reset request updated successfully."))
            return HttpResponse("<script>window.location.reload()</script>")

    context = {"form": form, "pr_request": pr_request}
    return render(request, "helpdesk/ticket/password_reset_form.html", context)


@login_required
def iso_review_password_reset(request, pr_id):
    """
    ISO Officer or Superuser: approve or reject a Password Reset request.
    Expects POST with action='approve'|'reject' and optional iso_feedback.
    """
    if not request.user.is_superuser and not _is_iso_officer(request.user):
        messages.info(request, _("You don't have permission."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    pr_request = PasswordResetRequest.objects.get(id=pr_id)

    if _is_password_reset_request_owner(request.user, pr_request):
        messages.info(
            request,
            _("You cannot approve or reject your own password reset request."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if pr_request.iso_status != "PENDING":
        messages.info(request, _("This request has already been reviewed."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if request.method == "POST":
        review_form = ISOReviewForm(request.POST)
        if review_form.is_valid():
            action = review_form.cleaned_data["action"]
            feedback = review_form.cleaned_data.get("iso_feedback", "").strip()

            old_iso_status = pr_request.get_iso_status_display()

            pr_request.reviewed_by = request.user
            pr_request.reviewed_at = timezone.now()
            pr_request.iso_feedback = feedback

            ticket = pr_request.ticket
            requestor = ticket.employee_id
            old_ticket_status = ticket.get_status_display()

            if action == "approve":
                # Approving does NOT create an "Approved" resting status: the
                # request transitions straight to IN_ACTION (spec §1/§4). The
                # word "Approved" only survives in the audit comment below.
                pr_request.iso_status = "IN_ACTION"
                pr_request.approved_by = request.user
                ticket.status = "in_progress"
                verb = f"Your password reset request for {pr_request.platform} has been approved."
                messages.success(request, _("Password reset request approved."))
            else:
                # Rejection is terminal. We reuse the existing reviewed_by field
                # to record the rejecting user (matches existing convention).
                pr_request.iso_status = "REJECTED"
                ticket.status = "canceled"
                verb = (
                    f"Your password reset request for {pr_request.platform} has been rejected. "
                    f"Reason: {feedback}"
                )
                messages.success(request, _("Password reset request rejected."))

            pr_request.save()
            ticket.save()

            _helpdesk_audit(
                request,
                "Password reset request approved" if action == "approve"
                else "Password reset request rejected",
                ticket,
                {
                    "status": {"from": old_iso_status, "to": pr_request.get_iso_status_display()},
                    "ticket_status": {"from": old_ticket_status, "to": ticket.get_status_display()},
                    "reviewer": request.user.get_full_name() or request.user.username,
                    "feedback": feedback or None,
                },
            )

            # Create a comment on the ticket so it shows up in the activity feed
            try:
                reviewer_employee = request.user.employee_get
                if action == "approve":
                    # Audit comment: keep the existing "ISO Review – Approved"
                    # heading and add a "Status: Approved" line directly beneath
                    # it (spec §4). The request itself is now IN_ACTION.
                    comment_text = (
                        f"<strong>ISO Review – Approved</strong><br>"
                        f"<strong>Status:</strong> Approved<br>"
                        f"Your password reset request for <strong>{pr_request.platform}</strong> "
                        f"has been approved."
                    )
                    if feedback:
                        comment_text += f"<br><strong>Feedback:</strong> {feedback}"
                else:
                    comment_text = (
                        f"<strong>ISO Review – Rejected</strong><br>"
                        f"<strong>Status:</strong> Rejected<br>"
                        f"Your password reset request for <strong>{pr_request.platform}</strong> "
                        f"has been rejected."
                    )
                    if feedback:
                        comment_text += f"<br><strong>Reason:</strong> {feedback}"

                Comment.objects.create(
                    comment=comment_text,
                    ticket=ticket,
                    employee_id=reviewer_employee,
                )
            except Exception as exc:
                logger.error("ISO review comment creation error: %s", exc)

            # In-app notification to requestor
            try:
                notify.send(
                    request.user.employee_get,
                    recipient=requestor.employee_user_id,
                    verb=verb,
                    icon="key",
                    redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
                )
            except Exception as exc:
                logger.error("ISO review notify error: %s", exc)

            # In-app notification to other ISO officers about the review
            try:
                status_text = "approved" if action == "approve" else "rejected"
                other_officers = [
                    u for u in _get_iso_officer_users()
                    if u.pk != request.user.pk
                ]
                if other_officers:
                    notify.send(
                        request.user.employee_get,
                        recipient=other_officers,
                        verb=(
                            f"Password reset request by {requestor.get_full_name()} "
                            f"for {pr_request.platform} has been {status_text}."
                        ),
                        verb_ar="تم مراجعة طلب إعادة تعيين كلمة المرور.",
                        verb_de="Der Passwort-Zurücksetzungsticket wurde überprüft.",
                        verb_es="La solicitud de restablecimiento de contraseña ha sido revisada.",
                        verb_fr="Le demande de réinitialisation de mot de passe a été examinée.",
                        icon="key",
                        redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
                    )
            except Exception as exc:
                logger.error("ISO review notify to admins error: %s", exc)

            # Email notification to requestor and ISO officers
            try:
                mail_thread = PasswordResetMailThread(
                    request,
                    ticket,
                    type="iso_review",
                    pr_request=pr_request,
                    action=action,
                    feedback=feedback,
                )
                mail_thread.start()
            except Exception as exc:
                logger.error("ISO review mail error: %s", exc)

        else:
            for error in review_form.errors.values():
                messages.error(request, error)

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def password_reset_mark_awaiting(request, pr_id):
    """
    ISO Officer / Superuser: move a request from In Action → Awaiting
    Acknowledgement (spec §5).

    Triggered after the ISO Officer has performed the actual out-of-system
    action (reset link / re-add user). Requires a mandatory comment which is
    used to notify the requestor. Role and mandatory-comment validation are
    authoritative server-side.
    """
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    # Server-side role enforcement: only ISO group members (or superusers) may
    # perform this transition. A wrong-role POST is rejected, never allowed.
    if not request.user.is_superuser and not _is_iso_officer(request.user):
        messages.error(request, _("You don't have permission to perform this action."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    pr_request = PasswordResetRequest.objects.get(id=pr_id)

    if pr_request.iso_status != "IN_ACTION":
        messages.info(request, _("This request is not awaiting an ISO action."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    form = ISOCommentTransitionForm(request.POST)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    comment = form.cleaned_data["comment"]
    ticket = pr_request.ticket
    requestor = ticket.employee_id

    old_iso_status = pr_request.get_iso_status_display()

    pr_request.iso_status = "AWAITING_ACKNOWLEDGEMENT"
    pr_request.actioned_by = request.user
    pr_request.save()

    # Keep the underlying ticket status meaningful for the rest of the helpdesk UI.
    ticket.status = "on_hold"
    ticket.save()

    _helpdesk_audit(
        request,
        "Password reset request marked awaiting acknowledgement",
        ticket,
        {
            "status": {"from": old_iso_status, "to": pr_request.get_iso_status_display()},
            "note": comment or None,
        },
    )

    # Inline audit entry on the existing comment thread (spec §5/§7).
    try:
        Comment.objects.create(
            comment=(
                f"<strong>ISO Action Completed</strong><br>"
                f"<strong>Status:</strong> Awaiting Acknowledgement<br>"
                f"{comment}"
            ),
            ticket=ticket,
            employee_id=request.user.employee_get,
        )
    except Exception as exc:
        logger.error("ISO awaiting-acknowledgement comment error: %s", exc)

    # Notify the requestor that their action is ready to acknowledge.
    try:
        notify.send(
            request.user.employee_get,
            recipient=requestor.employee_user_id,
            verb=(
                f"Your password reset request for {pr_request.platform} has been "
                f"actioned and is awaiting your acknowledgement."
            ),
            icon="key",
            redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
        )
    except Exception as exc:
        logger.error("ISO awaiting-acknowledgement notify error: %s", exc)

    messages.success(request, _("Request moved to Awaiting Acknowledgement."))
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def password_reset_acknowledge(request, pr_id):
    """
    Requestor acknowledgement (spec §6): the employee confirms the request was
    fulfilled, which transitions it to Closed (closed_by = requestor) with a
    mandatory comment. The "No"/reopen branch has been removed.

    Only the original requestor may perform this step; enforced server-side.
    """
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    pr_request = PasswordResetRequest.objects.get(id=pr_id)

    # Server-side role enforcement: only the original requestor may close.
    if not _is_password_reset_request_owner(request.user, pr_request):
        messages.error(request, _("Only the requestor can acknowledge this request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if pr_request.iso_status != "AWAITING_ACKNOWLEDGEMENT":
        messages.info(request, _("This request is not awaiting acknowledgement."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    form = ISOAcknowledgementForm(request.POST)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    comment = form.cleaned_data["comment"]
    ticket = pr_request.ticket

    old_iso_status = pr_request.get_iso_status_display()

    pr_request.iso_status = "CLOSED"
    pr_request.closed_by = request.user
    pr_request.save()
    ticket.status = "resolved"
    ticket.save()

    _helpdesk_audit(
        request,
        "Password reset request acknowledged",
        ticket,
        {
            "status": {"from": old_iso_status, "to": pr_request.get_iso_status_display()},
            "note": comment or None,
        },
    )

    try:
        Comment.objects.create(
            comment=format_html(
                "<strong>Request Acknowledged – Fulfilled</strong><br>"
                "<strong>Status:</strong> Closed<br>"
                "{}",
                comment,
            ),
            ticket=ticket,
            employee_id=request.user.employee_get,
        )
    except Exception as exc:
        logger.error("ISO close comment error: %s", exc)

    verb = (
        f"The password reset request for {pr_request.platform} has been "
        f"acknowledged and closed by the requestor."
    )
    messages.success(request, _("Request closed. Thank you for confirming."))

    # Notify ISO officers about the requestor's decision.
    try:
        officers = _get_iso_officer_users()
        if officers:
            notify.send(
                request.user.employee_get,
                recipient=officers,
                verb=verb,
                icon="key",
                redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
            )
    except Exception as exc:
        logger.error("ISO acknowledgement notify error: %s", exc)

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def password_reset_request_withdraw(request, pr_id):
    """
    Allow the ticket owner to withdraw their own PENDING Password Reset request.
    The request and its associated ticket are deleted from the system.
    """
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        pr_request = PasswordResetRequest.objects.get(id=pr_id)
    except PasswordResetRequest.DoesNotExist:
        messages.error(request, _("Password reset request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    ticket = pr_request.ticket

    # Only the request owner can withdraw
    current_employee = getattr(request.user, "employee_get", None)
    if current_employee != ticket.employee_id:
        messages.info(request, _("You don't have permission to withdraw this request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    # Only PENDING requests can be withdrawn
    if pr_request.iso_status != "PENDING":
        messages.info(
            request,
            _("This request has already been reviewed and cannot be withdrawn."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    platform = pr_request.platform
    ticket_title = str(ticket)

    _helpdesk_audit(
        request,
        "Password reset request withdrawn",
        ticket,
        {"platform": platform, "title": ticket_title},
    )

    # Delete the request and ticket
    pr_request.delete()
    ticket.delete()

    messages.success(
        request,
        _('Your password reset request "{}" has been withdrawn successfully.').format(
            ticket_title
        ),
    )
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def password_reset_request_delete(request, pr_id):
    """
    ISO Officer or Superuser: delete a Password Reset request and its associated ticket.
    """
    if not request.user.is_superuser and not _is_iso_officer(request.user):
        messages.info(request, _("You don't have permission."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if request.method == "POST":
        try:
            pr_request = PasswordResetRequest.objects.get(id=pr_id)
            ticket = pr_request.ticket
            ticket_title = str(ticket)

            _helpdesk_audit(
                request,
                "Password reset request deleted",
                ticket,
                {"platform": pr_request.platform, "title": ticket_title},
            )

            # Send delete notification
            try:
                employees = ticket.assigned_to.all()
                assignees = [employee.employee_user_id for employee in employees]
                assignees.append(ticket.employee_id.employee_user_id)
                notify.send(
                    request.user.employee_get,
                    recipient=assignees,
                    verb=f"The password reset request ticket has been deleted.",
                    verb_ar="تم حذف تذكرة طلب إعادة تعيين كلمة المرور.",
                    verb_de="Das Passwort-Zurücksetzungsticket wurde gelöscht.",
                    verb_es="El ticket de restablecimiento de contraseña ha sido eliminado.",
                    verb_fr="Le ticket de réinitialisation de mot de passe a été supprimé.",
                    icon="key",
                    redirect=reverse("iso-forms-home"),
                )
            except Exception as exc:
                logger.error("Password reset delete notify error: %s", exc)

            # Delete the PR (cascade will handle it) then the ticket
            pr_request.delete()
            ticket.delete()

            messages.success(
                request,
                _('The password reset request "{}" has been deleted successfully.').format(
                    ticket_title
                ),
            )
        except PasswordResetRequest.DoesNotExist:
            messages.error(request, _("Password reset request not found."))
        except Exception:
            messages.error(request, _("You cannot delete this password reset request."))

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


# ── ACCESS REQUEST & DEACTIVATION VIEWS ──────────────────────────────────────


def _get_access_request_ticket_type():
    """Return (creating if needed) the TicketType for Access Request tickets."""
    ticket_type, _created = TicketType.objects.get_or_create(
        title="Access Request & Deactivation",
        defaults={"type": "service_request", "prefix": "ACR"},
    )
    return ticket_type


def _build_access_request_description(access_request, user_display):
    """Build the HTML description block shown on the linked Ticket.
    User-controlled values (``user_id``, ``user_display`` and ``reason``) are
    interpolated via :func:`~django.utils.html.format_html`, which escapes every
    argument. This prevents stored XSS even though the resulting description is
    later rendered with the ``|safe`` filter.

    """
    if access_request.sub_type == "access_deactivation":
        effective_date = access_request.effective_date
        return format_html(
            "<b>{} Details:</b><br><br>"
            "<b>Sub Type:</b> {}<br>"
            "<b>User ID (Email):</b> {}<br>"
            "<b>Domain:</b> {}<br>"
            "<b>Effective Date:</b> {}<br>"
            "<b>User:</b> {}<br>"
            "<b>Reason:</b> {}",
            access_request.get_sub_type_display(),
            access_request.get_sub_type_display(),
            access_request.user_id,
            access_request.get_domain_display(),
            effective_date.isoformat() if effective_date else "",
            user_display,
            access_request.reason,
        )
    return format_html(
        "<b>{} Details:</b><br><br>"
        "<b>Sub Type:</b> {}<br>"
        "<b>User ID (Email):</b> {}<br>"
        "<b>Business Critical Systems:</b> {}<br>"
        "<b>Level of Access:</b> {}<br>"
        "<b>Domain:</b> {}<br>"
        "<b>User:</b> {}<br>"
        "<b>Reason:</b> {}",
        access_request.get_sub_type_display(),
        access_request.get_sub_type_display(),
        access_request.user_id,
        access_request.get_business_critical_display(),
        access_request.get_level_of_access_display(),
        access_request.get_domain_display(),
        user_display,
        access_request.reason,
    )


def _get_ticket_display_description(ticket):
    """Return the live description shown in ticket views."""
    access_request = getattr(ticket, "access_request", None)
    if access_request:
        return _build_access_request_description(
            access_request, _format_password_reset_user(ticket.employee_id)
        )
    exception_request = getattr(ticket, "exception_request", None)
    if exception_request:
        return _build_exception_request_description(
            exception_request, _format_password_reset_user(ticket.employee_id)
        )
    admin_access_request = getattr(ticket, "admin_access_request", None)
    if admin_access_request:
        return _build_admin_access_request_description(
            admin_access_request, _format_password_reset_user(ticket.employee_id)
        )
    return ticket.description


@login_required
@hx_request_required
def access_request_create(request):
    """
    GET  → renders the Access Request modal form.
    POST → creates Ticket + AccessRequest (status PENDING), notifies the
           ISO Officers (Stage 1 approvers).
    """
    form = AccessRequestForm(request=request)

    if request.method == "POST":
        form = AccessRequestForm(request.POST, request=request)
        if form.is_valid():
            ticket_type = _get_access_request_ticket_type()
            priority = form.cleaned_data.get("priority", "medium")
            deadline = form.cleaned_data.get("deadline") or (
                timezone.now() + timedelta(days=7)
            ).date()

            selected_employee = form.cleaned_data["employee"]
            selected_forward_users = list(form.cleaned_data["forward_to"])
            user_display = _format_password_reset_user(selected_employee)

            access_request = form.save(commit=False)
            access_request.status = "PENDING"

            forward_employee_ids, _emps = _get_forward_employee_ids_and_employees(
                selected_forward_users
            )
            # Stage 1 reviewers (Divisional Heads / IS Council) and Stage 2
            # reviewers (ISO Officers) must all be able to see the ticket, so
            # include their employee IDs in raised_on alongside the recipients
            # chosen in "Forward To".
            reviewer_users = _get_isc_users() + _get_iso_officer_users()
            reviewer_employee_ids, _rev_emps = _get_forward_employee_ids_and_employees(
                reviewer_users
            )
            combined_employee_ids = list(
                dict.fromkeys(reviewer_employee_ids + forward_employee_ids)
            )
            raised_on = ",".join(combined_employee_ids) or str(selected_employee.id)

            unit = (access_request.get_domain_display() or "").strip()
            short_unit = (unit[:24] + "...") if len(unit) > 27 else unit
            ticket = Ticket(
                title=f"Access Request – {short_unit}",
                employee_id=selected_employee,
                ticket_type=ticket_type,
                description=_build_access_request_description(
                    access_request, user_display
                ),
                priority=priority,
                assigning_type="individual",
                raised_on=raised_on,
                deadline=deadline,
                status="new",
            )
            ticket.save()
            ticket.assigned_to.add(selected_employee)

            access_request.ticket = ticket
            access_request.save()
            access_request.forward_to.set(selected_forward_users)

            _helpdesk_audit(
                request,
                "Access request created",
                ticket,
                {
                    "status": access_request.get_status_display(),
                    "forward_to": access_request.get_forward_to_display() or None,
                },
            )

            notification_actor = getattr(
                request.user, "employee_get", selected_employee
            )

            # Stage 1: notify Divisional Heads (IS Council) that a review is required.
            try:
                stage1_users = [
                    u
                    for u in _get_isc_users()
                    if u.pk != request.user.pk
                ]
                if stage1_users:
                    notify.send(
                        notification_actor,
                        recipient=stage1_users,
                        verb=(
                            f"New Access Request submitted by "
                            f"{selected_employee.get_full_name()} awaiting your approval."
                        ),
                        icon="shield-checkmark",
                        redirect=reverse(
                            "ticket-detail", kwargs={"ticket_id": ticket.id}
                        ),
                    )
            except Exception as exc:
                logger.error("Access request ISO notify error: %s", exc)

            messages.success(
                request,
                _(
                    "Your access request has been submitted and is pending "
                    "Divisional Head (IS Council) approval."
                ),
            )
            return HttpResponse("<script>window.location.reload()</script>")

    context = {"form": form}
    return render(request, "helpdesk/ticket/access_request_form.html", context)


@login_required
@hx_request_required
def access_request_update(request, ar_id):
    """Allow the owner / ISO officer to edit a PENDING Access Request."""
    access_request = AccessRequest.objects.get(id=ar_id)
    ticket = access_request.ticket

    current_employee = getattr(request.user, "employee_get", None)
    has_access = (
        request.user.is_superuser
        or _is_iso_officer(request.user)
        or current_employee == ticket.employee_id
    )
    if not has_access:
        messages.info(request, _("You don't have permission."))
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if access_request.status != "PENDING":
        messages.info(
            request,
            _("This request has already been reviewed and cannot be edited."),
        )
        return HttpResponse("<script>window.location.reload()</script>")

    form = AccessRequestForm(instance=access_request, request=request)
    if request.method == "POST":
        access_request.refresh_from_db()
        if access_request.status != "PENDING":
            messages.info(
                request,
                _("This request has already been reviewed and cannot be edited."),
            )
            return HttpResponse("<script>window.location.reload()</script>")
        form = AccessRequestForm(
            request.POST, instance=access_request, request=request
        )
        if form.is_valid():
            selected_employee = form.cleaned_data["employee"]
            selected_forward_users = list(form.cleaned_data["forward_to"])
            user_display = _format_password_reset_user(selected_employee)

            access_request = form.save(commit=False)
            access_request.save()
            access_request.forward_to.set(selected_forward_users)

            forward_employee_ids, _emps = _get_forward_employee_ids_and_employees(
                selected_forward_users
            )
            ticket = Ticket.objects.get(pk=access_request.ticket_id)
            ticket.employee_id = selected_employee
            ticket.priority = form.cleaned_data.get("priority")
            ticket.deadline = form.cleaned_data.get("deadline")
            unit = (access_request.get_domain_display() or "").strip()
            short_unit = (unit[:24] + "...") if len(unit) > 27 else unit
            ticket.title = f"Access Request – {short_unit}"
            ticket.description = _build_access_request_description(
                access_request, user_display
            )
            ticket.raised_on = ",".join(forward_employee_ids) or str(
                selected_employee.id
            )
            ticket.save()
            ticket.assigned_to.clear()
            ticket.assigned_to.add(selected_employee)

            log_form_changes(
                request.user,
                "helpdesk",
                "Access request updated",
                form,
                target=ticket,
            )

            messages.success(request, _("Access request updated successfully."))
            return HttpResponse("<script>window.location.reload()</script>")

    context = {"form": form, "access_request": access_request}
    return render(request, "helpdesk/ticket/access_request_form.html", context)


def _access_review_comment(ticket, actor_user, heading, status_label, body, feedback):
    """Create an inline comment entry on the ticket (reviewer audit trail).
    """
    try:
        clean_body = strip_tags(body) if body else body
        clean_feedback = strip_tags(feedback) if feedback else feedback
        comment_text = format_html(
            "<strong>{}</strong><br>"
            "<strong>Status:</strong> {}<br>"
            "{}",
            heading,
            status_label,
            clean_body,
        )
        if clean_feedback:
            comment_text += format_html(
                "<br><strong>Feedback:</strong> {}", clean_feedback
            )
        Comment.objects.create(
            comment=comment_text,
            ticket=ticket,
            employee_id=actor_user.employee_get,
        )
    except Exception as exc:
        logger.error("Access request review comment error: %s", exc)


@login_required
def iso_review_access_request(request, ar_id):
    """
    Stage 1 — Divisional Head (IS Council group) approves or rejects a PENDING
    Access Request. Approval advances the request to Stage 2 (ISO Officer
    review).
    """
    if not request.user.is_superuser and not _is_isc_member(request.user):
        messages.info(
            request,
            _("Only a Divisional Head (IS Council) can review this request."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    access_request = AccessRequest.objects.get(id=ar_id)

    if _is_access_request_owner(request.user, access_request):
        messages.info(request, _("You cannot approve or reject your own request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if access_request.status != "PENDING":
        messages.info(
            request, _("This request is not awaiting Divisional Head review.")
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if request.method == "POST":
        review_form = ISOReviewForm(request.POST)
        if review_form.is_valid():
            action = review_form.cleaned_data["action"]
            feedback = review_form.cleaned_data.get("iso_feedback", "").strip()

            access_request.isc_reviewed_by = request.user
            access_request.isc_reviewed_at = timezone.now()
            access_request.feedback = feedback
            ticket = access_request.ticket
            requestor = ticket.employee_id

            old_status = access_request.get_status_display()
            old_ticket_status = ticket.get_status_display()

            if action == "approve":
                access_request.status = "ISO_APPROVED"
                ticket.status = "in_progress"
                _access_review_comment(
                    ticket,
                    request.user,
                    _("Divisional Head Review – Approved"),
                    _("Divisional Head Approved"),
                    _("Forwarded to the ISO Officer for evaluation."),
                    feedback,
                )
                verb = _("Your access request has been approved by the Divisional Head and forwarded to the ISO Officer.")
                messages.success(request, _("Access request approved (Stage 1)."))
                # Notify Stage 2 reviewers (ISO Officers).
                try:
                    iso_recipients = [
                        u for u in _get_iso_officer_users() if u.pk != request.user.pk
                    ]
                    if iso_recipients:
                        notify.send(
                            request.user.employee_get,
                            recipient=iso_recipients,
                            verb=(
                                f"Access request by {requestor.get_full_name()} "
                                f"is awaiting ISO Officer approval."
                            ),
                            icon="shield-checkmark",
                            redirect=reverse(
                                "ticket-detail", kwargs={"ticket_id": ticket.id}
                            ),
                        )
                except Exception as exc:
                    logger.error("Access request ISO notify error: %s", exc)
            else:
                access_request.status = "REJECTED"
                ticket.status = "canceled"
                _access_review_comment(
                    ticket,
                    request.user,
                    _("Divisional Head Review – Rejected"),
                    _("Rejected"),
                    _("Your access request has been rejected by the Divisional Head."),
                    feedback,
                )
                verb = _("Your access request has been rejected by the Divisional Head.")
                messages.success(request, _("Access request rejected."))

            access_request.save()
            ticket.save()

            _helpdesk_audit(
                request,
                "Access request approved" if action == "approve"
                else "Access request rejected",
                ticket,
                {
                    "status": {"from": old_status, "to": access_request.get_status_display()},
                    "ticket_status": {"from": old_ticket_status, "to": ticket.get_status_display()},
                    "reviewer": request.user.get_full_name() or request.user.username,
                    "feedback": feedback or None,
                    "stage": "ISO Officer",
                },
            )

            try:
                notify.send(
                    request.user.employee_get,
                    recipient=requestor.employee_user_id,
                    verb=verb,
                    icon="shield-checkmark",
                    redirect=reverse(
                        "ticket-detail", kwargs={"ticket_id": ticket.id}
                    ),
                )
            except Exception as exc:
                logger.error("Access request ISO review notify error: %s", exc)
        else:
            for error in review_form.errors.values():
                messages.error(request, error)

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def isc_review_access_request(request, ar_id):
    """
    Stage 2 — ISO Officer approves or rejects an Access Request that has
    already cleared Stage 1 (Divisional Head / IS Council). Approval completes
    the request.
    """
    if not request.user.is_superuser and not _is_iso_officer(request.user):
        messages.info(request, _("Only an ISO Officer can review this request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    access_request = AccessRequest.objects.get(id=ar_id)

    if _is_access_request_owner(request.user, access_request):
        messages.info(request, _("You cannot approve or reject your own request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if access_request.status != "ISO_APPROVED":
        messages.info(
            request,
            _("This request must be approved by the Divisional Head before ISO Officer review."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if request.method == "POST":
        review_form = ISOReviewForm(request.POST)
        if review_form.is_valid():
            action = review_form.cleaned_data["action"]
            feedback = review_form.cleaned_data.get("iso_feedback", "").strip()

            access_request.iso_reviewed_by = request.user
            access_request.iso_reviewed_at = timezone.now()
            access_request.feedback = feedback
            ticket = access_request.ticket
            requestor = ticket.employee_id

            old_status = access_request.get_status_display()
            old_ticket_status = ticket.get_status_display()

            if action == "approve":
                access_request.status = "COMPLETED"
                ticket.status = "resolved"
                _access_review_comment(
                    ticket,
                    request.user,
                    _("ISO Officer Review – Approved"),
                    _("Completed"),
                    _("Your access request has been approved by the ISO Officer."),
                    feedback,
                )
                verb = _("Your access request has been approved by the ISO Officer and completed.")
                messages.success(request, _("Access request approved (Stage 2)."))
            else:
                access_request.status = "REJECTED"
                ticket.status = "canceled"
                _access_review_comment(
                    ticket,
                    request.user,
                    _("ISO Officer Review – Rejected"),
                    _("Rejected"),
                    _("Your access request has been rejected by the ISO Officer."),
                    feedback,
                )
                verb = _("Your access request has been rejected by the ISO Officer.")
                messages.success(request, _("Access request rejected."))

            access_request.save()
            ticket.save()

            _helpdesk_audit(
                request,
                "Access request approved" if action == "approve"
                else "Access request rejected",
                ticket,
                {
                    "status": {"from": old_status, "to": access_request.get_status_display()},
                    "ticket_status": {"from": old_ticket_status, "to": ticket.get_status_display()},
                    "reviewer": request.user.get_full_name() or request.user.username,
                    "feedback": feedback or None,
                    "stage": "IS Council",
                },
            )

            try:
                notify.send(
                    request.user.employee_get,
                    recipient=requestor.employee_user_id,
                    verb=verb,
                    icon="shield-checkmark",
                    redirect=reverse(
                        "ticket-detail", kwargs={"ticket_id": ticket.id}
                    ),
                )
            except Exception as exc:
                logger.error("Access request ISC review notify error: %s", exc)
        else:
            for error in review_form.errors.values():
                messages.error(request, error)

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def access_request_acknowledge(request, ar_id):
    """Requestor acknowledges a COMPLETED Access Request → CLOSED."""
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    access_request = AccessRequest.objects.get(id=ar_id)
    if not _is_access_request_owner(request.user, access_request):
        messages.error(request, _("Only the requestor can close this request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if access_request.status != "COMPLETED":
        messages.info(request, _("This request is not ready to be closed."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    form = ISOAcknowledgementForm(request.POST)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    comment = form.cleaned_data["comment"]
    ticket = access_request.ticket
    old_status = access_request.get_status_display()
    old_ticket_status = ticket.get_status_display()
    access_request.status = "CLOSED"
    access_request.closed_by = request.user
    access_request.save()
    ticket.status = "resolved"
    ticket.save()

    _helpdesk_audit(
        request,
        "Access request acknowledged",
        ticket,
        {
            "status": {"from": old_status, "to": access_request.get_status_display()},
            "ticket_status": {"from": old_ticket_status, "to": ticket.get_status_display()},
            "note": comment or None,
        },
    )

    _access_review_comment(
        ticket,
        request.user,
        _("Request Acknowledged – Fulfilled"),
        _("Closed"),
        comment,
        "",
    )
    messages.success(request, _("Request closed. Thank you for confirming."))
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def access_request_withdraw(request, ar_id):
    """Allow the owner to withdraw their own PENDING Access Request."""
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        access_request = AccessRequest.objects.get(id=ar_id)
    except AccessRequest.DoesNotExist:
        messages.error(request, _("Access request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    ticket = access_request.ticket
    current_employee = getattr(request.user, "employee_get", None)
    if current_employee != ticket.employee_id:
        messages.info(request, _("You don't have permission to withdraw this request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if access_request.status != "PENDING":
        messages.info(
            request,
            _("This request has already been reviewed and cannot be withdrawn."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    ticket_title = str(ticket)
    _helpdesk_audit(
        request,
        "Access request withdrawn",
        ticket,
        {"status": access_request.get_status_display(), "title": ticket_title},
    )
    access_request.delete()
    ticket.delete()
    messages.success(
        request,
        _('Your access request "{}" has been withdrawn successfully.').format(
            ticket_title
        ),
    )
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def access_request_delete(request, ar_id):
    """ISO Officer / Superuser deletes an Access Request and its ticket."""
    if not request.user.is_superuser and not _is_iso_officer(request.user):
        messages.info(request, _("You don't have permission."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if request.method == "POST":
        try:
            access_request = AccessRequest.objects.get(id=ar_id)
            ticket = access_request.ticket
            ticket_title = str(ticket)
            _helpdesk_audit(
                request,
                "Access request deleted",
                ticket,
                {"status": access_request.get_status_display(), "title": ticket_title},
            )
            access_request.delete()
            ticket.delete()
            messages.success(
                request,
                _('The access request "{}" has been deleted successfully.').format(
                    ticket_title
                ),
            )
        except AccessRequest.DoesNotExist:
            messages.error(request, _("Access request not found."))
        except Exception:
            messages.error(request, _("You cannot delete this access request."))

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


# ── EXCEPTION REQUEST VIEWS ──────────────────────────────────────────────────


def _get_exception_request_ticket_type():
    """Return (creating if needed) the TicketType for Exception Request tickets."""
    ticket_type, _created = TicketType.objects.get_or_create(
        title="Exception Request",
        defaults={"type": "service_request", "prefix": "EXR"},
    )
    return ticket_type


def _build_exception_request_description(exception_request, user_display):
    """Build the HTML description block shown on the linked Ticket.

    User-controlled values are interpolated via
    :func:`~django.utils.html.format_html`, which escapes every argument. This
    prevents stored XSS even though the resulting description is later rendered
    with the ``|safe`` filter.
    """
    return format_html(
        "<b>Exception Request Details:</b><br><br>"
        "<b>User ID (Email):</b> {}<br>"
        "<b>ISMS Reference:</b> {}<br>"
        "<b>User:</b> {}<br>"
        "<b>Description of Exception:</b> {}",
        exception_request.user_id,
        exception_request.isms_reference,
        user_display,
        exception_request.description,
    )


@login_required
@hx_request_required
def exception_request_create(request):
    """
    GET  → renders the Exception Request modal form.
    POST → creates Ticket + ExceptionRequest (status PENDING), notifies the
           ISO Officers (Stage 1 approvers).
    """
    form = ExceptionRequestForm(request=request)

    if request.method == "POST":
        form = ExceptionRequestForm(request.POST, request=request)
        if form.is_valid():
            ticket_type = _get_exception_request_ticket_type()
            priority = form.cleaned_data.get("priority", "medium")
            deadline = form.cleaned_data.get("deadline") or (
                timezone.now() + timedelta(days=7)
            ).date()

            selected_employee = form.cleaned_data["employee"]
            selected_forward_users = list(form.cleaned_data["forward_to"])
            user_display = _format_password_reset_user(selected_employee)

            exception_request = form.save(commit=False)
            exception_request.status = "PENDING"

            forward_employee_ids, _emps = _get_forward_employee_ids_and_employees(
                selected_forward_users
            )
            # Stage 1 reviewers (ISO Officers) must also receive the ticket, so
            # include their employee IDs in raised_on alongside the Stage 2
            # (IS Council) recipients chosen in "Forward To".
            iso_officer_users = _get_iso_officer_users()
            iso_employee_ids, _iso_emps = _get_forward_employee_ids_and_employees(
                iso_officer_users
            )
            combined_employee_ids = list(
                dict.fromkeys(iso_employee_ids + forward_employee_ids)
            )
            raised_on = ",".join(combined_employee_ids) or str(selected_employee.id)

            app = (exception_request.isms_reference or "").strip()
            short_app = (app[:27] + "...") if len(app) > 30 else app
            ticket = Ticket(
                title=f"Exception Request – {short_app}",
                employee_id=selected_employee,
                ticket_type=ticket_type,
                description=_build_exception_request_description(
                    exception_request, user_display
                ),
                priority=priority,
                assigning_type="individual",
                raised_on=raised_on,
                deadline=deadline,
                status="new",
            )
            ticket.save()
            ticket.assigned_to.add(selected_employee)

            exception_request.ticket = ticket
            exception_request.save()
            exception_request.forward_to.set(selected_forward_users)

            _helpdesk_audit(
                request,
                "Exception request created",
                ticket,
                {
                    "status": exception_request.get_status_display(),
                    "forward_to": exception_request.get_forward_to_display() or None,
                },
            )

            notification_actor = getattr(
                request.user, "employee_get", selected_employee
            )

            # Stage 1: notify ISO Officers that a review is required.
            try:
                iso_users = [
                    u
                    for u in _get_iso_officer_users()
                    if u.pk != request.user.pk
                ]
                if iso_users:
                    notify.send(
                        notification_actor,
                        recipient=iso_users,
                        verb=(
                            f"New Exception Request submitted by "
                            f"{selected_employee.get_full_name()} awaiting your approval."
                        ),
                        icon="alert-circle",
                        redirect=reverse(
                            "ticket-detail", kwargs={"ticket_id": ticket.id}
                        ),
                    )
            except Exception as exc:
                logger.error("Exception request ISO notify error: %s", exc)

            messages.success(
                request,
                _("Exception request submitted successfully."),
            )
            return HttpResponse("<script>window.location.reload()</script>")

    context = {"form": form}
    return render(request, "helpdesk/ticket/exception_request_form.html", context)


@login_required
@hx_request_required
def exception_request_update(request, er_id):
    """Allow the owner / ISO officer to edit a PENDING Exception Request."""
    try:
        exception_request = ExceptionRequest.objects.get(id=er_id)
    except ExceptionRequest.DoesNotExist:
        messages.error(request, _("Exception request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    ticket = exception_request.ticket

    current_employee = getattr(request.user, "employee_get", None)
    has_access = (
        request.user.is_superuser
        or _is_iso_officer(request.user)
        or current_employee == ticket.employee_id
    )
    if not has_access:
        messages.info(request, _("You don't have permission."))
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if exception_request.status != "PENDING":
        messages.info(
            request,
            _("This request has already been reviewed and cannot be edited."),
        )
        return HttpResponse("<script>window.location.reload()</script>")

    form = ExceptionRequestForm(instance=exception_request, request=request)
    if request.method == "POST":
        exception_request.refresh_from_db()
        if exception_request.status != "PENDING":
            messages.info(
                request,
                _("This request has already been reviewed and cannot be edited."),
            )
            return HttpResponse("<script>window.location.reload()</script>")
        form = ExceptionRequestForm(
            request.POST, instance=exception_request, request=request
        )
        if form.is_valid():
            selected_employee = form.cleaned_data["employee"]
            selected_forward_users = list(form.cleaned_data["forward_to"])
            user_display = _format_password_reset_user(selected_employee)

            exception_request = form.save(commit=False)
            exception_request.save()
            exception_request.forward_to.set(selected_forward_users)

            forward_employee_ids, _emps = _get_forward_employee_ids_and_employees(
                selected_forward_users
            )
            # Keep the Stage 1 reviewers (ISO Officers) among the ticket
            # recipients so an edit does not strip them from raised_on.
            iso_officer_users = _get_iso_officer_users()
            iso_employee_ids, _iso_emps = _get_forward_employee_ids_and_employees(
                iso_officer_users
            )
            combined_employee_ids = list(
                dict.fromkeys(iso_employee_ids + forward_employee_ids)
            )
            ticket = Ticket.objects.get(pk=exception_request.ticket_id)
            ticket.employee_id = selected_employee
            ticket.priority = form.cleaned_data.get("priority")
            ticket.deadline = form.cleaned_data.get("deadline")
            app = (exception_request.isms_reference or "").strip()
            short_app = (app[:27] + "...") if len(app) > 30 else app
            ticket.title = f"Exception Request – {short_app}"
            ticket.description = _build_exception_request_description(
                exception_request, user_display
            )
            ticket.raised_on = ",".join(combined_employee_ids) or str(
                selected_employee.id
            )
            ticket.save()
            ticket.assigned_to.clear()
            ticket.assigned_to.add(selected_employee)

            log_form_changes(
                request.user,
                "helpdesk",
                "Exception request updated",
                form,
                target=ticket,
            )

            messages.success(request, _("Exception request updated successfully."))
            return HttpResponse("<script>window.location.reload()</script>")

    context = {"form": form, "exception_request": exception_request}
    return render(request, "helpdesk/ticket/exception_request_form.html", context)


@login_required
def iso_review_exception_request(request, er_id):
    """
    Stage 1 — ISO Officer approves or rejects a PENDING Exception Request.
    Approval advances the request to Stage 2 (IS Council review).
    """
    if not request.user.is_superuser and not _is_iso_officer(request.user):
        messages.info(request, _("Only an ISO Officer can review this request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        exception_request = ExceptionRequest.objects.get(id=er_id)
    except ExceptionRequest.DoesNotExist:
        messages.error(request, _("Exception request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if _is_exception_request_owner(request.user, exception_request):
        messages.info(request, _("You cannot approve or reject your own request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if exception_request.status != "PENDING":
        messages.info(request, _("This request is not awaiting ISO Officer review."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if request.method == "POST":
        review_form = ISOReviewForm(request.POST)
        if review_form.is_valid():
            action = review_form.cleaned_data["action"]
            feedback = review_form.cleaned_data.get("iso_feedback", "").strip()

            exception_request.iso_reviewed_by = request.user
            exception_request.iso_reviewed_at = timezone.now()
            exception_request.feedback = feedback
            ticket = exception_request.ticket
            requestor = ticket.employee_id

            old_status = exception_request.get_status_display()
            old_ticket_status = ticket.get_status_display()

            if action == "approve":
                exception_request.status = "ISO_APPROVED"
                ticket.status = "in_progress"
                _access_review_comment(
                    ticket,
                    request.user,
                    _("ISO Review – Approved"),
                    _("ISO Approved"),
                    _("Forwarded to the IS Council for final approval."),
                    feedback,
                )
                verb = _("Your exception request has been approved by the ISO Officer and forwarded to the IS Council.")
                messages.success(request, _("Exception request approved (Stage 1)."))
                # Notify Stage 2 reviewers (IS Council members).
                try:
                    isc_recipients = list(exception_request.forward_to.all()) or _get_isc_users()
                    if isc_recipients:
                        notify.send(
                            request.user.employee_get,
                            recipient=isc_recipients,
                            verb=(
                                f"Exception request by {requestor.get_full_name()} "
                                f"is awaiting IS Council approval."
                            ),
                            icon="alert-circle",
                            redirect=reverse(
                                "ticket-detail", kwargs={"ticket_id": ticket.id}
                            ),
                        )
                except Exception as exc:
                    logger.error("Exception request ISC notify error: %s", exc)
            else:
                exception_request.status = "REJECTED"
                ticket.status = "canceled"
                _access_review_comment(
                    ticket,
                    request.user,
                    _("ISO Review – Rejected"),
                    _("Rejected"),
                    _("Your exception request has been rejected by the ISO Officer."),
                    feedback,
                )
                verb = _("Your exception request has been rejected by the ISO Officer.")
                messages.success(request, _("Exception request rejected."))

            exception_request.save()
            ticket.save()

            _helpdesk_audit(
                request,
                "Exception request approved" if action == "approve"
                else "Exception request rejected",
                ticket,
                {
                    "status": {"from": old_status, "to": exception_request.get_status_display()},
                    "ticket_status": {"from": old_ticket_status, "to": ticket.get_status_display()},
                    "reviewer": request.user.get_full_name() or request.user.username,
                    "feedback": feedback or None,
                    "stage": "ISO Officer",
                },
            )

            try:
                notify.send(
                    request.user.employee_get,
                    recipient=requestor.employee_user_id,
                    verb=verb,
                    icon="alert-circle",
                    redirect=reverse(
                        "ticket-detail", kwargs={"ticket_id": ticket.id}
                    ),
                )
            except Exception as exc:
                logger.error("Exception request ISO review notify error: %s", exc)
        else:
            for error in review_form.errors.values():
                messages.error(request, error)

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def isc_review_exception_request(request, er_id):
    """
    Stage 2 — IS Council approves or rejects an Exception Request that has
    already cleared Stage 1 (ISO Officer). Approval completes the request.
    """
    if not request.user.is_superuser and not _is_isc_member(request.user):
        messages.info(request, _("Only an IS Council member can review this request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        exception_request = ExceptionRequest.objects.get(id=er_id)
    except ExceptionRequest.DoesNotExist:
        messages.error(request, _("Exception request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if _is_exception_request_owner(request.user, exception_request):
        messages.info(request, _("You cannot approve or reject your own request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if exception_request.status != "ISO_APPROVED":
        messages.info(
            request,
            _("This request must be approved by the ISO Officer before IS Council review."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if request.method == "POST":
        review_form = ISOReviewForm(request.POST)
        if review_form.is_valid():
            action = review_form.cleaned_data["action"]
            feedback = review_form.cleaned_data.get("iso_feedback", "").strip()

            exception_request.isc_reviewed_by = request.user
            exception_request.isc_reviewed_at = timezone.now()
            exception_request.feedback = feedback
            ticket = exception_request.ticket
            requestor = ticket.employee_id

            old_status = exception_request.get_status_display()
            old_ticket_status = ticket.get_status_display()

            if action == "approve":
                exception_request.status = "COMPLETED"
                ticket.status = "resolved"
                _access_review_comment(
                    ticket,
                    request.user,
                    _("IS Council Review – Approved"),
                    _("Completed"),
                    _("Your exception request has been approved by the IS Council."),
                    feedback,
                )
                verb = _("Your exception request has been approved by the IS Council and completed.")
                messages.success(request, _("Exception request approved (Stage 2)."))
            else:
                exception_request.status = "REJECTED"
                ticket.status = "canceled"
                _access_review_comment(
                    ticket,
                    request.user,
                    _("IS Council Review – Rejected"),
                    _("Rejected"),
                    _("Your exception request has been rejected by the IS Council."),
                    feedback,
                )
                verb = _("Your exception request has been rejected by the IS Council.")
                messages.success(request, _("Exception request rejected."))

            exception_request.save()
            ticket.save()

            _helpdesk_audit(
                request,
                "Exception request approved" if action == "approve"
                else "Exception request rejected",
                ticket,
                {
                    "status": {"from": old_status, "to": exception_request.get_status_display()},
                    "ticket_status": {"from": old_ticket_status, "to": ticket.get_status_display()},
                    "reviewer": request.user.get_full_name() or request.user.username,
                    "feedback": feedback or None,
                    "stage": "IS Council",
                },
            )

            try:
                notify.send(
                    request.user.employee_get,
                    recipient=requestor.employee_user_id,
                    verb=verb,
                    icon="alert-circle",
                    redirect=reverse(
                        "ticket-detail", kwargs={"ticket_id": ticket.id}
                    ),
                )
            except Exception as exc:
                logger.error("Exception request ISC review notify error: %s", exc)
        else:
            for error in review_form.errors.values():
                messages.error(request, error)

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def exception_request_acknowledge(request, er_id):
    """Requestor acknowledges a COMPLETED Exception Request → CLOSED."""
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        exception_request = ExceptionRequest.objects.get(id=er_id)
    except ExceptionRequest.DoesNotExist:
        messages.error(request, _("Exception request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    if not _is_exception_request_owner(request.user, exception_request):
        messages.error(request, _("Only the requestor can close this request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if exception_request.status != "COMPLETED":
        messages.info(request, _("This request is not ready to be closed."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    form = ISOAcknowledgementForm(request.POST)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    comment = form.cleaned_data["comment"]
    ticket = exception_request.ticket
    old_status = exception_request.get_status_display()
    old_ticket_status = ticket.get_status_display()
    exception_request.status = "CLOSED"
    exception_request.closed_by = request.user
    exception_request.save()
    ticket.status = "resolved"
    ticket.save()

    _helpdesk_audit(
        request,
        "Exception request acknowledged",
        ticket,
        {
            "status": {"from": old_status, "to": exception_request.get_status_display()},
            "ticket_status": {"from": old_ticket_status, "to": ticket.get_status_display()},
            "note": comment or None,
        },
    )

    _access_review_comment(
        ticket,
        request.user,
        _("Request Acknowledged – Fulfilled"),
        _("Closed"),
        comment,
        "",
    )
    messages.success(request, _("Request closed. Thank you for confirming."))
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def exception_request_withdraw(request, er_id):
    """Allow the owner to withdraw their own PENDING Exception Request."""
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        exception_request = ExceptionRequest.objects.get(id=er_id)
    except ExceptionRequest.DoesNotExist:
        messages.error(request, _("Exception request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    ticket = exception_request.ticket
    current_employee = getattr(request.user, "employee_get", None)
    if current_employee != ticket.employee_id:
        messages.info(request, _("You don't have permission to withdraw this request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if exception_request.status != "PENDING":
        messages.info(
            request,
            _("This request has already been reviewed and cannot be withdrawn."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    ticket_title = str(ticket)
    _helpdesk_audit(
        request,
        "Exception request withdrawn",
        ticket,
        {"status": exception_request.get_status_display(), "title": ticket_title},
    )
    exception_request.delete()
    ticket.delete()
    messages.success(
        request,
        _('Your exception request "{}" has been withdrawn successfully.').format(
            ticket_title
        ),
    )
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def exception_request_delete(request, er_id):
    """ISO Officer / Superuser deletes an Exception Request and its ticket."""
    if not request.user.is_superuser and not _is_iso_officer(request.user):
        messages.info(request, _("You don't have permission."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if request.method == "POST":
        try:
            exception_request = ExceptionRequest.objects.get(id=er_id)
            ticket = exception_request.ticket
            ticket_title = str(ticket)
            _helpdesk_audit(
                request,
                "Exception request deleted",
                ticket,
                {"status": exception_request.get_status_display(), "title": ticket_title},
            )
            exception_request.delete()
            ticket.delete()
            messages.success(
                request,
                _('The exception request "{}" has been deleted successfully.').format(
                    ticket_title
                ),
            )
        except ExceptionRequest.DoesNotExist:
            messages.error(request, _("Exception request not found."))
        except Exception:
            messages.error(request, _("You cannot delete this exception request."))

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


# ── Admin Access Request views ───────────────────────────────────────────────

def _get_admin_access_request_ticket_type():
    """Return (creating if needed) the TicketType for Admin Access Request tickets."""
    ticket_type, _created = TicketType.objects.get_or_create(
        title="Admin Access Request",
        defaults={"type": "service_request", "prefix": "AAR"},
    )
    return ticket_type


def _build_admin_access_request_description(admin_access_request, user_display):
    """Build the HTML description block shown on the linked Ticket.

    User-controlled values are interpolated via
    :func:`~django.utils.html.format_html`, which escapes every argument. This
    prevents stored XSS even though the resulting description is later rendered
    with the ``|safe`` filter.
    """
    return format_html(
        "<b>Admin Access Request Details:</b><br><br>"
        "<b>User ID (Email):</b> {}<br>"
        "<b>Admin User Type:</b> {}<br>"
        "<b>System / Application:</b> {}<br>"
        "<b>Privilege Level:</b> {}<br>"
        "<b>User:</b> {}<br>"
        "<b>Reason for Need of Privilege:</b> {}",
        admin_access_request.user_id,
        admin_access_request.get_admin_user_type_display(),
        admin_access_request.system_application,
        admin_access_request.privilege_level,
        user_display,
        admin_access_request.reason,
    )


@login_required
@hx_request_required
def admin_access_request_create(request):
    """
    GET  → renders the Admin Access Request modal form.
    POST → creates Ticket + AdminAccessRequest (status PENDING), notifies the
           ISO Officers (Stage 1 approvers).
    """
    form = AdminAccessRequestForm(request=request)

    if request.method == "POST":
        form = AdminAccessRequestForm(request.POST, request=request)
        if form.is_valid():
            ticket_type = _get_admin_access_request_ticket_type()
            priority = form.cleaned_data.get("priority", "medium")
            deadline = form.cleaned_data.get("deadline") or (
                timezone.now() + timedelta(days=7)
            ).date()

            selected_employee = form.cleaned_data["employee"]
            selected_forward_users = list(form.cleaned_data["forward_to"])
            user_display = _format_password_reset_user(selected_employee)

            admin_access_request = form.save(commit=False)
            admin_access_request.status = "PENDING"

            forward_employee_ids, _emps = _get_forward_employee_ids_and_employees(
                selected_forward_users
            )
            # Stage 1 reviewers (ISO Officers) must also receive the ticket, so
            # include their employee IDs in raised_on alongside the Stage 2
            # (IS Council) recipients chosen in "Forward To".
            iso_officer_users = _get_iso_officer_users()
            iso_employee_ids, _iso_emps = _get_forward_employee_ids_and_employees(
                iso_officer_users
            )
            combined_employee_ids = list(
                dict.fromkeys(iso_employee_ids + forward_employee_ids)
            )
            raised_on = ",".join(combined_employee_ids) or str(selected_employee.id)

            app = (admin_access_request.system_application or "").strip()
            short_app = (app[:24] + "...") if len(app) > 27 else app
            ticket = Ticket(
                title=f"Admin Access Request – {short_app}",
                employee_id=selected_employee,
                ticket_type=ticket_type,
                description=_build_admin_access_request_description(
                    admin_access_request, user_display
                ),
                priority=priority,
                assigning_type="individual",
                raised_on=raised_on,
                deadline=deadline,
                status="new",
            )
            ticket.save()
            ticket.assigned_to.add(selected_employee)

            admin_access_request.ticket = ticket
            admin_access_request.save()
            admin_access_request.forward_to.set(selected_forward_users)

            _helpdesk_audit(
                request,
                "Admin access request created",
                ticket,
                {
                    "status": admin_access_request.get_status_display(),
                    "forward_to": admin_access_request.get_forward_to_display() or None,
                },
            )

            notification_actor = getattr(
                request.user, "employee_get", selected_employee
            )

            # Stage 1: notify ISO Officers that a review is required.
            try:
                iso_users = [
                    u
                    for u in _get_iso_officer_users()
                    if u.pk != request.user.pk
                ]
                if iso_users:
                    notify.send(
                        notification_actor,
                        recipient=iso_users,
                        verb=(
                            f"New Admin Access Request submitted by "
                            f"{selected_employee.get_full_name()} awaiting your approval."
                        ),
                        icon="shield",
                        redirect=reverse(
                            "ticket-detail", kwargs={"ticket_id": ticket.id}
                        ),
                    )
            except Exception as exc:
                logger.error("Admin access request ISO notify error: %s", exc)

            messages.success(
                request,
                _("Admin access request submitted successfully."),
            )
            return HttpResponse("<script>window.location.reload()</script>")

    context = {"form": form}
    return render(request, "helpdesk/ticket/admin_access_request_form.html", context)


@login_required
@hx_request_required
def admin_access_request_update(request, aar_id):
    """Allow the owner / ISO officer to edit a PENDING Admin Access Request."""
    try:
        admin_access_request = AdminAccessRequest.objects.get(id=aar_id)
    except AdminAccessRequest.DoesNotExist:
        messages.error(request, _("Admin access request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    ticket = admin_access_request.ticket

    current_employee = getattr(request.user, "employee_get", None)
    has_access = (
        request.user.is_superuser
        or _is_iso_officer(request.user)
        or current_employee == ticket.employee_id
    )
    if not has_access:
        messages.info(request, _("You don't have permission."))
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if admin_access_request.status != "PENDING":
        messages.info(
            request,
            _("This request has already been reviewed and cannot be edited."),
        )
        return HttpResponse("<script>window.location.reload()</script>")

    form = AdminAccessRequestForm(instance=admin_access_request, request=request)
    if request.method == "POST":
        admin_access_request.refresh_from_db()
        if admin_access_request.status != "PENDING":
            messages.info(
                request,
                _("This request has already been reviewed and cannot be edited."),
            )
            return HttpResponse("<script>window.location.reload()</script>")
        form = AdminAccessRequestForm(
            request.POST, instance=admin_access_request, request=request
        )
        if form.is_valid():
            selected_employee = form.cleaned_data["employee"]
            selected_forward_users = list(form.cleaned_data["forward_to"])
            user_display = _format_password_reset_user(selected_employee)

            admin_access_request = form.save(commit=False)
            admin_access_request.save()
            admin_access_request.forward_to.set(selected_forward_users)

            forward_employee_ids, _emps = _get_forward_employee_ids_and_employees(
                selected_forward_users
            )
            iso_officer_users = _get_iso_officer_users()
            iso_employee_ids, _iso_emps = _get_forward_employee_ids_and_employees(
                iso_officer_users
            )
            combined_employee_ids = list(
                dict.fromkeys(iso_employee_ids + forward_employee_ids)
            )
            ticket = Ticket.objects.get(pk=admin_access_request.ticket_id)
            ticket.employee_id = selected_employee
            ticket.priority = form.cleaned_data.get("priority")
            ticket.deadline = form.cleaned_data.get("deadline")
            app = (admin_access_request.system_application or "").strip()
            short_app = (app[:24] + "...") if len(app) > 27 else app
            ticket.title = f"Admin Access Request – {short_app}"
            ticket.description = _build_admin_access_request_description(
                admin_access_request, user_display
            )
            ticket.raised_on = ",".join(combined_employee_ids) or str(
                selected_employee.id
            )
            ticket.save()
            ticket.assigned_to.clear()
            ticket.assigned_to.add(selected_employee)

            log_form_changes(
                request.user,
                "helpdesk",
                "Admin access request updated",
                form,
                target=ticket,
            )

            messages.success(request, _("Admin access request updated successfully."))
            return HttpResponse("<script>window.location.reload()</script>")

    context = {"form": form, "admin_access_request": admin_access_request}
    return render(request, "helpdesk/ticket/admin_access_request_form.html", context)


@login_required
def iso_review_admin_access_request(request, aar_id):
    """
    Stage 1 — ISO Officer approves or rejects a PENDING Admin Access Request.
    Approval advances the request to Stage 2 (IS Council review).
    """
    if not request.user.is_superuser and not _is_iso_officer(request.user):
        messages.info(request, _("Only an ISO Officer can review this request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        admin_access_request = AdminAccessRequest.objects.get(id=aar_id)
    except AdminAccessRequest.DoesNotExist:
        messages.error(request, _("Admin access request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if _is_admin_access_request_owner(request.user, admin_access_request):
        messages.info(request, _("You cannot approve or reject your own request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if admin_access_request.status != "PENDING":
        messages.info(request, _("This request is not awaiting ISO Officer review."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if request.method == "POST":
        review_form = ISOReviewForm(request.POST)
        if review_form.is_valid():
            action = review_form.cleaned_data["action"]
            feedback = review_form.cleaned_data.get("iso_feedback", "").strip()

            admin_access_request.iso_reviewed_by = request.user
            admin_access_request.iso_reviewed_at = timezone.now()
            admin_access_request.feedback = feedback
            ticket = admin_access_request.ticket
            requestor = ticket.employee_id

            old_status = admin_access_request.get_status_display()
            old_ticket_status = ticket.get_status_display()

            if action == "approve":
                admin_access_request.status = "ISO_APPROVED"
                ticket.status = "in_progress"
                _access_review_comment(
                    ticket,
                    request.user,
                    _("ISO Review – Approved"),
                    _("ISO Approved"),
                    _("Forwarded to the IS Council for final approval."),
                    feedback,
                )
                verb = _("Your admin access request has been approved by the ISO Officer and forwarded to the IS Council.")
                messages.success(request, _("Admin access request approved (Stage 1)."))
                try:
                    isc_recipients = [
                        u for u in _get_isc_users() if u.pk != request.user.pk
                    ]
                    if isc_recipients:
                        notify.send(
                            request.user.employee_get,
                            recipient=isc_recipients,
                            verb=(
                                f"Admin access request by {requestor.get_full_name()} "
                                f"is awaiting IS Council approval."
                            ),
                            icon="shield",
                            redirect=reverse(
                                "ticket-detail", kwargs={"ticket_id": ticket.id}
                            ),
                        )
                except Exception as exc:
                    logger.error("Admin access request ISC notify error: %s", exc)
            else:
                admin_access_request.status = "REJECTED"
                ticket.status = "canceled"
                _access_review_comment(
                    ticket,
                    request.user,
                    _("ISO Review – Rejected"),
                    _("Rejected"),
                    _("Your admin access request has been rejected by the ISO Officer."),
                    feedback,
                )
                verb = _("Your admin access request has been rejected by the ISO Officer.")
                messages.success(request, _("Admin access request rejected."))

            admin_access_request.save()
            ticket.save()

            _helpdesk_audit(
                request,
                "Admin access request approved" if action == "approve"
                else "Admin access request rejected",
                ticket,
                {
                    "status": {"from": old_status, "to": admin_access_request.get_status_display()},
                    "ticket_status": {"from": old_ticket_status, "to": ticket.get_status_display()},
                    "reviewer": request.user.get_full_name() or request.user.username,
                    "feedback": feedback or None,
                    "stage": "ISO Officer",
                },
            )

            try:
                notify.send(
                    request.user.employee_get,
                    recipient=requestor.employee_user_id,
                    verb=verb,
                    icon="shield",
                    redirect=reverse(
                        "ticket-detail", kwargs={"ticket_id": ticket.id}
                    ),
                )
            except Exception as exc:
                logger.error("Admin access request ISO review notify error: %s", exc)
        else:
            for error in review_form.errors.values():
                messages.error(request, error)

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def isc_review_admin_access_request(request, aar_id):
    """
    Stage 2 — IS Council approves or rejects an Admin Access Request that has
    already cleared Stage 1 (ISO Officer). Approval completes the request.
    """
    if not request.user.is_superuser and not _is_isc_member(request.user):
        messages.info(request, _("Only an IS Council member can review this request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        admin_access_request = AdminAccessRequest.objects.get(id=aar_id)
    except AdminAccessRequest.DoesNotExist:
        messages.error(request, _("Admin access request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if _is_admin_access_request_owner(request.user, admin_access_request):
        messages.info(request, _("You cannot approve or reject your own request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if admin_access_request.status != "ISO_APPROVED":
        messages.info(
            request,
            _("This request must be approved by the ISO Officer before IS Council review."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if request.method == "POST":
        review_form = ISOReviewForm(request.POST)
        if review_form.is_valid():
            action = review_form.cleaned_data["action"]
            feedback = review_form.cleaned_data.get("iso_feedback", "").strip()

            admin_access_request.isc_reviewed_by = request.user
            admin_access_request.isc_reviewed_at = timezone.now()
            admin_access_request.feedback = feedback
            ticket = admin_access_request.ticket
            requestor = ticket.employee_id

            old_status = admin_access_request.get_status_display()
            old_ticket_status = ticket.get_status_display()

            if action == "approve":
                admin_access_request.status = "COMPLETED"
                ticket.status = "resolved"
                _access_review_comment(
                    ticket,
                    request.user,
                    _("IS Council Review – Approved"),
                    _("Completed"),
                    _("Your admin access request has been approved by the IS Council."),
                    feedback,
                )
                verb = _("Your admin access request has been approved by the IS Council and completed.")
                messages.success(request, _("Admin access request approved (Stage 2)."))
            else:
                admin_access_request.status = "REJECTED"
                ticket.status = "canceled"
                _access_review_comment(
                    ticket,
                    request.user,
                    _("IS Council Review – Rejected"),
                    _("Rejected"),
                    _("Your admin access request has been rejected by the IS Council."),
                    feedback,
                )
                verb = _("Your admin access request has been rejected by the IS Council.")
                messages.success(request, _("Admin access request rejected."))

            admin_access_request.save()
            ticket.save()

            _helpdesk_audit(
                request,
                "Admin access request approved" if action == "approve"
                else "Admin access request rejected",
                ticket,
                {
                    "status": {"from": old_status, "to": admin_access_request.get_status_display()},
                    "ticket_status": {"from": old_ticket_status, "to": ticket.get_status_display()},
                    "reviewer": request.user.get_full_name() or request.user.username,
                    "feedback": feedback or None,
                    "stage": "IS Council",
                },
            )

            try:
                notify.send(
                    request.user.employee_get,
                    recipient=requestor.employee_user_id,
                    verb=verb,
                    icon="shield",
                    redirect=reverse(
                        "ticket-detail", kwargs={"ticket_id": ticket.id}
                    ),
                )
            except Exception as exc:
                logger.error("Admin access request ISC review notify error: %s", exc)
        else:
            for error in review_form.errors.values():
                messages.error(request, error)

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def admin_access_request_acknowledge(request, aar_id):
    """Requestor acknowledges a COMPLETED Admin Access Request → CLOSED."""
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        admin_access_request = AdminAccessRequest.objects.get(id=aar_id)
    except AdminAccessRequest.DoesNotExist:
        messages.error(request, _("Admin access request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    if not _is_admin_access_request_owner(request.user, admin_access_request):
        messages.error(request, _("Only the requestor can close this request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if admin_access_request.status != "COMPLETED":
        messages.info(request, _("This request is not ready to be closed."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    form = ISOAcknowledgementForm(request.POST)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    comment = form.cleaned_data["comment"]
    ticket = admin_access_request.ticket
    old_status = admin_access_request.get_status_display()
    old_ticket_status = ticket.get_status_display()
    admin_access_request.status = "CLOSED"
    admin_access_request.closed_by = request.user
    admin_access_request.save()
    ticket.status = "resolved"
    ticket.save()

    _helpdesk_audit(
        request,
        "Admin access request acknowledged",
        ticket,
        {
            "status": {"from": old_status, "to": admin_access_request.get_status_display()},
            "ticket_status": {"from": old_ticket_status, "to": ticket.get_status_display()},
            "note": comment or None,
        },
    )

    _access_review_comment(
        ticket,
        request.user,
        _("Request Acknowledged – Fulfilled"),
        _("Closed"),
        comment,
        "",
    )
    messages.success(request, _("Request closed. Thank you for confirming."))
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def admin_access_request_withdraw(request, aar_id):
    """Allow the owner to withdraw their own PENDING Admin Access Request."""
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        admin_access_request = AdminAccessRequest.objects.get(id=aar_id)
    except AdminAccessRequest.DoesNotExist:
        messages.error(request, _("Admin access request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    ticket = admin_access_request.ticket
    current_employee = getattr(request.user, "employee_get", None)
    if current_employee != ticket.employee_id:
        messages.info(request, _("You don't have permission to withdraw this request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if admin_access_request.status != "PENDING":
        messages.info(
            request,
            _("This request has already been reviewed and cannot be withdrawn."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    ticket_title = str(ticket)
    _helpdesk_audit(
        request,
        "Admin access request withdrawn",
        ticket,
        {"status": admin_access_request.get_status_display(), "title": ticket_title},
    )
    admin_access_request.delete()
    ticket.delete()
    messages.success(
        request,
        _('Your admin access request "{}" has been withdrawn successfully.').format(
            ticket_title
        ),
    )
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def admin_access_request_delete(request, aar_id):
    """ISO Officer / Superuser deletes an Admin Access Request and its ticket."""
    if not request.user.is_superuser and not _is_iso_officer(request.user):
        messages.info(request, _("You don't have permission."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if request.method == "POST":
        try:
            admin_access_request = AdminAccessRequest.objects.get(id=aar_id)
            ticket = admin_access_request.ticket
            ticket_title = str(ticket)
            _helpdesk_audit(
                request,
                "Admin access request deleted",
                ticket,
                {"status": admin_access_request.get_status_display(), "title": ticket_title},
            )
            admin_access_request.delete()
            ticket.delete()
            messages.success(
                request,
                _('The admin access request "{}" has been deleted successfully.').format(
                    ticket_title
                ),
            )
        except AdminAccessRequest.DoesNotExist:
            messages.error(request, _("Admin access request not found."))
        except Exception:
            messages.error(request, _("You cannot delete this admin access request."))

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


# ── Incident Report views ────────────────────────────────────────────────────


def _get_incident_report_ticket_type():
    """Return (creating if needed) the TicketType for Incident Report tickets."""
    ticket_type, _created = TicketType.objects.get_or_create(
        title="Incident Report",
        defaults={"type": "others", "prefix": "INC"},
    )
    return ticket_type


def _is_incident_report_owner(user, incident_report):
    """Return True when the authenticated user owns the incident report."""
    current_employee = getattr(user, "employee_get", None)
    ticket_employee = getattr(incident_report.ticket, "employee_id", None)
    return bool(
        current_employee and ticket_employee and current_employee == ticket_employee
    )


def _build_incident_report_description(incident_report):
    """Build the HTML description block shown on the linked Ticket.

    Every interpolated value is escaped by :func:`format_html`, preventing
    stored XSS even though the description is later rendered with ``|safe``.
    """
    return format_html(
        "<b>Incident Report Details:</b><br><br>"
        "<b>IR Name:</b> {}<br>"
        "<b>IR Email:</b> {}<br>"
        "<b>Reported By:</b> {}<br>"
        "<b>Incident Reporting Date:</b> {}<br>"
        "<b>Incident Occurrence Date:</b> {}<br>"
        "<b>Incident Occurrence Time:</b> {}<br>"
        "<b>Business Unit / Process Affected:</b> {}<br>"
        "<b>Location of Incident:</b> {}<br>"
        "<b>Duration of Incident:</b> {}<br>"
        "<b>Initial Classification:</b> {}<br>"
        "<b>Incident Description:</b> {}",
        incident_report.ir_name,
        incident_report.ir_email,
        incident_report.get_reported_by_display(),
        incident_report.reporting_date,
        incident_report.occurrence_date,
        incident_report.occurrence_time,
        incident_report.business_unit,
        incident_report.get_location_type_display(),
        incident_report.get_duration_display(),
        incident_report.get_initial_classification_display(),
        incident_report.description,
    )


@login_required
@hx_request_required
def incident_report_create(request):
    """
    GET  → renders the Incident Report modal form (Reporter section).
    POST → creates Ticket + IncidentReport (status PENDING), notifies the
           IS Council members who drive the workflow.
    """
    form = IncidentReportForm(request=request)

    if request.method == "POST":
        form = IncidentReportForm(request.POST, request=request)
        if form.is_valid():
            ticket_type = _get_incident_report_ticket_type()
            priority = form.cleaned_data.get("priority", "medium")
            deadline = form.cleaned_data.get("deadline") or (
                timezone.now() + timedelta(days=7)
            ).date()

            selected_employee = form.cleaned_data["employee"]
            selected_forward_users = list(form.cleaned_data["forward_to"])

            incident_report = form.save(commit=False)
            incident_report.status = "PENDING"

            forward_employee_ids, _emps = _get_forward_employee_ids_and_employees(
                selected_forward_users
            )
            # IS Council members drive the workflow, so route the ticket to the
            # selected ISC recipients (plus all ISC members as a fallback).
            isc_users = _get_isc_users()
            isc_employee_ids, _isc_emps = _get_forward_employee_ids_and_employees(
                isc_users
            )
            combined_employee_ids = list(
                dict.fromkeys(isc_employee_ids + forward_employee_ids)
            )
            raised_on = ",".join(combined_employee_ids) or str(selected_employee.id)

            unit = (incident_report.business_unit or "").strip()
            short_unit = (unit[:27] + "...") if len(unit) > 30 else unit
            ticket = Ticket(
                title=f"Incident Report – {short_unit}",
                employee_id=selected_employee,
                ticket_type=ticket_type,
                description=_build_incident_report_description(incident_report),
                priority=priority,
                assigning_type="individual",
                raised_on=raised_on,
                deadline=deadline,
                status="new",
            )
            ticket.save()
            ticket.assigned_to.add(selected_employee)

            incident_report.ticket = ticket
            incident_report.save()
            incident_report.forward_to.set(selected_forward_users)

            _helpdesk_audit(request, "Incident report created", ticket)

            notification_actor = getattr(
                request.user, "employee_get", selected_employee
            )
            try:
                isc_recipients = [
                    u for u in (selected_forward_users or isc_users)
                    if u.pk != request.user.pk
                ]
                if isc_recipients:
                    notify.send(
                        notification_actor,
                        recipient=isc_recipients,
                        verb=(
                            f"New Incident Report submitted by "
                            f"{selected_employee.get_full_name()} awaiting IS Council review."
                        ),
                        icon="warning",
                        redirect=reverse(
                            "ticket-detail", kwargs={"ticket_id": ticket.id}
                        ),
                    )
            except Exception as exc:
                logger.error("Incident report ISC notify error: %s", exc)

            messages.success(
                request,
                _("Incident report submitted successfully."),
            )
            return HttpResponse("<script>window.location.reload()</script>")

    context = {"form": form}
    return render(request, "helpdesk/ticket/incident_report_form.html", context)


@login_required
@hx_request_required
def incident_report_update(request, inc_id):
    """Allow the owner / ISC member to edit a PENDING Incident Report."""
    try:
        incident_report = IncidentReport.objects.get(id=inc_id)
    except IncidentReport.DoesNotExist:
        messages.error(request, _("Incident report not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    ticket = incident_report.ticket

    current_employee = getattr(request.user, "employee_get", None)
    has_access = (
        request.user.is_superuser
        or _is_isc_member(request.user)
        or current_employee == ticket.employee_id
    )
    if not has_access:
        messages.info(request, _("You don't have permission."))
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if incident_report.status != "PENDING":
        messages.info(
            request,
            _("This report is already under review and cannot be edited."),
        )
        return HttpResponse("<script>window.location.reload()</script>")

    form = IncidentReportForm(instance=incident_report, request=request)
    if request.method == "POST":
        incident_report.refresh_from_db()
        if incident_report.status != "PENDING":
            messages.info(
                request,
                _("This report is already under review and cannot be edited."),
            )
            return HttpResponse("<script>window.location.reload()</script>")
        form = IncidentReportForm(
            request.POST, instance=incident_report, request=request
        )
        if form.is_valid():
            selected_employee = form.cleaned_data["employee"]
            selected_forward_users = list(form.cleaned_data["forward_to"])

            incident_report = form.save(commit=False)
            incident_report.save()
            incident_report.forward_to.set(selected_forward_users)

            forward_employee_ids, _emps = _get_forward_employee_ids_and_employees(
                selected_forward_users
            )
            isc_users = _get_isc_users()
            isc_employee_ids, _isc_emps = _get_forward_employee_ids_and_employees(
                isc_users
            )
            combined_employee_ids = list(
                dict.fromkeys(isc_employee_ids + forward_employee_ids)
            )
            ticket = Ticket.objects.get(pk=incident_report.ticket_id)
            ticket.employee_id = selected_employee
            ticket.priority = form.cleaned_data.get("priority")
            ticket.deadline = form.cleaned_data.get("deadline")
            unit = (incident_report.business_unit or "").strip()
            short_unit = (unit[:27] + "...") if len(unit) > 30 else unit
            ticket.title = f"Incident Report – {short_unit}"
            ticket.description = _build_incident_report_description(incident_report)
            ticket.raised_on = ",".join(combined_employee_ids) or str(
                selected_employee.id
            )
            ticket.save()
            ticket.assigned_to.clear()
            ticket.assigned_to.add(selected_employee)

            _helpdesk_audit(request, "Incident report updated", ticket)
            messages.success(request, _("Incident report updated successfully."))
            return HttpResponse("<script>window.location.reload()</script>")

    context = {"form": form, "incident_report": incident_report}
    return render(request, "helpdesk/ticket/incident_report_form.html", context)


def _incident_report_isc_guard(request, incident_report):
    """Shared ISC-authorization guard for Incident Report transitions.

    Returns an HttpResponse to short-circuit on failure, or ``None`` when the
    caller may proceed.
    """
    if not request.user.is_superuser and not _is_isc_member(request.user):
        messages.info(
            request, _("Only an IS Council member can perform this action.")
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    return None


@login_required
def incident_report_take_review(request, inc_id):
    """ISC takes ownership: PENDING → UNDER_REVIEW (mandatory comment)."""
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        incident_report = IncidentReport.objects.get(id=inc_id)
    except IncidentReport.DoesNotExist:
        messages.error(request, _("Incident report not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    guard = _incident_report_isc_guard(request, incident_report)
    if guard is not None:
        return guard

    if incident_report.status != "PENDING":
        messages.info(request, _("This report is not awaiting review."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    form = IncidentTransitionForm(request.POST)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    comment = form.cleaned_data["comment"]
    ticket = incident_report.ticket
    incident_report.status = "UNDER_REVIEW"
    incident_report.reviewed_by = request.user
    incident_report.reviewed_at = timezone.now()
    incident_report.save()
    ticket.status = "in_progress"
    ticket.save()

    _access_review_comment(
        ticket,
        request.user,
        _("Incident Report – Under Review"),
        _("Under Review"),
        _("The IS Council has taken ownership of this incident report."),
        comment,
    )
    _helpdesk_audit(
        request,
        "Incident report moved to Under Review",
        ticket,
        {"status": {"from": "PENDING", "to": "UNDER_REVIEW"}},
    )

    try:
        notify.send(
            request.user.employee_get,
            recipient=ticket.employee_id.employee_user_id,
            verb=_("Your incident report is now under review by the IS Council."),
            icon="warning",
            redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
        )
    except Exception as exc:
        logger.error("Incident report take-review notify error: %s", exc)

    messages.success(request, _("Incident report is now under review."))
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def incident_report_save_classification(request, inc_id):
    """ISC sets/edits the Post-Review Classification while UNDER_REVIEW."""
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        incident_report = IncidentReport.objects.get(id=inc_id)
    except IncidentReport.DoesNotExist:
        messages.error(request, _("Incident report not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    guard = _incident_report_isc_guard(request, incident_report)
    if guard is not None:
        return guard

    if incident_report.status != "UNDER_REVIEW":
        messages.info(
            request,
            _("Post-Review Classification can only be edited while under review."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    form = IncidentPostReviewForm(request.POST, instance=incident_report)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    incident_report = form.save()
    _helpdesk_audit(
        request,
        "Incident report post-review classification updated",
        incident_report.ticket,
        {"post_review_classification": incident_report.get_post_review_classification_display()},
    )
    messages.success(request, _("Post-Review Classification saved."))
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def incident_report_resolve(request, inc_id):
    """ISC resolves: UNDER_REVIEW → RESOLVED.

    Blocked until the Post-Review Classification is filled (it may be supplied
    on this request or beforehand via the classification editor).
    """
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        incident_report = IncidentReport.objects.get(id=inc_id)
    except IncidentReport.DoesNotExist:
        messages.error(request, _("Incident report not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    guard = _incident_report_isc_guard(request, incident_report)
    if guard is not None:
        return guard

    if incident_report.status != "UNDER_REVIEW":
        messages.info(request, _("This report is not under review."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    form = IncidentTransitionForm(request.POST)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    comment = form.cleaned_data["comment"]
    classification = form.cleaned_data.get("post_review_classification")
    if classification:
        incident_report.post_review_classification = classification

    if not incident_report.post_review_classification:
        messages.error(
            request,
            _("Post-Review Classification is required before resolving."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    ticket = incident_report.ticket
    incident_report.status = "RESOLVED"
    incident_report.resolved_by = request.user
    incident_report.resolved_at = timezone.now()
    incident_report.save()
    ticket.status = "resolved"
    ticket.save()

    _access_review_comment(
        ticket,
        request.user,
        _("Incident Report – Resolved"),
        _("Resolved"),
        _("Post-Review Classification: %(value)s")
        % {"value": incident_report.get_post_review_classification_display()},
        comment,
    )
    _helpdesk_audit(
        request,
        "Incident report resolved",
        ticket,
        {"status": {"from": "UNDER_REVIEW", "to": "RESOLVED"}},
    )

    try:
        notify.send(
            request.user.employee_get,
            recipient=ticket.employee_id.employee_user_id,
            verb=_("Your incident report has been resolved by the IS Council."),
            icon="warning",
            redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
        )
    except Exception as exc:
        logger.error("Incident report resolve notify error: %s", exc)

    messages.success(request, _("Incident report resolved."))
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def incident_report_close(request, inc_id):
    """ISC closes: RESOLVED → CLOSED (mandatory comment)."""
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        incident_report = IncidentReport.objects.get(id=inc_id)
    except IncidentReport.DoesNotExist:
        messages.error(request, _("Incident report not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    guard = _incident_report_isc_guard(request, incident_report)
    if guard is not None:
        return guard

    if incident_report.status != "RESOLVED":
        messages.info(request, _("This report is not ready to be closed."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    form = IncidentTransitionForm(request.POST)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    comment = form.cleaned_data["comment"]
    ticket = incident_report.ticket
    incident_report.status = "CLOSED"
    incident_report.closed_by = request.user
    incident_report.closed_at = timezone.now()
    incident_report.save()
    ticket.status = "resolved"
    ticket.save()

    _access_review_comment(
        ticket,
        request.user,
        _("Incident Report – Closed"),
        _("Closed"),
        _("The IS Council has confirmed this incident is fully closed."),
        comment,
    )
    _helpdesk_audit(
        request,
        "Incident report closed",
        ticket,
        {"status": {"from": "RESOLVED", "to": "CLOSED"}},
    )

    messages.success(request, _("Incident report closed."))
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def incident_report_withdraw(request, inc_id):
    """Allow the owner to withdraw their own PENDING Incident Report."""
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        incident_report = IncidentReport.objects.get(id=inc_id)
    except IncidentReport.DoesNotExist:
        messages.error(request, _("Incident report not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    ticket = incident_report.ticket
    current_employee = getattr(request.user, "employee_get", None)
    if current_employee != ticket.employee_id:
        messages.info(request, _("You don't have permission to withdraw this report."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if incident_report.status != "PENDING":
        messages.info(
            request,
            _("This report is already under review and cannot be withdrawn."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    ticket_title = str(ticket)
    incident_report.delete()
    ticket.delete()
    messages.success(
        request,
        _('Your incident report "{}" has been withdrawn successfully.').format(
            ticket_title
        ),
    )
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def incident_report_delete(request, inc_id):
    """IS Council / Superuser deletes an Incident Report and its ticket."""
    if not request.user.is_superuser and not _is_isc_member(request.user):
        messages.info(request, _("You don't have permission."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if request.method == "POST":
        try:
            incident_report = IncidentReport.objects.get(id=inc_id)
            ticket = incident_report.ticket
            ticket_title = str(ticket)
            incident_report.delete()
            ticket.delete()
            messages.success(
                request,
                _('The incident report "{}" has been deleted successfully.').format(
                    ticket_title
                ),
            )
        except IncidentReport.DoesNotExist:
            messages.error(request, _("Incident report not found."))
        except Exception:
            messages.error(request, _("You cannot delete this incident report."))

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


# ── CHANGE REQUEST VIEWS ─────────────────────────────────────────────────────


def _get_change_request_ticket_type():
    """Return the TicketType for Change Request tickets, creating it if needed."""
    ticket_type, _created = TicketType.objects.get_or_create(
        title="Change Request",
        defaults={"type": "service_request", "prefix": "CHR"},
    )
    return ticket_type


def _employee_division(employee):
    """Return the employee's division (department) name, or empty string."""
    if not employee:
        return ""
    try:
        department = employee.get_department()
        return str(department) if department else ""
    except Exception:
        return ""


def _build_change_request_description(change_request):
    """Render the Section 1 (Change Requester) summary as ticket description."""
    parts = [
        "<b>Change Request Details:</b><br><br>",
        f"<b>Summary:</b> {strip_tags(change_request.summary)}<br>",
        f"<b>Categorisation:</b> {change_request.get_categorisation_display()}<br>",
        f"<b>Reason for Change Categorisation:</b> {strip_tags(change_request.categorisation_reason)}<br>",
        f"<b>Change Type:</b> {change_request.get_change_type_display()}<br>",
    ]
    if change_request.change_type == "temporary" and change_request.expiry_date:
        parts.append(f"<b>Expiry Date:</b> {change_request.expiry_date}<br>")
    parts.extend(
        [
            f"<b>Services / Systems Impacted:</b> {strip_tags(change_request.services_impacted)}<br>",
            f"<b>Change Required By:</b> {change_request.change_required_by}<br>",
            f"<b>Change Requested By:</b> {change_request.change_requested_by}",
        ]
    )
    return "".join(parts)


def _is_change_request_owner(user, change_request):
    """Return True when the authenticated user owns the change request."""
    current_employee = getattr(user, "employee_get", None)
    ticket_employee = getattr(change_request.ticket, "employee_id", None)
    return bool(
        current_employee and ticket_employee and current_employee == ticket_employee
    )


def _change_request_has_edit_access(request, change_request):
    """Return True for any authenticated participant with access to the ticket.

    Used for the Change Implementer (Section 2) and Change Release sections,
    which are collaboratively editable by ticket participants.
    """
    ticket = change_request.ticket
    current_employee = getattr(request.user, "employee_get", None)
    return bool(
        request.user.is_superuser
        or _is_iso_officer(request.user)
        or _is_isc_member(request.user)
        or (current_employee and current_employee == ticket.employee_id)
        or (current_employee and current_employee in ticket.assigned_to.all())
        or (
            current_employee
            and change_request.implementer_id == getattr(current_employee, "id", None)
        )
        or change_request.forward_to.filter(pk=request.user.pk).exists()
    )


@login_required
@hx_request_required
def change_request_create(request):
    """
    GET  → renders the Change Request modal (Section 1 — Change Requester).
    POST → creates Ticket + ChangeRequest (status PENDING), routes to the
           Divisional Head approvers (ISC user group).
    """
    form = ChangeRequesterForm(request=request)

    if request.method == "POST":
        form = ChangeRequesterForm(request.POST, request=request)
        if form.is_valid():
            ticket_type = _get_change_request_ticket_type()
            priority = form.cleaned_data.get("priority", "medium")
            deadline = form.cleaned_data.get("deadline") or (
                timezone.now() + timedelta(days=7)
            ).date()

            selected_employee = form.cleaned_data["employee"]
            selected_forward_users = list(form.cleaned_data["forward_to"])

            change_request = form.save(commit=False)
            change_request.status = "PENDING"

            forward_employee_ids, _emps = _get_forward_employee_ids_and_employees(
                selected_forward_users
            )
            # Stage 1 approvers are the ISC user group (Divisional Head role).
            isc_users = _get_isc_users()
            isc_employee_ids, _isc_emps = _get_forward_employee_ids_and_employees(
                isc_users
            )
            combined_employee_ids = list(
                dict.fromkeys(isc_employee_ids + forward_employee_ids)
            )
            raised_on = ",".join(combined_employee_ids) or str(selected_employee.id)

            ticket = Ticket(
                title=f"Change Request – {change_request.get_categorisation_display()}",
                employee_id=selected_employee,
                ticket_type=ticket_type,
                description=_build_change_request_description(change_request),
                priority=priority,
                assigning_type="individual",
                raised_on=raised_on,
                deadline=deadline,
                status="new",
            )
            ticket.save()
            ticket.assigned_to.add(selected_employee)

            change_request.ticket = ticket
            change_request.save()
            change_request.forward_to.set(selected_forward_users)

            _helpdesk_audit(request, "Change request created", ticket)

            notification_actor = getattr(
                request.user, "employee_get", selected_employee
            )
            try:
                recipients = [
                    u for u in (selected_forward_users or isc_users)
                    if u.pk != request.user.pk
                ]
                if recipients:
                    notify.send(
                        notification_actor,
                        recipient=recipients,
                        verb=(
                            f"New Change Request submitted by "
                            f"{selected_employee.get_full_name()} awaiting Divisional Head review."
                        ),
                        icon="git-branch",
                        redirect=reverse(
                            "ticket-detail", kwargs={"ticket_id": ticket.id}
                        ),
                    )
            except Exception as exc:
                logger.error("Change request DH notify error: %s", exc)

            messages.success(request, _("Change request submitted successfully."))
            return HttpResponse("<script>window.location.reload()</script>")

    context = {"form": form}
    return render(request, "helpdesk/ticket/change_request_form.html", context)


@login_required
@hx_request_required
def change_request_update(request, cr_id):
    """Allow the owner / ISC member to edit a PENDING Change Request (Section 1)."""
    try:
        change_request = ChangeRequest.objects.get(id=cr_id)
    except ChangeRequest.DoesNotExist:
        messages.error(request, _("Change request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    ticket = change_request.ticket

    current_employee = getattr(request.user, "employee_get", None)
    has_access = (
        request.user.is_superuser
        or _is_isc_member(request.user)
        or current_employee == ticket.employee_id
    )
    if not has_access:
        messages.info(request, _("You don't have permission."))
        if "HTTP_HX_REQUEST" in request.META:
            return render(request, "decorator_404.html")
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if change_request.status != "PENDING":
        messages.info(
            request,
            _("This change request has already been reviewed and cannot be edited."),
        )
        return HttpResponse("<script>window.location.reload()</script>")

    form = ChangeRequesterForm(instance=change_request, request=request)
    if request.method == "POST":
        change_request.refresh_from_db()
        if change_request.status != "PENDING":
            messages.info(
                request,
                _("This change request has already been reviewed and cannot be edited."),
            )
            return HttpResponse("<script>window.location.reload()</script>")
        form = ChangeRequesterForm(
            request.POST, instance=change_request, request=request
        )
        if form.is_valid():
            selected_employee = form.cleaned_data["employee"]
            selected_forward_users = list(form.cleaned_data["forward_to"])

            change_request = form.save(commit=False)
            change_request.save()
            change_request.forward_to.set(selected_forward_users)

            forward_employee_ids, _emps = _get_forward_employee_ids_and_employees(
                selected_forward_users
            )
            isc_users = _get_isc_users()
            isc_employee_ids, _isc_emps = _get_forward_employee_ids_and_employees(
                isc_users
            )
            combined_employee_ids = list(
                dict.fromkeys(isc_employee_ids + forward_employee_ids)
            )
            ticket = Ticket.objects.get(pk=change_request.ticket_id)
            ticket.employee_id = selected_employee
            ticket.priority = form.cleaned_data.get("priority")
            ticket.deadline = form.cleaned_data.get("deadline")
            ticket.title = f"Change Request – {change_request.get_categorisation_display()}"
            ticket.description = _build_change_request_description(change_request)
            ticket.raised_on = ",".join(combined_employee_ids) or str(
                selected_employee.id
            )
            ticket.save()
            ticket.assigned_to.clear()
            ticket.assigned_to.add(selected_employee)

            _helpdesk_audit(request, "Change request updated", ticket)
            messages.success(request, _("Change request updated successfully."))
            return HttpResponse("<script>window.location.reload()</script>")

    context = {"form": form, "change_request": change_request}
    return render(request, "helpdesk/ticket/change_request_form.html", context)


@login_required
def change_request_dh_review(request, cr_id):
    """
    Stage 1 — Divisional Head (ISC user group) approves or rejects a PENDING
    Change Request. Approval unlocks Section 2 (Change Implementer).
    """
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if not request.user.is_superuser and not _is_isc_member(request.user):
        messages.info(
            request, _("Only a Divisional Head (ISC) can review this request.")
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        change_request = ChangeRequest.objects.get(id=cr_id)
    except ChangeRequest.DoesNotExist:
        messages.error(request, _("Change request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if _is_change_request_owner(request.user, change_request):
        messages.info(request, _("You cannot approve or reject your own request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if change_request.status != "PENDING":
        messages.info(request, _("This request is not awaiting Divisional Head review."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    review_form = ISOReviewForm(request.POST)
    if not review_form.is_valid():
        for error in review_form.errors.values():
            messages.error(request, error)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    action = review_form.cleaned_data["action"]
    feedback = review_form.cleaned_data.get("iso_feedback", "").strip()
    ticket = change_request.ticket
    requestor = ticket.employee_id

    change_request.dh_reviewed_by = request.user
    change_request.dh_reviewed_at = timezone.now()
    approver_employee = getattr(request.user, "employee_get", None)
    change_request.dh_name = (
        approver_employee.get_full_name() if approver_employee else request.user.username
    )
    change_request.dh_division = _employee_division(approver_employee)

    if action == "approve":
        change_request.status = "DIVISIONAL_HEAD_APPROVED"
        ticket.status = "in_progress"
        _access_review_comment(
            ticket,
            request.user,
            _("Divisional Head Review – Approved"),
            _("Divisional Head Approved"),
            _("Section 2 (Change Implementer) is now unlocked."),
            feedback,
        )
        verb = _("Your change request has been approved by the Divisional Head.")
        messages.success(request, _("Change request approved (Stage 1)."))
    else:
        change_request.status = "REJECTED"
        change_request.feedback = feedback
        ticket.status = "canceled"
        _access_review_comment(
            ticket,
            request.user,
            _("Divisional Head Review – Rejected"),
            _("Rejected"),
            _("Your change request has been rejected by the Divisional Head."),
            feedback,
        )
        verb = _("Your change request has been rejected by the Divisional Head.")
        messages.success(request, _("Change request rejected."))

    change_request.save()
    ticket.save()

    _helpdesk_audit(
        request,
        "Change request Divisional Head review",
        ticket,
        {"status": {"to": change_request.status}},
    )

    try:
        notify.send(
            request.user.employee_get,
            recipient=requestor.employee_user_id,
            verb=verb,
            icon="git-branch",
            redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
        )
    except Exception as exc:
        logger.error("Change request DH review notify error: %s", exc)

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def change_request_save_implementer(request, cr_id):
    """
    Section 2 — Change Implementer. Editable only after Divisional Head approval
    and before the ISO Officer has approved. On save, the implementer's Name and
    Division are auto-populated from the resolved implementer's profile.
    """
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        change_request = ChangeRequest.objects.get(id=cr_id)
    except ChangeRequest.DoesNotExist:
        messages.error(request, _("Change request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if not _change_request_has_edit_access(request, change_request):
        messages.info(request, _("You don't have permission."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if change_request.status not in ("DIVISIONAL_HEAD_APPROVED",):
        messages.info(
            request,
            _("Section 2 can only be edited after Divisional Head approval."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    form = ChangeImplementerForm(
        request.POST, instance=change_request, request=request
    )
    if not form.is_valid():
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    change_request = form.save(commit=False)
    # Resolve the implementer identity and auto-populate Name / Division.
    if change_request.is_self_implementer == "yes":
        implementer_employee = getattr(request.user, "employee_get", None)
        change_request.implementer = implementer_employee
    else:
        implementer_employee = form.cleaned_data.get("implementer")
    if implementer_employee:
        change_request.implementer_name = implementer_employee.get_full_name()
        change_request.implementer_division = _employee_division(implementer_employee)
    change_request.save()

    _access_review_comment(
        change_request.ticket,
        request.user,
        _("Change Implementer – Section Saved"),
        change_request.get_status_display(),
        _("The Change Implementer section has been completed and submitted for ISO review."),
        "",
    )
    _helpdesk_audit(request, "Change request implementer section saved", change_request.ticket)
    messages.success(request, _("Change Implementer section saved."))
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def change_request_iso_review(request, cr_id):
    """
    Stage 2 — ISO Officer evaluation & approval. Branches on ISO Approval and
    the Section 1 categorisation.
    """
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if not request.user.is_superuser and not _is_iso_officer(request.user):
        messages.info(request, _("Only an ISO Officer can evaluate this request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        change_request = ChangeRequest.objects.get(id=cr_id)
    except ChangeRequest.DoesNotExist:
        messages.error(request, _("Change request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if change_request.status != "DIVISIONAL_HEAD_APPROVED":
        messages.info(request, _("This request is not awaiting ISO evaluation."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if not change_request.implementation_overview:
        messages.info(
            request,
            _("The Change Implementer section must be completed before ISO evaluation."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    form = ISOEvaluationForm(request.POST, instance=change_request)
    if not form.is_valid():
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    change_request = form.save(commit=False)
    change_request.iso_reviewed_by = request.user
    change_request.iso_reviewed_at = timezone.now()
    ticket = change_request.ticket
    requestor = ticket.employee_id
    iso_comments = (change_request.iso_comments or "").strip()

    if change_request.iso_approval == "no":
        change_request.status = "REJECTED"
        change_request.feedback = iso_comments
        ticket.status = "canceled"
        change_request.save()
        ticket.save()
        _access_review_comment(
            ticket,
            request.user,
            _("ISO Evaluation – Rejected"),
            _("Rejected"),
            _("Your change request has been rejected by the ISO Officer."),
            iso_comments,
        )
        verb = _("Your change request has been rejected by the ISO Officer.")
        messages.success(request, _("Change request rejected."))
    elif change_request.requires_isc():
        change_request.status = "ISO_APPROVED_PENDING_ISC"
        change_request.isc_needed = "yes"
        ticket.status = "in_progress"
        change_request.save()
        ticket.save()
        _access_review_comment(
            ticket,
            request.user,
            _("ISO Evaluation – Approved"),
            change_request.get_status_display(),
            _("Approved by ISO. Forwarded to the IS Council for final approval."),
            iso_comments,
        )
        verb = _(
            "Your change request has been approved by the ISO Officer and forwarded to the IS Council."
        )
        messages.success(request, _("Change request approved – pending ISC."))
        try:
            isc_recipients = [
                u for u in _get_isc_users() if u.pk != request.user.pk
            ]
            if isc_recipients:
                notify.send(
                    request.user.employee_get,
                    recipient=isc_recipients,
                    verb=(
                        f"Change request by {requestor.get_full_name()} "
                        f"is awaiting IS Council approval."
                    ),
                    icon="git-branch",
                    redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
                )
        except Exception as exc:
            logger.error("Change request ISC notify error: %s", exc)
    else:
        change_request.status = "ISO_APPROVED"
        change_request.isc_needed = "no"
        ticket.status = "in_progress"
        change_request.save()
        ticket.save()
        _access_review_comment(
            ticket,
            request.user,
            _("ISO Evaluation – Approved"),
            change_request.get_status_display(),
            _("Approved by ISO. Proceed directly to Change Release."),
            iso_comments,
        )
        verb = _("Your change request has been approved by the ISO Officer.")
        messages.success(request, _("Change request approved."))

    _helpdesk_audit(
        request,
        "Change request ISO evaluation",
        ticket,
        {"status": {"to": change_request.status}},
    )

    try:
        notify.send(
            request.user.employee_get,
            recipient=requestor.employee_user_id,
            verb=verb,
            icon="git-branch",
            redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
        )
    except Exception as exc:
        logger.error("Change request ISO review notify error: %s", exc)

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def change_request_isc_review(request, cr_id):
    """Stage 3 — ISC approval (Major / Emergency categorisation only)."""
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if not request.user.is_superuser and not _is_isc_member(request.user):
        messages.info(request, _("Only an IS Council member can approve this request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        change_request = ChangeRequest.objects.get(id=cr_id)
    except ChangeRequest.DoesNotExist:
        messages.error(request, _("Change request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if change_request.status != "ISO_APPROVED_PENDING_ISC":
        messages.info(request, _("This request is not awaiting IS Council approval."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    form = ISCApprovalForm(request.POST, instance=change_request)
    if not form.is_valid():
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    change_request = form.save(commit=False)
    change_request.isc_reviewed_by = request.user
    change_request.isc_reviewed_at = timezone.now()
    ticket = change_request.ticket
    requestor = ticket.employee_id
    isc_comments = (change_request.isc_comments or "").strip()

    if change_request.isc_approval == "yes":
        change_request.status = "FULLY_APPROVED"
        ticket.status = "in_progress"
        _access_review_comment(
            ticket,
            request.user,
            _("ISC Approval – Approved"),
            _("Fully Approved"),
            _("Approved by the IS Council. Change Release is now unlocked."),
            isc_comments,
        )
        verb = _("Your change request has been fully approved by the IS Council.")
        messages.success(request, _("Change request fully approved."))
    else:
        change_request.status = "REJECTED"
        change_request.feedback = isc_comments
        ticket.status = "canceled"
        _access_review_comment(
            ticket,
            request.user,
            _("ISC Approval – Rejected"),
            _("Rejected"),
            _("Your change request has been rejected by the IS Council."),
            isc_comments,
        )
        verb = _("Your change request has been rejected by the IS Council.")
        messages.success(request, _("Change request rejected."))

    change_request.save()
    ticket.save()

    _helpdesk_audit(
        request,
        "Change request ISC approval",
        ticket,
        {"status": {"to": change_request.status}},
    )

    try:
        notify.send(
            request.user.employee_get,
            recipient=requestor.employee_user_id,
            verb=verb,
            icon="git-branch",
            redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
        )
    except Exception as exc:
        logger.error("Change request ISC review notify error: %s", exc)

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def change_request_save_release(request, cr_id):
    """
    Change Release — collaboratively editable by any ticket participant once all
    required approvals are complete. When all six Release fields are completed,
    the ticket moves to Closed.
    """
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        change_request = ChangeRequest.objects.get(id=cr_id)
    except ChangeRequest.DoesNotExist:
        messages.error(request, _("Change request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if not _change_request_has_edit_access(request, change_request):
        messages.info(request, _("You don't have permission."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if not change_request.release_unlocked():
        messages.info(
            request,
            _("Change Release is available only once all approvals are complete."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    form = ChangeReleaseForm(request.POST, instance=change_request)
    if not form.is_valid():
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    change_request = form.save(commit=False)
    ticket = change_request.ticket

    # All six Release fields are required by the form, so a successful save
    # means the release is complete → move to Closed.
    change_request.status = "CLOSED"
    change_request.closed_by = request.user
    ticket.status = "resolved"
    change_request.save()
    ticket.save()

    _access_review_comment(
        ticket,
        request.user,
        _("Change Release – Completed"),
        _("Closed"),
        _("All Change Release fields have been completed. The change is now closed."),
        "",
    )
    _helpdesk_audit(
        request,
        "Change request closed",
        ticket,
        {"status": {"to": "CLOSED"}},
    )
    messages.success(request, _("Change Release completed. The change request is now closed."))
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def change_request_withdraw(request, cr_id):
    """Allow the owner to withdraw their own PENDING Change Request."""
    if request.method != "POST":
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    try:
        change_request = ChangeRequest.objects.get(id=cr_id)
    except ChangeRequest.DoesNotExist:
        messages.error(request, _("Change request not found."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    ticket = change_request.ticket
    current_employee = getattr(request.user, "employee_get", None)
    if current_employee != ticket.employee_id:
        messages.info(request, _("You don't have permission to withdraw this request."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if change_request.status != "PENDING":
        messages.info(
            request,
            _("This request has already been reviewed and cannot be withdrawn."),
        )
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    ticket_title = str(ticket)
    change_request.delete()
    ticket.delete()
    messages.success(
        request,
        _('Your change request "{}" has been withdrawn successfully.').format(
            ticket_title
        ),
    )
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
def change_request_delete(request, cr_id):
    """IS Council / Superuser deletes a Change Request and its ticket."""
    if not request.user.is_superuser and not _is_isc_member(request.user):
        messages.info(request, _("You don't have permission."))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    if request.method == "POST":
        try:
            change_request = ChangeRequest.objects.get(id=cr_id)
            ticket = change_request.ticket
            ticket_title = str(ticket)
            change_request.delete()
            ticket.delete()
            messages.success(
                request,
                _('The change request "{}" has been deleted successfully.').format(
                    ticket_title
                ),
            )
        except ChangeRequest.DoesNotExist:
            messages.error(request, _("Change request not found."))
        except Exception:
            messages.error(request, _("You cannot delete this change request."))

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
