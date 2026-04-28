import logging
from threading import Thread

from django.core.mail import EmailMessage
from django.template.loader import render_to_string

from base.backends import ConfiguredEmailBackend

logger = logging.getLogger(__name__)


class CheckupMailThread(Thread):
    """
    Sends asset yearly check-up notification emails in a background thread.
    """

    def __init__(self, recipients, context, is_overdue=False):
        """
        Args:
            recipients: list of Employee instances to email
            context: dict with keys: asset_name, tracking_id, assigned_to,
                     checkup_date, service_shop, message
            is_overdue: bool indicating if this is an overdue follow-up
        """
        Thread.__init__(self)
        self.recipients = recipients
        self.context = context
        self.is_overdue = is_overdue

    def run(self):
        email_backend = ConfiguredEmailBackend()
        from_email = email_backend.dynamic_from_email_with_display_name
        subject_prefix = "OVERDUE: " if self.is_overdue else ""
        subject = (
            f"{subject_prefix}Asset Yearly Check-up - "
            f"{self.context['asset_name']}"
        )

        for employee in self.recipients:
            recipient_email = employee.get_mail()
            if not recipient_email:
                continue

            html_message = render_to_string(
                "asset/mail_templates/checkup_notification.html",
                {
                    **self.context,
                    "recipient_name": employee.get_full_name(),
                    "is_overdue": self.is_overdue,
                },
            )

            email = EmailMessage(
                subject=subject,
                body=html_message,
                from_email=from_email,
                to=[recipient_email],
                reply_to=[from_email],
            )
            email.content_subtype = "html"
            try:
                email.send()
                logger.info(
                    "Checkup email sent to %s for asset %s",
                    recipient_email,
                    self.context["asset_name"],
                )
            except Exception as e:
                logger.exception(
                    "Failed to send checkup email to %s: %s",
                    recipient_email,
                    e,
                )