from django.urls import path

from . import views

app_name = "pmo_api"

urlpatterns = [
    path("employees/", views.PMOEmployeeListView.as_view(), name="employee-list"),
    path(
        "employees/<int:pk>/",
        views.PMOEmployeeDetailView.as_view(),
        name="employee-detail",
    ),
    path("leaves/", views.PMOLeaveListView.as_view(), name="leave-list"),
    path(
        "leave-types/", views.PMOLeaveTypeListView.as_view(), name="leave-type-list"
    ),
    path("holidays/", views.PMOHolidayListView.as_view(), name="holiday-list"),
]

