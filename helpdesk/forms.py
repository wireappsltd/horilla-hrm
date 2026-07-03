"""
forms.py

This module contains the form classes used in the application.

Each form represents a specific functionality or data input in the
application. They are responsible for validating
and processing user input data.

Classes:
- YourForm: Represents a form for handling specific data input.

Usage:
from django import forms
from django.db.models import Q

class YourForm(forms.Form):
    field_name = forms.CharField()

    def clean_field_name(self):
        # Custom validation logic goes here
        pass
"""

from typing import Any

from django import forms
from django.contrib.auth.models import User
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm
from base.methods import filtersubordinatesemployeemodel, is_reportingmanager
from base.models import Department, JobPosition
from employee.forms import MultipleFileField
from employee.models import Employee
from helpdesk.models import (
    FAQ,
    PRIORITY,
    ISO_GROUP_NAME,
    ISC_GROUP_NAME,
    AccessRequest,
    AdminAccessRequest,
    Attachment,
    Comment,
    DepartmentManager,
    ExceptionRequest,
    FAQCategory,
    PasswordResetRequest,
    Ticket,
    TicketType,
)
from horilla import horilla_middlewares


class TicketTypeForm(ModelForm):

    class Meta:
        model = TicketType
        fields = "__all__"
        exclude = ["is_active"]

    def as_p(self, *args, **kwargs):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("horilla_form.html", context)
        return table_html


