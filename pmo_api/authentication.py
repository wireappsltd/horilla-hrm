"""
API key authentication for the Meridian PMO integration.

Clients send the key in the ``X-API-Key`` header (or ``Authorization: Api-Key <key>``).
Failures produce a JSON 401 (never a redirect to the HTML login page).
"""

import hmac

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission


class PMOApiUser:
    """Minimal non-database principal representing the Meridian service."""

    is_authenticated = True
    is_active = True
    is_anonymous = False
    username = "meridian-pmo"

    def __str__(self):
        return self.username


class APIKeyAuthentication(BaseAuthentication):
    keyword = "Api-Key"

    def authenticate(self, request):
        expected = getattr(settings, "PMO_API_KEY", "") or ""
        provided = request.headers.get("X-API-Key")
        if not provided:
            auth = request.headers.get("Authorization", "").split()
            if len(auth) == 2 and auth[0].lower() == self.keyword.lower():
                provided = auth[1]
        if not provided:
            return None
        if not expected or not hmac.compare_digest(
            provided.encode(), expected.encode()
        ):
            raise AuthenticationFailed("Invalid API key.")
        return (PMOApiUser(), None)

    def authenticate_header(self, request):
        # Presence of this header makes DRF return 401 (not 403).
        return self.keyword


class HasValidAPIKey(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.user, PMOApiUser)

