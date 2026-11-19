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
    # Holidays endpoint disabled for now (HRMOD-598) - not needed by Meridian yet.
    # path(
    #     "holidays/",
    #     leave_views.PMOHolidayListAPIView.as_view(),
    #     name="api-pmo-holiday-list",
    # ),
]
