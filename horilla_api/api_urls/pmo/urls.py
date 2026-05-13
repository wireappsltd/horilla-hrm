from django.urls import path

from ...api_views.pmo import views

urlpatterns = [
    path(
        "employees/",
        views.PMOEmployeeListAPIView.as_view(),
        name="api-pmo-employee-list",
    ),
    path(
        "employees/<int:pk>/",
        views.PMOEmployeeDetailAPIView.as_view(),
        name="api-pmo-employee-detail",
    ),
]

