"""
offboarding_filter.py

This page is used to write custom template filters.
"""

from django import template
from django.template.defaultfilters import register

from employee.models import Employee
from offboarding.models import (
    EmployeeTask,
    Offboarding,
    OffboardingEmployee,
    OffboardingStage,
    OffboardingTask,
)

register = template.Library()


@register.filter(name="stages")
def stages(stages_dict: dict, stage: OffboardingStage):
    """
    This method will return stage drop accordingly to the offboarding
    """
    form = stages_dict[str(stage.offboarding_id.id)]
    attrs = form.fields["stage_id"].widget.attrs
    attrs["id"] = "stage" + str(stage.id) + str(stage.offboarding_id.id)
    attrs["data-initial-stage"] = stage.id
    form.fields["stage_id"].widget.attrs.update(attrs)
    return form


@register.filter(name="individual_view_stages")
def individual_view_stages(stages_dict: dict, stage: OffboardingStage):
    """
    This method will return stage drop accordingly to the offboarding
    """
    form = stages_dict[str(stage.offboarding_id.id)]
    attrs = form.fields["stage_id"].widget.attrs
    attrs["id"] = "stage" + str(stage.id) + str(stage.offboarding_id.id)
    attrs["data-selected-stage"] = stage.id
    attrs["onchange"] = "myFunction($(this).val())"
    form.fields["stage_id"].widget.attrs.update(attrs)
    return form


@register.filter(name="have_task")
def have_task(task: OffboardingTask, employee: Employee):
    """
    used to check the task is for the employee
    """
    return EmployeeTask.objects.filter(employee_id=employee, task_id=task).exists()


@register.filter(name="get_assigned_task")
def get_assigned_tak(employee: Employee, task: OffboardingTask):
    """
    This method is used to filterout the assigned task
    """
    # retun like list to access it in varialbe when first iteration of the loop
    return [
        EmployeeTask.objects.filter(employee_id=employee, task_id=task).first(),
    ]


@register.filter(name="any_manager")
def any_manager(employee: Employee):
    """
    This method is used to check the employee is in managers
    employee: Employee model instance
    """
    if employee is None:
        return False
    cached = getattr(employee, "_any_manager_cached", None)
    if cached is not None:
        return cached
    result = (
        Offboarding.objects.filter(managers=employee).exists()
        | OffboardingStage.objects.filter(managers=employee).exists()
        | OffboardingTask.objects.filter(managers=employee).exists()
    )
    employee._any_manager_cached = result
    return result


@register.filter(name="is_offboarding_manager")
def is_offboarding_manager(employee: Employee):
    """
    This method is used to check the employee is manager of any offboarding
    """
    if employee is None:
        return False
    cached = getattr(employee, "_is_offboarding_manager_cached", None)
    if cached is not None:
        return cached
    result = Offboarding.objects.filter(managers=employee).exists()
    employee._is_offboarding_manager_cached = result
    return result


@register.filter(name="is_offboarding_employee")
def is_offboarding_employee(employee: Employee):
    """
    This method is used to check the employee is in offboarding employee
    """
    return OffboardingEmployee.objects.filter(employee_id=employee).exists()


@register.filter("is_in_managers")
def is_in_managers(employee: Employee, instance: object):
    """
    This method is used to check the employee in the managers or not
    """
    if employee is None or instance is None:
        return False
    cache_key = (type(instance).__name__, getattr(instance, "id", None))
    cache = getattr(employee, "_is_in_managers_cached", None)
    if cache is None:
        cache = {}
        employee._is_in_managers_cached = cache
    if cache_key in cache:
        return cache[cache_key]
    is_in = False
    if isinstance(instance, (Offboarding, OffboardingStage)):
        is_in = instance.managers.filter(employee_id=employee).exists()
    if isinstance(instance, OffboardingEmployee):
        is_in = is_in | (employee == instance.employee_id)
    cache[cache_key] = is_in
    return is_in


@register.filter("is_in_offboarding")
def is_in_offboarding(employee: Employee, offboarding: Offboarding):
    """
    This method is used to check the employee in the offboarding or not
    """
    if employee is None or offboarding is None:
        return False
    cache = getattr(employee, "_is_in_offboarding_cached", None)
    if cache is None:
        cache = {}
        employee._is_in_offboarding_cached = cache
    if offboarding.id in cache:
        return cache[offboarding.id]
    result = (
        (employee in offboarding.managers.all())
        or OffboardingStage.objects.filter(
            offboarding_id=offboarding, managers=employee
        ).exists()
        or OffboardingEmployee.objects.filter(
            stage_id__offboarding_id=offboarding, employee_id=employee
        ).exists()
    )
    cache[offboarding.id] = result
    return result


