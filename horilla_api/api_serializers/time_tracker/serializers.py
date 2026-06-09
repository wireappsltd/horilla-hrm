"""
horilla_api/api_serializers/time_tracker/serializers.py

DRF serializers for the Time Tracker app.
"""

from rest_framework import serializers

from time_tracker.models import ActiveTimer, Client, Tag, TimeEntry


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name", "color"]


class ClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = ["id", "name", "email", "currency", "default_rate", "color"]


class TimeEntrySerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee_id.__str__", read_only=True
    )
    project_name = serializers.CharField(
        source="project_id.title", read_only=True, default=""
    )
    task_name = serializers.CharField(
        source="task_id.title", read_only=True, default=""
    )
    client_name = serializers.CharField(
        source="client_id.name", read_only=True, default=""
    )
    duration_display = serializers.CharField(read_only=True)
    tags = TagSerializer(source="tag_ids", many=True, read_only=True)

    class Meta:
        model = TimeEntry
        fields = [
            "id",
            "employee_id",
            "employee_name",
            "project_id",
            "project_name",
            "task_id",
            "task_name",
            "client_id",
            "client_name",
            "tags",
            "tag_ids",
            "description",
            "date",
            "start_time",
            "end_time",
            "duration_seconds",
            "duration_display",
            "is_billable",
            "billable_rate",
            "currency",
            "status",
            "is_locked",
        ]
        read_only_fields = [
            "duration_seconds",
            "duration_display",
            "is_locked",
            "employee_name",
            "project_name",
            "task_name",
            "client_name",
            "tags",
        ]


class ActiveTimerSerializer(serializers.ModelSerializer):
    elapsed_seconds = serializers.SerializerMethodField()
    project_name = serializers.CharField(
        source="project_id.title", read_only=True, default=""
    )

    class Meta:
        model = ActiveTimer
        fields = [
            "id",
            "employee_id",
            "project_id",
            "project_name",
            "task_id",
            "client_id",
            "description",
            "is_billable",
            "started_at",
            "last_heartbeat",
            "elapsed_seconds",
        ]

    def get_elapsed_seconds(self, obj):
        from django.utils import timezone

        return int((timezone.now() - obj.started_at).total_seconds())
