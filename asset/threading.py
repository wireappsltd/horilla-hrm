import logging
from threading import Thread

from django.core.mail import EmailMessage

from base.backends import ConfiguredEmailBackend
from base.email_handlers import render_branded_email

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
        logger.info(
            "[CheckupMailThread] START type=%s recipients=%s asset=%s",
            self.notification_type,
            len(self.recipients),
            self.context.get("asset_name"),
        )

        try:
            email_backend = ConfiguredEmailBackend()
        except Exception:
            logger.exception(
                "[CheckupMailThread] failed to initialise ConfiguredEmailBackend"
            )
            return

        from_email = email_backend.dynamic_from_email_with_display_name
        logger.info(
            "[CheckupMailThread] SMTP host=%s port=%s username=%s use_tls=%s from=%s",
            getattr(email_backend, "dynamic_host", None),
            getattr(email_backend, "dynamic_port", None),
            getattr(email_backend, "dynamic_username", None),
            getattr(email_backend, "dynamic_use_tls", None),
            from_email,
        )
        if not from_email:
            logger.error(
                "[CheckupMailThread] no from_email configured "
                "(check DynamicEmailConfiguration or EMAIL_HOST_USER / DEFAULT_FROM_EMAIL in .env); aborting"
            )
            return

        subject_prefix = self.SUBJECT_PREFIXES.get(self.notification_type, "")
        subject = (
            f"{subject_prefix}Asset Yearly Check-up - "
            f"{self.context['asset_name']}"
        )
        logger.info("[CheckupMailThread] subject=%r", subject)

        title = {
            "upcoming": "Asset check-up reminder",
            "overdue": "Asset check-up is overdue",
            "completed": "Asset check-up completed",
        }.get(self.notification_type, "Asset check-up update")

        details = (
            f"Asset Name: {self.context.get('asset_name', '')}\n"
            f"Tracking ID: {self.context.get('tracking_id', '')}\n"
            f"Assigned To: {self.context.get('assigned_to', '')}\n"
            f"Check-up Date: {self.context.get('checkup_date', '')}\n"
            f"Service Shop: {self.context.get('service_shop', '')}"
        )

        sent_count = 0
        skipped_no_email = 0
        failed_count = 0

        for employee in self.recipients:
            employee_label = (
                getattr(employee, "get_full_name", lambda: str(employee))()
            )
            recipient_email = employee.get_mail()
            logger.info(
                "[CheckupMailThread] recipient candidate employee=%s email=%r",
                employee_label,
                recipient_email,
            )
            if not recipient_email:
                logger.warning(
                    "[CheckupMailThread] skipping employee=%s — get_mail() returned empty",
                    employee_label,
                )
                skipped_no_email += 1
                continue

            try:
                company = (
                    employee.get_company()
                    if hasattr(employee, "get_company")
                    else None
                )
                content = f"{self.context.get('message', '')}\n\n{details}"
                html_message = render_branded_email(
                    recipient_name=employee_label,
                    title=title,
                    content=content,
                    company_name=str(company) if company else "",
                )
            except Exception:
                logger.exception(
                    "[CheckupMailThread] template render failed for employee=%s",
                    employee_label,
                )
                failed_count += 1
                continue

            email = EmailMessage(
                subject=subject,
                body=html_message,
                from_email=from_email,
                to=[recipient_email],
                reply_to=[from_email],
            )
            email.content_subtype = "html"
            try:
                send_result = email.send()
                logger.info(
                    "[CheckupMailThread] SENT type=%s to=%s asset=%s send_result=%s",
                    self.notification_type,
                    recipient_email,
                    self.context.get("asset_name"),
                    send_result,
                )
                sent_count += 1
            except Exception:
                logger.exception(
                    "[CheckupMailThread] SMTP send failed type=%s to=%s asset=%s",
                    self.notification_type,
                    recipient_email,
                    self.context.get("asset_name"),
                )
                failed_count += 1

        logger.info(
            "[CheckupMailThread] DONE type=%s sent=%s skipped_no_email=%s failed=%s",
            self.notification_type,
            sent_count,
            skipped_no_email,
            failed_count,
        )