@register.filter("is_any_stage_manager")
def is_any_stage_manager(employee):
    """
    This method is used to to check any stage manager
    """
    if employee is None:
        return False
    cached = getattr(employee, "_is_any_stage_manager_cached", None)
    if cached is not None:
        return cached
    result = (
        OffboardingStage.objects.filter(managers=employee).exists()
        | Offboarding.objects.filter(managers=employee).exists()
    )
    employee._is_any_stage_manager_cached = result
    return result


@register.filter("is_stage_manager")
def is_stage_manager(employee, stage: OffboardingStage):
    """
    This method is used to check if an employee is a stage manager
    """
    for stag in OffboardingStage.objects.filter(title=stage):
        if employee in stag.managers.all():
            return True
    return False


@register.filter("is_task_manager")
def is_task_manager(employee, task: OffboardingTask):
    """
    This method is used to check if an employee is a stage manager
    """
    for tas in OffboardingTask.objects.filter(title=task):
        if employee in tas.managers.all():
            return True
    return False


@register.filter("completed_tasks")
def completed_tasks(tasks):
    """
    This method is used to to check any stage manager
    """
    return tasks.filter(status__in=["completed", "not_applicable"]).count()


def _tasks_through_stage(employee: OffboardingEmployee, stage: OffboardingStage):
    """
    Tasks assigned to ``employee`` that target ``stage`` or any earlier stage
    in the same offboarding flow (ordered by sequence, with id as tie-breaker
    for the common case where every stage shares sequence=0). Cross-flow
    common tasks (stage_id null with a stage_title) are included if their
    title matches one of the reached stages; fully global tasks (both null)
    are always included.
    """
    from django.db.models import Q

    flow_stage_ids = list(
        OffboardingStage.objects.filter(offboarding_id=stage.offboarding_id)
        .order_by("sequence", "id")
        .values_list("id", flat=True)
    )
    try:
        idx = flow_stage_ids.index(stage.id)
    except ValueError:
        idx = 0
    reached_ids = flow_stage_ids[: idx + 1]
    reached_titles = list(
        OffboardingStage.objects.filter(id__in=reached_ids).values_list(
            "title", flat=True
        )
    )

    return employee.employeetask_set.filter(
        Q(task_id__stage_id__in=reached_ids)
        | (
            Q(task_id__stage_id__isnull=True)
            & (
                Q(task_id__stage_title__in=reached_titles)
                | Q(task_id__stage_title__isnull=True)
            )
        )
    )


@register.filter("stage_completed_tasks")
def stage_completed_tasks(employee: OffboardingEmployee, stage: OffboardingStage):
    """
    Count of completed/not_applicable tasks the employee has accumulated up
    to and including ``stage``.
    """
    return _tasks_through_stage(employee, stage).filter(
        status__in=["completed", "not_applicable"],
    ).count()


@register.filter("stage_total_tasks")
def stage_total_tasks(employee: OffboardingEmployee, stage: OffboardingStage):
    """
    Total tasks the employee has accumulated up to and including ``stage``.
    """
    return _tasks_through_stage(employee, stage).count()


@register.filter("current_stage_tasks")
def current_stage_tasks(employee: OffboardingEmployee):
    """
    Tasks the employee should see now: assignments for the current stage plus
    every stage they have already passed through.
    """
    if not employee.stage_id_id:
        return employee.employeetask_set.none()
    return _tasks_through_stage(employee, employee.stage_id)

@register.filter("is_employee_tasks")
def is_employee_tasks(employee_tasks, task):
    """
    This method is used to to check any stage manager
    """
    try:
        if task.title in employee_tasks.values_list("task_id__title", flat=True):
            return True
        return False
    except:
        return False


@register.filter("is_manager_for_any_task")
def is_manager_for_any_task(employee, tasks):
    """
    Returns True if the employee is a manager for any task in the list of tasks.
    """
    for task in tasks:
        if employee in task.managers.all():
            return True
    return False
