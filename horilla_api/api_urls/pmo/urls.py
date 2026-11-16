from django.urls import path

from ...api_views.pmo import leave_views, views

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
    path(
        "leaves/",
        leave_views.PMOLeaveListAPIView.as_view(),
        name="api-pmo-leave-list",
    ),
    path(
        "leave-types/",
        leave_views.PMOLeaveTypeListAPIView.as_view(),
        name="api-pmo-leave-type-list",
    ),
    path(
        "holidays/",
        leave_views.PMOHolidayListAPIView.as_view(),
        name="api-pmo-holiday-list",
    ),
]
