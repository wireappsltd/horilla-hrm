"""
email_handlers.py

Shared helpers to render the single, standardized branded email template
(`base/mail_templates/horilla_mail_template.html`) used across every Horilla
feature that sends email to users.

Every feature keeps its own wording (title / content / call-to-action button)
but shares the exact same look & feel as the leave-request email.
"""

from django.template.loader import render_to_string

BRANDED_MAIL_TEMPLATE = "base/mail_templates/horilla_mail_template.html"


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
    request=None,
):
    """
    Render the standardized branded email and return the HTML string.

    Args:
        recipient_name: Name shown after "Hello ".
        title: Feature-specific headline (e.g. "Leave request created").
        content: Body text. Plain text unless ``content_is_html`` is True.
        button_label / button_url: Optional call-to-action button. The button
            is only rendered when both are provided.
        company_name: Shown in the footer. Falls back to the white-label name.
        host / protocol: Used to build the absolute logo URL.
        content_is_html: Set True when ``content`` is already-rendered HTML
            (e.g. admin-authored WYSIWYG body) so it is not escaped.
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
    }
    if request is not None:
        return render_to_string(BRANDED_MAIL_TEMPLATE, context, request=request)
    return render_to_string(BRANDED_MAIL_TEMPLATE, context)

