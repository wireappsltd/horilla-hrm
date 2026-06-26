import contextlib
import importlib

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse

from horilla.horilla_settings import APP_URLS, DYNAMIC_URL_PATTERNS


def is_full_page_navigation(request):
    """
    Return ``True`` only for top-level document navigations (typing a URL,
    clicking a normal link, submitting a non-AJAX form).

    Every partial request issued by HTMX, jQuery AJAX, ``fetch`` or
    ``XMLHttpRequest`` returns ``False`` so a login page / redirect is never
    swapped into the currently open module. Those callers must instead be told
    to perform a full-page redirect (see :func:`session_expired_response`).
    """
    headers = request.headers

    # HTMX requests.
    if headers.get("HX-Request"):
        return False

    # jQuery / classic XMLHttpRequest.
    if headers.get("x-requested-with") == "XMLHttpRequest":
        return False

    # Modern browsers advertise the request context via Fetch Metadata.
    # ``navigate`` is sent for real page navigations; fetch()/XHR send
    # ``cors``/``same-origin``/``no-cors`` instead.
    sec_fetch_mode = headers.get("Sec-Fetch-Mode")
    if sec_fetch_mode:
        return sec_fetch_mode == "navigate"

    # ``Sec-Fetch-Dest`` is ``document`` for full-page loads and ``empty``
    # for programmatic fetch/XHR requests.
    sec_fetch_dest = headers.get("Sec-Fetch-Dest")
    if sec_fetch_dest:
        return sec_fetch_dest == "document"

    # Fallback for older clients without Fetch Metadata: treat it as a real
    # navigation only when the client explicitly asks for an HTML document.
    accept = headers.get("Accept", "")
    return "text/html" in accept


def session_expired_response(location):
    """
    Build a response that forces the browser to perform a *full-page* redirect
    to the standalone login screen.

    HTMX honours ``HX-Redirect`` natively, while the ``X-Session-Expired`` /
    ``X-Login-Redirect`` headers are picked up by ``static/index/sessionExpiry.js``
    for jQuery AJAX, ``fetch`` and raw ``XMLHttpRequest`` calls. Returning a 401
    (instead of a 302) prevents the login page HTML from being transparently
    fetched and swapped into the current module.
    """
    response = HttpResponse(status=401)
    response["HX-Redirect"] = location
    response["X-Session-Expired"] = "1"
    response["X-Login-Redirect"] = location
    response["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0, private"
    )
    return response


def get_horilla_model_class(app_label, model):
    """
    Retrieves the model class for the given app label and model name using Django's ContentType framework.

    Args:
        app_label (str): The label of the application where the model is defined.
        model (str): The name of the model to retrieve.

    Returns:
        Model: The Django model class corresponding to the specified app label and model name.

    """
    content_type = ContentType.objects.get(app_label=app_label, model=model)
    model_class = content_type.model_class()
    return model_class


def dynamic_attr(obj, attribute_path):
    """
    Retrieves the value of a nested attribute from a related object dynamically.

    Args:
        obj: The base object from which to start accessing attributes.
        attribute_path (str): The path of the nested attribute to retrieve, using
        double underscores ('__') to indicate relationship traversal.

    Returns:
        The value of the nested attribute if it exists, or None if it doesn't exist.
    """
    attributes = attribute_path.split("__")

    for attr in attributes:
        with contextlib.suppress(Exception):
            Contract = get_horilla_model_class(app_label="payroll", model="contract")
            if isinstance(obj.first(), Contract):
                obj = obj.filter(is_active=True).first()

        obj = getattr(obj, attr, None)
        if obj is None:
            break
    return obj


def horilla_users_with_perms(permissions):
    """
    Filters users who have any of the specified permissions or are superusers.

    :param permissions: A list of permission strings in the format 'app_label.codename'
                        or a single permission string.
    :return: A queryset of users who have any of the specified permissions or are superusers.
    """
    # Ensure permissions is a list even if a single permission string is provided
    if isinstance(permissions, str):
        permissions = [permissions]

    # Start with a queryset that includes all superusers
    users_with_permissions = User.objects.filter(is_superuser=True)

    # Filter users based on the permissions list
    for perm in permissions:
        app_label, codename = perm.split(".")
        users_with_permissions |= User.objects.filter(
            user_permissions__codename=codename,
            user_permissions__content_type__app_label=app_label,
        )

    return users_with_permissions.distinct()


def get_urlencode(request):
    get_data = request.GET.copy()
    get_data.pop("instances_ids", None)
    previous_data = get_data.urlencode()
    return previous_data


def remove_dynamic_url(path_info):
    """Function to remove a dynamically added URL from any app's urlpatterns."""

    # Iterate over all app URL patterns
    for app_urls in APP_URLS:
        try:
            # Dynamically import the app's urls.py module
            urls_module = importlib.import_module(app_urls)
            # Access the urlpatterns in the module
            urlpatterns = getattr(urls_module, "urlpatterns", None)

            if urlpatterns:
                # Check if the pattern exists in this app's urlpatterns
                for path in urlpatterns:
                    if path.name == path_info:
                        urlpatterns.remove(path)
                        break

        except ModuleNotFoundError:
            print(f"Module {app_urls} not found. Skipping...")

    # Also remove it from the tracked dynamic paths
    if path_info in DYNAMIC_URL_PATTERNS:
        DYNAMIC_URL_PATTERNS.remove(path_info)
