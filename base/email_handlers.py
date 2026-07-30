"""
email_handlers.py

Shared helpers to render AND send the single, standardized branded email
template (`base/mail_templates/horilla_mail_template.html`) used across every
Horilla feature that sends email to users.

Every feature keeps its own wording (title / content / call-to-action button)
but shares the exact same look & feel as the leave-request email.

Logo handling
-------------
The WireApps logo is embedded as an inline **CID attachment** (``cid:``) rather
than a hosted URL or a base64 data-URI. This is the only approach that renders
reliably in every context:

* It needs no request/host, so background-thread emails (leave, asset, payroll,
  helpdesk, etc.) show the logo too.
* It does not depend on ``collectstatic`` or a reachable public domain.
* Unlike base64 ``data:`` URIs, CID images are NOT blocked by Gmail or
  Outlook.com.

Call ``render_branded_email(...)`` to build the HTML, then attach the logo to
the outgoing message with ``attach_inline_logo(email)`` before ``email.send()``.
"""

import logging
import os
from email.mime.image import MIMEImage
from functools import lru_cache

from django.conf import settings
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

BRANDED_MAIL_TEMPLATE = "base/mail_templates/horilla_mail_template.html"

# Static path (relative to a static dir) and the Content-ID used to reference
# the inline logo from the template (``src="cid:wireapps_logo"``).
LOGO_STATIC_PATH = "images/ui/wireapps-logo.png"
LOGO_CID = "wireapps_logo"


@lru_cache(maxsize=1)
def _load_logo_bytes():
    """Locate and read the WireApps logo once, caching the bytes.

    Uses the staticfiles finders first (works in dev and prod regardless of
    ``collectstatic``), then falls back to ``STATIC_ROOT`` / ``STATICFILES_DIRS``.
    Returns ``None`` if the file cannot be found or read.
    """
    path = None
    try:
        from django.contrib.staticfiles import finders

        path = finders.find(LOGO_STATIC_PATH)
    except Exception:
        path = None

    if not path:
        candidate_bases = [getattr(settings, "STATIC_ROOT", None)]
        candidate_bases += list(getattr(settings, "STATICFILES_DIRS", []) or [])
        for base in candidate_bases:
            if not base:
                continue
            candidate = os.path.join(str(base), LOGO_STATIC_PATH)
            if os.path.exists(candidate):
                path = candidate
                break

    if not path:
        logger.warning(
            "Branded email logo not found (looked for %s). Emails will show "
            "alt text instead of the logo.",
            LOGO_STATIC_PATH,
        )
        return None

    try:
        with open(path, "rb") as handle:
            return handle.read()
    except Exception:
        logger.exception("Failed to read branded email logo at %s", path)
        return None


def attach_inline_logo(email):
    """Attach the WireApps logo to ``email`` as an inline CID image.

    Safe to call on any ``EmailMessage`` / ``EmailMultiAlternatives`` (including
    messages that already carry file attachments such as payslip PDFs). If the
    logo can't be loaded, the email is returned unchanged and the template's
    ``alt`` text is shown instead.

    Returns the same ``email`` object for convenience.
    """
    data = _load_logo_bytes()
    if not data or email is None:
        return email
    try:
        image = MIMEImage(data)
        image.add_header("Content-ID", f"<{LOGO_CID}>")
        image.add_header(
            "Content-Disposition", "inline", filename="wireapps-logo.png"
        )
        email.attach(image)
        # Make the container multipart/related so the CID reference resolves in
        # all major clients (Gmail, Outlook, Apple Mail).
        email.mixed_subtype = "related"
    except Exception:
        logger.exception("Failed to attach inline logo to branded email")
    return email


def render_branded_email(
    *,
    recipient_name="",
    title="",
    content="",
    button_label="",
    button_url="",
    company_name="",
    host="",
    protocol="https",
    content_is_html=False,
    logo_url="",
    request=None,
):
    """
    Render the standardized branded email and return the HTML string.

    The logo is referenced as ``cid:wireapps_logo`` by default, so remember to
    call :func:`attach_inline_logo` on the outgoing message. Pass ``logo_url``
    only for standalone previews (e.g. a base64 data-URI) where CID cannot
    resolve.

    Args:
        recipient_name: Name shown after "Hello ".
        title: Feature-specific headline (e.g. "Leave request created").
        content: Body text. Plain text unless ``content_is_html`` is True.
        button_label / button_url: Optional call-to-action button. The button
            is only rendered when both are provided.
        company_name: Shown in the footer. Falls back to the white-label name.
        host / protocol: Used only by callers to build absolute button URLs;
            no longer needed for the logo.
        content_is_html: Set True when ``content`` is already-rendered HTML
            (e.g. admin-authored WYSIWYG body) so it is not escaped.
        logo_url: Optional explicit logo URL/data-URI (previews only). When set
            it overrides the inline CID logo.
        request: Optional request; pass it only when running on the main
            thread so request-based context processors work.
    """
    context = {
        "recipient_name": recipient_name or "",
        "title": title or "",
        "content": content or "",
        "button_label": button_label or "",
        "button_url": button_url or "",
        "company_name": company_name or "",
        "host": host or "",
        "protocol": protocol or "https",
        "content_is_html": content_is_html,
        "logo_url": logo_url or "",
        "logo_cid": LOGO_CID,
    }
    if request is not None:
        return render_to_string(BRANDED_MAIL_TEMPLATE, context, request=request)
    return render_to_string(BRANDED_MAIL_TEMPLATE, context)

