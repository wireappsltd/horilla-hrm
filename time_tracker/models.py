"""
time_tracker/models.py

Models for the Time Tracker app.
"""

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from base.models import Company
from horilla import horilla_middlewares
from horilla.horilla_middlewares import _thread_locals
from horilla.models import HorillaModel


class Tag(HorillaModel):
    """
    Tag model for categorising time entries.
    """

    name = models.CharField(max_length=100, verbose_name=_("Name"))
    color = models.CharField(max_length=7, default="#E74C3C", verbose_name=_("Color"))
    company_id = models.ForeignKey(
        "base.Company",
        null=True,
        editable=False,
        on_delete=models.PROTECT,
        verbose_name=_("Company"),
    )
    objects = HorillaCompanyManager("company_id")

    class Meta:
        unique_together = [["name", "company_id"]]
        verbose_name = _("Tag")
        verbose_name_plural = _("Tags")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        request = getattr(_thread_locals, "request", None)
        if is_new and request:
            cid = request.session.get("selected_company")
            if cid and cid != "all":
                self.company_id = Company.find(cid)
        super().save(*args, **kwargs)


class Client(HorillaModel):
    """
    Client model for associating time entries with external clients.
    """

    name = models.CharField(max_length=200, verbose_name=_("Name"))
    email = models.EmailField(blank=True, verbose_name=_("Email"))
    address = models.TextField(blank=True, verbose_name=_("Address"))
    currency = models.CharField(
        max_length=3, default="USD", verbose_name=_("Currency")
    )
    default_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("Default Rate"),
    )
    color = models.CharField(
        max_length=7, default="#4A90E2", verbose_name=_("Color")
    )
    company_id = models.ForeignKey(
        "base.Company",
        null=True,
        editable=False,
        on_delete=models.PROTECT,
        verbose_name=_("Company"),
    )
    objects = HorillaCompanyManager("company_id")

    class Meta:
        verbose_name = _("Client")
        verbose_name_plural = _("Clients")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        request = getattr(_thread_locals, "request", None)
        if is_new and request:
            cid = request.session.get("selected_company")
            if cid and cid != "all":
                self.company_id = Company.find(cid)
        super().save(*args, **kwargs)


class TimeEntry(HorillaModel):
    """
    TimeEntry records work done by an employee on a project/task.
    """

    ENTRY_STATUS = [
        ("draft", _("Draft")),
        ("submitted", _("Submitted")),
        ("approved", _("Approved")),
        ("rejected", _("Rejected")),
        ("locked", _("Locked")),
    ]

    employee_id = models.ForeignKey(
        "employee.Employee",
        on_delete=models.PROTECT,
        related_name="time_entries",
        verbose_name=_("Employee"),
    )
    project_id = models.ForeignKey(
        "project.Project",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tt_entries",
        verbose_name=_("Project"),
    )
    task_id = models.ForeignKey(
        "project.Task",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tt_entries",
        verbose_name=_("Task"),
    )
    client_id = models.ForeignKey(
        Client,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name=_("Client"),
    )
    tag_ids = models.ManyToManyField(Tag, blank=True, verbose_name=_("Tags"))
    description = models.TextField(
        blank=True, default="", verbose_name=_("Description")
    )
    date = models.DateField(default=timezone.now, verbose_name=_("Date"))
    start_time = models.DateTimeField(
        null=True, blank=True, verbose_name=_("Start Time")
    )
    end_time = models.DateTimeField(
        null=True, blank=True, verbose_name=_("End Time")
    )
    duration_seconds = models.PositiveIntegerField(
        default=0, verbose_name=_("Duration (seconds)")
    )
    is_billable = models.BooleanField(default=False, verbose_name=_("Billable"))
    billable_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("Billable Rate"),
    )
    currency = models.CharField(
        max_length=3, default="USD", verbose_name=_("Currency")
    )
    status = models.CharField(
        choices=ENTRY_STATUS,
        max_length=20,
        default="draft",
        verbose_name=_("Status"),
    )
    is_locked = models.BooleanField(default=False, verbose_name=_("Locked"))
    custom_fields = models.JSONField(
        default=dict, blank=True, verbose_name=_("Custom Fields")
    )
    company_id = models.ForeignKey(
        "base.Company",
        null=True,
        editable=False,
        on_delete=models.PROTECT,
        verbose_name=_("Company"),
    )
    objects = HorillaCompanyManager("company_id")

    class Meta:
        verbose_name = _("Time Entry")
        verbose_name_plural = _("Time Entries")
        ordering = ["-date", "-start_time"]

    def __str__(self):
        return f"{self.employee_id} - {self.date} - {self.duration_display}"

    @property
    def duration_display(self):
        """Return duration as HH:MM:SS string."""
        total = self.duration_seconds or 0
        hours = total // 3600
        minutes = (total % 3600) // 60
        seconds = total % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        # Compute duration from start/end times
        if self.start_time and self.end_time:
            delta = self.end_time - self.start_time
            self.duration_seconds = max(0, int(delta.total_seconds()))
        # Assign company on create
        request = getattr(_thread_locals, "request", None)
        if is_new and request:
            cid = request.session.get("selected_company")
            if cid and cid != "all":
                self.company_id = Company.find(cid)
        super().save(*args, **kwargs)


