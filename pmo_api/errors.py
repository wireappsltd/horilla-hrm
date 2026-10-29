"""
JSON-only error handling for the Meridian API (/api/pmo/).

* ``pmo_exception_handler`` is used by ``PMOBaseAPIView``: DRF errors keep
  their status code (401/403/404/405/429 ...), and unexpected exceptions
  become a JSON 500 instead of Django's HTML page.
* ``json_*`` handlers are wired as ``handler400/403/404/500`` in the root
  URLconf. Paths under ``/api/pmo/`` get JSON; everything else keeps Django's
  default behaviour.
"""

import logging

from django.http import JsonResponse
from django.views import defaults
from rest_framework import status
from rest_framework.exceptions import APIException, Throttled
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)

API_PREFIX = "/api/pmo/"


def _body(detail, code, **extra):
    return {"detail": str(detail), "code": code, **extra}


def pmo_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled error in PMO API", exc_info=exc)
        return Response(
            _body("Internal server error.", "server_error"),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if isinstance(exc, Throttled):
        wait = int(exc.wait) + 1 if exc.wait is not None else 1
        response.data = _body(
            "Rate limit exceeded.", "throttled", retry_after=wait
        )
        response["Retry-After"] = str(wait)
    elif isinstance(exc, APIException) and isinstance(response.data, dict):
        # Normalise to {"detail", "code"}; keep field errors (400) as-is.
        if "detail" in response.data:
            response.data = _body(
                response.data["detail"], exc.get_codes() or "error"
            )
    return response


def _json_error(request, status_code, detail, code, fallback, exception=None):
    if request.path.startswith(API_PREFIX):
        return JsonResponse(_body(detail, code), status=status_code)
    if exception is not None:
        return fallback(request, exception)
    return fallback(request)


def json_bad_request(request, exception=None):
    return _json_error(
        request, 400, "Bad request.", "bad_request", defaults.bad_request, exception
    )


def json_permission_denied(request, exception=None):
    return _json_error(
        request, 403, "Forbidden.", "forbidden", defaults.permission_denied, exception
    )


def json_not_found(request, exception=None):
    return _json_error(
        request, 404, "Not found.", "not_found", defaults.page_not_found, exception
    )


def json_server_error(request):
    return _json_error(
        request,
        500,
        "Internal server error.",
        "server_error",
        defaults.server_error,
    )