class FAQForm(ModelForm):
    class Meta:
        model = FAQ
        fields = "__all__"
        exclude = ["is_active"]
        widgets = {
            "category": forms.HiddenInput(),
            "tags": forms.SelectMultiple(
                attrs={
                    "class": "oh-select oh-select-2 select2-hidden-accessible",
                    "onchange": "updateTag(this)",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        """
        Initializes the Ticket tag form instance.
        If an instance is provided, sets the initial value for the form's .
        """
        super().__init__(*args, **kwargs)
        self.fields["tags"].choices = list(self.fields["tags"].choices)
        self.fields["tags"].choices.append(("create_new_tag", "Create new tag"))


class TicketForm(ModelForm):
    deadline = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = Ticket
        fields = [
            "id",
            "title",
            "employee_id",
            "description",
            "ticket_type",
            "priority",
            "assigning_type",
            "raised_on",
            "deadline",
            "status",
            "tags",
        ]
        widgets = {
            "raised_on": forms.Select(
                attrs={"class": "oh-select oh-select-2", "required": "true"}
            ),
            "description": forms.Textarea(
                attrs={
                    "data-summernote": True,
                    "hidden": True,
                }
            ),
        }

    def as_p(self, *args, **kwargs):
        """
        Render the form fields as HTML table rows with Bootstrap styling.
        """
        context = {"form": self}
        table_html = render_to_string("horilla_form.html", context)
        return table_html

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields.pop("attachment", None)
        else:
            self.fields["attachment"] = MultipleFileField(
                label="Attachements", required=False
            )
        request = getattr(horilla_middlewares._thread_locals, "request", None)
        instance = kwargs.get("instance")
        if instance:
            employee = instance.employee_id
        else:
            employee = request.user.employee_get
        # initialising employee queryset according to the user
        self.fields["employee_id"].queryset = filtersubordinatesemployeemodel(
            request, Employee.objects.filter(is_active=True), perm="helpdesk.add_ticket"
        ) | Employee.objects.filter(employee_user_id=request.user)
        self.fields["employee_id"].initial = employee
        # appending dynamic create option according to user
        if is_reportingmanager(request) or request.user.has_perm(
            "helpdesk.add_tickettype"
        ):
            self.fields["ticket_type"].choices = list(
                self.fields["ticket_type"].choices
            )
            self.fields["ticket_type"].choices.append(
                ("create_new_ticket_type", "Create new ticket type")
            )
        if is_reportingmanager(request) or request.user.has_perm("base.add_tags"):
            self.fields["tags"].choices = list(self.fields["tags"].choices)
            self.fields["tags"].choices.append(("create_new_tag", "Create new tag"))


class PasswordResetRequestForm(forms.ModelForm):
    """
    Form for employees to submit a Password Reset request.

    The `employee` field is a dropdown:
      - Regular users only see themselves (pre-selected, read-only effectively).
      - Superusers/admins see all active employees.

    On save, user_email is populated from the selected employee's company email.
    """

    REASON_MAX_LENGTH = 250

    employee = forms.ModelChoiceField(
        queryset=Employee.objects.none(),  # populated in __init__
        label=_("User ID (Email)"),
        widget=forms.Select(attrs={"class": "oh-select oh-select-2 w-100"}),
    )

    priority = forms.ChoiceField(
        choices=PRIORITY,
        initial="medium",
        label=_("Priority"),
        widget=forms.Select(attrs={"class": "oh-select oh-select-2 w-100"}),
    )

    forward_to = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        label=_("Forward To"),
        required=True,
        widget=forms.SelectMultiple(
            attrs={"class": "oh-select oh-select-2 w-100"}
        ),
    )

    deadline = forms.DateField(
        required=False,
        label=_("Due Date"),
        widget=forms.DateInput(
            attrs={
                "class": "oh-input w-100",
                "type": "date",
            }
        ),
    )

    class Meta:
        model = PasswordResetRequest
        fields = ["request_type", "platform", "employee", "forward_to", "reason"]
        widgets = {
            "request_type": forms.Select(
                attrs={"class": "oh-select oh-select-2 w-100"}
            ),
            "platform": forms.Select(
                attrs={"class": "oh-select oh-select-2 w-100"}
            ),
            "reason": forms.Textarea(
                attrs={
                    "class": "oh-input w-100",
                    "rows": 4,
                    "placeholder": _("Describe why you need a password reset"),
                }
            ),
        }
        labels = {
            "request_type": _("Type"),
            "platform": _("Platform"),
            "reason": _("Reason for request"),
        }

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        self.fields["deadline"].widget.attrs["min"] = today.isoformat()

        # Resolve the employee tied to this request (ticket owner)
        selected_employee = None
        if self.instance and self.instance.pk and getattr(self.instance, "ticket", None):
            selected_employee = getattr(self.instance.ticket, "employee_id", None)
        if not selected_employee and self.instance and self.instance.pk and self.instance.user_id:
            try:
                selected_employee = Employee.objects.get(
                    employee_work_info__email=self.instance.user_id
                )
            except Exception:
                selected_employee = None

        # Forward To → show all ISO officers and IS Council members.
        self.fields["forward_to"].queryset = (
            User.objects.filter(
                groups__name__in=[ISO_GROUP_NAME, ISC_GROUP_NAME], is_active=True
            )
            .distinct()
            .order_by("first_name", "username")
        )
        self.fields["forward_to"].label_from_instance = self._forward_to_label

        reason_error_message = _("Reason cannot exceed %(max_length)s characters.") % {
            "max_length": self.REASON_MAX_LENGTH,
        }
        reason_field = self.fields["reason"]
        reason_field.max_length = self.REASON_MAX_LENGTH
        reason_field.help_text = _("Max %(max_length)s characters") % {
            "max_length": self.REASON_MAX_LENGTH,
        }
        reason_field.error_messages["max_length"] = reason_error_message
        reason_field.widget.attrs.update(
            {
                "data-maxlength": str(self.REASON_MAX_LENGTH),
                "data-maxlength-message": reason_error_message,
            }
        )

        # Who can pick any employee: superuser or ISO officer
        is_iso_officer = False
        if request:
            is_iso_officer = request.user.groups.filter(name=ISO_GROUP_NAME).exists()

        employee_filter = Q(pk=-1)  # start empty; add allowed employees below
        if request and (request.user.is_superuser or is_iso_officer):
            employee_filter |= Q(is_active=True)
        elif request:
            try:
                emp = request.user.employee_get
                employee_filter |= Q(pk=emp.pk)
                selected_employee = selected_employee or emp
            except Exception:
                pass

        if selected_employee:
            employee_filter |= Q(pk=selected_employee.pk)
            self.initial["employee"] = selected_employee
            self.fields["employee"].initial = selected_employee

        self.fields["employee"].queryset = Employee.objects.filter(employee_filter).order_by(
            "employee_first_name"
        )

        # ── Forward To: keep queryset as User objects (set above) ──
        # The model's forward_to M2M targets User, so the queryset must use
        # User objects.  The queryset was already set above; we only need to
        # build a reference to the ISO-member User queryset for initial values.
        iso_user_qs = self.fields["forward_to"].queryset

        # Pre-select: if editing, use the saved forward_to M2M; otherwise default to all ISO members
        if self.instance and self.instance.pk:
            # For editing, use the saved forward_to M2M as the authoritative initial.
            # Filter to only include users still in the ISO / IS Council groups.
            saved_forward = self.instance.forward_to.filter(
                groups__name__in=[ISO_GROUP_NAME, ISC_GROUP_NAME], is_active=True
            ).distinct()
            if saved_forward.exists():
                self.fields["forward_to"].initial = saved_forward
                # Override ModelForm's auto-populated initial (which includes
                # users that were removed from the ISO group) with the
                # filtered queryset.
                self.initial["forward_to"] = list(saved_forward.values_list("pk", flat=True))
            else:
                # Fallback: map raised_on Employee IDs → User objects (for legacy data)
                existing_ids = [
                    rid.strip()
                    for rid in (getattr(self.instance, "ticket", None) and self.instance.ticket.raised_on or "").split(",")
                    if rid.strip()
                ]
                if existing_ids:
                    fallback_qs = iso_user_qs.filter(employee_get__id__in=existing_ids)
                    self.fields["forward_to"].initial = fallback_qs
                    self.initial["forward_to"] = list(fallback_qs.values_list("pk", flat=True))
                else:
                    self.fields["forward_to"].initial = iso_user_qs
                    self.initial["forward_to"] = list(iso_user_qs.values_list("pk", flat=True))
        else:
            # New form: default to all ISO group members
            self.initial["forward_to"] = list(iso_user_qs.values_list("pk", flat=True))

        # If editing, pre-populate priority and deadline from the linked ticket
        if self.instance and self.instance.pk and hasattr(self.instance, "ticket") and self.instance.ticket:
            self.fields["priority"].initial = self.instance.ticket.priority
            self.fields["deadline"].initial = self.instance.ticket.deadline


    def _forward_to_label(self, user):
        try:
            employee = user.employee_get
            full_name = employee.get_full_name()
            if full_name:
                return full_name
        except Exception:
            pass
        return user.get_full_name() or user.username

    def clean_reason(self):
        reason = (self.cleaned_data.get("reason") or "").strip()
        if len(reason) > self.REASON_MAX_LENGTH:
            raise forms.ValidationError(
                _("Reason cannot exceed %(max_length)s characters.")
                % {"max_length": self.REASON_MAX_LENGTH}
            )
        return reason

    def clean_deadline(self):
        deadline = self.cleaned_data.get("deadline")
        if deadline is None:
            return deadline
        today = timezone.localdate()
        if deadline < today:
            raise forms.ValidationError(_("Due date cannot be in the past."))
        return deadline

    def save(self, commit=True):
        is_new = self.instance.pk is None
        instance = super().save(commit=False)
        # Derive user_id from the selected employee's work email,
        # falling back to the auth user's email if not available.
        employee = self.cleaned_data.get("employee")
        if employee:
            email = ""
            try:
                email = employee.employee_work_info.email or ""
            except Exception:
                pass
            if not email:
                try:
                    email = employee.employee_user_id.email or ""
                except Exception:
                    pass
            if not email:
                try:
                    email = employee.email or ""
                except Exception:
                    pass
            instance.user_id = email
        if commit:
            instance.save()
            instance.forward_to.set(self.cleaned_data.get("forward_to", []))
            if is_new:
                self._consolidate_create_history(instance)
        return instance

    @staticmethod
    def _consolidate_create_history(instance):
        """
        On initial creation, simple_history records a '+' (create) entry with
        an empty ``forward_to`` snapshot, followed by one or more '~' entries
        triggered by the m2m_changed signal when the default ISO recipients
        are assigned. The diff between them produces a misleading
        "changed Forward to from None to None" audit log entry, even though
        the user did not intentionally change the field.

        Consolidate the auto-generated entries into the create record so the
        audit log shows only "Created the ticket" on first save, while still
        capturing the actual initial ``forward_to`` membership for future
        diffs.
        """
        try:
            history_qs = instance.history.all().order_by(
                "history_date", "history_id"
            )
            create_entry = history_qs.filter(history_type="+").first()
            if not create_entry:
                return
            extra_entries = list(history_qs.exclude(pk=create_entry.pk))
            if not extra_entries:
                return
            latest_entry = extra_entries[-1]
            # Resolve the m2m history model attached to the historical record
            # via simple_history's HistoryDescriptor.
            try:
                m2m_history_model = type(create_entry).forward_to.model
            except Exception:
                m2m_history_model = None
            if m2m_history_model is not None:
                # Replace the (empty) m2m snapshot on the create entry with
                # the latest snapshot taken after ``forward_to.set(...)``.
                # NOTE: simple_history's ``m2m_history_id`` is the m2m row's
                # own PK; the FK pointing to the parent historical record is
                # ``history`` (column ``history_id``).
                m2m_history_model.objects.filter(
                    history_id=create_entry.pk
                ).delete()
                m2m_history_model.objects.filter(
                    history_id=latest_entry.pk
                ).update(history_id=create_entry.pk)
            # Drop the redundant '~' entries (their remaining m2m rows, if
            # any, cascade-delete via FK).
            for entry in extra_entries:
                entry.delete()
        except Exception:
            # Audit-log consolidation must never block the save flow.
            pass


class AccessRequestForm(forms.ModelForm):
    """
    Form for employees to submit an "Access Request" (ISO Forms category).

    Mirrors :class:`PasswordResetRequestForm`:
      * ``User ID (Email)`` is auto-populated with the logged-in user and is
        read-only.
      * ``Requested Date`` is auto-populated with today's date and read-only.
      * ``Reason for Request`` is a rich-text comment area capped at 250 chars.
    """

    REASON_MAX_LENGTH = 250

    employee = forms.ModelChoiceField(
        queryset=Employee.objects.none(),
        widget=forms.HiddenInput(),
        required=True,
    )
    user_email = forms.CharField(
        label=_("User ID (Email)"),
        required=False,
        widget=forms.TextInput(
            attrs={"class": "oh-input w-100", "readonly": "readonly"}
        ),
    )
    requested_date = forms.DateField(
        label=_("Requested Date"),
        required=False,
        widget=forms.DateInput(
            attrs={
                "class": "oh-input w-100",
                "type": "date",
                "readonly": "readonly",
            }
        ),
    )
    effective_date = forms.DateField(
        label=_("Effective Date"),
        required=False,
        widget=forms.DateInput(
            attrs={
                "class": "oh-input w-100",
                "type": "date",
            }
        ),
    )
    priority = forms.ChoiceField(
        choices=PRIORITY,
        initial="medium",
        label=_("Priority"),
        widget=forms.Select(attrs={"class": "oh-select oh-select-2 w-100"}),
    )
    forward_to = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        label=_("Forward To"),
        required=True,
        widget=forms.SelectMultiple(attrs={"class": "oh-select oh-select-2 w-100"}),
    )
    deadline = forms.DateField(
        required=False,
        label=_("Due Date"),
        widget=forms.DateInput(attrs={"class": "oh-input w-100", "type": "date"}),
    )

    class Meta:
        model = AccessRequest
        fields = [
            "sub_type",
            "business_critical",
            "level_of_access",
            "domain",
            "forward_to",
            "reason",
        ]
        widgets = {
            "sub_type": forms.Select(attrs={"class": "oh-select oh-select-2 w-100"}),
            "business_critical": forms.Select(
                attrs={"class": "oh-select oh-select-2 w-100"}
            ),
            "level_of_access": forms.Select(
                attrs={"class": "oh-select oh-select-2 w-100"}
            ),
            "domain": forms.Select(attrs={"class": "oh-select oh-select-2 w-100"}),
            "reason": forms.Textarea(
                attrs={
                    "class": "oh-input w-100",
                    "rows": 4,
                    "placeholder": _("Describe why you need this access"),
                }
            ),
        }
        labels = {
            "sub_type": _("Sub Type"),
            "business_critical": _("Business Critical Systems"),
            "level_of_access": _("Level of Access"),
            "domain": _("Domain"),
            "reason": _("Reason for Request"),
        }

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        today = timezone.localdate()
        self.fields["deadline"].widget.attrs["min"] = today.isoformat()
        self.fields["effective_date"].widget.attrs["min"] = today.isoformat()

        # Business Critical / Level of Access apply to the "Access Request"
        self.fields["business_critical"].required = False
        self.fields["level_of_access"].required = False

        # Resolve the logged-in user's Employee (ticket owner).
        current_employee = None
        if request is not None:
            try:
                current_employee = request.user.employee_get
            except Exception:
                current_employee = None

        # Editing: derive owner/email/date from the saved instance.
        if self.instance and self.instance.pk and getattr(self.instance, "ticket", None):
            owner = getattr(self.instance.ticket, "employee_id", None) or current_employee
            self.initial["requested_date"] = self.instance.requested_date
            self.initial["effective_date"] = self.instance.effective_date
            email_initial = self.instance.user_id
        else:
            owner = current_employee
            self.initial["requested_date"] = today
            email_initial = self._employee_email(owner)

        if owner:
            self.fields["employee"].queryset = Employee.objects.filter(pk=owner.pk)
            self.initial["employee"] = owner
            self.fields["employee"].initial = owner
        self.initial["user_email"] = email_initial

        # Forward To → show all ISO officers and IS Council members.
        self.fields["forward_to"].queryset = (
            User.objects.filter(
                groups__name__in=[ISO_GROUP_NAME, ISC_GROUP_NAME], is_active=True
            )
            .distinct()
            .order_by("first_name", "username")
        )
        self.fields["forward_to"].label_from_instance = self._forward_to_label

        # Reason character cap (mirrors Password Reset behaviour).
        reason_error_message = _(
            "Reason cannot exceed %(max_length)s characters."
        ) % {"max_length": self.REASON_MAX_LENGTH}
        reason_field = self.fields["reason"]
        reason_field.max_length = self.REASON_MAX_LENGTH
        reason_field.error_messages["max_length"] = reason_error_message
        reason_field.widget.attrs.update(
            {
                "data-maxlength": str(self.REASON_MAX_LENGTH),
                "data-maxlength-message": reason_error_message,
                "maxlength": str(self.REASON_MAX_LENGTH),
            }
        )

        iso_user_qs = self.fields["forward_to"].queryset
        if self.instance and self.instance.pk:
            saved_forward = self.instance.forward_to.filter(
                groups__name__in=[ISO_GROUP_NAME, ISC_GROUP_NAME], is_active=True
            ).distinct()
            if saved_forward.exists():
                self.initial["forward_to"] = list(
                    saved_forward.values_list("pk", flat=True)
                )
            else:
                self.initial["forward_to"] = list(
                    iso_user_qs.values_list("pk", flat=True)
                )
            if hasattr(self.instance, "ticket") and self.instance.ticket:
                self.fields["priority"].initial = self.instance.ticket.priority
                self.fields["deadline"].initial = self.instance.ticket.deadline
        else:
            self.initial["forward_to"] = list(iso_user_qs.values_list("pk", flat=True))

    @staticmethod
    def _employee_email(employee):
        if not employee:
            return ""
        for getter in (
            lambda e: e.employee_work_info.email,
            lambda e: e.employee_user_id.email,
            lambda e: e.email,
        ):
            try:
                email = getter(employee) or ""
                if email:
                    return email
            except Exception:
                continue
        return ""

    def _forward_to_label(self, user):
        try:
            full_name = user.employee_get.get_full_name()
            if full_name:
                return full_name
        except Exception:
            pass
        return user.get_full_name() or user.username

    def clean_reason(self):
        reason = (self.cleaned_data.get("reason") or "").strip()
        if not reason:
            raise forms.ValidationError(_("This field is required."))
        if len(reason) > self.REASON_MAX_LENGTH:
            raise forms.ValidationError(
                _("Reason cannot exceed %(max_length)s characters.")
                % {"max_length": self.REASON_MAX_LENGTH}
            )
        return reason

    def clean_deadline(self):
        deadline = self.cleaned_data.get("deadline")
        if deadline is None:
            return deadline
        if deadline < timezone.localdate():
            raise forms.ValidationError(_("Due date cannot be in the past."))
        return deadline

    def clean_effective_date(self):
        effective_date = self.cleaned_data.get("effective_date")
        if effective_date is None:
            return effective_date
        if effective_date < timezone.localdate():
            raise forms.ValidationError(
                _("Effective date cannot be in the past.")
            )
        return effective_date

    def clean(self):
        cleaned_data = super().clean()
        sub_type = cleaned_data.get("sub_type")
        required_message = _("This field is required.")

        if sub_type == "access_deactivation":
            # Deactivation captures the domain + effective date only
            if not cleaned_data.get("effective_date"):
                self.add_error("effective_date", required_message)
            cleaned_data["business_critical"] = None
            cleaned_data["level_of_access"] = None
        else:
            # Access Request requires the structured access fields.
            if not cleaned_data.get("business_critical"):
                self.add_error("business_critical", required_message)
            if not cleaned_data.get("level_of_access"):
                self.add_error("level_of_access", required_message)
            cleaned_data["effective_date"] = None

        if not cleaned_data.get("domain"):
            self.add_error("domain", required_message)
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        employee = self.cleaned_data.get("employee")
        instance.requested_date = self.cleaned_data.get(
            "requested_date"
        ) or timezone.localdate()
        instance.effective_date = self.cleaned_data.get("effective_date")
        instance.business_critical = self.cleaned_data.get("business_critical")
        instance.level_of_access = self.cleaned_data.get("level_of_access")
        instance.user_id = self._employee_email(employee)
        if commit:
            instance.save()
            instance.forward_to.set(self.cleaned_data.get("forward_to", []))
        return instance


class ExceptionRequestForm(forms.ModelForm):
    """
    Form for employees to submit an "Exception Request" (ISO Forms category).

    Mirrors :class:`AccessRequestForm`:
      * ``User ID (Email)`` is auto-populated with the logged-in user and is
        read-only.
      * ``Description of Exception`` is a rich-text comment area capped at 250
        characters (same behaviour as the Password Reset "Reason" field).
      * ``ISMS Reference`` is a free-text field capped at 250 characters.

    The two-stage approval workflow is ISO Officer (Stage 1) → IS Council
    (Stage 2); ``Forward To`` selects the Stage 2 (IS Council) reviewers.
    """

    DESCRIPTION_MAX_LENGTH = 250
    ISMS_REFERENCE_MAX_LENGTH = 250

    employee = forms.ModelChoiceField(
        queryset=Employee.objects.none(),
        widget=forms.HiddenInput(),
        required=True,
    )
    user_email = forms.CharField(
        label=_("User ID (Email)"),
        required=False,
        widget=forms.TextInput(
            attrs={"class": "oh-input w-100", "readonly": "readonly"}
        ),
    )
    priority = forms.ChoiceField(
        choices=PRIORITY,
        initial="medium",
        label=_("Priority"),
        widget=forms.Select(attrs={"class": "oh-select oh-select-2 w-100"}),
    )
    forward_to = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        label=_("Forward To"),
        required=True,
        widget=forms.SelectMultiple(attrs={"class": "oh-select oh-select-2 w-100"}),
    )
    deadline = forms.DateField(
        required=False,
        label=_("Due Date"),
        widget=forms.DateInput(attrs={"class": "oh-input w-100", "type": "date"}),
    )

    class Meta:
        model = ExceptionRequest
        fields = [
            "isms_reference",
            "forward_to",
            "description",
        ]
        widgets = {
            "isms_reference": forms.TextInput(
                attrs={
                    "class": "oh-input w-100",
                    "placeholder": _("Reference the ISMS policy or procedure"),
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "oh-input w-100",
                    "rows": 4,
                    "placeholder": _("Describe the exception being requested"),
                }
            ),
        }
        labels = {
            "isms_reference": _("ISMS Reference"),
            "description": _("Description of Exception"),
        }

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        today = timezone.localdate()
        self.fields["deadline"].widget.attrs["min"] = today.isoformat()

        # Resolve the logged-in user's Employee (ticket owner).
        current_employee = None
        if request is not None:
            try:
                current_employee = request.user.employee_get
            except Exception:
                current_employee = None

        # Editing: derive owner/email from the saved instance.
        if self.instance and self.instance.pk and getattr(self.instance, "ticket", None):
            owner = getattr(self.instance.ticket, "employee_id", None) or current_employee
            email_initial = self.instance.user_id
        else:
            owner = current_employee
            email_initial = self._employee_email(owner)

        if owner:
            self.fields["employee"].queryset = Employee.objects.filter(pk=owner.pk)
            self.initial["employee"] = owner
            self.fields["employee"].initial = owner
        self.initial["user_email"] = email_initial

        # Forward To → show all ISO officers and IS Council members.
        self.fields["forward_to"].queryset = (
            User.objects.filter(
                groups__name__in=[ISO_GROUP_NAME, ISC_GROUP_NAME], is_active=True
            )
            .distinct()
            .order_by("first_name", "username")
        )
        self.fields["forward_to"].label_from_instance = self._forward_to_label

        # Description character cap (mirrors Password Reset reason behaviour).
        description_error_message = _(
            "Description cannot exceed %(max_length)s characters."
        ) % {"max_length": self.DESCRIPTION_MAX_LENGTH}
        description_field = self.fields["description"]
        description_field.max_length = self.DESCRIPTION_MAX_LENGTH
        description_field.error_messages["max_length"] = description_error_message
        description_field.widget.attrs.update(
            {
                "data-maxlength": str(self.DESCRIPTION_MAX_LENGTH),
                "data-maxlength-message": description_error_message,
                "maxlength": str(self.DESCRIPTION_MAX_LENGTH),
            }
        )
        self.fields["isms_reference"].widget.attrs["maxlength"] = str(
            self.ISMS_REFERENCE_MAX_LENGTH
        )

        isc_user_qs = self.fields["forward_to"].queryset
        if self.instance and self.instance.pk:
            saved_forward = self.instance.forward_to.filter(
                groups__name__in=[ISO_GROUP_NAME, ISC_GROUP_NAME], is_active=True
            ).distinct()
            if saved_forward.exists():
                self.initial["forward_to"] = list(
                    saved_forward.values_list("pk", flat=True)
                )
            else:
                self.initial["forward_to"] = list(
                    isc_user_qs.values_list("pk", flat=True)
                )
            if hasattr(self.instance, "ticket") and self.instance.ticket:
                self.fields["priority"].initial = self.instance.ticket.priority
                self.fields["deadline"].initial = self.instance.ticket.deadline
        else:
            self.initial["forward_to"] = list(isc_user_qs.values_list("pk", flat=True))

    @staticmethod
    def _employee_email(employee):
        if not employee:
            return ""
        for getter in (
            lambda e: e.employee_work_info.email,
            lambda e: e.employee_user_id.email,
            lambda e: e.email,
        ):
            try:
                email = getter(employee) or ""
                if email:
                    return email
            except Exception:
                continue
        return ""

    def _forward_to_label(self, user):
        try:
            full_name = user.employee_get.get_full_name()
            if full_name:
                return full_name
        except Exception:
            pass
        return user.get_full_name() or user.username

    def clean_description(self):
        description = (self.cleaned_data.get("description") or "").strip()
        if not description:
            raise forms.ValidationError(_("This field is required."))
        if len(description) > self.DESCRIPTION_MAX_LENGTH:
            raise forms.ValidationError(
                _("Description cannot exceed %(max_length)s characters.")
                % {"max_length": self.DESCRIPTION_MAX_LENGTH}
            )
        return description

    def clean_isms_reference(self):
        isms_reference = (self.cleaned_data.get("isms_reference") or "").strip()
        if not isms_reference:
            raise forms.ValidationError(_("This field is required."))
        if len(isms_reference) > self.ISMS_REFERENCE_MAX_LENGTH:
            raise forms.ValidationError(
                _("ISMS Reference cannot exceed %(max_length)s characters.")
                % {"max_length": self.ISMS_REFERENCE_MAX_LENGTH}
            )
        return isms_reference

    def clean_deadline(self):
        deadline = self.cleaned_data.get("deadline")
        if deadline is None:
            return deadline
        if deadline < timezone.localdate():
            raise forms.ValidationError(_("Due date cannot be in the past."))
        return deadline

    def save(self, commit=True):
        instance = super().save(commit=False)
        employee = self.cleaned_data.get("employee")
        instance.user_id = self._employee_email(employee)
        if commit:
            instance.save()
            instance.forward_to.set(self.cleaned_data.get("forward_to", []))
        return instance


