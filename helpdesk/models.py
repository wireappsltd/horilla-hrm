import os
from datetime import datetime

from django import apps
from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_delete, post_save
from django.forms import ValidationError
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from base.models import Company, Department, JobPosition, Tags
from employee.models import Employee
from horilla import horilla_middlewares
from horilla.models import HorillaModel
from horilla_audit.methods import get_diff
from horilla_audit.models import HorillaAuditInfo, HorillaAuditLog

PRIORITY = [
    ("low", "Low"),
    ("medium", "Medium"),
    ("high", "High"),
]

# Name of the Django auth Group whose members act as ISO Officers.
ISO_GROUP_NAME = "ISO"

# Name of the Django auth Group whose members act as IS Council (ISC) members
# (Stage 2 approvers of the two-stage Exception Request workflow).
ISC_GROUP_NAME = "IS Council"

MANAGER_TYPES = [
    ("department", "Department"),
    ("job_position", "Job Position"),
    ("individual", "Individual"),
]

TICKET_TYPES = [
    ("suggestion", "Suggestion"),
    ("complaint", "Complaint"),
    ("service_request", "Service Request"),
    ("meeting_request", "Meeting Request"),
    ("anounymous_complaint", "Anonymous Complaint"),
    ("others", "Others"),
]

TICKET_STATUS = [
    ("new", "New"),
    ("in_progress", "In Progress"),
    ("on_hold", "On Hold"),
    ("resolved", "Resolved"),
    ("canceled", "Canceled"),
]

PASSWORD_RESET_PLATFORMS = [
    ("Microsoft", "Microsoft"),
    ("Passbolt", "Passbolt"),
    ("Plane", "Plane"),
    ("Other", "Other"),
]

# ISO Review lifecycle (single source of truth for the password-reset workflow).

ISO_STATUS_CHOICES = [
    ("PENDING", "Pending"),
    ("IN_ACTION", "In Action"),
    ("AWAITING_ACKNOWLEDGEMENT", "Awaiting Acknowledgement"),
    ("CLOSED", "Closed"),
    ("REJECTED", "Rejected"),
]

ISO_REQUEST_TYPE_CHOICES = [
    ("password_reset", "Password Reset Request"),
]

# ── Access Request & Deactivation (ISO Forms) ────────────────────────────────

# Sub-type selector shown in the "Access Request & Deactivation" modal.
ACCESS_REQUEST_SUBTYPE_CHOICES = [
    ("access_request", "Access Request"),
    ("access_deactivation", "Access Deactivation"),
]

ACCESS_BUSINESS_CRITICAL_CHOICES = [
    ("yes", "Yes"),
    ("no", "No"),
]

ACCESS_LEVEL_CHOICES = [
    ("read", "Read"),
    ("write", "Write"),
    ("repository", "Repository"),
]

ACCESS_DOMAIN_CHOICES = [
    ("ftp_access", "FTP Access"),
    ("o365_access", "O365 Access"),
    ("email", "Email"),
]

# Two-stage approval lifecycle for Access Requests:
#   PENDING → (Divisional Head / IS Council approves) ISO_APPROVED
#           → (ISO Officer approves) COMPLETED
#           → (requestor acknowledges) CLOSED
# Rejection at either stage is terminal → REJECTED.
# NOTE: The intermediate status value remains "ISO_APPROVED" for backwards
# compatibility, but it now represents Divisional Head (Stage 1) approval.
ACCESS_REQUEST_STATUS_CHOICES = [
    ("PENDING", "Pending"),
    ("ISO_APPROVED", "Divisional Head Approved"),
    ("COMPLETED", "Completed"),
    ("CLOSED", "Closed"),
    ("REJECTED", "Rejected"),
]

# Two-stage approval lifecycle for Exception Requests:
#   PENDING → (ISO Officer approves) ISO_APPROVED → (IS Council approves)
#           COMPLETED → (requestor acknowledges) CLOSED
# Rejection at either stage is terminal → REJECTED.
EXCEPTION_REQUEST_STATUS_CHOICES = [
    ("PENDING", "Pending"),
    ("ISO_APPROVED", "ISO Approved"),
    ("COMPLETED", "Completed"),
    ("CLOSED", "Closed"),
    ("REJECTED", "Rejected"),
]

ADMIN_ACCESS_TYPE_CHOICES = [
    ("PROMOTE_EXISTING", "Promote Existing User"),
    ("NEW_USER", "New User (Admin)"),
]
ADMIN_ACCESS_REQUEST_STATUS_CHOICES = [
    ("PENDING", "Pending"),
    ("ISO_APPROVED", "ISO Approved"),
    ("COMPLETED", "Completed"),
    ("CLOSED", "Closed"),
    ("REJECTED", "Rejected"),
]

# ── Incident Report (ISO Forms) ──────────────────────────────────────────────
# "Reported By" selector on the Reporter section.
INCIDENT_REPORTED_BY_CHOICES = [
    ("employee", "Employee"),
    ("client", "Client"),
    ("vendor", "Vendor"),
]

# Physical / Virtual location of the incident.
INCIDENT_LOCATION_CHOICES = [
    ("physical", "Physical"),
    ("virtual", "Virtual"),
]

# Classification used by both the reporter (Initial Classification) and the
# ISC (Post-Review Classification).
INCIDENT_CLASSIFICATION_CHOICES = [
    ("devastating", "Devastating"),
    ("high", "High"),
    ("medium", "Medium"),
    ("low_event", "Low - Event"),
]

# ISC-driven lifecycle for Incident Reports (distinct from the two-stage
# ISO Officer → IS Council workflow used by the other ISO Forms). There is no
# rejection status for this form type:
#   PENDING → (ISC takes ownership) UNDER_REVIEW
#           → (ISC completes Post-Review Classification) RESOLVED
#           → (ISC confirms closure) CLOSED
INCIDENT_REPORT_STATUS_CHOICES = [
    ("PENDING", "Pending"),
    ("UNDER_REVIEW", "Under Review"),
    ("RESOLVED", "Resolved"),
    ("CLOSED", "Closed"),
]

