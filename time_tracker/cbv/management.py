"""
time_tracker/cbv/management.py

Management views: RequiredFieldConfig settings, Client CRUD, Tag CRUD.
"""

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from horilla.decorators import login_required, permission_required

from time_tracker.forms import ClientForm, RequiredFieldConfigForm, TagForm
from time_tracker.models import Client, RequiredFieldConfig, Tag

# Lock management is handled in approvals.py — imported here for URL routing only.


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@login_required
def tracker_settings(request):
    """
    GET/POST — View and update per-company RequiredFieldConfig.
    """
    # Get or create the config for the current company
    config = RequiredFieldConfig.objects.first()

    if request.method == "POST":
        form = RequiredFieldConfigForm(request.POST, instance=config)
        if form.is_valid():
            instance = form.save(commit=False)
            instance.save()
            messages.success(request, _("Settings saved successfully."))
            response = HttpResponse(status=204)
            response["HX-Refresh"] = "true"
            return response
    else:
        form = RequiredFieldConfigForm(instance=config)

    return render(
        request,
        "time_tracker/management/settings.html",
        {"form": form, "config": config},
    )


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


class ClientListView(TemplateView):
    """
    List all clients.
    """

    template_name = "time_tracker/management/client_list.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.shortcuts import redirect

            return redirect("login")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["clients"] = Client.objects.all()
        context["form"] = ClientForm()
        return context


@login_required
def client_create(request):
    """
    GET — Return client form partial.
    POST — Create a new Client.
    """
    if request.method == "POST":
        form = ClientForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _("Client created successfully."))
            response = HttpResponse(status=204)
            response["HX-Refresh"] = "true"
            return response
        if request.META.get("HTTP_HX_REQUEST") == "true":
            return render(
                request,
                "time_tracker/management/client_form.html",
                {"form": form},
            )
    else:
        form = ClientForm()

    return render(
        request,
        "time_tracker/management/client_form.html",
        {"form": form},
    )


@login_required
def client_update(request, pk):
    """
    GET/POST — Update a Client.
    """
    client = get_object_or_404(Client, pk=pk)

    if request.method == "POST":
        form = ClientForm(request.POST, instance=client)
        if form.is_valid():
            form.save()
            messages.success(request, _("Client updated successfully."))
            response = HttpResponse(status=204)
            response["HX-Refresh"] = "true"
            return response
        if request.META.get("HTTP_HX_REQUEST") == "true":
            return render(
                request,
                "time_tracker/management/client_form.html",
                {"form": form, "client": client},
            )
    else:
        form = ClientForm(instance=client)

    return render(
        request,
        "time_tracker/management/client_form.html",
        {"form": form, "client": client},
    )


@login_required
def client_delete(request, pk):
    """
    POST — Delete a Client.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    client = get_object_or_404(Client, pk=pk)
    client.delete()
    messages.success(request, _("Client deleted."))
    return JsonResponse({"success": True})


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


class TagListView(TemplateView):
    """
    List all tags.
    """

    template_name = "time_tracker/management/tag_list.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.shortcuts import redirect

            return redirect("login")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tags"] = Tag.objects.all()
        context["form"] = TagForm()
        return context


@login_required
def tag_create(request):
    """
    GET — Return tag form partial.
    POST — Create a new Tag.
    """
    if request.method == "POST":
        form = TagForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _("Tag created successfully."))
            response = HttpResponse(status=204)
            response["HX-Refresh"] = "true"
            return response
        if request.META.get("HTTP_HX_REQUEST") == "true":
            return render(
                request,
                "time_tracker/management/tag_form.html",
                {"form": form},
            )
    else:
        form = TagForm()

    return render(
        request,
        "time_tracker/management/tag_form.html",
        {"form": form},
    )


@login_required
def tag_update(request, pk):
    """
    GET/POST — Update a Tag.
    """
    tag = get_object_or_404(Tag, pk=pk)

    if request.method == "POST":
        form = TagForm(request.POST, instance=tag)
        if form.is_valid():
            form.save()
            messages.success(request, _("Tag updated."))
            response = HttpResponse(status=204)
            response["HX-Refresh"] = "true"
            return response
        if request.META.get("HTTP_HX_REQUEST") == "true":
            return render(
                request,
                "time_tracker/management/tag_form.html",
                {"form": form, "tag": tag},
            )
    else:
        form = TagForm(instance=tag)

    return render(
        request,
        "time_tracker/management/tag_form.html",
        {"form": form, "tag": tag},
    )


@login_required
def tag_delete(request, pk):
    """
    POST — Delete a Tag.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    tag = get_object_or_404(Tag, pk=pk)
    tag.delete()
    messages.success(request, _("Tag deleted."))
    return JsonResponse({"success": True})