class ActiveTimer(HorillaModel):
    """
    Tracks a currently running timer for an employee.
    Only one active timer per employee (enforced by OneToOneField).
    """

    employee_id = models.OneToOneField(
        "employee.Employee",
        on_delete=models.CASCADE,
        related_name="active_timer",
        verbose_name=_("Employee"),
    )
    project_id = models.ForeignKey(
        "project.Project",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name=_("Project"),
    )
    task_id = models.ForeignKey(
        "project.Task",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name=_("Task"),
    )
    client_id = models.ForeignKey(
        Client,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name=_("Client"),
    )
    tag_ids = models.ManyToManyField(Tag, blank=True, verbose_name=_("Tags"))
    description = models.TextField(
        blank=True, default="", verbose_name=_("Description")
    )
    is_billable = models.BooleanField(default=False, verbose_name=_("Billable"))
    started_at = models.DateTimeField(
        default=timezone.now, verbose_name=_("Started At")
    )
    last_heartbeat = models.DateTimeField(
        default=timezone.now, verbose_name=_("Last Heartbeat")
    )

    class Meta:
        verbose_name = _("Active Timer")
        verbose_name_plural = _("Active Timers")

    def __str__(self):
        return f"{self.employee_id} - running since {self.started_at}"


class Break(HorillaModel):
    """
    Records break periods within a time entry or active timer.
    """

    BREAK_TYPES = [
        ("manual", _("Manual")),
        ("idle", _("Idle Detected")),
    ]

    time_entry_id = models.ForeignKey(
        TimeEntry,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="breaks",
        verbose_name=_("Time Entry"),
    )
    active_timer = models.ForeignKey(
        ActiveTimer,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="breaks",
        verbose_name=_("Active Timer"),
    )
    start_time = models.DateTimeField(verbose_name=_("Start Time"))
    end_time = models.DateTimeField(
        null=True, blank=True, verbose_name=_("End Time")
    )
    break_type = models.CharField(
        choices=BREAK_TYPES,
        max_length=10,
        default="manual",
        verbose_name=_("Break Type"),
    )
    duration_seconds = models.PositiveIntegerField(
        default=0, verbose_name=_("Duration (seconds)")
    )

    class Meta:
        verbose_name = _("Break")
        verbose_name_plural = _("Breaks")

    def __str__(self):
        return f"Break at {self.start_time}"

    def save(self, *args, **kwargs):
        if self.start_time and self.end_time:
            delta = self.end_time - self.start_time
            self.duration_seconds = max(0, int(delta.total_seconds()))
        super().save(*args, **kwargs)


class RequiredFieldConfig(HorillaModel):
    """
    Per-company configuration for which fields are required on time entries.
    """

    company_id = models.ForeignKey(
        "base.Company",
        null=True,
        editable=False,
        on_delete=models.PROTECT,
        verbose_name=_("Company"),
    )
    require_project = models.BooleanField(
        default=False, verbose_name=_("Require Project")
    )
    require_task = models.BooleanField(
        default=False, verbose_name=_("Require Task")
    )
    require_client = models.BooleanField(
        default=False, verbose_name=_("Require Client")
    )
    require_description = models.BooleanField(
        default=True, verbose_name=_("Require Description")
    )
    require_tags = models.BooleanField(
        default=False, verbose_name=_("Require Tags")
    )
    force_timer_only = models.BooleanField(
        default=False,
        verbose_name=_("Force Timer Only"),
        help_text=_("When enabled, manual time entry is disabled."),
    )
    objects = HorillaCompanyManager("company_id")

    class Meta:
        unique_together = [["company_id"]]
        verbose_name = _("Required Field Config")
        verbose_name_plural = _("Required Field Configs")

    def __str__(self):
        return f"Config for {self.company_id}"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        request = getattr(_thread_locals, "request", None)
        if is_new and request:
            cid = request.session.get("selected_company")
            if cid and cid != "all":
                self.company_id = Company.find(cid)
        super().save(*args, **kwargs)
