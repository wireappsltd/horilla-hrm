"""
Signals for the Meridian PMO integration.

``Employee.updated_at`` is what Meridian's incremental sync (``?updated_since=``)
relies on. Department, job title and join date live on EmployeeWorkInformation,
so saving that model must also bump the parent Employee.
"""

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from employee.models import Employee, EmployeeWorkInformation
from leave.models import LeaveRequest
from offboarding.models import OffboardingEmployee, ResignationLetter

from .models import PMODeletedRecord, PMOEmployeeDeactivation


def _touch_employee(employee_pk):
    if not employee_pk:
        return
    # Plain base manager + queryset.update(): no Employee.save() side effects,
    # no signal recursion, and no company-filtering manager logic.
    Employee._base_manager.filter(pk=employee_pk).update(updated_at=timezone.now())


@receiver(
    post_save,
    sender=EmployeeWorkInformation,
    dispatch_uid="pmo_api.touch_employee_on_work_info_save",
)
def touch_employee_updated_at(sender, instance, **kwargs):
    """Bump ``Employee.updated_at`` whenever its work information is saved."""
    _touch_employee(instance.employee_id_id)


@receiver(post_save, sender=OffboardingEmployee, dispatch_uid="pmo_api.touch_off_save")
@receiver(post_delete, sender=OffboardingEmployee, dispatch_uid="pmo_api.touch_off_del")
@receiver(post_save, sender=ResignationLetter, dispatch_uid="pmo_api.touch_res_save")
@receiver(post_delete, sender=ResignationLetter, dispatch_uid="pmo_api.touch_res_del")
def touch_employee_on_leaving_change(sender, instance, **kwargs):
    """Leaving date is derived from these records; changes must bump updated_at."""
    _touch_employee(instance.employee_id_id)


@receiver(pre_save, sender=Employee, dispatch_uid="pmo_api.employee_pre_save")
def remember_previous_is_active(sender, instance, **kwargs):
    instance._pmo_was_active = None
    if instance.pk:
        instance._pmo_was_active = (
            Employee._base_manager.filter(pk=instance.pk)
            .values_list("is_active", flat=True)
            .first()
        )


@receiver(post_save, sender=Employee, dispatch_uid="pmo_api.employee_post_save")
def track_deactivation_date(sender, instance, created, **kwargs):
    """Record when is_active flipped to False (leaving_date fallback)."""
    if instance.is_active:
        PMOEmployeeDeactivation.objects.filter(employee_id=instance.pk).delete()
        return
    if created or instance._pmo_was_active in (True, None):
        PMOEmployeeDeactivation.objects.update_or_create(
            employee_id=instance.pk,
            defaults={"deactivated_on": timezone.localdate()},
        )


@receiver(post_delete, sender=Employee, dispatch_uid="pmo_api.employee_deleted")
def record_employee_deleted(sender, instance, **kwargs):
    PMODeletedRecord.objects.get_or_create(resource="employee", record_id=instance.pk)
    PMOEmployeeDeactivation.objects.filter(employee_id=instance.pk).delete()


@receiver(post_delete, sender=LeaveRequest, dispatch_uid="pmo_api.leave_deleted")
def record_leave_deleted(sender, instance, **kwargs):
    PMODeletedRecord.objects.get_or_create(resource="leave", record_id=instance.pk)