# ── Change Request (ISO Forms) ───────────────────────────────────────────────
# System / process change management form. Project deployments are out of scope
# (handled in Plane). Four-section, multi-actor, multi-stage workflow that is
# distinct from the other Help Desk / ISO forms.

# Section 1 — Change Categorisation drives whether Stage 3 (ISC) is required.
CHANGE_CATEGORISATION_CHOICES = [
    ("standard", "Standard"),
    ("minor", "Minor"),
    ("major", "Major"),
    ("emergency", "Emergency"),
]

CHANGE_TYPE_CHOICES = [
    ("permanent", "Permanent"),
    ("temporary", "Temporary"),
]

# Generic Yes / No selector reused across Sections 2–4.
CHANGE_YES_NO_CHOICES = [
    ("yes", "Yes"),
    ("no", "No"),
]

# Categorisations that require the Stage 3 (ISC) approval branch after the ISO
# Officer approves.
CHANGE_ISC_REQUIRED_CATEGORISATIONS = ("major", "emergency")

# Full status workflow (Rejected is terminal at any stage):
#   PENDING
#     → (Divisional Head / ISC approves) DIVISIONAL_HEAD_APPROVED
#     → (ISO Officer approves, Standard/Minor) ISO_APPROVED
#     → (ISO Officer approves, Major/Emergency) ISO_APPROVED_PENDING_ISC
#         → (ISC approves) FULLY_APPROVED
#     → (Change Release started) IN_RELEASE
#     → (all Release fields completed) CLOSED
CHANGE_REQUEST_STATUS_CHOICES = [
    ("PENDING", "Pending"),
    ("DIVISIONAL_HEAD_APPROVED", "Divisional Head Approved"),
    ("ISO_APPROVED", "ISO Approved"),
    ("ISO_APPROVED_PENDING_ISC", "ISO Approved — Pending ISC"),
    ("FULLY_APPROVED", "Fully Approved"),
    ("IN_RELEASE", "In Release"),
    ("CLOSED", "Closed"),
    ("REJECTED", "Rejected"),
]


class DepartmentManager(HorillaModel):
    manager = models.ForeignKey(
        Employee,
        verbose_name=_("Manager"),
        related_name="dep_manager",
        on_delete=models.CASCADE,
    )
    department = models.ForeignKey(
        Department,
        verbose_name=_("Department"),
        related_name="dept_manager",
        on_delete=models.CASCADE,
    )
    company_id = models.ForeignKey(
        Company, null=True, editable=False, on_delete=models.PROTECT
    )
    objects = HorillaCompanyManager("manager__employee_work_info__company_id")

    class Meta:
        unique_together = ("department", "manager")
        verbose_name = _("Department Manager")
        verbose_name_plural = _("Department Managers")

    def clean(self, *args, **kwargs):
        super().clean(*args, **kwargs)
        if not self.manager.get_department() == self.department:
            raise ValidationError(_(f"This employee is not from {self.department} ."))


class TicketType(HorillaModel):
    title = models.CharField(max_length=100, unique=True, verbose_name=_("Title"))
    type = models.CharField(choices=TICKET_TYPES, max_length=50, verbose_name=_("Type"))
    prefix = models.CharField(max_length=3, unique=True, verbose_name=_("Prefix"))
    company_id = models.ForeignKey(
        Company, null=True, editable=False, on_delete=models.PROTECT
    )
    objects = HorillaCompanyManager(related_company_field="company_id")

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = _("Ticket Type")
        verbose_name_plural = _("Ticket Types")


class Ticket(HorillaModel):

    title = models.CharField(max_length=50)
    employee_id = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="ticket", verbose_name="Owner"
    )
    ticket_type = models.ForeignKey(
        TicketType,
        on_delete=models.PROTECT,
        verbose_name="Ticket Type",
    )
    description = models.TextField(max_length=255)
    priority = models.CharField(choices=PRIORITY, max_length=100, default="low")
    created_date = models.DateField(auto_now_add=True)
    resolved_date = models.DateField(blank=True, null=True)
    assigning_type = models.CharField(
        choices=MANAGER_TYPES, max_length=100, verbose_name=_("Assigning Type")
    )
    raised_on = models.CharField(max_length=500, verbose_name=_("Forward To"))
    assigned_to = models.ManyToManyField(
        Employee, blank=True, related_name="ticket_assigned_to"
    )
    deadline = models.DateField(null=True, blank=True)
    tags = models.ManyToManyField(Tags, blank=True, related_name="ticket_tags")
    status = models.CharField(choices=TICKET_STATUS, default="new", max_length=50)
    history = HorillaAuditLog(
        related_name="history_set",
        bases=[
            HorillaAuditInfo,
        ],
        excluded_fields=[
            "modified_by",
            "created_by",
            "created_at",
            "is_active",
            "description",
            "title",
            "assigning_type",
            "ticket_type",
            "raised_on",
        ],
    )
    objects = HorillaCompanyManager(
        related_company_field="employee_id__employee_work_info__company_id"
    )

    class Meta:
        ordering = ["-created_date"]
        verbose_name = _("Ticket")
        verbose_name_plural = _("Tickets")

    def clean(self, *args, **kwargs):
        super().clean(*args, **kwargs)
        deadline = self.deadline
        today = datetime.today().date()
        if deadline and deadline < today:
            raise ValidationError(_("Deadline should be greater than today"))

    def _parse_raised_on_ids(self):
        """Return list of ID strings stored in raised_on (supports comma-separated)."""
        if not self.raised_on:
            return []
        return [rid.strip() for rid in str(self.raised_on).split(",") if rid.strip()]

    def get_raised_on(self):
        ids = self._parse_raised_on_ids()
        if not ids:
            return ""
        names = []
        for obj_id in ids:
            try:
                if self.assigning_type == "department":
                    names.append(Department.objects.get(id=obj_id).department)
                elif self.assigning_type == "job_position":
                    names.append(JobPosition.objects.get(id=obj_id).job_position)
                elif self.assigning_type == "individual":
                    names.append(Employee.objects.get(id=obj_id).get_full_name())
            except (ValueError, Department.DoesNotExist, JobPosition.DoesNotExist, Employee.DoesNotExist):
                names.append(str(_("Unknown/Deleted Entity")))
        return ", ".join(names)

    def get_raised_on_object(self):
        """Return the first forwarded-to object (backward-compatible)."""
        ids = self._parse_raised_on_ids()
        if not ids:
            return None
        obj_id = ids[0]
        try:
            if self.assigning_type == "department":
                return Department.objects.get(id=obj_id)
            elif self.assigning_type == "job_position":
                return JobPosition.objects.get(id=obj_id)
            elif self.assigning_type == "individual":
                return Employee.objects.get(id=obj_id)
        except (ValueError, Department.DoesNotExist, JobPosition.DoesNotExist, Employee.DoesNotExist):
            return None

    def get_raised_on_objects(self):
        """Return a list of all forwarded-to objects."""
        ids = self._parse_raised_on_ids()
        objects = []
        for obj_id in ids:
            try:
                if self.assigning_type == "department":
                    objects.append(Department.objects.get(id=obj_id))
                elif self.assigning_type == "job_position":
                    objects.append(JobPosition.objects.get(id=obj_id))
                elif self.assigning_type == "individual":
                    objects.append(Employee.objects.get(id=obj_id))
            except (ValueError, Department.DoesNotExist, JobPosition.DoesNotExist, Employee.DoesNotExist):
                pass
        return objects

    def __str__(self):
        return self.title or f"Ticket {self.id}"

    def tracking(self):
        """
        This method is used to return the tracked history of the instance
        """
        return get_diff(self)


