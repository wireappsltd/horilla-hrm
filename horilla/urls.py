"""horilla URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path, re_path

import notifications.urls

from . import settings


def health_check(request):
    return JsonResponse({"status": "ok"}, status=200)


# JSON errors under /api/pmo/ (other paths keep Django's default pages).
handler400 = "pmo_api.errors.json_bad_request"
handler403 = "pmo_api.errors.json_permission_denied"
handler404 = "pmo_api.errors.json_not_found"
handler500 = "pmo_api.errors.json_server_error"


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("api/pmo/v1/", include("pmo_api.urls")),
    path("", include("base.urls")),
    path("", include("horilla_automations.urls")),
    path("", include("horilla_views.urls")),
    path("", include("horilla_audit.urls")),
    path("employee/", include("employee.urls")),
    path("horilla-widget/", include("horilla_widgets.urls")),
    re_path(
        "^inbox/notifications/", include(notifications.urls, namespace="notifications")
    ),
    path("i18n/", include("django.conf.urls.i18n")),
    path("health/", health_check),
]

# if settings.DEBUG:
#     urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
