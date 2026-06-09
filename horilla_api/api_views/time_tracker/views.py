"""
horilla_api/api_views/time_tracker/views.py

DRF API views for the Time Tracker app.
"""

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from horilla_api.api_serializers.time_tracker.serializers import (
    ActiveTimerSerializer,
    ClientSerializer,
    TagSerializer,
    TimeEntrySerializer,
)
from time_tracker.models import ActiveTimer, Client, Tag, TimeEntry


def _get_employee(request):
    """Safely retrieve the Employee linked to the current API user."""
    try:
        return request.user.employee_get
    except Exception:
        return None


class TimeEntryAPIView(APIView):
    """
    GET  /api/time-tracker/entries/         — list entries (filtered)
    POST /api/time-tracker/entries/         — create entry
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        employee = _get_employee(request)
        qs = TimeEntry.objects.all().select_related(
            "employee_id", "project_id", "task_id", "client_id"
        )
        # Non-superusers see only their own entries
        if employee and not request.user.is_superuser:
            qs = qs.filter(employee_id=employee)

        # Filter by date range
        date_from = request.GET.get("date_from")
        date_to = request.GET.get("date_to")
        project_id = request.GET.get("project_id")
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)
        if project_id:
            qs = qs.filter(project_id=project_id)

        serializer = TimeEntrySerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        employee = _get_employee(request)
        serializer = TimeEntrySerializer(data=request.data)
        if serializer.is_valid():
            instance = serializer.save()
            if employee and not instance.employee_id_id:
                instance.employee_id = employee
                instance.save(update_fields=["employee_id"])
            return Response(
                TimeEntrySerializer(instance).data,
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class TimeEntryDetailAPIView(APIView):
    """
    GET    /api/time-tracker/entries/<pk>/  — retrieve
    PUT    /api/time-tracker/entries/<pk>/  — update
    DELETE /api/time-tracker/entries/<pk>/  — delete
    """

    permission_classes = [IsAuthenticated]

    def _get_object(self, pk):
        try:
            return TimeEntry.objects.get(pk=pk)
        except TimeEntry.DoesNotExist:
            return None

    def get(self, request, pk):
        entry = self._get_object(pk)
        if entry is None:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(TimeEntrySerializer(entry).data)

    def put(self, request, pk):
        entry = self._get_object(pk)
        if entry is None:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        if entry.is_locked:
            return Response(
                {"error": "This entry is locked."}, status=status.HTTP_403_FORBIDDEN
            )
        serializer = TimeEntrySerializer(entry, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        entry = self._get_object(pk)
        if entry is None:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        if entry.is_locked:
            return Response(
                {"error": "This entry is locked."}, status=status.HTTP_403_FORBIDDEN
            )
        entry.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TimerStartAPIView(APIView):
    """POST /api/time-tracker/timer/start/ — start timer for current user."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        employee = _get_employee(request)
        if employee is None:
            return Response(
                {"error": "Employee profile not found."}, status=status.HTTP_400_BAD_REQUEST
            )
        if ActiveTimer.objects.filter(employee_id=employee).exists():
            return Response(
                {"error": "A timer is already running."}, status=status.HTTP_409_CONFLICT
            )

        project_id = request.data.get("project_id")
        task_id = request.data.get("task_id")
        description = request.data.get("description", "")
        is_billable = bool(request.data.get("is_billable", False))

        project, task = None, None
        if project_id:
            try:
                from project.models import Project
                project = Project.objects.get(pk=project_id)
            except Exception:
                pass
        if task_id:
            try:
                from project.models import Task
                task = Task.objects.get(pk=task_id)
            except Exception:
                pass

        timer = ActiveTimer.objects.create(
            employee_id=employee,
            project_id=project,
            task_id=task,
            description=description,
            is_billable=is_billable,
        )
        return Response(ActiveTimerSerializer(timer).data, status=status.HTTP_201_CREATED)


class TimerStopAPIView(APIView):
    """POST /api/time-tracker/timer/stop/ — stop running timer, create entry."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        employee = _get_employee(request)
        if employee is None:
            return Response(
                {"error": "Employee profile not found."}, status=status.HTTP_400_BAD_REQUEST
            )
        timer = ActiveTimer.objects.filter(employee_id=employee).first()
        if timer is None:
            return Response(
                {"error": "No running timer found."}, status=status.HTTP_404_NOT_FOUND
            )

        end_time = timezone.now()
        entry = TimeEntry(
            employee_id=employee,
            project_id=timer.project_id,
            task_id=timer.task_id,
            client_id=timer.client_id,
            description=timer.description,
            is_billable=timer.is_billable,
            date=timer.started_at.date(),
            start_time=timer.started_at,
            end_time=end_time,
            status="draft",
        )
        entry.save()
        if timer.tag_ids.exists():
            entry.tag_ids.set(timer.tag_ids.all())
        timer.delete()

        return Response(TimeEntrySerializer(entry).data, status=status.HTTP_201_CREATED)


class TimerStateAPIView(APIView):
    """GET /api/time-tracker/timer/state/ — current timer state."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        employee = _get_employee(request)
        if employee is None:
            return Response({"running": False, "started_at": None, "elapsed_seconds": 0})

        timer = ActiveTimer.objects.filter(employee_id=employee).first()
        if timer:
            return Response(ActiveTimerSerializer(timer).data)
        return Response({"running": False, "started_at": None, "elapsed_seconds": 0})