class PasswordResetRequest(HorillaModel):
    """
    Stores the extra details for a Password Reset ticket.
    Linked 1-to-1 with a Ticket via the `ticket` field.
    """

    ticket = models.OneToOneField(
        Ticket,
        on_delete=models.CASCADE,
        related_name="password_reset_request",
    )
    request_type = models.CharField(
        max_length=50,
        choices=ISO_REQUEST_TYPE_CHOICES,
        default="password_reset",
        verbose_name=_("Type"),
    )
    platform = models.CharField(
        max_length=50,
        choices=PASSWORD_RESET_PLATFORMS,
        verbose_name=_("Platform"),
    )
    user_id = models.EmailField(verbose_name=_("User ID (Email)"))
    reason = models.TextField(verbose_name=_("Reason for Request"))
    forward_to = models.ManyToManyField(
        User,
        blank=True,
        related_name="forwarded_password_reset_requests",
        verbose_name=_("Forward To"),
    )

    iso_status = models.CharField(
        # max_length=30 to accommodate the longest code "AWAITING_ACKNOWLEDGEMENT".
        max_length=30,
        choices=ISO_STATUS_CHOICES,
        default="PENDING",
        verbose_name=_("ISO Status"),
    )
    iso_feedback = models.TextField(blank=True, null=True, verbose_name=_("ISO Feedback"))

    reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_password_resets",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    approved_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_password_resets",
        verbose_name=_("Approved By"),
        help_text=_("Set when a Pending request is approved (→ In Action)."),
    )
    actioned_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="actioned_password_resets",
        verbose_name=_("Actioned By"),
        help_text=_(
            "Set when the request moves In Action → Awaiting Acknowledgement."
        ),
    )
    closed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="closed_password_resets",
        verbose_name=_("Closed By"),
        help_text=_("Set when the requestor closes the request (→ Closed)."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated At"))
    history = HorillaAuditLog(
        related_name="history_set",
        bases=[
            HorillaAuditInfo,
        ],
        m2m_fields=["forward_to"],
        excluded_fields=[
            "ticket",
            "request_type",
            "reviewed_by",
            "reviewed_at",
            "approved_by",
            "actioned_by",
            "closed_by",
            "created_at",
            "updated_at",
            "is_active",
            "modified_by",
            "created_by",
            "user_id",
        ],
    )

    class Meta:
        verbose_name = _("Password Reset Request")
        verbose_name_plural = _("Password Reset Requests")

    def __str__(self):
        return f"Password Reset – {self.platform} – {self.ticket}"

    def tracking(self):
        """
        Return the tracked history of this PasswordResetRequest instance.
        """
        return get_diff(self)

    def get_forward_to_users(self):
        """Return selected forwarding users as a queryset."""
        return self.forward_to.select_related("employee_get").all()

    def get_forward_to_display(self):
        """Return a comma-separated list of forwarded-to user display names."""
        users = self.get_forward_to_users()
        names = []
        for user in users:
            try:
                names.append(user.employee_get.get_full_name())
            except Exception:
                names.append(user.get_full_name() or user.username)
        return ", ".join([name for name in names if name])

    def clean(self, *args, **kwargs):
        super().clean(*args, **kwargs)
        if self.iso_status == "REJECTED" and not self.iso_feedback:
            raise ValidationError(
                {"iso_feedback": _("Feedback is required when rejecting a request.")}
            )


class AccessRequest(HorillaModel):
    """
    Stores the extra details for an "Access Request & Deactivation" ticket
    (ISO Forms category). Linked 1-to-1 with a Ticket via the ``ticket`` field.

    Mirrors :class:`PasswordResetRequest` but adds the structured access-request
    fields and a two-stage approval workflow (ISO Officer → IS Council).
    """

    ticket = models.OneToOneField(
        Ticket,
        on_delete=models.CASCADE,
        related_name="access_request",
    )
    sub_type = models.CharField(
        max_length=30,
        choices=ACCESS_REQUEST_SUBTYPE_CHOICES,
        default="access_request",
        verbose_name=_("Sub Type"),
    )
    user_id = models.EmailField(verbose_name=_("User ID (Email)"))
    requested_date = models.DateField(verbose_name=_("Requested Date"))
    effective_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("Effective Date"),
    )

    business_critical = models.CharField(
        max_length=3,
        choices=ACCESS_BUSINESS_CRITICAL_CHOICES,
        null=True,
        blank=True,
        verbose_name=_("Business Critical Systems"),
    )
    level_of_access = models.CharField(
        max_length=20,
        choices=ACCESS_LEVEL_CHOICES,
        null=True,
        blank=True,
        verbose_name=_("Level of Access"),
    )
    domain = models.CharField(
        max_length=20,
        choices=ACCESS_DOMAIN_CHOICES,
        verbose_name=_("Domain"),
    )
    reason = models.TextField(verbose_name=_("Reason for Request"))
    forward_to = models.ManyToManyField(
        User,
        blank=True,
        related_name="forwarded_access_requests",
        verbose_name=_("Forward To"),
    )

    status = models.CharField(
        max_length=20,
        choices=ACCESS_REQUEST_STATUS_CHOICES,
        default="PENDING",
        verbose_name=_("Status"),
    )
    feedback = models.TextField(blank=True, null=True, verbose_name=_("Feedback"))

    # Stage 1 — ISO Officer review
    iso_reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="iso_reviewed_access_requests",
        verbose_name=_("ISO Officer"),
    )
    iso_reviewed_at = models.DateTimeField(null=True, blank=True)

    # Stage 2 — IS Council review
    isc_reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="isc_reviewed_access_requests",
        verbose_name=_("IS Council"),
    )
    isc_reviewed_at = models.DateTimeField(null=True, blank=True)

    closed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="closed_access_requests",
        verbose_name=_("Closed By"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated At"))
    history = HorillaAuditLog(
        related_name="history_set",
        bases=[
            HorillaAuditInfo,
        ],
        m2m_fields=["forward_to"],
        excluded_fields=[
            "ticket",
            "user_id",
            "iso_reviewed_by",
            "iso_reviewed_at",
            "isc_reviewed_by",
            "isc_reviewed_at",
            "closed_by",
            "created_at",
            "updated_at",
            "is_active",
            "modified_by",
            "created_by",
        ],
    )

    class Meta:
        verbose_name = _("Access Request")
        verbose_name_plural = _("Access Requests")

    def __str__(self):
        return f"Access Request – {self.get_domain_display()} – {self.ticket}"

    def tracking(self):
        """Return the tracked history of this AccessRequest instance."""
        return get_diff(self)

    def get_forward_to_users(self):
        """Return selected forwarding users as a queryset."""
        return self.forward_to.select_related("employee_get").all()

    def get_forward_to_display(self):
        """Return a comma-separated list of forwarded-to user display names."""
        names = []
        for user in self.get_forward_to_users():
            try:
                names.append(user.employee_get.get_full_name())
            except Exception:
                names.append(user.get_full_name() or user.username)
        return ", ".join([name for name in names if name])

    def clean(self, *args, **kwargs):
        super().clean(*args, **kwargs)
        if self.status == "REJECTED" and not self.feedback:
            raise ValidationError(
                {"feedback": _("Feedback is required when rejecting a request.")}
            )


class ExceptionRequest(HorillaModel):
    """
    Stores the extra details for an "Exception Request" ticket (ISO Forms
    category).
    """

    ticket = models.OneToOneField(
        Ticket,
        on_delete=models.CASCADE,
        related_name="exception_request",
    )
    user_id = models.EmailField(verbose_name=_("User ID (Email)"))
    description = models.TextField(verbose_name=_("Description of Exception"))
    isms_reference = models.CharField(
        max_length=250,
        verbose_name=_("ISMS Reference"),
    )
    forward_to = models.ManyToManyField(
        User,
        blank=True,
        related_name="forwarded_exception_requests",
        verbose_name=_("Forward To"),
    )

    status = models.CharField(
        max_length=20,
        choices=EXCEPTION_REQUEST_STATUS_CHOICES,
        default="PENDING",
        verbose_name=_("Status"),
    )
    feedback = models.TextField(blank=True, null=True, verbose_name=_("Feedback"))

    # Stage 1 — ISO Officer review
    iso_reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="iso_reviewed_exception_requests",
        verbose_name=_("ISO Officer"),
    )
    iso_reviewed_at = models.DateTimeField(null=True, blank=True)

    # Stage 2 — IS Council review
    isc_reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="isc_reviewed_exception_requests",
        verbose_name=_("IS Council"),
    )
    isc_reviewed_at = models.DateTimeField(null=True, blank=True)

    closed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="closed_exception_requests",
        verbose_name=_("Closed By"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated At"))
    history = HorillaAuditLog(
        related_name="history_set",
        bases=[
            HorillaAuditInfo,
        ],
        m2m_fields=["forward_to"],
        excluded_fields=[
            "ticket",
            "user_id",
            "iso_reviewed_by",
            "iso_reviewed_at",
            "isc_reviewed_by",
            "isc_reviewed_at",
            "closed_by",
            "created_at",
            "updated_at",
            "is_active",
            "modified_by",
            "created_by",
        ],
    )

    class Meta:
        verbose_name = _("Exception Request")
        verbose_name_plural = _("Exception Requests")

    def __str__(self):
        return f"Exception Request – {self.isms_reference} – {self.ticket}"

    def tracking(self):
        """Return the tracked history of this ExceptionRequest instance."""
        return get_diff(self)

    def get_forward_to_users(self):
        """Return selected forwarding users as a queryset."""
        return self.forward_to.select_related("employee_get").all()

    def get_forward_to_display(self):
        """Return a comma-separated list of forwarded-to user display names."""
        names = []
        for user in self.get_forward_to_users():
            try:
                names.append(user.employee_get.get_full_name())
            except Exception:
                names.append(user.get_full_name() or user.username)
        return ", ".join([name for name in names if name])

    def clean(self, *args, **kwargs):
        super().clean(*args, **kwargs)
        if self.status == "REJECTED" and not self.feedback:
            raise ValidationError(
                {"feedback": _("Feedback is required when rejecting a request.")}
            )


class AdminAccessRequest(HorillaModel):
    """
    Stores the extra details for an "Admin Access Request" ticket (ISO Forms
    category).
    """

    ticket = models.OneToOneField(
        Ticket,
        on_delete=models.CASCADE,
        related_name="admin_access_request",
    )
    user_id = models.EmailField(verbose_name=_("User ID (Email)"))
    admin_user_type = models.CharField(
        max_length=20,
        choices=ADMIN_ACCESS_TYPE_CHOICES,
        verbose_name=_("Admin User Type"),
    )
    system_application = models.CharField(
        max_length=250,
        verbose_name=_("System / Application"),
    )
    privilege_level = models.CharField(
        max_length=250,
        verbose_name=_("Privilege Level"),
    )
    reason = models.TextField(verbose_name=_("Reason for Need of Privilege"))
    forward_to = models.ManyToManyField(
        User,
        blank=True,
        related_name="forwarded_admin_access_requests",
        verbose_name=_("Forward To"),
    )

    status = models.CharField(
        max_length=20,
        choices=ADMIN_ACCESS_REQUEST_STATUS_CHOICES,
        default="PENDING",
        verbose_name=_("Status"),
    )
    feedback = models.TextField(blank=True, null=True, verbose_name=_("Feedback"))

    # Stage 1 — ISO Officer review
    iso_reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="iso_reviewed_admin_access_requests",
        verbose_name=_("ISO Officer"),
    )
    iso_reviewed_at = models.DateTimeField(null=True, blank=True)

    # Stage 2 — IS Council review
    isc_reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="isc_reviewed_admin_access_requests",
        verbose_name=_("IS Council"),
    )
    isc_reviewed_at = models.DateTimeField(null=True, blank=True)

    closed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="closed_admin_access_requests",
        verbose_name=_("Closed By"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated At"))
    history = HorillaAuditLog(
        related_name="history_set",
        bases=[
            HorillaAuditInfo,
        ],
        m2m_fields=["forward_to"],
        excluded_fields=[
            "ticket",
            "user_id",
            "iso_reviewed_by",
            "iso_reviewed_at",
            "isc_reviewed_by",
            "isc_reviewed_at",
            "closed_by",
            "created_at",
            "updated_at",
            "is_active",
            "modified_by",
            "created_by",
        ],
    )

    class Meta:
        verbose_name = _("Admin Access Request")
        verbose_name_plural = _("Admin Access Requests")

    def __str__(self):
        return f"Admin Access Request – {self.system_application} – {self.ticket}"

    def tracking(self):
        """Return the tracked history of this AdminAccessRequest instance."""
        return get_diff(self)

    def get_forward_to_users(self):
        """Return selected forwarding users as a queryset."""
        return self.forward_to.select_related("employee_get").all()

    def get_forward_to_display(self):
        """Return a comma-separated list of forwarded-to user display names."""
        names = []
        for user in self.get_forward_to_users():
            try:
                names.append(user.employee_get.get_full_name())
            except Exception:
                names.append(user.get_full_name() or user.username)
        return ", ".join([name for name in names if name])

    def clean(self, *args, **kwargs):
        super().clean(*args, **kwargs)
        if self.status == "REJECTED" and not self.feedback:
            raise ValidationError(
                {"feedback": _("Feedback is required when rejecting a request.")}
            )


class IncidentReport(HorillaModel):
    """
    Stores the extra details for an "Incident Report" ticket (ISO Forms
    category).

    The Reporter section is filled by the logged-in user at submission. The
    Post-Review Classification section is filled exclusively by IS Council
    (ISC) members once the report reaches ``UNDER_REVIEW``.

    Unlike the other ISO Forms, the lifecycle is a single-track, ISC-driven
    workflow (Pending → Under Review → Resolved → Closed) with no rejection
    state.
    """

    DESCRIPTION_MAX_LENGTH = 1000

    ticket = models.OneToOneField(
        Ticket,
        on_delete=models.CASCADE,
        related_name="incident_report",
    )

    # ── Reporter section (filled by the requestor) ──
    ir_name = models.CharField(max_length=200, verbose_name=_("IR Name"))
    ir_email = models.EmailField(verbose_name=_("IR Email"))
    reported_by = models.CharField(
        max_length=20,
        choices=INCIDENT_REPORTED_BY_CHOICES,
        verbose_name=_("Reported By"),
    )
    reporting_date = models.DateField(verbose_name=_("Incident Reporting Date"))
    occurrence_date = models.DateField(verbose_name=_("Incident Occurrence Date"))
    occurrence_time = models.TimeField(verbose_name=_("Incident Occurrence Time"))
    business_unit = models.CharField(
        max_length=250,
        verbose_name=_("Business Unit / Process Affected"),
    )
    location_type = models.CharField(
        max_length=20,
        choices=INCIDENT_LOCATION_CHOICES,
        verbose_name=_("Physical / Virtual Location of Incident"),
    )
    duration_hours = models.PositiveIntegerField(
        default=0, verbose_name=_("Duration of Incident (Hours)")
    )
    duration_minutes = models.PositiveIntegerField(
        default=0, verbose_name=_("Duration of Incident (Minutes)")
    )
    description = models.TextField(
        max_length=DESCRIPTION_MAX_LENGTH,
        verbose_name=_("Incident Description"),
    )
    initial_classification = models.CharField(
        max_length=20,
        choices=INCIDENT_CLASSIFICATION_CHOICES,
        verbose_name=_("Initial Classification"),
    )

    # ── Post-Review Classification section (ISC only, editable at UNDER_REVIEW) ──
    post_review_classification = models.CharField(
        max_length=20,
        choices=INCIDENT_CLASSIFICATION_CHOICES,
        null=True,
        blank=True,
        verbose_name=_("Post-Review Classification"),
    )

    forward_to = models.ManyToManyField(
        User,
        blank=True,
        related_name="forwarded_incident_reports",
        verbose_name=_("Forward To"),
    )

    status = models.CharField(
        max_length=20,
        choices=INCIDENT_REPORT_STATUS_CHOICES,
        default="PENDING",
        verbose_name=_("Status"),
    )

    # ISC member who moved PENDING → UNDER_REVIEW (took ownership).
    reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_incident_reports",
        verbose_name=_("Reviewed By"),
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # ISC member who moved UNDER_REVIEW → RESOLVED.
    resolved_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resolved_incident_reports",
        verbose_name=_("Resolved By"),
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    # ISC member who moved RESOLVED → CLOSED.
    closed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="closed_incident_reports",
        verbose_name=_("Closed By"),
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated At"))

    class Meta:
        verbose_name = _("Incident Report")
        verbose_name_plural = _("Incident Reports")

    def __str__(self):
        return f"Incident Report – {self.business_unit} – {self.ticket}"

    def get_duration_display(self):
        """Return a human readable duration like '2 hr 30 min'."""
        hours = self.duration_hours or 0
        minutes = self.duration_minutes or 0
        return f"{hours} hr {minutes} min"

    def get_forward_to_users(self):
        """Return selected forwarding users as a queryset."""
        return self.forward_to.select_related("employee_get").all()

    def get_forward_to_display(self):
        """Return a comma-separated list of forwarded-to user display names."""
        names = []
        for user in self.get_forward_to_users():
            try:
                names.append(user.employee_get.get_full_name())
            except Exception:
                names.append(user.get_full_name() or user.username)
        return ", ".join([name for name in names if name])

    def clean(self, *args, **kwargs):
        super().clean(*args, **kwargs)
        # Block the Under Review → Resolved transition until the Post-Review
        # Classification is filled (required before that transition).
        if self.status == "RESOLVED" and not self.post_review_classification:
            raise ValidationError(
                {
                    "post_review_classification": _(
                        "Post-Review Classification is required before resolving."
                    )
                }
            )


class ChangeRequest(HorillaModel):
    """
    Stores the extra details for a "Change Request" ticket (ISO Forms
    category) covering system and process changes (project deployments are out
    of scope). Linked 1-to-1 with a Ticket via the ``ticket`` field.

    Four sections, filled by different actors at different stages:
      * Section 1 — Change Requester (filled at submission).
      * Section 2 — Change Implementer (unlocked after Divisional Head approval).
      * Stage 2 — ISO Officer evaluation & approval.
      * Stage 3 — ISC approval (Major / Emergency categorisation only).
      * Change Release — editable by any ticket participant once approved.
    """

    SUMMARY_MAX_LENGTH = 1000
    IMPLEMENTATION_OVERVIEW_MAX_LENGTH = 1000
    COMMENTS_MAX_LENGTH = 500
    RELEASE_TEXT_MAX_LENGTH = 1000

    ticket = models.OneToOneField(
        Ticket,
        on_delete=models.CASCADE,
        related_name="change_request",
    )

    # ── Section 1 — Change Requester (filled at submission) ──
    user_id = models.EmailField(verbose_name=_("User ID (Email)"))
    summary = models.TextField(
        max_length=SUMMARY_MAX_LENGTH,
        verbose_name=_("Summary of Change Requirement"),
    )
    categorisation = models.CharField(
        max_length=20,
        choices=CHANGE_CATEGORISATION_CHOICES,
        verbose_name=_("Change Categorisation"),
    )
    categorisation_reason = models.TextField(
        verbose_name=_("Reason for Change Categorisation"),
    )
    change_type = models.CharField(
        max_length=20,
        choices=CHANGE_TYPE_CHOICES,
        verbose_name=_("Change Type"),
    )
    expiry_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("Expiry Date"),
    )
    services_impacted = models.TextField(
        verbose_name=_("List of Services / Systems Impacted"),
    )
    change_required_by = models.DateField(verbose_name=_("Change Required By Date"))
    change_requested_by = models.DateField(verbose_name=_("Change Requested By Date"))

    # ── Section 2 — Change Implementer (unlocked after DH approval) ──
    is_self_implementer = models.CharField(
        max_length=3,
        choices=CHANGE_YES_NO_CHOICES,
        null=True,
        blank=True,
        verbose_name=_("Are you the Change Implementer?"),
    )
    implementer = models.ForeignKey(
        Employee,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="change_request_implementer",
        verbose_name=_("Change Implementer"),
    )
    implementer_name = models.CharField(
        max_length=200, blank=True, verbose_name=_("Implementer Name")
    )
    implementer_division = models.CharField(
        max_length=200, blank=True, verbose_name=_("Implementer Division")
    )
    implementation_overview = models.TextField(
        max_length=IMPLEMENTATION_OVERVIEW_MAX_LENGTH,
        null=True,
        blank=True,
        verbose_name=_("Implementation Overview"),
    )
    effort_estimate = models.CharField(
        max_length=250, null=True, blank=True, verbose_name=_("Effort Estimate")
    )
    special_support = models.CharField(
        max_length=3,
        choices=CHANGE_YES_NO_CHOICES,
        null=True,
        blank=True,
        verbose_name=_("Special Support Required"),
    )
    special_support_description = models.TextField(
        null=True, blank=True, verbose_name=_("Special Support Description")
    )
    other_resources = models.TextField(
        null=True, blank=True, verbose_name=_("Other Resources")
    )
    alternatives = models.TextField(
        null=True, blank=True, verbose_name=_("Alternatives (if any)")
    )
    system_outage = models.CharField(
        max_length=3,
        choices=CHANGE_YES_NO_CHOICES,
        null=True,
        blank=True,
        verbose_name=_("System Outage Required"),
    )
    scheduled_outage_date = models.DateField(
        null=True, blank=True, verbose_name=_("Scheduled Outage Date")
    )
    scheduled_outage_time = models.TimeField(
        null=True, blank=True, verbose_name=_("Scheduled Outage Time")
    )
    business_impact = models.TextField(
        null=True, blank=True, verbose_name=_("Business Impact")
    )

    # ── Stage 1 — Divisional Head (fulfilled by the ISC user group) ──
    dh_reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dh_reviewed_change_requests",
        verbose_name=_("Divisional Head"),
    )
    dh_reviewed_at = models.DateTimeField(null=True, blank=True)
    dh_name = models.CharField(
        max_length=200, blank=True, verbose_name=_("Divisional Head Name")
    )
    dh_division = models.CharField(
        max_length=200, blank=True, verbose_name=_("Divisional Head Division")
    )

    # ── Stage 2 — ISO Officer evaluation & approval ──
    iso_complies = models.CharField(
        max_length=3,
        choices=CHANGE_YES_NO_CHOICES,
        null=True,
        blank=True,
        verbose_name=_("Change Complies with Company Policies?"),
    )
    iso_impact = models.TextField(
        null=True, blank=True, verbose_name=_("Impact of Implementing Change")
    )
    iso_risk_assessment = models.TextField(
        null=True, blank=True, verbose_name=_("Risk Assessment on the Change")
    )
    iso_approval = models.CharField(
        max_length=3,
        choices=CHANGE_YES_NO_CHOICES,
        null=True,
        blank=True,
        verbose_name=_("ISO Approval"),
    )
    iso_comments = models.TextField(
        max_length=COMMENTS_MAX_LENGTH,
        null=True,
        blank=True,
        verbose_name=_("ISO Comments"),
    )
    iso_reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="iso_reviewed_change_requests",
        verbose_name=_("ISO Officer"),
    )
    iso_reviewed_at = models.DateTimeField(null=True, blank=True)

    # ── Stage 3 — ISC approval (Major / Emergency only) ──
    isc_needed = models.CharField(
        max_length=3,
        choices=CHANGE_YES_NO_CHOICES,
        default="no",
        verbose_name=_("Is ISC Approval Needed?"),
    )
    isc_approval = models.CharField(
        max_length=3,
        choices=CHANGE_YES_NO_CHOICES,
        null=True,
        blank=True,
        verbose_name=_("ISC Approval"),
    )
    isc_comments = models.TextField(
        max_length=COMMENTS_MAX_LENGTH,
        null=True,
        blank=True,
        verbose_name=_("ISC Comments"),
    )
    isc_reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="isc_reviewed_change_requests",
        verbose_name=_("IS Council"),
    )
    isc_reviewed_at = models.DateTimeField(null=True, blank=True)

    # ── Change Release ──
    test_plan = models.TextField(
        max_length=RELEASE_TEXT_MAX_LENGTH,
        null=True,
        blank=True,
        verbose_name=_("Test Plan"),
    )
    test_results = models.TextField(
        max_length=RELEASE_TEXT_MAX_LENGTH,
        null=True,
        blank=True,
        verbose_name=_("Test Results"),
    )
    rollback_plan = models.TextField(
        max_length=RELEASE_TEXT_MAX_LENGTH,
        null=True,
        blank=True,
        verbose_name=_("Rollback Plan"),
    )
    uat_accepted = models.CharField(
        max_length=3,
        choices=CHANGE_YES_NO_CHOICES,
        null=True,
        blank=True,
        verbose_name=_("UAT Accepted"),
    )
    released_to_production = models.CharField(
        max_length=3,
        choices=CHANGE_YES_NO_CHOICES,
        null=True,
        blank=True,
        verbose_name=_("Change Released to Production"),
    )
    acceptance_of_completion = models.CharField(
        max_length=3,
        choices=CHANGE_YES_NO_CHOICES,
        null=True,
        blank=True,
        verbose_name=_("Acceptance of Change Completion"),
    )

    forward_to = models.ManyToManyField(
        User,
        blank=True,
        related_name="forwarded_change_requests",
        verbose_name=_("Forward To"),
    )

    status = models.CharField(
        max_length=30,
        choices=CHANGE_REQUEST_STATUS_CHOICES,
        default="PENDING",
        verbose_name=_("Status"),
    )
    feedback = models.TextField(blank=True, null=True, verbose_name=_("Feedback"))

    closed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="closed_change_requests",
        verbose_name=_("Closed By"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated At"))

    class Meta:
        verbose_name = _("Change Request")
        verbose_name_plural = _("Change Requests")

    def __str__(self):
        return f"Change Request – {self.get_categorisation_display()} – {self.ticket}"

    # ── Workflow helpers ──
    def requires_isc(self):
        """Return True when the categorisation triggers the Stage 3 (ISC) branch."""
        return self.categorisation in CHANGE_ISC_REQUIRED_CATEGORISATIONS

    def implementer_section_unlocked(self):
        """Section 2 becomes editable only once the Divisional Head has approved."""
        return self.status not in ("PENDING", "REJECTED")

    def iso_section_unlocked(self):
        """Stage 2 becomes available once Section 2 is saved / submitted."""
        return self.status in (
            "DIVISIONAL_HEAD_APPROVED",
            "ISO_APPROVED",
            "ISO_APPROVED_PENDING_ISC",
            "FULLY_APPROVED",
            "IN_RELEASE",
            "CLOSED",
        )

    def release_unlocked(self):
        """Change Release is editable once all required approvals are complete.

        Standard / Minor unlock at ISO Approved; Major / Emergency unlock at
        Fully Approved. Once release editing has started (IN_RELEASE) it stays
        unlocked.
        """
        return self.status in ("ISO_APPROVED", "FULLY_APPROVED", "IN_RELEASE")

    def is_terminal(self):
        return self.status in ("REJECTED", "CLOSED")

    def get_forward_to_users(self):
        """Return selected forwarding users as a queryset."""
        return self.forward_to.select_related("employee_get").all()

    def get_forward_to_display(self):
        """Return a comma-separated list of forwarded-to user display names."""
        names = []
        for user in self.get_forward_to_users():
            try:
                names.append(user.employee_get.get_full_name())
            except Exception:
                names.append(user.get_full_name() or user.username)
        return ", ".join([name for name in names if name])

    def clean(self, *args, **kwargs):
        super().clean(*args, **kwargs)
        if self.change_type == "temporary" and not self.expiry_date:
            raise ValidationError(
                {"expiry_date": _("Expiry Date is required for a temporary change.")}
            )
        if self.status == "REJECTED" and not self.feedback:
            raise ValidationError(
                {"feedback": _("Feedback is required when rejecting a request.")}
            )


def cleanup_iso_user_password_reset_assignments(user):
    """
    Remove a user from ISO password reset routing when ISO group membership is removed.
    """
    if not user:
        return

    employee = getattr(user, "employee_get", None)
    request_filter = Q(forward_to=user)
    if employee:
        request_filter |= Q(ticket__assigned_to=employee)

    requests = (
        PasswordResetRequest.objects.filter(request_filter)
        .select_related("ticket", "ticket__employee_id")
        .distinct()
    )

    for request in requests:
        ticket = request.ticket

        if request.forward_to.filter(pk=user.pk).exists():
            request.forward_to.remove(user)

        if employee and ticket.assigned_to.filter(pk=employee.pk).exists():
            ticket.assigned_to.remove(employee)

        if employee and ticket.assigning_type == "individual":
            existing_ids = ticket._parse_raised_on_ids()
            filtered_ids = [obj_id for obj_id in existing_ids if obj_id != str(employee.id)]
            if not filtered_ids and ticket.employee_id_id:
                filtered_ids = [str(ticket.employee_id_id)]

            updated_raised_on = ",".join(filtered_ids)
            if updated_raised_on != (ticket.raised_on or ""):
                ticket.raised_on = updated_raised_on
                ticket.save(update_fields=["raised_on"])


class ClaimRequest(HorillaModel):
    ticket_id = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    employee_id = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    is_approved = models.BooleanField(default=False)
    is_rejected = models.BooleanField(default=False)

    class Meta:
        unique_together = ("ticket_id", "employee_id")

    def __str__(self) -> str:
        return f"{self.ticket_id}|{self.employee_id}"

    def clean(self, *args, **kwargs):
        super().clean(*args, **kwargs)
        if not self.ticket_id:
            raise ValidationError({"ticket_id": _("This field is required.")})
        if not self.employee_id:
            raise ValidationError({"employee_id": _("This field is required.")})


class Comment(HorillaModel):
    comment = models.TextField(null=True, blank=True)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="comment")
    employee_id = models.ForeignKey(
        Employee, on_delete=models.DO_NOTHING, related_name="employee_comment"
    )
    date = models.DateTimeField(auto_now_add=True)
    is_auto_generated = models.BooleanField(default=False)
    xss_exempt_fields = ["comment"]  # 850

    def __str__(self):
        return self.comment or ""