class AdminAccessRequestForm(forms.ModelForm):
    """
    Form for employees to submit an "Admin Access Request" (ISO Forms category).
    The two-stage approval workflow is ISO Officer (Stage 1) → IS Council
    (Stage 2); ``Forward To`` selects the Stage 2 (IS Council) reviewers.
    """

    REASON_MAX_LENGTH = 250

    employee = forms.ModelChoiceField(
        queryset=Employee.objects.none(),
        widget=forms.HiddenInput(),
        required=True,
    )
    user_email = forms.CharField(
        label=_("User ID (Email)"),
        required=False,
        widget=forms.TextInput(
            attrs={"class": "oh-input w-100", "readonly": "readonly"}
        ),
    )
    priority = forms.ChoiceField(
        choices=PRIORITY,
        initial="medium",
        label=_("Priority"),
        widget=forms.Select(attrs={"class": "oh-select oh-select-2 w-100"}),
    )
    forward_to = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        label=_("Forward To"),
        required=True,
        widget=forms.SelectMultiple(attrs={"class": "oh-select oh-select-2 w-100"}),
    )
    deadline = forms.DateField(
        required=False,
        label=_("Due Date"),
        widget=forms.DateInput(attrs={"class": "oh-input w-100", "type": "date"}),
    )

    class Meta:
        model = AdminAccessRequest
        fields = [
            "admin_user_type",
            "system_application",
            "privilege_level",
            "forward_to",
            "reason",
        ]
        widgets = {
            "admin_user_type": forms.Select(
                attrs={"class": "oh-select oh-select-2 w-100"}
            ),
            "system_application": forms.TextInput(
                attrs={
                    "class": "oh-input w-100",
                    "placeholder": _("Name the system or application"),
                }
            ),
            "privilege_level": forms.TextInput(
                attrs={
                    "class": "oh-input w-100",
                    "placeholder": _("Describe the privilege level required"),
                }
            ),
            "reason": forms.Textarea(
                attrs={
                    "class": "oh-input w-100",
                    "rows": 4,
                    "placeholder": _("Explain why the privilege is needed"),
                }
            ),
        }
        labels = {
            "admin_user_type": _("Admin User Type"),
            "system_application": _("System / Application"),
            "privilege_level": _("Privilege Level"),
            "reason": _("Reason for Need of Privilege"),
        }

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        today = timezone.localdate()
        self.fields["deadline"].widget.attrs["min"] = today.isoformat()

        # Resolve the logged-in user's Employee (ticket owner).
        current_employee = None
        if request is not None:
            try:
                current_employee = request.user.employee_get
            except Exception:
                current_employee = None

        # Editing: derive owner/email from the saved instance.
        if self.instance and self.instance.pk and getattr(self.instance, "ticket", None):
            owner = getattr(self.instance.ticket, "employee_id", None) or current_employee
            email_initial = self.instance.user_id
        else:
            owner = current_employee
            email_initial = self._employee_email(owner)

        if owner:
            self.fields["employee"].queryset = Employee.objects.filter(pk=owner.pk)
            self.initial["employee"] = owner
            self.fields["employee"].initial = owner
        self.initial["user_email"] = email_initial

        # Forward To → show all ISO officers and IS Council members.
        self.fields["forward_to"].queryset = (
            User.objects.filter(
                groups__name__in=[ISO_GROUP_NAME, ISC_GROUP_NAME], is_active=True
            )
            .distinct()
            .order_by("first_name", "username")
        )
        self.fields["forward_to"].label_from_instance = self._forward_to_label

        # Reason character cap (mirrors Password Reset reason behaviour).
        reason_error_message = _(
            "Reason cannot exceed %(max_length)s characters."
        ) % {"max_length": self.REASON_MAX_LENGTH}
        reason_field = self.fields["reason"]
        reason_field.max_length = self.REASON_MAX_LENGTH
        reason_field.error_messages["max_length"] = reason_error_message
        reason_field.widget.attrs.update(
            {
                "data-maxlength": str(self.REASON_MAX_LENGTH),
                "data-maxlength-message": reason_error_message,
                "maxlength": str(self.REASON_MAX_LENGTH),
            }
        )

        isc_user_qs = self.fields["forward_to"].queryset
        if self.instance and self.instance.pk:
            saved_forward = self.instance.forward_to.filter(
                groups__name__in=[ISO_GROUP_NAME, ISC_GROUP_NAME], is_active=True
            ).distinct()
            if saved_forward.exists():
                self.initial["forward_to"] = list(
                    saved_forward.values_list("pk", flat=True)
                )
            else:
                self.initial["forward_to"] = list(
                    isc_user_qs.values_list("pk", flat=True)
                )
            if hasattr(self.instance, "ticket") and self.instance.ticket:
                self.fields["priority"].initial = self.instance.ticket.priority
                self.fields["deadline"].initial = self.instance.ticket.deadline
        else:
            self.initial["forward_to"] = list(isc_user_qs.values_list("pk", flat=True))

    @staticmethod
    def _employee_email(employee):
        if not employee:
            return ""
        for getter in (
            lambda e: e.employee_work_info.email,
            lambda e: e.employee_user_id.email,
            lambda e: e.email,
        ):
            try:
                email = getter(employee) or ""
                if email:
                    return email
            except Exception:
                continue
        return ""

    def _forward_to_label(self, user):
        try:
            full_name = user.employee_get.get_full_name()
            if full_name:
                return full_name
        except Exception:
            pass
        return user.get_full_name() or user.username

    def clean_reason(self):
        reason = (self.cleaned_data.get("reason") or "").strip()
        if not reason:
            raise forms.ValidationError(_("This field is required."))
        if len(reason) > self.REASON_MAX_LENGTH:
            raise forms.ValidationError(
                _("Reason cannot exceed %(max_length)s characters.")
                % {"max_length": self.REASON_MAX_LENGTH}
            )
        return reason

    def clean_system_application(self):
        value = (self.cleaned_data.get("system_application") or "").strip()
        if not value:
            raise forms.ValidationError(_("This field is required."))
        return value

    def clean_privilege_level(self):
        value = (self.cleaned_data.get("privilege_level") or "").strip()
        if not value:
            raise forms.ValidationError(_("This field is required."))
        return value

    def clean_deadline(self):
        deadline = self.cleaned_data.get("deadline")
        if deadline is None:
            return deadline
        if deadline < timezone.localdate():
            raise forms.ValidationError(_("Due date cannot be in the past."))
        return deadline

    def save(self, commit=True):
        instance = super().save(commit=False)
        employee = self.cleaned_data.get("employee")
        instance.user_id = self._employee_email(employee)
        if commit:
            instance.save()
            instance.forward_to.set(self.cleaned_data.get("forward_to", []))
        return instance


