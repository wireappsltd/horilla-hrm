from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from ...api_views.auth.views import LoginAPIView

urlpatterns = [
    path("login/", LoginAPIView.as_view(), name="api-login"),
    path("token/refresh/", TokenRefreshView.as_view(), name="api-token-refresh"),
]
