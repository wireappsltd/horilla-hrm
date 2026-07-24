"""
mail.py

This module is used handle mail sent in thread
"""

import logging
from threading import Thread

from django.core.mail import EmailMessage
from django.urls import reverse

from base.backends import ConfiguredEmailBackend
from base.email_handlers import render_branded_email
from employee.models import EmployeeWorkInformation
from payroll.models.models import Payslip
from payroll.views.views import payslip_pdf

logger = logging.getLogger(__name__)


class MailSendThread(Thread):
    """
    MailSend
    """

    def __init__(self, request, result_dict, ids):
        Thread.__init__(self)
        self.result_dict = result_dict
        self.ids = ids
        self.request = request
        self.host = request.get_host()
        self.protocol = "https" if request.is_secure() else "http"

    def run(self) -> None:
        super().run()
        for record in list(self.result_dict.values()):
            first_instance = record["instances"][0]
            recipient_name = first_instance.get_name()
            company = (
                first_instance.get_company()
                if hasattr(first_instance, "get_company")
                else None
            )
            try:
                button_url = f"{self.protocol}://{self.host}{reverse('view-payslip')}"
            except Exception:
                button_url = ""
            content = (
                f"You have {record['count']} payslip attachment(s) in this email. "
                f"Have a good day."
            )
            html_message = render_branded_email(
                recipient_name=recipient_name,
                title="Your payslips are ready",
                content=content,
                button_label="View Payslips" if button_url else "",
                button_url=button_url,
                company_name=str(company) if company else "",
                host=self.host,
                protocol=self.protocol,
                request=self.request,
            )
            attachments = []
            for instance in record["instances"]:
                pdf_bytes = payslip_pdf(self.request, instance.id)
                if not isinstance(pdf_bytes, bytes):
                    logger.error(
                        "PDF generation failed for payslip %s, skipping attachment.",
                        instance.id,
                    )
                    continue
                attachments.append(
                    (
                        f"{instance.get_payslip_title()}.pdf",
                        pdf_bytes,
                        "application/pdf",
                    )
                )
            employee = record["instances"][0].employee_id
            email_backend = ConfiguredEmailBackend()
            display_email_name = email_backend.dynamic_from_email_with_display_name
            if self.request:
                try:
                    display_email_name = f"{self.request.user.employee_get.get_full_name()} <{self.request.user.employee_get.email}>"
                except:
                    logger.error(Exception)

            email = EmailMessage(
                f"Hello, {record['instances'][0].get_name()} Your Payslips is Ready!",
                html_message,
                display_email_name,
                [employee.get_mail()],
                reply_to=[display_email_name],
            )
            email.attachments = attachments

            # Send the email
            email.content_subtype = "html"
            try:
                email.send()
                Payslip.objects.filter(id__in=self.ids).update(sent_to_employee=True)
            except Exception as e:
                logger.exception(e)

        return
