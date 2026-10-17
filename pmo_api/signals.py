"""
Signals for the Meridian PMO integration.

``Employee.updated_at`` is what Meridian's incremental sync (``?updated_since=``)
relies on. Department, job title and join date live on EmployeeWorkInformation,
so saving that model must also bump the parent Employee.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from employee.models import Employee, EmployeeWorkInformation


@receiver(
    post_save,
    sender=EmployeeWorkInformation,
    dispatch_uid="pmo_api.touch_employee_on_work_info_save",
)
def touch_employee_updated_at(sender, instance, **kwargs):
    """Bump ``Employee.updated_at`` whenever its work information is saved."""
    employee_pk = instance.employee_id_id
    if not employee_pk:
        return
    # Plain base manager + queryset.update(): no Employee.save() side effects,
    # no signal recursion, and no company-filtering manager logic.
    Employee._base_manager.filter(pk=employee_pk).update(updated_at=timezone.now())

