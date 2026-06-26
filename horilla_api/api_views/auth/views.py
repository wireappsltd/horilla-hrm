import logging

from django.contrib.auth import authenticate
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from base.views import (
    LOGIN_LOCKOUT_SECONDS,
    LOGIN_MAX_ATTEMPTS,
    get_login_lockout_remaining,
    increment_login_attempts,
    reset_login_attempts,
    set_login_lockout,
)

from ...api_serializers.auth.serializers import GetEmployeeSerializer

logger = logging.getLogger(__name__)


class LoginAPIView(APIView):
    def post(self, request):
        if "username" and "password" in request.data.keys():
            username = request.data.get("username")
            password = request.data.get("password")

            lockout_remaining = get_login_lockout_remaining(username)
            if lockout_remaining > 0:
                return Response(
                    {
                        "error": "Too many failed login attempts. Please try again later.",
                        "retry_after": lockout_remaining,
                    },
                    status=429,
                )

            user = authenticate(username=username, password=password)
            if user:
                employee = user.employee_get
                if not user.is_superuser:
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
                        return Response(
                            {"error": "Invalid credentials"}, status=401
                        )
                reset_login_attempts(username)
                refresh = RefreshToken.for_user(user)
                face_detection = False
                face_detection_image = None
                geo_fencing = False
                try:
                    face_detection = employee.get_company().face_detection.start
                except:
                    pass
                try:
                    geo_fencing = employee.get_company().geo_fencing.start
                except:
                    pass
                try:
                    face_detection_image = employee.face_detection.image.url
                except:
                    pass
                try:
                    company_id = employee.get_company().id
                except:
                    pass
                result = {
                    "employee": GetEmployeeSerializer(employee).data,
                    "access": str(refresh.access_token),
                    "face_detection": face_detection,
                    "face_detection_image": face_detection_image,
                    "geo_fencing": geo_fencing,
                    "company_id": company_id,
                }
                return Response(result, status=200)
            else:
                attempts = increment_login_attempts(username)
                if attempts >= LOGIN_MAX_ATTEMPTS:
                    set_login_lockout(username)
                    return Response(
                        {
                            "error": (
                                "Too many failed login attempts. This account "
                                "has been temporarily locked."
                            ),
                            "retry_after": LOGIN_LOCKOUT_SECONDS,
                        },
                        status=429,
                    )
                return Response({"error": "Invalid credentials"}, status=401)
        else:
            return Response({"error": "Please provide Username and Password"})