class ISOReviewForm(forms.Form):
    """
    Form used by ISO/Admin to approve or reject a Password Reset request.

    A comment (iso_feedback) is now MANDATORY for both actions:
      * Approve → the Review Comment is required (spec §4).
      * Reject  → the Reason for Rejection is required (unchanged).
    """

    ACTION_CHOICES = [
        ("approve", _("Approve")),
        ("reject", _("Reject")),
    ]

    action = forms.ChoiceField(
        choices=ACTION_CHOICES,
        widget=forms.HiddenInput(),
    )
    iso_feedback = forms.CharField(
        # Server-side authoritative: required for both approve and reject.
        required=True,
        label=_("Comment"),
        widget=forms.Textarea(
            attrs={
                "class": "oh-input w-100",
                "rows": 3,
                "placeholder": _("A comment is required..."),
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        feedback = (cleaned_data.get("iso_feedback") or "").strip()
        if not feedback:
            # Authoritative server-side mandatory-comment enforcement.
            raise forms.ValidationError(_("A comment is required."))
        cleaned_data["iso_feedback"] = feedback
        return cleaned_data


class ISOCommentTransitionForm(forms.Form):
    """
    Generic single-comment transition form used by the ISO workflow steps that
    only require a mandatory comment:
      * In Action → Awaiting Acknowledgement (ISO officer, §5).
    """

    comment = forms.CharField(
        required=True,
        label=_("Comment"),
        widget=forms.Textarea(
            attrs={
                "class": "oh-input w-100",
                "rows": 3,
                "placeholder": _("A comment is required..."),
            }
        ),
    )

    def clean_comment(self):
        comment = (self.cleaned_data.get("comment") or "").strip()
        if not comment:
            raise forms.ValidationError(_("A comment is required."))
        return comment


class ISOAcknowledgementForm(forms.Form):
    """
    Requestor acknowledgement form (§6) driven from the detail top-bar control.
    The employee confirms the request was fulfilled (→ Closed) with a mandatory
    comment. (The "No"/reopen branch has been removed.)
    """

    comment = forms.CharField(
        required=True,
        label=_("Comment"),
        widget=forms.Textarea(
            attrs={
                "class": "oh-input w-100",
                "rows": 3,
                "placeholder": _("A comment is required..."),
            }
        ),
    )

    def clean_comment(self):
        comment = (self.cleaned_data.get("comment") or "").strip()
        if not comment:
            raise forms.ValidationError(_("A comment is required."))
        return comment


class TicketTagForm(ModelForm):
    class Meta:
        model = Ticket
        fields = [
            "tags",
        ]
        widgets = {
            "tags": forms.SelectMultiple(
                attrs={
                    "class": "oh-select oh-select-2 select2-hidden-accessible",
                    "onchange": "updateTag()",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        """
        Initializes the Ticket tag form instance.
        If an instance is provided, sets the initial value for the form's .
        """
        super().__init__(*args, **kwargs)
        request = getattr(horilla_middlewares._thread_locals, "request", None)
        if is_reportingmanager(request) or request.user.has_perm("base.add_tags"):
            self.fields["tags"].choices = list(self.fields["tags"].choices)
            self.fields["tags"].choices.append(("create_new_tag", "Create new tag"))


class TicketRaisedOnForm(ModelForm):
    raised_on = forms.MultipleChoiceField(
        widget=forms.SelectMultiple(
            attrs={"class": "oh-select oh-select-2", "required": "true"},
        ),
        label=_("Forward To"),
    )

    class Meta:
        model = Ticket
        fields = ["raised_on"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = []
        if self.instance and self.instance.pk:
            atype = self.instance.assigning_type
            if atype == "department":
                choices = [
                    (str(d.pk), str(d.department))
                    for d in Department.objects.all()
                ]
            elif atype == "job_position":
                choices = [
                    (str(j.pk), str(j.job_position))
                    for j in JobPosition.objects.all()
                ]
            elif atype == "individual":
                choices = [
                    (str(e.pk), e.get_full_name())
                    for e in Employee.objects.filter(is_active=True)
                ]
            if self.instance.raised_on:
                self.initial["raised_on"] = [
                    rid.strip()
                    for rid in self.instance.raised_on.split(",")
                    if rid.strip()
                ]
        self.fields["raised_on"].choices = choices

    def clean_raised_on(self):
        values = self.cleaned_data.get("raised_on", [])
        return ",".join(values)


class TicketAssigneesForm(ModelForm):
    class Meta:
        model = Ticket
        fields = [
            "assigned_to",
        ]


class FAQCategoryForm(ModelForm):
    class Meta:
        model = FAQCategory
        fields = "__all__"
        exclude = ["is_active"]


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = [
            "comment",
        ]
        exclude = ["is_active"]
        widgets = {"employee_id": forms.HiddenInput()}


ALLOWED_FILE_EXTENSIONS = [
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
    ".txt", ".csv", ".html",
    ".mp3", ".wav", ".ogg", ".m4a",
]
MAX_FILE_SIZE_MB = 5  # Maximum file size in MB


class AttachmentForm(forms.ModelForm):
    file = forms.FileField(
        widget=forms.TextInput(
            attrs={
                "name": "file",
                "type": "File",
                "class": "form-control",
                "multiple": "True",
            }
        ),
        label="",
    )

    class Meta:
        model = Attachment
        fields = ["file", "comment", "ticket"]
        exclude = ["is_active"]

    def clean_file(self):
        uploaded_file = self.cleaned_data.get("file")
        if uploaded_file:
            import os

            ext = os.path.splitext(uploaded_file.name)[1].lower()
            if ext not in ALLOWED_FILE_EXTENSIONS:
                raise forms.ValidationError(
                    _("File type '%(ext)s' is not allowed. Allowed types: %(allowed)s")
                    % {"ext": ext, "allowed": ", ".join(ALLOWED_FILE_EXTENSIONS)}
                )
            max_size = MAX_FILE_SIZE_MB * 1024 * 1024
            if uploaded_file.size > max_size:
                raise forms.ValidationError(
                    _("File size exceeds the maximum limit of %(max_size)s MB.")
                    % {"max_size": MAX_FILE_SIZE_MB}
                )
        return uploaded_file


class DepartmentManagerCreateForm(ModelForm):
    class Meta:
        model = DepartmentManager
        fields = ["department", "manager"]
        widgets = {
            "department": forms.Select(
                attrs={
                    "onchange": "getDepartmentEmployees($(this))",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "instance" in kwargs:
            department = kwargs["instance"].department
            # Get the employees related to this department
            employees = department.employeeworkinformation_set.values_list(
                "employee_id", flat=True
            )
            # Set the manager field queryset to be those employees
            self.fields["manager"].queryset = Employee.objects.filter(id__in=employees)
