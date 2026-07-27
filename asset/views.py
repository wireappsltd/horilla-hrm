""" "
asset.py

This module is used to"""

import csv
import json
import os
from datetime import date, datetime
from urllib.parse import parse_qs

import pandas as pd
from django.contrib import messages
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.paginator import Paginator
from django.db.models import ProtectedError, Q
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from asset.filters import (
    AssetAllocationFilter,
    AssetAllocationReGroup,
    AssetCategoryFilter,
    AssetExportFilter,
    AssetFilter,
    AssetHistoryFilter,
    AssetHistoryReGroup,
    AssetRequestFilter,
    AssetRequestReGroup,
    CustomAssetFilter,
)
from asset.forms import (
    AssetAllocationForm,
    AssetBatchForm,
    AssetCategoryForm,
    AssetForm,
    AssetReportForm,
    AssetRequestForm,
    AssetReturnForm,
    YearlyCheckupForm,
)
from asset.models import (
    Asset,
    AssetAssignment,
    AssetCategory,
    AssetDocuments,
    AssetLot,
    AssetRequest,
    ReturnImages,
    YearlyCheckupLog,
)
from base.methods import (
    closest_numbers,
    eval_validate,
    filtersubordinates,
    get_key_instances,
    get_pagination,
    paginator_qry,
    sortby,
)
from base.models import Company
from employee.models import Employee, EmployeeWorkInformation
from horilla import settings
from horilla.decorators import (
    hx_request_required,
    login_required,
    manager_can_enter,
    owner_can_enter,
    permission_required,
)
from horilla.group_by import group_by_queryset
from horilla.horilla_settings import HORILLA_DATE_FORMATS
from horilla.methods import horilla_users_with_perms
from horilla_audit.methods import log_activity, log_form_changes
from notifications.signals import notify


