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

    SUBJECT_PREFIXES = {
        "upcoming": "",
        "overdue": "OVERDUE: ",
        "completed": "COMPLETED: ",
    }

    def __init__(self, recipients, context, is_overdue=False, notification_type=None):
        """
        Args:
            recipients: list of Employee instances to email
            context: dict with keys: asset_name, tracking_id, assigned_to,
                     checkup_date, service_shop, message
            is_overdue: legacy bool. If notification_type is unset, True maps
                        to "overdue" and False maps to "upcoming".
            notification_type: one of "upcoming", "overdue", "completed".
        """
        Thread.__init__(self)
        self.recipients = recipients
        self.context = context
        if notification_type is None:
            notification_type = "overdue" if is_overdue else "upcoming"
        self.notification_type = notification_type
        self.is_overdue = notification_type == "overdue"
        self.is_completed = notification_type == "completed"

    def run(self):
        email_backend = ConfiguredEmailBackend()
        from_email = email_backend.dynamic_from_email_with_display_name
        subject_prefix = self.SUBJECT_PREFIXES.get(self.notification_type, "")
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
                    "is_completed": self.is_completed,
                    "notification_type": self.notification_type,
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
                    "Checkup %s email sent to %s for asset %s",
                    self.notification_type,
                    recipient_email,
                    self.context["asset_name"],
                )
            except Exception as e:
                logger.exception(
                    "Failed to send checkup email to %s: %s",
                    recipient_email,
                    e,
                )