class Attachment(HorillaModel):
    file = models.FileField(upload_to="Tickets/Attachment")
    description = models.CharField(max_length=100, blank=True, null=True)
    format = models.CharField(max_length=50, blank=True, null=True)
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ticket_attachment",
    )
    comment = models.ForeignKey(
        Comment,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="comment_attachment",
    )

    def get_file_format(self):
        image_format = [".jpg", ".jpeg", ".png", ".svg"]
        audio_format = [".m4a", ".mp3"]
        file_extension = os.path.splitext(self.file.url)[1].lower()
        if file_extension in audio_format:
            self.format = "audio"
        elif file_extension in image_format:
            self.format = "image"
        else:
            self.format = "file"

    def save(self, *args, **kwargs):
        self.get_file_format()
        super().save(*args, **kwargs)

    def __str__(self):
        return os.path.basename(self.file.name)


class FAQCategory(HorillaModel):
    title = models.CharField(max_length=30)
    description = models.TextField(blank=True, null=True, max_length=255)
    company_id = models.ForeignKey(
        Company,
        null=True,
        blank=True,
        editable=False,
        verbose_name=_("Company"),
        on_delete=models.CASCADE,
    )
    objects = HorillaCompanyManager()

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        request = getattr(horilla_middlewares._thread_locals, "request", None)
        if request:
            selected_company = request.session.get("selected_company")
            if (
                not self.id
                and not self.company_id
                and selected_company
                and selected_company != "all"
            ):
                self.company_id = Company.find(selected_company)
        super().save()

    class Meta:
        verbose_name = _("FAQ Category")
        verbose_name_plural = _("FAQ Categories")


class FAQ(HorillaModel):
    question = models.CharField(max_length=255)
    answer = models.TextField()
    tags = models.ManyToManyField(Tags, blank=True)
    category = models.ForeignKey(FAQCategory, on_delete=models.PROTECT)
    company_id = models.ForeignKey(
        Company, null=True, editable=False, on_delete=models.PROTECT
    )
    objects = HorillaCompanyManager()

    def __str__(self):
        return self.question

    def save(self, *args, **kwargs):
        request = getattr(horilla_middlewares._thread_locals, "request", None)
        if request:
            selected_company = request.session.get("selected_company")
            if (
                not self.id
                and not self.company_id
                and selected_company
                and selected_company != "all"
            ):
                self.company_id = Company.find(selected_company)
        super().save()

    class Meta:
        verbose_name = _("FAQ")
        verbose_name_plural = _("FAQs")