def _asset_client_ip(request):
    """Best-effort client IP: first X-Forwarded-For hop, else REMOTE_ADDR."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _asset_audit(request, action, target=None, changes=None):
    """Write an asset-module ActivityLog entry for a lifecycle event.

    Thin wrapper over ``log_activity`` so every asset event lands in the
    "Assets" audit tab with a consistent actor and (where available) the
    client IP stamped into ``changes``. Never blocks the request — the
    underlying helper swallows and logs its own failures.
    """
    changes = dict(changes or {})
    ip = _asset_client_ip(request)
    if ip:
        changes.setdefault("IP", ip)
    log_activity(
        request.user,
        module="asset",
        action=action,
        target=target,
        changes=changes or None,
    )


@login_required
@permission_required("asset.add_asset")
def asset_duplicate(request, obj_id, **kwargs):
    """Duplicate an asset and record the new record in the audit trail.

    ``duplicate-asset`` normally points straight at the shared
    ``base.views.object_duplicate``, which has no audit hook. This wrapper
    delegates to it and, when a duplicate is actually created (POST), logs the
    new asset under the Assets audit tab. The new object is identified by
    diffing asset ids around the call, since ``object_duplicate`` does not
    return the created instance.
    """
    from base.views import object_duplicate

    before_ids = set(Asset.objects.values_list("id", flat=True))
    response = object_duplicate(request, obj_id, **kwargs)
    if request.method == "POST":
        new_asset = Asset.objects.exclude(id__in=before_ids).order_by("id").last()
        if new_asset:
            _asset_audit(
                request,
                "Asset created (duplicated)",
                target=new_asset,
                changes={
                    "Asset": new_asset.asset_name,
                    "Tracking ID": new_asset.asset_tracking_id,
                    "Category": str(new_asset.asset_category_id),
                    "Status": new_asset.get_asset_status_display(),
                    "Duplicated from": str(obj_id),
                },
            )
    return response


def asset_del(request, asset):
    """
    Handle the deletion of an asset and provide message to the user.
    """
    try:
        details = {
            "Asset": asset.asset_name,
            "Tracking ID": asset.asset_tracking_id,
            "Status": asset.get_asset_status_display(),
        }
        asset.delete()
        messages.success(request, _("Asset deleted successfully"))
        _asset_audit(request, "Asset deleted", changes=details)
    except ProtectedError:
        messages.error(request, _("You cannot delete this asset."))


@login_required
@hx_request_required
@permission_required("asset.add_asset")
def asset_creation(request, asset_category_id):
    """
    View function for creating a new asset object.
    Args:
        request (HttpRequest): A Django HttpRequest object that contains information
        about the current request.
        asset_category_id (int): An integer representing the ID of the asset category for which
        the asset is being created.

    Returns:
        If the request method is 'POST' and the form is valid, the function saves the
        new asset object to the database
        and redirects to the asset creation page with a success message.
        If the form is not valid, the function returns the asset creation page with the
        form containing the invalid data.
        If the request method is not 'POST', the function renders the asset creation
        page with the form initialized with
        the ID of the asset category for which the asset is being created.
    Raises:
        None
    """
    initial_data = {"asset_category_id": asset_category_id}
    # Use request.GET to pre-fill the form with dynamic create batch number data if available
    form = (
        AssetForm(initial={**initial_data, **request.GET.dict()})
        if request.GET.get("csrfmiddlewaretoken")
        else AssetForm(initial=initial_data)
    )
    if request.method == "POST":
        form = AssetForm(request.POST, initial=initial_data)
        if form.is_valid():
            asset = form.save()
            _asset_audit(
                request,
                "Asset created",
                target=asset,
                changes={
                    "Asset": asset.asset_name,
                    "Tracking ID": asset.asset_tracking_id,
                    "Category": str(asset.asset_category_id),
                    "Status": asset.get_asset_status_display(),
                },
            )
            messages.success(request, _("Asset created successfully"))
            return redirect("asset-creation", asset_category_id=asset_category_id)
    context = {"asset_creation_form": form}
    return render(request, "asset/asset_creation.html", context)


@login_required
def add_asset_report(request, asset_id=None):
    """
    Function for adding asset report to the asset
    """
    asset_report_form = AssetReportForm()
    if asset_id:
        asset = Asset.objects.get(id=asset_id)
        asset_report_form = AssetReportForm(initial={"asset_id": asset})
        if not request.GET.get("asset_list"):
            if request.user.employee_get == AssetAssignment.objects.get(
                asset_id=asset_id, return_date__isnull=True
            ).assigned_to_employee_id or request.user.has_perm("asset.change_asset"):
                pass
            else:
                return redirect(asset_request_allocation_view)

    if request.method == "POST":
        asset_report_form = AssetReportForm(
            request.POST, request.FILES, initial={"asset_id": asset_id}
        )

        if asset_report_form.is_valid():
            asset_report = asset_report_form.save()
            _asset_audit(
                request,
                "Maintenance record added",
                target=asset_report,
                changes={
                    "Asset": str(asset_report.asset_id),
                    "Title": asset_report.title,
                },
            )
            messages.success(request, _("Report added successfully."))

            if asset_report_form.is_valid() and request.FILES:
                for file in request.FILES.getlist("file"):
                    AssetDocuments.objects.create(asset_report=asset_report, file=file)

                return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
            # return HttpResponse("<script>window.location.reload()</script>")

    return render(
        request,
        "asset/asset_report_form.html",
        {"asset_report_form": asset_report_form, "asset_id": asset_id},
    )


@login_required
@hx_request_required
@permission_required("asset.change_asset")
def asset_update(request, asset_id):
    """
    Updates an asset with the given ID.
    If the request method is GET, it displays the form to update the asset. If the
    request method is POST and the form is valid, it updates the asset and
    redirects to the asset list view for the asset's category.
    Args:
    - request: the HTTP request object
    - id (int): the ID of the asset to be updated
    Returns:
    - If the request method is GET, the rendered 'asset_update.html' template
      with the form to update the asset.
    - If the request method is POST and the form is valid, a redirect to the asset
      list view for the asset's category.
    """

    if request.method == "GET":
        # modal form get
        asset_under = request.GET.get("asset_under")
    elif request.method == "POST":
        # modal form post
        asset_under = request.POST.get("asset_under")

    if not asset_under:
        # if asset there is no asset_under data that means the request is form the category list
        asset_under = "asset_category"
    instance = Asset.objects.get(id=asset_id)
    asset_form = AssetForm(instance=instance)
    previous_data = request.GET.urlencode()

    if request.method == "POST":
        asset_form = AssetForm(request.POST, instance=instance)
        if asset_form.is_valid():
            asset_form.save()
            log_form_changes(
                request.user,
                "asset",
                "Asset updated",
                form=asset_form,
                target=instance,
            )
            messages.success(request, _("Asset Updated"))
    context = {
        "instance": instance,
        "asset_form": asset_form,
        "asset_under": asset_under,
        "pg": previous_data,
        "asset_cat_id": instance.asset_category_id.id,
    }
    requests_ids_json = request.GET.get("requests_ids")
    if requests_ids_json:
        requests_ids = json.loads(requests_ids_json)
        request_copy = request.GET.copy()
        request_copy.pop("requests_ids", None)
        previous_data = request_copy.urlencode()
        context["requests_ids"] = requests_ids
        context["pd"] = previous_data
    return render(request, "asset/asset_update.html", context=context)


def build_asset_timeline(asset):
    """
    Build a unified, time-sorted audit trail of every notable event in an
    asset's life: creation, each allocation, each return, each yearly
    check-up, and each report.

    Returns a list of dicts ordered newest first. Each dict has:
        - timestamp: datetime/date used for sorting
        - sort_key:  comparable datetime (timestamp normalised)
        - kind:      'created' | 'allocated' | 'returned' | 'checkup' | 'report'
        - actor:     Employee or User who performed the action (or None)
        - title:     short headline shown in the bubble header
        - body:      free-text body
        - images:    iterable of ReturnImages
        - documents: iterable of AssetDocuments
        - extras:    dict for kind-specific badges/values
    """
    from datetime import datetime, time

    events = []

    def _to_dt(value):
        if value is None:
            return datetime.min
        if isinstance(value, datetime):
            return value.replace(tzinfo=None) if value.tzinfo else value
        return datetime.combine(value, time.min)

    if asset.created_at:
        events.append({
            "timestamp": _to_dt(asset.created_at),
            "sort_key": _to_dt(asset.created_at),
            "kind": "created",
            "actor": asset.created_by,
            "title": _("Asset added to inventory"),
            "body": asset.asset_description or "",
            "images": [],
            "documents": [],
            "extras": {"tracking_id": asset.asset_tracking_id},
        })

    assignments = AssetAssignment.objects.filter(asset_id=asset).select_related(
        "assigned_to_employee_id", "assigned_by_employee_id"
    )
    for assignment in assignments:
        if assignment.assigned_date:
            events.append({
                "timestamp": _to_dt(assignment.assigned_date),
                "sort_key": _to_dt(assignment.assigned_date),
                "kind": "allocated",
                "actor": assignment.assigned_by_employee_id,
                "title": _("Allocated to %(employee)s") % {
                    "employee": assignment.assigned_to_employee_id or "-",
                },
                "body": "",
                "images": list(assignment.assign_images.all()),
                "documents": [],
                "extras": {
                    "assigned_to": assignment.assigned_to_employee_id,
                    "assignment_id": assignment.id,
                },
            })
        if assignment.return_date:
            events.append({
                "timestamp": _to_dt(assignment.return_date),
                "sort_key": _to_dt(assignment.return_date),
                "kind": "returned",
                "actor": assignment.assigned_to_employee_id,
                "title": _("Returned by %(employee)s") % {
                    "employee": assignment.assigned_to_employee_id or "-",
                },
                "body": assignment.return_condition or "",
                "images": list(assignment.return_images.all()),
                "documents": [],
                "extras": {
                    "return_status": assignment.return_status,
                    "assignment_id": assignment.id,
                },
            })
        for log in assignment.checkup_logs.all():
            log_ts = log.created_at or log.checkup_date
            events.append({
                "timestamp": _to_dt(log_ts),
                "sort_key": _to_dt(log_ts),
                "kind": "checkup",
                "actor": log.submitted_by,
                "title": _("Yearly check-up performed"),
                "body": log.description or "",
                "images": list(log.images.all()),
                "documents": [],
                "extras": {
                    "checkup_date": log.checkup_date,
                    "assigned_to": assignment.assigned_to_employee_id,
                    "assignment_id": assignment.id,
                },
            })

    for report in asset.asset_report.all():
        events.append({
            "timestamp": _to_dt(report.created_at),
            "sort_key": _to_dt(report.created_at),
            "kind": "report",
            "actor": report.created_by,
            "title": _("Report filed: %(title)s") % {"title": report.title or "-"},
            "body": "",
            "images": [],
            "documents": list(report.documents.all()) if hasattr(report, "documents") else [],
            "extras": {"report_id": report.id},
        })

    events.sort(key=lambda e: e["sort_key"], reverse=True)
    return events


@login_required
@hx_request_required
def asset_information(request, asset_id):
    """
    Display information about a specific Asset object.
    Args:
        request: the HTTP request object
        asset_id (int): the ID of the Asset object to retrieve
    Returns:
        A rendered HTML template displaying the information about the requested Asset object.
    """

    asset = Asset.objects.get(id=asset_id)
    context = {
        "asset": asset,
        "asset_timeline": build_asset_timeline(asset),
    }
    requests_ids_json = request.GET.get("requests_ids")
    if requests_ids_json:
        requests_ids = json.loads(requests_ids_json)
        previous_id, next_id = closest_numbers(requests_ids, asset_id)
        context["requests_ids"] = requests_ids_json
        context["previous"] = previous_id
        context["next"] = next_id
    return render(request, "asset/asset_information.html", context)


@login_required
@permission_required(perm="asset.delete_asset")
def asset_delete(request, asset_id):
    """Delete the asset with the given id.
    If the asset is currently in use, display an info message and
    redirect to the asset list.
    Otherwise, delete the asset and display a success message.
    Args:
        request: HttpRequest object representing the current request.
        asset_id: int representing the id of the asset to be deleted.
    Returns:
        If the asset is currently in use or the asset list filter is
        applied, render the asset list template
        with the corresponding context.
        Otherwise, redirect to the asset list view for the asset
        category of the deleted asset.
    """

    request_copy = request.GET.copy()
    request_copy.pop("requests_ids", None)
    previous_data = request_copy.urlencode()
    try:
        asset = Asset.objects.get(id=asset_id)
    except Asset.DoesNotExist:
        messages.error(request, _("Asset not found"))
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
    asset_cat_id = asset.asset_category_id.id
    status = asset.asset_status
    asset_list_filter = request.GET.get("asset_list")
    asset_allocation = AssetAssignment.objects.filter(asset_id=asset).first()
    if asset_list_filter:
        # if the asset deleted is from the filtered list of asset
        asset_under = "asset_filter"
        assets = Asset.objects.all()
        previous_data = request.GET.urlencode()
        asset_filtered = AssetFilter(request.GET, queryset=assets)
        asset_list = asset_filtered.qs
        paginator = Paginator(asset_list, get_pagination())
        page_number = request.GET.get("page")
        page_obj = paginator.get_page(page_number)
        context = {
            "assets": page_obj,
            "pg": previous_data,
            "asset_category_id": asset.asset_category_id.id,
            "asset_under": asset_under,
        }
        if status == "In use":
            messages.info(request, _("Asset is in use"))
        elif asset_allocation:
            messages.error(request, _("Asset is used in allocation!."))
        else:
            asset_del(request, asset)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    instances_ids = request.GET.get("requests_ids", "[]")
    instances_list = eval_validate(instances_ids)
    if status == "In use":
        messages.info(request, _("Asset is in use"))
        return redirect(
            f"/asset/asset-information/{asset.id}/?{previous_data}&requests_ids={instances_list}&asset_info=true"
        )
    elif asset_allocation:
        messages.error(request, _("Asset is used in allocation!."))
        return redirect(
            f"/asset/asset-information/{asset.id}/?{previous_data}&requests_ids={instances_list}&asset_info=true"
        )
    else:
        asset_del(request, asset)
        if len(eval_validate(instances_ids)) <= 1:
            return HttpResponse("<script>window.location.reload();</script>")

        if Asset.find(asset.id):
            return redirect(
                f"/asset/asset-information/{asset.id}/?{previous_data}&requests_ids={instances_list}&asset_info=true"
            )
        else:
            instances_ids = request.GET.get("requests_ids")
            instances_list = json.loads(instances_ids)
            if asset_id in instances_list:
                instances_list.remove(asset_id)
    previous_instance, next_instance = closest_numbers(
        json.loads(instances_ids), asset_id
    )
    return redirect(
        f"/asset/asset-information/{next_instance}/?{previous_data}&requests_ids={instances_list}&asset_info=true"
    )


@login_required
@hx_request_required
def asset_list(request, cat_id):
    """
    View function is used as asset list inside a category and also in
    filter asset list
    Args:
        request (HttpRequest): A Django HttpRequest object that contains
        information about the  current request.
        cat_id (int): An integer representing the id of the asset category
        to list assets for.
    Returns:
        A rendered HTML template that displays a paginated list of assets in the given
        asset  category.
    Raises:
        None
    """
    context = {}
    asset_under = ""
    asset_filtered = AssetFilter(request.GET)
    asset_list = asset_filtered.qs.filter(asset_category_id=cat_id)

    paginator = Paginator(asset_list, get_pagination())
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    requests_ids = json.dumps([instance.id for instance in page_obj.object_list])
    previous_data = request.GET.urlencode()
    data_dict = parse_qs(previous_data)
    get_key_instances(Asset, data_dict)
    context = {
        "assets": page_obj,
        "pg": previous_data,
        "asset_category_id": cat_id,
        "asset_under": asset_under,
        "asset_count": len(asset_list) or None,
        "filter_dict": data_dict,
        "requests_ids": requests_ids,
    }
    return render(request, "asset/asset_list.html", context)


@login_required
@hx_request_required
@permission_required(perm="asset.add_assetcategory")
def asset_category_creation(request):
    """
    Allow a user to create a new AssetCategory object using a form.
    Args:
        request: the HTTP request object
    Returns:
        A rendered HTML template displaying the AssetCategory creation form.
    """
    form = AssetCategoryForm()

    if request.method == "POST":
        form = AssetCategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _("Asset category created successfully"))
            form = AssetCategoryForm()
            if AssetCategory.objects.filter().count() == 1:
                if AssetCategory.objects.count() == 1:
                    return HttpResponse(status=204, headers={"HX-Refresh": "true"})
    context = {"form": form}
    return render(request, "category/asset_category_form.html", context)


@login_required
@hx_request_required
@permission_required(perm="asset.change_assetcategory")
def asset_category_update(request, cat_id):
    """
    This view is used to update an existing asset category.
    Args:
        request: HttpRequest object.
        id: int value representing the id of the asset category to update.
    Returns:
        Rendered HTML template.
    """

    previous_data = request.GET.urlencode()
    asset_category = AssetCategory.objects.get(id=cat_id)
    form = AssetCategoryForm(instance=asset_category)
    context = {"form": form, "pg": previous_data}
    if request.method == "POST":
        form = AssetCategoryForm(request.POST, instance=asset_category)
        if form.is_valid():
            form.save()
            messages.success(request, _("Asset category updated successfully"))
        else:
            context["form"] = form

    return render(request, "category/asset_category_form.html", context)


@login_required
@permission_required(perm="asset.delete_assetcategory")
def delete_asset_category(request, cat_id):
    """
    This method is used to delete asset category
    """
    previous_data = request.GET.urlencode()
    try:
        AssetCategory.objects.get(id=cat_id).delete()
        messages.success(request, _("Asset category deleted."))
    except:
        messages.error(request, _("Assets are located within this category."))
    if not AssetCategory.objects.filter():
        return HttpResponse(status=204, headers={"HX-Refresh": "true"})
    return redirect(f"/asset/asset-category-view-search-filter?{previous_data}")


def filter_pagination_asset_category(request):
    """
    This view is used for pagination and filtering asset categories
    """
    search = request.GET.get("search", "")

    previous_data = request.GET.urlencode()

    asset_category_queryset = AssetCategory.objects.all()

    if request.GET:
        asset_category_filtered = AssetCategoryFilter(
            request.GET, queryset=asset_category_queryset
        )
        asset_category_queryset = (
            asset_category_filtered.qs
        )  # Filter the queryset based on the GET params
        asset_category_filtered_form = asset_category_filtered.form  # Show filter form
    else:
        asset_category_filtered_form = None

    # Pagination
    asset_category_paginator = Paginator(asset_category_queryset, get_pagination())
    page_number = request.GET.get("page")
    asset_categories = asset_category_paginator.get_page(page_number)

    data_dict = parse_qs(previous_data)
    get_key_instances(AssetCategory, data_dict)

    asset_creation_form = AssetForm()
    if data_dict.get("type"):
        del data_dict["type"]
    asset_category_form = AssetCategoryForm()
    asset_filter_form = AssetFilter()
    return {
        "asset_creation_form": asset_creation_form,
        "asset_category_form": asset_category_form,
        "asset_categories": asset_categories,
        "asset_category_filter_form": asset_category_filtered_form,
        "asset_filter_form": asset_filter_form.form,
        "pg": previous_data,
        "filter_dict": data_dict,
        "dashboard": request.GET.get("dashboard"),
        "model": AssetCategory,
    }


@login_required
@permission_required(perm="asset.view_assetcategory")
def asset_category_view(request):
    """
    View function for rendering a paginated list of asset categories.
    Args:
        request (HttpRequest): A Django HttpRequest object that contains information
        about the current request.
    Returns:
        A rendered HTML template that displays a paginated list of asset categories.
    Raises:
        None
    """

    queryset = AssetCategory.objects.all()
    if queryset.exists():
        template = "category/asset_category_view.html"
    else:
        template = "category/asset_empty.html"
    context = filter_pagination_asset_category(request)
    return render(request, template, context)


@login_required
@permission_required(perm="asset.view_assetcategory")
def asset_category_view_search_filter(request):
    """
    View function for rendering a paginated list of asset categories with search and filter options.
    Args:
        request (HttpRequest): A Django HttpRequest object that contains information
        about the current request.
    Returns:
        A rendered HTML template that displays a paginated list of asset
        categories  with search and filter options.
    Raises:
        None
    """
    context = filter_pagination_asset_category(request)
    return render(request, "category/asset_category.html", context)


def request_creation_hx_returns(referer, user):
    """
    Determines the hx_url and hx_target based on the referer path
    for asset request creation
    """
    referer = "/" + "/".join(referer.split("/")[3:])
    # Map referer paths to corresponding URLs and targets
    hx_map = {
        "/": ("asset-dashboard-requests", "dashboardAssetRequests"),
        "/asset/dashboard/": ("asset-dashboard-requests", "dashboardAssetRequests"),
        "/asset/asset-request-allocation-view/": (
            "asset-request-allocation-view-search-filter",
            "asset_request_allocation_list",
        ),
        "/employee/employee-profile/": (
            "profile-asset-tab",
            "asset_target",
        ),
    }

    hx_url, hx_target = hx_map.get(
        referer, (None, None)
    )  # Default to None if not in map

    if hx_url == "profile-asset-tab":
        hx_url = reverse(hx_url, kwargs={"emp_id": user.employee_get.id})
    else:
        hx_url = reverse(hx_url) if hx_url else None

    return hx_url, hx_target


@login_required
@hx_request_required
def asset_request_creation(request):
    """
    Creates a new AssetRequest object and saves it to the database.
    Renders the asset_request_creation.html template if the request method is GET.
    If the request method is POST and the form data is valid, the new
    AssetRequest is saved to the database and
    the user is redirected to the asset_request_view_search_filter view.
    If the form data is invalid, or if the request method is POST but the
    form data is not present, the user is
    presented with the asset_request_creation.html template with error
    messages displayed.
    """
    # intitial  = {'requested_employee_id':request.user.employee_get}

    referer = request.META.get("HTTP_REFERER", "/")
    hx_url, hx_target = request_creation_hx_returns(referer, request.user)
    form = AssetRequestForm(user=request.user)
    context = {"asset_request_form": form, "hx_url": hx_url, "hx_target": hx_target}
    if request.method == "POST":
        form = AssetRequestForm(request.POST, user=request.user)
        if form.is_valid():
            asset_request = form.save()
            _asset_audit(
                request,
                "Asset requested",
                target=asset_request,
                changes={
                    "Requested by": str(asset_request.requested_employee_id),
                    "Category": str(asset_request.asset_category_id),
                    "Description": asset_request.description or "",
                },
            )
            messages.success(request, _("Asset request created!"))
        context["asset_request_form"] = form

    return render(request, "request_allocation/asset_request_creation.html", context)


@login_required
@hx_request_required
@permission_required(perm="asset.add_assetassignment")
def asset_request_approve(request, req_id):
    """
    Approves an asset request with the given ID and updates the corresponding asset record
    to mark it as allocated.
    """
    asset_request = AssetRequest.find(req_id)
    homepage_url = request.build_absolute_uri("/")
    error_response = (
        f"<script>" f'window.location.href = "{homepage_url}";' f"</script>"
    )
    if not asset_request:
        messages.error(request, _("Asset request does not exist."))
        return HttpResponse(error_response)

    assets = asset_request.asset_category_id.asset_set.filter(asset_status="Available")
    if request.method == "POST":
        post_data = request.POST.copy()
        post_data["assigned_to_employee_id"] = asset_request.requested_employee_id
        post_data["assigned_by_employee_id"] = request.user.employee_get

        form = AssetAllocationForm(post_data, request.FILES)
        if form.is_valid():
            try:
                asset = form.cleaned_data["asset_id"]
                asset.asset_status = "In use"
                asset.save()

                allocation = form.save(commit=False)
                allocation.assigned_by_employee_id = request.user.employee_get
                allocation.save()

                asset_request.asset_request_status = "Approved"
                asset_request.save()

                _asset_audit(
                    request,
                    "Asset request approved",
                    target=asset_request,
                    changes={
                        "Requested by": str(asset_request.requested_employee_id),
                        "Category": str(asset_request.asset_category_id),
                        "Asset assigned": str(asset),
                        "Assigned to": str(allocation.assigned_to_employee_id),
                        "Request status": {"from": "Requested", "to": "Approved"},
                        "Asset status": {"from": "Available", "to": "In use"},
                    },
                )

                notify.send(
                    request.user.employee_get,
                    recipient=allocation.assigned_to_employee_id.employee_user_id,
                    verb=_("Your asset request has been approved!"),
                    redirect=reverse("asset-request-allocation-view")
                    + f"?asset_request_date={asset_request.asset_request_date}&"
                    f"asset_request_status={asset_request.asset_request_status}",
                    icon="bag-check",
                )

                messages.success(request, _("Asset request approved successfully!"))
                return HttpResponse("<script>window.location.reload();</script>")
            except Exception as e:
                messages.error(request, _("An error occurred: ") + str(e))
                return HttpResponse(error_response)
    else:
        form = AssetAllocationForm()
        form.fields["asset_id"].queryset = assets

    context = {"asset_allocation_form": form, "id": req_id}
    return render(request, "request_allocation/asset_approve.html", context)


def reject_request_return(request, asset_request, req_id):
    if not request.META.get("HTTP_HX_REQUEST"):
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

    hx_target = request.META.get("HTTP_HX_TARGET")
    if hx_target == "objectDetailsModalW25Target":
        try:
            requests_ids = json.loads(request.GET.get("requests_ids", "[]"))
        except json.JSONDecodeError:
            requests_ids = []
        return redirect(
            reverse(
                "asset-request-individual-view", kwargs={"asset_request_id": req_id}
            )
            + f"?requests_ids={requests_ids}"
        )

    referrer = request.META.get("HTTP_REFERER", "")
    referrer = "/" + "/".join(referrer.split("/")[3:])
    if referrer.startswith("/employee/employee-view/"):
        return redirect(
            f"/asset/asset-request-tab/{asset_request.requested_employee_id.id}"
        )

    if referrer.endswith("/asset/dashboard/") or referrer == "/":
        return redirect(reverse("asset-dashboard-requests"))

    return redirect(
        f"{reverse('asset-request-allocation-view-search-filter')}?{request.GET.urlencode()}"
    )


@login_required
@permission_required(perm="asset.add_assetassignment")
def asset_request_reject(request, req_id):
    """
    View function to reject an asset request.
    Parameters:
    request (HttpRequest): the request object sent by the client
    req_id (int): the id of the AssetRequest object to reject

    Returns:
    HttpResponse: a redirect to the asset request list view with a success
        message if the asset request is rejected successfully, or a redirect to the
        asset request detail view with an error message if the asset request is not
        found or already rejected
    """
    asset_request = AssetRequest.objects.get(id=req_id)
    asset_request.asset_request_status = "Rejected"
    asset_request.save()
    _asset_audit(
        request,
        "Asset request rejected",
        target=asset_request,
        changes={
            "Requested by": str(asset_request.requested_employee_id),
            "Category": str(asset_request.asset_category_id),
            "Request status": {"from": "Requested", "to": "Rejected"},
        },
    )
    messages.info(request, _("Asset request has been rejected."))
    notify.send(
        request.user.employee_get,
        recipient=asset_request.requested_employee_id.employee_user_id,
        verb="Your asset request rejected!.",
        verb_ar="تم رفض طلب الأصول الخاص بك!",
        verb_de="Ihr Antragsantrag wurde abgelehnt!",
        verb_es="¡Se ha rechazado su solicitud de activo!",
        verb_fr="Votre demande d'actif a été rejetée !",
        redirect=reverse("asset-request-allocation-view")
        + f"?asset_request_date={asset_request.asset_request_date}\
        &asset_request_status={asset_request.asset_request_status}",
        icon="bag-check",
    )
    return reject_request_return(request, asset_request, req_id)


@login_required
@permission_required(perm="asset.add_assetassignment")
def asset_allocate_creation(request):
    """
    View function to create asset allocation.
    Returns:
    - to allocated view.
    """

    form = AssetAllocationForm(
        initial={"assigned_by_employee_id": request.user.employee_get}
    )
    context = {"asset_allocation_form": form}
    if request.method == "POST":
        form = AssetAllocationForm(request.POST)
        if form.is_valid():
            asset = form.instance.asset_id.id
            asset = Asset.objects.filter(id=asset).first()
            asset.asset_status = "In use"
            asset.save()
            instance = form.save()
            _asset_audit(
                request,
                "Asset assigned",
                target=instance,
                changes={
                    "Asset": str(instance.asset_id),
                    "Assigned to": str(instance.assigned_to_employee_id),
                    "Assigned by": str(instance.assigned_by_employee_id),
                    "Asset status": {"from": "Available", "to": "In use"},
                },
            )
            files = request.FILES.getlist("assign_images")
            attachments = []
            if request.FILES:
                for file in files:
                    attachment = ReturnImages()
                    attachment.image = file
                    attachment.save()
                    attachments.append(attachment)
                instance.assign_images.add(*attachments)
            form = AssetAllocationForm(
                initial={"assigned_by_employee_id": request.user.employee_get}
            )
            messages.success(request, _("Asset allocated successfully!."))
        context["asset_allocation_form"] = form
    return render(request, "request_allocation/asset_allocation_creation.html", context)


@login_required
def asset_allocate_return_request(request, asset_id):
    """
    Handle the initiation of a return request for an allocated asset.
    """
    previous_data = request.GET.urlencode()
    asset_assign = AssetAssignment.objects.get(id=asset_id)
    asset_assign.return_request = True
    asset_assign.save()
    _asset_audit(
        request,
        "Asset return requested",
        target=asset_assign,
        changes={
            "Asset": str(asset_assign.asset_id),
            "Assigned to": str(asset_assign.assigned_to_employee_id),
        },
    )
    message = _("Return request for {} initiated.").format(asset_assign.asset_id)
    messages.success(request, message)
    permed_users = horilla_users_with_perms("asset.change_assetassignment")
    notify.send(
        request.user.employee_get,
        recipient=permed_users,
        verb=f"Return request for {asset_assign.asset_id} initiated from\
            {asset_assign.assigned_to_employee_id}",
        verb_ar=f"تم بدء طلب الإرجاع للمورد {asset_assign.asset_id}\
            من الموظف {asset_assign.assigned_to_employee_id}",
        verb_de=f"Rückgabewunsch für {asset_assign.asset_id} vom Mitarbeiter\
            {asset_assign.assigned_to_employee_id} initiiert",
        verb_es=f"Solicitud de devolución para {asset_assign.asset_id}\
            iniciada por el empleado {asset_assign.assigned_to_employee_id}",
        verb_fr=f"Demande de retour pour {asset_assign.asset_id}\
            initiée par l'employé {asset_assign.assigned_to_employee_id}",
        redirect=reverse("asset-request-allocation-view")
        + f"?assigned_to_employee_id={asset_assign.assigned_to_employee_id}&\
        asset_id={asset_assign.asset_id}&assigned_date={asset_assign.assigned_date}",
        icon="bag-check",
    )
    if request.META.get("HTTP_HX_REQUEST") == "true":
        url = reverse("asset-request-allocation-view-search-filter")
        return redirect(f"{url}?{previous_data}")

    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
@permission_required(perm="asset.change_assetassignment")
def asset_allocate_return(request, asset_id):
    """
    View function to return asset.
    Args:
    - asset_id: integer value representing the ID of the asset
    Returns:
    - message of the return
    """

    asset_return_form = AssetReturnForm()
    asset_allocation = AssetAssignment.objects.filter(
        asset_id=asset_id, return_status__isnull=True
    ).first()
    if request.method == "POST":
        asset_return_form = AssetReturnForm(request.POST, request.FILES)

        if asset_return_form.is_valid():
            asset = Asset.objects.filter(id=asset_id).first()
            asset_return_status = request.POST.get("return_status")
            asset_return_date = request.POST.get("return_date")
            asset_return_condition = request.POST.get("return_condition")
            files = request.FILES.getlist("return_images")
            attachments = []
            context = {"asset_return_form": asset_return_form, "asset_id": asset_id}
            response = render(request, "asset/asset_return_form.html", context)
            if asset_return_status == "Healthy":
                asset_allocation = AssetAssignment.objects.filter(
                    asset_id=asset_id, return_status__isnull=True
                ).first()
                asset_allocation.return_date = asset_return_date
                asset_allocation.return_status = asset_return_status
                asset_allocation.return_condition = asset_return_condition
                asset_allocation.return_request = False
                asset_allocation.save()
                if request.FILES:
                    for file in files:
                        attachment = ReturnImages()
                        attachment.image = file
                        attachment.save()
                        attachments.append(attachment)
                    asset_allocation.return_images.add(*attachments)
                asset.asset_status = "Available"
                asset.save()
                _asset_audit(
                    request,
                    "Asset returned",
                    target=asset_allocation,
                    changes={
                        "Asset": asset.asset_name,
                        "Returned by": str(asset_allocation.assigned_to_employee_id),
                        "Condition": asset_return_condition or "",
                        "Return status": asset_return_status,
                        "Asset status": {"from": "In use", "to": "Available"},
                    },
                )
                messages.info(request, _("Asset Return Successful !."))
                return HttpResponse(
                    response.content.decode("utf-8")
                    + "<script>location.reload();</script>"
                )
            asset.asset_status = "Not-Available"
            asset.save()
            asset_allocation = AssetAssignment.objects.filter(
                asset_id=asset_id, return_status__isnull=True
            ).first()
            asset_allocation.return_date = asset_return_date
            asset_allocation.return_status = asset_return_status
            asset_allocation.return_condition = asset_return_condition
            asset_allocation.save()
            if request.FILES:
                for file in files:
                    attachment = ReturnImages()
                    attachment.image = file
                    attachment.save()
                    attachments.append(attachment)
                asset_allocation.return_images.add(*attachments)
            _asset_audit(
                request,
                "Asset returned (decommissioned)",
                target=asset_allocation,
                changes={
                    "Asset": asset.asset_name,
                    "Returned by": str(asset_allocation.assigned_to_employee_id),
                    "Condition": asset_return_condition or "",
                    "Return status": asset_return_status,
                    "Asset status": {"from": "In use", "to": "Not-Available"},
                },
            )
            messages.info(request, _("Asset Return Successful!."))
            return HttpResponse(
                response.content.decode("utf-8") + "<script>location.reload();</script>"
            )

    context = {"asset_return_form": asset_return_form, "asset_id": asset_id}
    context["asset_alocation"] = asset_allocation
    return render(request, "asset/asset_return_form.html", context)


def filter_pagination_asset_request_allocation(request):
    """
    Filter and paginate asset request and allocation data based on search criteria and sort options.

    This function handles the retrieval, filtering, and pagination of asset request and allocation
    data.It processes GET parameters to search, sort, and filter asset requests and allocations,
    and returns a context dictionary with the filtered data and associated forms for rendering in
    a template.
    """
    from django.contrib.auth.models import Group

    asset_request_allocation_search = request.GET.get("search")
    request_field = request.GET.get("request_field")
    allocation_field = request.GET.get("allocation_field")
    if asset_request_allocation_search is None:
        asset_request_allocation_search = ""
    employee = request.user.employee_get
    is_hr_or_ops = (
        request.user.is_superuser
        or request.user.groups.filter(name__in=("HR", "OPS")).exists()
    )
    if is_hr_or_ops:
        asset_assignment = AssetAssignment.objects.all()
        asset_request = AssetRequest.objects.all()
    else:
        asset_assignment = AssetAssignment.objects.filter(
            assigned_to_employee_id=employee
        )
        asset_request = AssetRequest.objects.filter(requested_employee_id=employee)
    asset_request = asset_request.distinct()
    if request.GET.get("assign_sortby"):
        asset_assignment = sortby(request, asset_assignment, "assign_sortby")
    if request.GET.get("request_sortby"):
        asset_request = sortby(request, asset_request, "request_sortby")

    assets = (
        asset_assignment.filter(assigned_to_employee_id=employee)
        .exclude(return_status__isnull=False)
        .filter(asset_id__asset_name__icontains=asset_request_allocation_search)
    )

    previous_data = request.GET.urlencode()
    assets_filtered = CustomAssetFilter(request.GET, queryset=assets)
    asset_request_filtered = AssetRequestFilter(request.GET, queryset=asset_request).qs
    if request_field != "" and request_field is not None:
        asset_request_filtered = group_by_queryset(
            asset_request_filtered, request_field, request.GET.get("page"), "page"
        )
        list_values = [entry["list"] for entry in asset_request_filtered]
        id_list = []
        for value in list_values:
            for instance in value.object_list:
                id_list.append(instance.id)

        requests_ids = json.dumps(list(id_list))

    else:
        asset_request_filtered = paginator_qry(
            asset_request_filtered, request.GET.get("page")
        )
        requests_ids = json.dumps(
            [instance.id for instance in asset_request_filtered.object_list]
        )

    asset_allocation_filtered = AssetAllocationFilter(
        request.GET, queryset=asset_assignment
    ).qs

    if allocation_field != "" and allocation_field is not None:
        asset_allocation_filtered = group_by_queryset(
            asset_allocation_filtered, allocation_field, request.GET.get("page"), "page"
        )
        list_values = [entry["list"] for entry in asset_allocation_filtered]
        id_list = []
        for value in list_values:
            for instance in value.object_list:
                id_list.append(instance.id)

        allocations_ids = json.dumps(list(id_list))

    else:
        asset_allocation_filtered = paginator_qry(
            asset_allocation_filtered, request.GET.get("page")
        )
        allocations_ids = json.dumps(
            [instance.id for instance in asset_allocation_filtered.object_list]
        )

    assets_ids = paginator_qry(assets, request.GET.get("page"))
    assets_id = json.dumps([instance.id for instance in assets_ids.object_list])
    asset_paginator = Paginator(assets_filtered.qs, get_pagination())
    page_number = request.GET.get("page")
    assets = asset_paginator.get_page(page_number)
    data_dict = parse_qs(previous_data)
    get_key_instances(AssetRequest, data_dict)
    get_key_instances(AssetAssignment, data_dict)
    get_key_instances(Asset, data_dict)
    return {
        "assets": assets,
        "asset_requests": asset_request_filtered,
        "asset_allocations": asset_allocation_filtered,
        "assets_filter_form": assets_filtered.form,
        "asset_request_filter_form": AssetRequestFilter().form,
        "asset_allocation_filter_form": AssetAllocationFilter().form,
        "pg": previous_data,
        "filter_dict": data_dict,
        "gp_request_fields": AssetRequestReGroup.fields,
        "gp_Allocation_fields": AssetAllocationReGroup.fields,
        "request_field": request_field,
        "allocation_field": allocation_field,
        "requests_ids": requests_ids,
        "allocations_ids": allocations_ids,
        "asset_ids": assets_id,
    }


@login_required
def asset_request_allocation_view(request):
    """
    This view is used to display a paginated list of asset allocation requests.
    Args:
        request (HttpRequest): The HTTP request object.
    Returns:
        HttpResponse: The HTTP response object with the rendered HTML template.
    """
    context = filter_pagination_asset_request_allocation(request)
    template = "request_allocation/asset_request_allocation_view.html"

    if (
        request.GET.get("request_field") != ""
        and request.GET.get("request_field") is not None
        or request.GET.get("allocation_field") != ""
        and request.GET.get("allocation_field") is not None
    ):
        template = "request_allocation/group_by.html"

    return render(request, template, context)


@login_required
def asset_request_alloaction_view_search_filter(request):
    """
    This view handles the search and filter functionality for the asset request allocation list.
    Args:
        request: HTTP request object.
    Returns:
        Rendered HTTP response with the filtered and paginated asset request allocation list.
    """
    context = filter_pagination_asset_request_allocation(request)
    template = "request_allocation/asset_request_allocation_list.html"
    if (
        request.GET.get("request_field") != ""
        and request.GET.get("request_field") is not None
        or request.GET.get("allocation_field") != ""
        and request.GET.get("allocation_field") is not None
    ):
        template = "request_allocation/group_by.html"

    return render(request, template, context)


@login_required
@hx_request_required
def own_asset_individual_view(request, asset_id):
    """
    This function is responsible for view the individual own asset

    Args:
        request : HTTP request object
        id (int): Id of the asset assignment
    """
    asset_assignment = get_object_or_404(AssetAssignment, id=asset_id)
    is_hr_or_ops = (
        request.user.is_superuser
        or request.user.groups.filter(name__in=("HR", "OPS")).exists()
    )
    if not is_hr_or_ops and asset_assignment.assigned_to_employee_id != request.user.employee_get:
        return HttpResponse(status=403)
    asset = asset_assignment.asset_id
    context = {
        "asset": asset,
        "asset_assignment": asset_assignment,
    }
    requests_ids_json = request.GET.get("assets_ids")
    if requests_ids_json:
        requests_ids = json.loads(requests_ids_json)
        previous_id, next_id = closest_numbers(requests_ids, asset_id)
        context["assets_ids"] = requests_ids_json
        context["previous"] = previous_id
        context["next"] = next_id
    return render(request, "request_allocation/individual_own.html", context)


@login_required
@hx_request_required
def asset_request_individual_view(request, asset_request_id):
    """
    Display the details of an individual asset request.

    This view retrieves the asset request with the given ID and renders it in the
    'individual_request.html' template. If a JSON-encoded list of request IDs is
    provided in the GET parameters, the view also determines the previous and next
    request IDs for easy navigation.

    Args:
        request (HttpRequest): The HTTP request object containing metadata about the request.
        id (int): The ID of the asset request to be viewed.

    Returns:
        HttpResponse: The rendered 'individual_request.html' template with the context data.
    """
    dashboard = not request.META.get("HTTP_HX_CURRENT_URL", "").endswith(
        "asset-request-allocation-view/"
    )
    asset_request = get_object_or_404(AssetRequest, id=asset_request_id)
    is_hr_or_ops = (
        request.user.is_superuser
        or request.user.groups.filter(name__in=("HR", "OPS")).exists()
    )
    if not is_hr_or_ops and asset_request.requested_employee_id != request.user.employee_get:
        return HttpResponse(status=403)
    context = {
        "asset_request": asset_request,
        "dashboard": dashboard,
    }
    requests_ids_json = request.GET.get("requests_ids")
    if requests_ids_json:
        requests_ids = json.loads(requests_ids_json)
        previous_id, next_id = closest_numbers(requests_ids, asset_request_id)
        context["requests_ids"] = requests_ids_json
        context["previous"] = previous_id
        context["next"] = next_id
    return render(request, "request_allocation/individual_request.html", context)


@login_required
@hx_request_required
def asset_allocation_individual_view(request, asset_allocation_id):
    """
    Display the details of an individual asset allocation.

    This view retrieves the asset allocation with the given ID and renders it in the
    'individual_allocation.html' template. If a JSON-encoded list of allocation IDs is
    provided in the GET parameters, the view also determines the previous and next
    allocation IDs for easy navigation.

    Args:
        request (HttpRequest): The HTTP request object containing metadata about the request.
        id (int): The ID of the asset allocation to be viewed.

    Returns:
        HttpResponse: The rendered 'individual_allocation.html' template with the context data.
    """
    asset_allocation = get_object_or_404(AssetAssignment, id=asset_allocation_id)
    is_hr_or_ops = (
        request.user.is_superuser
        or request.user.groups.filter(name__in=("HR", "OPS")).exists()
    )
    if not is_hr_or_ops and asset_allocation.assigned_to_employee_id != request.user.employee_get:
        return HttpResponse(status=403)
    context = {"asset_allocation": asset_allocation}
    allocation_ids_json = request.GET.get("allocations_ids")
    if allocation_ids_json:
        allocation_ids = json.loads(allocation_ids_json)
        previous_id, next_id = closest_numbers(allocation_ids, asset_allocation_id)
        context["allocations_ids"] = allocation_ids_json
        context["previous"] = previous_id
        context["next"] = next_id
    return render(request, "request_allocation/individual allocation.html", context)


def convert_nan(val):
    """
    Convert NaN values to None.
    """
    if pd.isna(val):
        return None
    return val


fs = FileSystemStorage(location="csv_tmp/")


def csv_asset_import(file):
    file_content = ContentFile(file.read())
    file_name = fs.save("_tmp.csv", file_content)
    tmp_file = fs.path(file_name)

    with open(tmp_file, errors="ignore") as csv_file:
        reader = csv.reader(csv_file)
        next(reader)  # Skip header row

        asset_list = []
        for row in reader:
            (
                asset_name,
                asset_description,
                asset_tracking_id,
                asset_purchase_date,
                asset_purchase_cost,
                asset_category_name,
                asset_status,
                asset_lot_number,
            ) = row

            # Helper function to get or create categories and lots
            asset_category, _ = AssetCategory.objects.get_or_create(
                asset_category_name=asset_category_name
            )
            asset_lot, _ = AssetLot.objects.get_or_create(lot_number=asset_lot_number)

            asset_list.append(
                Asset(
                    asset_name=asset_name,
                    asset_description=asset_description,
                    asset_tracking_id=asset_tracking_id,
                    asset_purchase_date=asset_purchase_date,
                    asset_purchase_cost=asset_purchase_cost,
                    asset_status=asset_status,
                    asset_category_id=asset_category,
                    asset_lot_number_id=asset_lot,
                )
            )

    # Bulk create assets from CSV
    Asset.objects.bulk_create(asset_list)

    # Delete the temporary file
    if os.path.exists(tmp_file):
        os.remove(tmp_file)


def spreadsheetml_asset_import(dataframe):
    for index, row in dataframe.iterrows():
        asset_name = convert_nan(row["Asset name"])
        asset_description = convert_nan(row["Description"])
        asset_tracking_id = convert_nan(row["Tracking id"])
        purchase_date = convert_nan(row["Purchase date"])
        purchase_cost = convert_nan(row["Purchase cost"])
        category_name = convert_nan(row["Category"])
        lot_number = convert_nan(row["Batch number"])
        status = convert_nan(row["Status"])

        asset_category, create = AssetCategory.objects.get_or_create(
            asset_category_name=category_name
        )
        asset_lot_number, create = AssetLot.objects.get_or_create(lot_number=lot_number)
        Asset.objects.create(
            asset_name=asset_name,
            asset_description=asset_description,
            asset_tracking_id=asset_tracking_id,
            asset_purchase_date=purchase_date,
            asset_purchase_cost=purchase_cost,
            asset_category_id=asset_category,
            asset_status=status,
            asset_lot_number_id=asset_lot_number,
        )


@login_required
@permission_required(perm="asset.add_asset")
def asset_import(request):
    """
    Handle the import of asset data from an uploaded Excel file.

    This view processes a POST request containing an Excel file, reads the data,
    creates Asset objects from the data, and saves them to the database. If the
    import is successful, a success message is displayed. Otherwise, appropriate
    error messages are shown.

    Args:
        request (HttpRequest): The HTTP request object containing metadata about the request.

    Returns:
        HttpResponseRedirect: A redirect to the asset category view after processing the import.
    """
    if request.META.get("HTTP_HX_REQUEST"):
        return render(request, "asset/asset_import.html")
    try:
        if request.method == "POST":
            file = request.FILES.get("asset_import")
            if file is not None and file.content_type == "text/csv":
                try:
                    before = Asset.objects.count()
                    csv_asset_import(file)
                    _asset_audit(
                        request,
                        "Assets imported",
                        changes={
                            "File": file.name,
                            "Format": "CSV",
                            "Records": {"from": before, "to": Asset.objects.count()},
                        },
                    )
                    messages.success(request, _("Successfully imported Assets"))
                except Exception as exception:
                    messages.error(request, f"{exception}")
            elif (
                file is not None
                and file.content_type
                == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ):
                try:
                    before = Asset.objects.count()
                    dataframe = pd.read_excel(file)
                    spreadsheetml_asset_import(dataframe)
                    _asset_audit(
                        request,
                        "Assets imported",
                        changes={
                            "File": file.name,
                            "Format": "Excel",
                            "Records": {"from": before, "to": Asset.objects.count()},
                        },
                    )
                    messages.success(request, _("Successfully imported Assets"))
                except KeyError as exception:
                    messages.error(request, f"{exception}")
            else:
                messages.error(request, _("File Error"))
            return redirect(asset_category_view)
    except Exception as exception:
        messages.error(request, f"{exception}")
    return redirect(asset_category_view)


@login_required
def asset_excel(_request):
    """asset excel download view"""

    try:
        columns = [
            "Asset name",
            "Description",
            "Tracking id",
            "Purchase date",
            "Purchase cost",
            "Category",
            "Status",
            "Batch number",
        ]
        # Create a pandas DataFrame with columns but no data
        dataframe = pd.DataFrame(columns=columns)
        # Write the DataFrame to an Excel file
        response = HttpResponse(content_type="application/ms-excel")
        response["Content-Disposition"] = 'attachment; filename="my_excel_file.xlsx"'
        dataframe.to_excel(response, index=False)
        return response
    except Exception as exception:
        return HttpResponse(exception)


@login_required
@permission_required("asset.view_assetcategory")
def asset_export_excel(request):
    """asset export view"""
    asset_export_filter = AssetExportFilter(request.GET, queryset=Asset.objects.all())
    if request.method == "POST":
        queryset_all = Asset.objects.all()
        if not queryset_all:
            messages.warning(request, _("There are no assets to export."))
            return redirect("asset-category-view")  # or some other URL

        queryset = AssetExportFilter(request.POST, queryset=queryset_all).qs

        # Convert the queryset to a Pandas DataFrame
        data = {
            "asset_name": [],
            "asset_description": [],
            "asset_tracking_id": [],
            "asset_purchase_date": [],
            "asset_purchase_cost": [],
            "asset_category_id": [],
            "asset_status": [],
            "asset_lot_number_id": [],
        }

        fields_to_check = [
            "asset_name",
            "asset_description",
            "asset_tracking_id",
            "asset_purchase_date",
            "asset_purchase_cost",
            "asset_category_id",
            "asset_status",
            "asset_lot_number_id",
        ]

        for asset in queryset:
            for field in fields_to_check:
                # Get the value of the field for the current asset
                value = getattr(asset, field)

                if isinstance(value, date):
                    user = request.user
                    emp = user.employee_get

                    # Taking the company_name of the user
                    info = EmployeeWorkInformation.objects.filter(employee_id=emp)
                    if info.exists():
                        for i in info:
                            employee_company = i.company_id
                        company_name = Company.objects.filter(company=employee_company)
                        emp_company = company_name.first()

                        # Access the date_format attribute directly
                        date_format = (
                            emp_company.date_format if emp_company else "MMM. D, YYYY"
                        )
                    else:
                        date_format = "MMM. D, YYYY"

                    # Convert the string to a datetime.date object
                    start_date = datetime.strptime(str(value), "%Y-%m-%d").date()

                    # The formatted date for each format
                    for format_name, format_string in HORILLA_DATE_FORMATS.items():
                        if format_name == date_format:
                            value = start_date.strftime(format_string)

                # Append the value if it exists, or append None if it's None
                data[field].append(value if value is not None else None)

        # Fill any missing values with None
        for key in data:
            data[key] = data[key] + [None] * (len(queryset) - len(data[key]))

        # Convert the data dictionary to a Pandas DataFrame
        dataframe = pd.DataFrame(data)

        # Convert any date fields to the desired format
        # Rename the columns as needed
        dataframe = dataframe.rename(
            columns={
                "asset_name": "Asset name",
                "asset_description": "Description",
                "asset_tracking_id": "Tracking id",
                "asset_purchase_date": "Purchase date",
                "asset_purchase_cost": "Purchase cost",
                "asset_category_id": "Category",
                "asset_status": "Status",
                "asset_lot_number_id": "Batch number",
            }
        )

        # Write the DataFrame to an Excel file
        response = HttpResponse(content_type="application/vnd.ms-excel")
        response["Content-Disposition"] = 'attachment; filename="assets.xlsx"'
        dataframe.to_excel(response, index=False)
        _asset_audit(
            request,
            "Assets exported",
            changes={"Format": "Excel", "Records": len(queryset)},
        )
        return response
    context = {"asset_export_filter": asset_export_filter}
    return render(request, "category/asset_filter_export.html", context)


@login_required
@hx_request_required
@permission_required(perm="asset.add_assetlot")
def asset_batch_number_creation(request):
    """asset batch number creation view"""
    hx_vals = (
        request.GET.get("data") if request.GET.get("data") else request.GET.urlencode()
    )
    asset_batch_form = AssetBatchForm()
    context = {
        "asset_batch_form": asset_batch_form,
        "hx_vals": hx_vals,
        "hx_get": None,
        "hx_target": None,
    }
    if request.method == "POST":
        asset_batch_form = AssetBatchForm(request.POST)
        if asset_batch_form.is_valid():
            asset_batch_form.save()
            asset_batch_form = AssetBatchForm()
            messages.success(request, _("Batch number created successfully."))
            if AssetLot.objects.filter().count() == 1 and not hx_vals:
                return HttpResponse(status=204, headers={"HX-Refresh": "true"})
            if hx_vals:
                category_id = request.GET.get("asset_category_id")
                url = reverse("asset-creation", args=[category_id])
                instance = AssetLot.objects.all().order_by("-id").first()
                mutable_get = request.GET.copy()
                mutable_get["asset_lot_number_id"] = str(instance.id)
                context["hx_get"] = f"{url}?{mutable_get.urlencode()}"
                context["hx_target"] = "#objectCreateModalTarget"
        context["asset_batch_form"] = asset_batch_form
    return render(request, "batch/asset_batch_number_creation.html", context)


@login_required
@permission_required(perm="asset.view_assetlot")
def asset_batch_view(request):
    """
    View function to display details of all batch numbers.

    Returns:
    -  all asset batch numbers based on page
    """

    asset_batches = AssetLot.objects.all()
    previous_data = request.GET.urlencode()
    asset_batch_numbers_search_paginator = Paginator(asset_batches, get_pagination())
    page_number = request.GET.get("page")
    asset_batch_numbers = asset_batch_numbers_search_paginator.get_page(page_number)
    asset_batch_form = AssetBatchForm()
    if asset_batches.exists():
        template = "batch/asset_batch_number_view.html"
    else:
        template = "batch/asset_batch_empty.html"
    context = {
        "batch_numbers": asset_batch_numbers,
        "asset_batch_form": asset_batch_form,
        "pg": previous_data,
    }
    return render(request, template, context)


@login_required
@permission_required(perm="asset.change_assetlot")
def asset_batch_update(request, batch_id):
    """
    View function to return asset.
    Args:
    - batch_id: integer value representing the ID of the asset
    Returns:
    - message of the return
    """
    asset_batch_number = AssetLot.objects.get(id=batch_id)
    asset_batch = AssetLot.objects.get(id=batch_id)
    asset_batch_form = AssetBatchForm(instance=asset_batch)
    context = {
        "asset_batch_update_form": asset_batch_form,
    }
    assigned_batch_number = Asset.objects.filter(asset_lot_number_id=asset_batch_number)
    if assigned_batch_number:
        asset_batch_form = AssetBatchForm(instance=asset_batch)
        asset_batch_form["lot_number"].field.widget.attrs.update(
            {"readonly": "readonly"}
        )
        context["asset_batch_update_form"] = asset_batch_form
        context["in_use_message"] = (
            _("This batch number is already in-use")
            if request.method == "GET"
            else None
        )
    if request.method == "POST":
        asset_batch_form = AssetBatchForm(request.POST, instance=asset_batch_number)
        if asset_batch_form.is_valid():
            asset_batch_form.save()
            messages.success(request, _("Batch updated successfully."))
        context["asset_batch_update_form"] = asset_batch_form
    return render(request, "batch/asset_batch_number_update.html", context)


@login_required
@hx_request_required
@permission_required(perm="asset.delete_assetlot")
def asset_batch_number_delete(request, batch_id):
    """
    View function to return asset.
    Args:
    - batch_id: integer value representing the ID of the asset
    Returns:
    - message of the return
    """
    previous_data = request.GET.urlencode()
    try:
        asset_batch_number = AssetLot.objects.get(id=batch_id)
        assigned_batch_number = Asset.objects.filter(
            asset_lot_number_id=asset_batch_number
        )
        if assigned_batch_number:
            messages.error(request, _("Batch number in-use"))
            return redirect(f"/asset/asset-batch-number-search?{previous_data}")
        asset_batch_number.delete()
        messages.success(request, _("Batch number deleted"))
    except AssetLot.DoesNotExist:
        messages.error(request, _("Batch number not found"))
    except ProtectedError:
        messages.error(request, _("You cannot delete this Batch number."))
    if not AssetLot.objects.filter():
        return HttpResponse(status=204, headers={"HX-Refresh": "true"})
    return redirect(f"/asset/asset-batch-number-search?{previous_data}")


@login_required
@hx_request_required
def asset_batch_number_search(request):
    """
    View function to return search  data of asset batch number.

    Args:
    - id: integer value representing the ID of the asset

    Returns:
    - message of the return
    """
    search_query = request.GET.get("search")
    if search_query is None:
        search_query = ""

    asset_batches = AssetLot.objects.all().filter(lot_number__icontains=search_query)
    previous_data = request.GET.urlencode()
    asset_batch_numbers_search_paginator = Paginator(asset_batches, get_pagination())
    page_number = request.GET.get("page")
    asset_batch_numbers = asset_batch_numbers_search_paginator.get_page(page_number)

    context = {
        "batch_numbers": asset_batch_numbers,
        "pg": previous_data,
    }

    return render(request, "batch/asset_batch_number_list.html", context)


@login_required
def asset_count_update(request):
    """
    View function to return update asset count at asset category.
    Args:
    - id: integer value representing the ID of the asset category
    Returns:
    - count of asset inside the category
    """
    if request.method == "POST":
        category_id = request.POST.get("asset_category_id")
        if category_id is not None:
            category = AssetCategory.objects.get(id=category_id)
            asset_count = category.asset_set.count()
            return HttpResponse(asset_count)
    return HttpResponse("error")


@login_required
@permission_required(perm="asset.view_assetcategory")
def asset_dashboard(request):
    """
    This method is used to render the dashboard of the asset module.
    """
    assets = Asset.objects.all()
    asset_in_use = Asset.objects.filter(asset_status="In use")
    asset_requests = AssetRequest.objects.filter(
        asset_request_status="Requested", requested_employee_id__is_active=True
    )

    context = {
        "assets": assets,
        "asset_in_use": asset_in_use,
        "asset_requests": asset_requests,
    }
    return render(request, "asset/dashboard.html", context)


@login_required
@permission_required(perm="asset.view_assetrequest")
def asset_dashboard_requests(request):
    """
    Handles the asset request approval dashboard view.

    This view fetches and filters asset requests that are currently in the
    "Requested" status and belong to employees who are active.

    The filtered asset requests are then passed to the template for rendering,
    along with a JSON-encoded list of the request IDs.
    """
    asset_requests = AssetRequest.objects.filter(
        asset_request_status="Requested", requested_employee_id__is_active=True
    )
    requests_ids = json.dumps([instance.id for instance in asset_requests])
    context = {
        "asset_requests": asset_requests,
        "requests_ids": requests_ids,
    }
    return render(request, "asset/dashboard_asset_requests.html", context)


@login_required
@permission_required(perm="asset.view_assetassignment")
def asset_dashboard_allocates(request):
    asset_allocations = AssetAssignment.objects.filter(
        asset_id__asset_status="In use", assigned_to_employee_id__is_active=True
    )
    context = {
        "asset_allocations": asset_allocations,
    }
    return render(request, "asset/dashboard_allocated_assets.html", context)


@login_required
@permission_required(perm="asset.view_assetcategory")
def asset_available_chart(_request):
    """
    This function returns the response for the available asset chart in the asset dashboard.
    """
    asset_available = Asset.objects.filter(asset_status="Available")
    asset_unavailable = Asset.objects.filter(asset_status="Not-Available")
    asset_in_use = Asset.objects.filter(asset_status="In use")

    labels = ["In use", "Available", "Not-Available"]
    dataset = [
        {
            "label": _("asset"),
            "data": [len(asset_in_use), len(asset_available), len(asset_unavailable)],
        },
    ]

    response = {
        "labels": labels,
        "dataset": dataset,
        "message": _("Oops!! No Asset found..."),
        "emptyImageSrc": f"/{settings.STATIC_URL}images/ui/asset.png",
    }
    return JsonResponse(response)


@login_required
@permission_required(perm="asset.view_assetcategory")
def asset_category_chart(_request):
    """
    This function returns the response for the asset category chart in the asset dashboard.
    """
    asset_categories = AssetCategory.objects.all()
    data = []
    for asset_category in asset_categories:
        category_count = 0
        category_count = len(asset_category.asset_set.filter(asset_status="In use"))
        data.append(category_count)

    labels = [category.asset_category_name for category in asset_categories]
    dataset = [
        {
            "label": _("assets in use"),
            "data": data,
        },
    ]

    response = {
        "labels": labels,
        "dataset": dataset,
        "message": _("Oops!! No Asset found..."),
        "emptyImageSrc": f"/{settings.STATIC_URL}images/ui/asset.png",
    }
    return JsonResponse(response)


@login_required
@permission_required(perm="asset.view_assetassignment")
def asset_history(request):
    """
    This function is responsible for loading the asset history view

    Args:


    Returns:
        returns asset history view template
    """
    previous_data = request.GET.urlencode() + "&returned_assets=True"
    asset_assignments = AssetHistoryFilter({"returned_assets": "True"}).qs.order_by(
        "-id"
    )
    data_dict = parse_qs(previous_data)
    get_key_instances(AssetAssignment, data_dict)
    asset_assignments = paginator_qry(asset_assignments, request.GET.get("page"))
    requests_ids = json.dumps(
        [instance.id for instance in asset_assignments.object_list]
    )
    context = {
        "asset_assignments": asset_assignments,
        "f": AssetHistoryFilter(),
        "filter_dict": data_dict,
        "gp_fields": AssetHistoryReGroup().fields,
        "pd": previous_data,
        "requests_ids": requests_ids,
    }
    return render(request, "asset_history/asset_history_view.html", context)


@login_required
@permission_required(perm="asset.view_assetassignment")
def asset_history_single_view(request, asset_id):
    """
    this method is used to view details of individual asset assignments

    Args:
        request (HTTPrequest): http request
        asset_id (int): ID of the asset assignment

    Returns:
        html: Returns asset history single view template
    """
    asset_assignment = get_object_or_404(AssetAssignment, id=asset_id)
    context = {"asset_assignment": asset_assignment}
    requests_ids_json = request.GET.get("requests_ids")
    if requests_ids_json:
        requests_ids = json.loads(requests_ids_json)
        previous_id, next_id = closest_numbers(requests_ids, asset_id)
        context["requests_ids"] = requests_ids_json
        context["previous"] = previous_id
        context["next"] = next_id
    return render(
        request,
        "asset_history/asset_history_single_view.html",
        context,
    )


@login_required
@permission_required(perm="asset.view_assetassignment")
def asset_history_search(request):
    """
    This method is used to filter the asset history view or to group by the datas.

    Args:
        request (HTTPrequest):http request

    Returns:
        returns asset history list or group by
    """
    previous_data = request.GET.urlencode()
    asset_assignments = AssetHistoryFilter(request.GET).qs.order_by("-id")
    asset_assignments = sortby(request, asset_assignments, "sortby")
    template = "asset_history/asset_history_list.html"
    field = request.GET.get("field")
    if field != "" and field is not None:
        asset_assignments = group_by_queryset(
            asset_assignments, field, request.GET.get("page"), "page"
        )
        template = "asset_history/group_by.html"
        list_values = [entry["list"] for entry in asset_assignments]
        id_list = []
        for value in list_values:
            for instance in value.object_list:
                id_list.append(instance.id)

        requests_ids = json.dumps(list(id_list))
    else:
        asset_assignments = paginator_qry(asset_assignments, request.GET.get("page"))

        requests_ids = json.dumps(
            [instance.id for instance in asset_assignments.object_list]
        )
    data_dict = parse_qs(previous_data)
    get_key_instances(AssetAssignment, data_dict)

    return render(
        request,
        template,
        {
            "asset_assignments": asset_assignments,
            "filter_dict": data_dict,
            "field": field,
            "pd": previous_data,
            "requests_ids": requests_ids,
        },
    )


@login_required
@owner_can_enter("asset.view_asset", Employee)
def asset_tab(request, emp_id):
    """
    This function is used to view asset tab of an employee in employee individual view.

    Parameters:
    request (HttpRequest): The HTTP request object.
    emp_id (int): The id of the employee.

    Returns: return asset-tab template

    """
    employee = Employee.objects.get(id=emp_id)
    assets_requests = employee.requested_employee.all()
    assets = employee.allocated_employee.all()
    assets_ids = (
        json.dumps([instance.id for instance in assets]) if assets else json.dumps([])
    )
    context = {
        "assets": assets,
        "requests": assets_requests,
        "assets_ids": assets_ids,
        "employee": emp_id,
    }
    return render(request, "tabs/asset-tab.html", context=context)


@login_required
@hx_request_required
def profile_asset_tab(request, emp_id):
    """
    This function is used to view asset tab of an employee in employee profile view.

    Parameters:
    request (HttpRequest): The HTTP request object.
    emp_id (int): The id of the employee.

    Returns: return profile-asset-tab template

    """
    employee = Employee.objects.get(id=emp_id)
    assets = employee.allocated_employee.all()
    assets_ids = json.dumps([instance.id for instance in assets])
    context = {
        "assets": assets,
        "assets_ids": assets_ids,
    }
    return render(request, "tabs/profile-asset-tab.html", context=context)


@login_required
@hx_request_required
def asset_request_tab(request, emp_id):
    """
    This function is used to view asset request tab of an employee in employee individual view.

    Parameters:
    request (HttpRequest): The HTTP request object.
    emp_id (int): The id of the employee.

    Returns: return asset-request-tab template

    """
    employee = Employee.objects.get(id=emp_id)
    assets_requests = employee.requested_employee.all()
    requests_ids = json.dumps([instance.id for instance in assets_requests])
    context = {
        "asset_requests": assets_requests,
        "emp_id": emp_id,
        "requests_ids": requests_ids,
    }
    return render(request, "tabs/asset_request_tab.html", context=context)


# TODO: Remove this view before production — temporary QA testing endpoint
@login_required
@permission_required(perm="asset.view_assetassignment")
def trigger_checkup_notifications(request):
    """
    Manually trigger checkup notification schedulers for QA testing.

    Query params:
        type=upcoming|overdue (default: upcoming)
        reset=1 — clears the matching idempotency flag on every active
                  assignment first, so the job will re-fire even if you
                  already tested today.
    """
    from datetime import date, timedelta

    from asset.models import AssetAssignment
    from asset.scheduler import notify_overdue_checkups, notify_upcoming_checkups

    notification_type = request.GET.get("type", "upcoming")
    reset = request.GET.get("reset") == "1"
    today = date.today()

    # Snapshot what the filter will see before we run the job, so the user
    # immediately knows whether anything *can* match.
    if notification_type == "overdue":
        flag_field = "last_overdue_notification_date"
        if reset:
            reset_count = AssetAssignment.objects.filter(
                yearly_checkup_date__lt=today,
                checkup_completed=False,
                return_date__isnull=True,
            ).update(last_overdue_notification_date=None)
        else:
            reset_count = 0
        candidates = AssetAssignment.objects.filter(
            yearly_checkup_date__lt=today,
            checkup_completed=False,
            return_date__isnull=True,
            last_overdue_notification_date__isnull=True,
        )
    else:
        flag_field = "last_upcoming_notification_date"
        if reset:
            reset_count = AssetAssignment.objects.filter(
                yearly_checkup_date__gt=today,
                yearly_checkup_date__lte=today + timedelta(days=30),
                checkup_completed=False,
                return_date__isnull=True,
            ).update(last_upcoming_notification_date=None)
        else:
            reset_count = 0
        candidates = AssetAssignment.objects.filter(
            yearly_checkup_date__gt=today,
            yearly_checkup_date__lte=today + timedelta(days=30),
            checkup_completed=False,
            return_date__isnull=True,
            last_upcoming_notification_date__isnull=True,
        )

    candidate_ids = list(candidates.values_list("pk", flat=True))
    match_count = len(candidate_ids)

    if notification_type == "overdue":
        notify_overdue_checkups()
    else:
        notify_upcoming_checkups()

    if match_count == 0:
        # Help the admin understand why nothing fired.
        total_assigned = AssetAssignment.objects.filter(
            checkup_completed=False, return_date__isnull=True
        ).count()
        already_flagged = AssetAssignment.objects.filter(
            checkup_completed=False,
            return_date__isnull=True,
            **{f"{flag_field}__isnull": False},
        ).count()
        messages.warning(
            request,
            _(
                "%(type)s trigger ran but matched 0 assignments. "
                "Active assignments: %(total)s. Already-notified (flag set): %(flagged)s. "
                "Add ?reset=1 to the URL to clear the flag and retest."
            ) % {
                "type": notification_type.capitalize(),
                "total": total_assigned,
                "flagged": already_flagged,
            },
        )
    else:
        messages.success(
            request,
            _(
                "%(type)s trigger ran. Matched %(count)s assignment(s) (ids: %(ids)s)%(reset)s. "
                "Check the server console for [CheckupMailThread] SENT/failed lines."
            ) % {
                "type": notification_type.capitalize(),
                "count": match_count,
                "ids": ", ".join(str(i) for i in candidate_ids) or "-",
                "reset": (
                    f" — reset %d flag(s)" % reset_count if reset else ""
                ),
            },
        )

    return redirect("asset-request-allocation-view")


@login_required
@hx_request_required
def asset_yearly_checkup_submit(request, asset_allocation_id):
    """
    Submit a yearly check-up for an asset allocation.

    All authenticated users can submit a check-up. On success the assignment is
    marked Complete and a notification is sent to Admin (superusers) and the
    ISO group immediately.
    """
    asset_allocation = get_object_or_404(AssetAssignment, id=asset_allocation_id)

    if not asset_allocation.is_checkup_button_enabled:
        messages.error(
            request,
            _("Yearly check-up can only be submitted within 30 days of the scheduled date."),
        )
        if request.META.get("HTTP_HX_REQUEST") == "true":
            return HttpResponse("<script>location.reload();</script>")
        return redirect("asset-request-allocation-view")

    initial = {"yearly_checkup_date": date.today()}
    form = YearlyCheckupForm(instance=asset_allocation, initial=initial)

    if request.method == "POST":
        form = YearlyCheckupForm(
            request.POST, request.FILES, instance=asset_allocation
        )
        if form.is_valid():
            assignment = form.save(commit=False)
            assignment.checkup_completed = True
            checkup_date = form.cleaned_data.get(
                "yearly_checkup_date"
            ) or date.today()
            # Advance the next scheduled check-up by one year so the eligibility
            # window re-opens for the next cycle.
            try:
                assignment.yearly_checkup_date = checkup_date.replace(
                    year=checkup_date.year + 1
                )
            except ValueError:
                assignment.yearly_checkup_date = checkup_date.replace(
                    year=checkup_date.year + 1, day=28
                )
            # Clear notification flags so next year's Upcoming/Overdue re-trigger.
            assignment.last_upcoming_notification_date = None
            assignment.last_overdue_notification_date = None
            assignment.save()

            files = request.FILES.getlist("checkup_images")
            attachments = []
            for file in files:
                attachment = ReturnImages()
                attachment.image = file
                attachment.save()
                attachments.append(attachment)

            log = YearlyCheckupLog.objects.create(
                asset_assignment=assignment,
                checkup_date=checkup_date,
                description=form.cleaned_data.get("checkup_description") or "",
                submitted_by=getattr(request.user, "employee_get", None),
            )
            if attachments:
                log.images.add(*attachments)

            _asset_audit(
                request,
                "Yearly check-up completed",
                target=assignment,
                changes={
                    "Asset": str(assignment.asset_id),
                    "Assigned to": str(assignment.assigned_to_employee_id),
                    "Check-up date": str(checkup_date),
                    "Description": form.cleaned_data.get("checkup_description") or "",
                },
            )

            send_checkup_completion_notification(request, assignment)
            messages.success(
                request, _("Yearly check-up submitted successfully.")
            )
            response = render(
                request,
                "request_allocation/yearly_checkup_form.html",
                {
                    "yearly_checkup_form": YearlyCheckupForm(initial=initial),
                    "asset_allocation": assignment,
                },
            )
            return HttpResponse(
                response.content.decode("utf-8")
                + "<script>location.reload();</script>"
            )

    context = {
        "yearly_checkup_form": form,
        "asset_allocation": asset_allocation,
    }
    return render(
        request, "request_allocation/yearly_checkup_form.html", context
    )


def send_checkup_completion_notification(request, assignment):
    """
    Notify Admin (superusers) and ISO group users that a yearly check-up has
    been marked complete for an asset assignment.
    """
    import logging

    from django.contrib.auth.models import Group, User

    logger = logging.getLogger(__name__)
    logger.info(
        "[send_checkup_completion_notification] START assignment=%s",
        assignment.pk,
    )

    asset = assignment.asset_id
    employee = assignment.assigned_to_employee_id
    checkup_date = (
        assignment.yearly_checkup_date.strftime("%Y-%m-%d")
        if assignment.yearly_checkup_date
        else "-"
    )

    submitted_by = (
        request.user.employee_get
        if hasattr(request.user, "employee_get")
        else request.user
    )

    message = (
        f"Yearly check-up for asset '{asset.asset_name}' "
        f"({asset.asset_tracking_id}) assigned to {employee.get_full_name()} "
        f"has been marked Complete on {checkup_date}."
    )
    message_ar = (
        f"تم وضع علامة على الفحص السنوي للأصل '{asset.asset_name}' "
        f"({asset.asset_tracking_id}) المخصص للموظف {employee.get_full_name()} "
        f"كمكتمل بتاريخ {checkup_date}."
    )
    message_de = (
        f"Die jährliche Überprüfung für Asset '{asset.asset_name}' "
        f"({asset.asset_tracking_id}), zugewiesen an {employee.get_full_name()}, "
        f"wurde am {checkup_date} als abgeschlossen markiert."
    )
    message_es = (
        f"La revisión anual del activo '{asset.asset_name}' "
        f"({asset.asset_tracking_id}) asignado a {employee.get_full_name()} "
        f"se marcó como completada el {checkup_date}."
    )
    message_fr = (
        f"Le contrôle annuel de l'actif '{asset.asset_name}' "
        f"({asset.asset_tracking_id}) attribué à {employee.get_full_name()} "
        f"a été marqué comme terminé le {checkup_date}."
    )

    iso_group_name = "ISO"
    recipient_user_qs = User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(groups__name=iso_group_name)
    ).distinct()
    logger.info(
        "[send_checkup_completion_notification] recipients (superusers + %s group) count=%s ids=%s",
        iso_group_name,
        recipient_user_qs.count(),
        list(recipient_user_qs.values_list("pk", flat=True)),
    )

    if not recipient_user_qs.exists():
        logger.warning(
            "[send_checkup_completion_notification] no superusers or %s group members; aborting",
            iso_group_name,
        )
        return

    notify.send(
        submitted_by,
        recipient=recipient_user_qs,
        verb=message,
        verb_ar=message_ar,
        verb_de=message_de,
        verb_es=message_es,
        verb_fr=message_fr,
        redirect=reverse("asset-request-allocation-view"),
        label="System",
        icon="checkmark-circle",
    )
    logger.info(
        "[send_checkup_completion_notification] in-app notify sent to %s recipient(s)",
        recipient_user_qs.count(),
    )

    from asset.threading import CheckupMailThread
    from employee.models import Employee

    email_context = {
        "asset_name": asset.asset_name,
        "tracking_id": asset.asset_tracking_id,
        "assigned_to": employee.get_full_name(),
        "checkup_date": checkup_date,
        "service_shop": assignment.service_shop_name or "N/A",
        "message": message,
    }
    email_recipients = list(
        Employee.objects.filter(employee_user_id__in=recipient_user_qs)
    )
    logger.info(
        "[send_checkup_completion_notification] email recipients employees=%s emails=%s",
        [str(e) for e in email_recipients],
        [e.get_mail() for e in email_recipients],
    )
    if email_recipients:
        CheckupMailThread(
            email_recipients, email_context, notification_type="completed"
        ).start()
        logger.info(
            "[send_checkup_completion_notification] CheckupMailThread started type=completed"
        )
    else:
        logger.warning(
            "[send_checkup_completion_notification] no Employee rows linked to recipient users; "
            "no email will be sent. Check that the superusers/ISO users have linked Employee records."
        )


def _is_asset_admin(user):
    return (
        user.is_superuser
        or user.groups.filter(name__in=("HR", "OPS", "ISO")).exists()
        or user.has_perm("asset.view_assetassignment")
    )


@login_required
def asset_yearly_checkup_list(request):
    """
    Standalone list of asset allocations for yearly check-up tracking.

    HR/OPS/ISO/superusers and users with `asset.view_assetassignment` see every
    allocation; everyone else sees only their own. Clicking a row navigates to
    the full-page detail view that shows the chat-style submission timeline.
    """
    qs = (
        AssetAssignment.objects.select_related(
            "asset_id",
            "asset_id__asset_category_id",
            "assigned_to_employee_id",
        )
        .prefetch_related("checkup_logs")
    )
    if not _is_asset_admin(request.user):
        qs = qs.filter(assigned_to_employee_id=request.user.employee_get)

    search = request.GET.get("search", "").strip()
    if search:
        qs = qs.filter(
            Q(asset_id__asset_name__icontains=search)
            | Q(assigned_to_employee_id__employee_first_name__icontains=search)
            | Q(assigned_to_employee_id__employee_last_name__icontains=search)
        )

    status_filter = request.GET.get("status", "").strip()
    if status_filter == "complete":
        qs = qs.filter(checkup_completed=True)
    elif status_filter == "overdue":
        qs = qs.filter(
            checkup_completed=False,
            yearly_checkup_date__lte=date.today(),
            return_date__isnull=True,
        )
    elif status_filter == "pending":
        qs = qs.exclude(checkup_completed=True).exclude(
            yearly_checkup_date__lte=date.today(),
            return_date__isnull=True,
        )

    qs = qs.order_by("yearly_checkup_date", "-id")
    page_obj = Paginator(qs, get_pagination()).get_page(request.GET.get("page"))
    context = {
        "allocations": page_obj,
        "search": search,
        "status_filter": status_filter,
    }
    if request.headers.get("HX-Request"):
        return render(
            request,
            "request_allocation/_yearly_checkup_rows.html",
            context,
        )
    return render(
        request,
        "request_allocation/yearly_checkup_list.html",
        context,
    )


@login_required
def asset_yearly_checkup_detail(request, allocation_id):
    """
    Full-page detail for an asset allocation, modeled after the helpdesk
    ticket-detail layout: a chat-style timeline of check-up submissions on the
    left and summary cards (asset, allocation, status, documents) on the right.
    """
    asset_allocation = get_object_or_404(AssetAssignment, id=allocation_id)
    if (
        not _is_asset_admin(request.user)
        and asset_allocation.assigned_to_employee_id != request.user.employee_get
    ):
        return HttpResponse(status=403)

    checkup_logs = asset_allocation.checkup_logs.all().order_by("created_at", "id")
    context = {
        "asset_allocation": asset_allocation,
        "checkup_logs": checkup_logs,
    }
    return render(
        request,
        "request_allocation/yearly_checkup_detail.html",
        context,
    )
