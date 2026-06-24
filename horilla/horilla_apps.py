"""
horilla_apps

This module is used to register horilla addons
"""

from horilla import settings
from horilla.settings import INSTALLED_APPS

INSTALLED_APPS.append("accessibility")
INSTALLED_APPS.append("horilla_audit")
INSTALLED_APPS.append("horilla_widgets")
INSTALLED_APPS.append("horilla_crumbs")
INSTALLED_APPS.append("horilla_documents")
INSTALLED_APPS.append("horilla_views")
INSTALLED_APPS.append("horilla_automations")
INSTALLED_APPS.append("auditlog")
INSTALLED_APPS.append("biometric")
INSTALLED_APPS.append("helpdesk")
INSTALLED_APPS.append("offboarding")
INSTALLED_APPS.append("horilla_backup")
INSTALLED_APPS.append("project")
INSTALLED_APPS.append("horilla_retention")
# INSTALLED_APPS.append("turnstile")

# Cloudflare Turnstile keys for login challenge. Default to Cloudflare's
# published "always passes" test keys so local dev works without any setup;
# production deployments MUST set real keys via env (TURNSTILE_SITEKEY,
# TURNSTILE_SECRETKEY) in the .env file.
# setattr(
#     settings,
#     "TURNSTILE_SITEKEY",
#     settings.env("TURNSTILE_SITEKEY", default="1x00000000000000000000AA"),
# )
# setattr(
#     settings,
#     "TURNSTILE_SECRETKEY",
#     settings.env(
#         "TURNSTILE_SECRETKEY", default="1x0000000000000000000000000000000AA"
#     ),
# )

if settings.env("AWS_ACCESS_KEY_ID", default=None) and "storages" not in INSTALLED_APPS:
    INSTALLED_APPS.append("storages")


AUDITLOG_INCLUDE_ALL_MODELS = True

AUDITLOG_EXCLUDE_TRACKING_MODELS = (
    # "<app_name>",
    # "<app_name>.<model>"
)

setattr(settings, "AUDITLOG_INCLUDE_ALL_MODELS", AUDITLOG_INCLUDE_ALL_MODELS)
setattr(settings, "AUDITLOG_EXCLUDE_TRACKING_MODELS", AUDITLOG_EXCLUDE_TRACKING_MODELS)

settings.MIDDLEWARE.append(
    "auditlog.middleware.AuditlogMiddleware",
)

SETTINGS_EMAIL_BACKEND = getattr(settings, "EMAIL_BACKEND", False)
setattr(settings, "EMAIL_BACKEND", "base.backends.ConfiguredEmailBackend")
if SETTINGS_EMAIL_BACKEND:
    setattr(settings, "EMAIL_BACKEND", SETTINGS_EMAIL_BACKEND)


SIDEBARS = [
    "recruitment",
    "onboarding",
    "employee",
    "time_tracker",
    "attendance",
    "leave",
    "payroll",
    "pms",
    "offboarding",
    "asset",
    "helpdesk",
    "project",
    "horilla_retention",
    "horilla_audit",
]

WHITE_LABELLING = False
NESTED_SUBORDINATE_VISIBILITY = False
TWO_FACTORS_AUTHENTICATION = False
