import logging

from django.contrib.auth import authenticate
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from employee.models import Employee

from ...api_serializers.auth.serializers import GetEmployeeSerializer

logger = logging.getLogger(__name__)


class LoginAPIView(APIView):
    """
    Authenticate a Horilla user and issue a JWT access/refresh token pair.

    Behaviour:
        * Non-superusers must have an active, non-archived Contract AND
          a linked Employee record.
        * Superusers may log in even if they aren't linked to an Employee
          record (supports service/admin integrations such as PMO).
        * Throttled via the ``login`` scope (see REST_FRAMEWORK settings)
          to mitigate brute-force attempts.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        username = request.data.get("username")
        password = request.data.get("password")
        if not username or not password:
            return Response(
                {"error": "Please provide Username and Password"}, status=400
            )

        user = authenticate(username=username, password=password)
        if not user:
            return Response({"error": "Invalid credentials"}, status=401)

        # Safely resolve the linked Employee. An admin/service superuser may
        # not have one, so we must not 500 in that case.
        try:
            employee = user.employee_get
        except Employee.DoesNotExist:
            employee = None
        except Exception:  # pragma: no cover - defensive
            employee = None

        if not user.is_superuser:
            if employee is None:
                logger.warning(
                    "Login denied for user '%s' (id=%s): no linked employee.",
                    username,
                    user.pk,
                )
                return Response({"error": "Invalid credentials"}, status=401)

            from payroll.models.models import Contract

            has_active_contract = Contract.objects.filter(
                employee_id=employee,
                contract_status="active",
                is_active=True,
            ).exists()
            if not has_active_contract:
                logger.warning(
                    "Login denied for user '%s' (id=%s): no active contract.",
                    username,
                    user.pk,
                )
                return Response({"error": "Invalid credentials"}, status=401)

        refresh = RefreshToken.for_user(user)

        # Initialise all employee-derived fields so we never reference them
        # before assignment when the user has no linked Employee.
        face_detection = False
        face_detection_image = None
        geo_fencing = False
        company_id = None
        employee_payload = None

        if employee is not None:
            try:
                employee_payload = GetEmployeeSerializer(employee).data
            except Exception:
                employee_payload = None
            try:
                face_detection = employee.get_company().face_detection.start
            except Exception:
                pass
            try:
                geo_fencing = employee.get_company().geo_fencing.start
            except Exception:
                pass
            try:
                face_detection_image = employee.face_detection.image.url
            except Exception:
                pass
            try:
                company_id = employee.get_company().id
            except Exception:
                pass

        result = {
            "employee": employee_payload,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "face_detection": face_detection,
            "face_detection_image": face_detection_image,
            "geo_fencing": geo_fencing,
            "company_id": company_id,
        }
        return Response(result, status=200)
