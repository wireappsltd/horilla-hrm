"""
threading.py

This module is used handle mail sent in thread
"""

import logging
from threading import Thread

from django.contrib import messages
from django.contrib.auth.models import Group, User
from django.core.mail import EmailMessage

from base.backends import ConfiguredEmailBackend
from base.email_handlers import render_branded_email
from base.models import Department
from employee.models import EmployeeWorkInformation
from helpdesk.models import ISO_GROUP_NAME, Ticket

logger = logging.getLogger(__name__)


class TicketSendThread(Thread):
    """
    MailSend
    """

    def __init__(self, request, ticket, type):
        Thread.__init__(self)
        self.ticket = ticket
        self.type = type
        self.request = request
        self.assignees = ticket.assigned_to.all()
        self.host = request.get_host()
        self.protocol = "https" if request.is_secure() else "http"
        self.department_manager = None
        raised_on = ticket.get_raised_on_object()
        if isinstance(raised_on, Department):
            if raised_on.dept_manager.all():
                self.department_manager = raised_on.dept_manager.all().first().manager

    def send_email(self, subject, content, recipients, ticket_id="#"):
        host = self.host
        protocol = self.protocol
        link = "#"
        email_backend = ConfiguredEmailBackend()

        display_email_name = email_backend.dynamic_from_email_with_display_name
        if self.request:
            try:
                display_email_name = f"{self.request.user.employee_get.get_full_name()} <{self.request.user.employee_get.email}>"
            except:
                logger.error(Exception)

        if ticket_id != "#":
            link = f"{protocol}://{host}/helpdesk/ticket-detail/{ticket_id}/"
        for recipient in recipients:
            company = (
                recipient.get_company() if hasattr(recipient, "get_company") else None
            )
            html_message = render_branded_email(
                recipient_name=recipient.get_full_name(),
                title=subject,
                content=content,
                button_label="View Ticket" if link != "#" else "",
                button_url=link if link != "#" else "",
                company_name=str(company) if company else "",
                host=host,
                protocol=protocol,
                request=self.request,
            )

            email = EmailMessage(
                subject=subject,
                body=html_message,
                from_email=display_email_name,
                to=[recipient.email],
                reply_to=[display_email_name],
            )
            email.content_subtype = "html"
            try:
                email.send()
            except:
                messages.error(
                    self.request, f"Mail not sent to {recipient.get_full_name()}"
                )

    def run(self) -> None:
        super().run()

        if self.type == "create":
            owner = self.ticket.employee_id
            manager = self.department_manager

            if manager:
                content_manager = f"This is to inform you that a ticket has been raised on your department. Take the necessary actions to address the issue or request outlined in the ticket. Should you have any additional information or updates, please feel free to communicate directly with the {owner}."
                subject_manager = "Ticket created raised on your department"

                self.send_email(subject_manager, content_manager, [manager], self.ticket.id)

            content_owner = "This is to inform you that the ticket you created has been successfully logged into our system. The assigned team/individual will now take the necessary actions to address the issue or request outlined in the ticket. Should you have any additional information or updates, please feel free to communicate directly with the Support/Helpdesk team."
            subject_owner = "Ticket created successfully"

            self.send_email(subject_owner, content_owner, [owner], self.ticket.id)

        elif self.type == "status_change":
            assignees = self.assignees
            owner = self.ticket.employee_id
            manager = self.department_manager

            tracking = self.ticket.tracking()
            updated_by = tracking[0]["updated_by"]
            new_status = tracking[0]["changes"][0]["new"]
            old_status = tracking[0]["changes"][0]["old"]

            subject = "The Status of the Ticket has been updated"
            content = f"This is to inform you that the status of the following ticket has been updated by {updated_by} from {old_status} to {new_status}. If you have any questions or require further information, feel free to reach out to the Support/Helpdesk team."

            recipients = set(assignees) | {owner}
            if manager:
                recipients.add(manager)

            self.send_email(
                subject, content, recipients, self.ticket.id
            )

        elif self.type == "delete":
            assignees = self.assignees
            owner = self.ticket.employee_id
            manager = self.department_manager

            subject = "The Ticket has been deleted"
            content = f'This is to inform you that the Ticket "{self.ticket.title}" has been deleted. If you have any questions or require further information, feel free to reach out to the Support/Helpdesk team.'

            recipients = set(assignees) | {owner}
            if manager:
                recipients.add(manager)

            self.send_email(subject, content, recipients)

        return


class AddAssigneeThread(Thread):
    """
    MailSend
    """

    def __init__(self, request, ticket, recipient):
        Thread.__init__(self)
        self.ticket = ticket
        self.recipients = recipient
        self.request = request
        self.host = request.get_host()
        self.protocol = "https" if request.is_secure() else "http"

    def run(self) -> None:
        super().run()

        content = "Please review the ticket details and take appropriate action accordingly. If you have any questions or require further information, feel free to reach out to the owner or the Support/Helpdesk team."
        subject = "You have been assigned to a Ticket"

        host = self.host
        protocol = self.protocol
        email_backend = ConfiguredEmailBackend()
        display_email_name = email_backend.dynamic_from_email_with_display_name
        if self.request:
            try:
                display_email_name = f"{self.request.user.employee_get.get_full_name()} <{self.request.user.employee_get.email}>"
            except:
                pass
        link = f"{protocol}://{host}/helpdesk/ticket-detail/{self.ticket.id}/"
        for recipient in self.recipients:
            company = (
                recipient.get_company() if hasattr(recipient, "get_company") else None
            )
            html_message = render_branded_email(
                recipient_name=recipient.get_full_name(),
                title=subject,
                content=content,
                button_label="View Ticket",
                button_url=link,
                company_name=str(company) if company else "",
                host=host,
                protocol=protocol,
                request=self.request,
            )

            email = EmailMessage(
                subject=subject,
                body=html_message,
                from_email=display_email_name,
                to=[recipient.email],
                reply_to=[display_email_name],
            )
            email.content_subtype = "html"
            try:
                email.send()
            except:
                messages.error(
                    self.request, f"Mail not sent to {recipient.get_full_name()}"
                )


class RemoveAssigneeThread(Thread):
    """
    MailSend
    """

    def __init__(self, request, ticket, recipient):
        Thread.__init__(self)
        self.ticket = ticket
        self.recipients = recipient
        self.request = request
        self.host = request.get_host()
        self.protocol = "https" if request.is_secure() else "http"

    def run(self) -> None:
        super().run()

        content = "Please review the ticket details and take appropriate action accordingly. If you have any questions or require further information, feel free to reach out to the owner or the Support/Helpdesk team."
        subject = "You have been removed from a Ticket"
        email_backend = ConfiguredEmailBackend()

        display_email_name = email_backend.dynamic_from_email_with_display_name
        if self.request:
            try:
                display_email_name = f"{self.request.user.employee_get.get_full_name()} <{self.request.user.employee_get.email}>"
            except:
                pass
        host = self.host
        protocol = self.protocol
        link = f"{protocol}://{host}/helpdesk/ticket-detail/{self.ticket.id}/"
        for recipient in self.recipients:
            company = (
                recipient.get_company() if hasattr(recipient, "get_company") else None
            )
            html_message = render_branded_email(
                recipient_name=recipient.get_full_name(),
                title=subject,
                content=content,
                button_label="View Ticket",
                button_url=link,
                company_name=str(company) if company else "",
                host=host,
                protocol=protocol,
                request=self.request,
            )

            email = EmailMessage(
                subject=subject,
                body=html_message,
                from_email=display_email_name,
                to=[recipient.email],
                reply_to=[display_email_name],
            )
            email.content_subtype = "html"
            try:
                email.send()
            except:
                messages.error(
                    self.request, f"Mail not sent to {recipient.get_full_name()}"
                )


class PasswordResetMailThread(Thread):
    """
    Thread for sending email notifications related to Password Reset requests.

    Supported types:
      - "new_request": Notifies ISO officers about a new request.
      - "iso_review": Notifies the requesting employee about approval/rejection.
    """

    def __init__(
        self,
        request,
        ticket,
        type,
        pr_request=None,
        action=None,
        feedback=None,
        iso_recipients=None,
    ):
        Thread.__init__(self)
        self.daemon = True
        self.ticket = ticket
        self.type = type
        self.pr_request = pr_request
        self.action = action
        self.feedback = feedback or ""
        self.iso_recipients = iso_recipients or []
        self.host = request.get_host()
        self.protocol = "https" if request.is_secure() else "http"

        # Capture request-derived values in __init__ (runs on main thread)
        # so we don't need to access the request object from the child thread.
        self.request_user = getattr(request, "user", None)
        try:
            emp = request.user.employee_get
            # Only used for reply_to, NOT from_email.
            # from_email must come from the configured mail server
            # (Resend requires a verified domain as the sender).
            self.sender_reply_to = (
                f"{emp.get_full_name()} <{emp.email}>"
            )
        except Exception:
            self.sender_reply_to = None

    def get_iso_users(self):
        """
        Return a list of Employee objects for ISO officers (members of the
        ISO group) AND superusers.  This mirrors the in-app notification
        helper ``_get_iso_officer_users`` so both channels reach the same
        audience.
        """
        from django.contrib.auth.models import User as AuthUser
        from django.db.models import Q

        employees = []
        if self.iso_recipients:
            for user in self.iso_recipients:
                try:
                    if hasattr(user, "employee_get") and user.employee_get:
                        employees.append(user.employee_get)
                except Exception:
                    continue
            return employees

        try:
            iso_or_super = AuthUser.objects.filter(
                Q(groups__name=ISO_GROUP_NAME) | Q(is_superuser=True),
                is_active=True,
            ).distinct().select_related("employee_get")

            for user in iso_or_super:
                if hasattr(user, "employee_get") and user.employee_get:
                    employees.append(user.employee_get)
        except Exception as exc:
            logger.error("Error fetching ISO officers for email: %s", exc)

        if not employees:
            logger.warning(
                "No ISO officer employees found. No ISO email notifications will be sent."
            )
        return employees

    def _send_email(self, subject, content, recipients, ticket_id=None):
        """Send an email to each recipient Employee using an explicit backend
        connection so the thread does not depend on ``_thread_locals``."""
        host = self.host
        protocol = self.protocol
        link = "#"

        # Build the email backend connection explicitly – this reads the
        # DynamicEmailConfiguration from the DB (falls back to the primary
        # config when _thread_locals.request is unavailable in this thread).
        try:
            email_backend = ConfiguredEmailBackend()
        except Exception as exc:
            logger.error(
                "PasswordResetMailThread: failed to create email backend: %s", exc
            )
            return

        # Always use the configured mail server's from_email (verified domain).
        # Never use the employee's email as from_email — providers like
        # Resend reject emails whose From address isn't from a verified domain.
        from_email = email_backend.dynamic_from_email_with_display_name
        reply_to = [self.sender_reply_to] if self.sender_reply_to else [from_email]

        if ticket_id:
            link = f"{protocol}://{host}/helpdesk/ticket-detail/{ticket_id}/"

        for recipient in recipients:
            try:
                # Do NOT pass request= to render_branded_email – we are in a
                # child thread where context processors that rely on the
                # request / _thread_locals will fail.
                company = (
                    recipient.get_company()
                    if hasattr(recipient, "get_company")
                    else None
                )
                html_message = render_branded_email(
                    recipient_name=recipient.get_full_name(),
                    title=subject,
                    content=content,
                    button_label="View Ticket" if link != "#" else "",
                    button_url=link if link != "#" else "",
                    company_name=str(company) if company else "",
                    host=host,
                    protocol=protocol,
                )

                email = EmailMessage(
                    subject=subject,
                    body=html_message,
                    from_email=from_email,
                    to=[recipient.email],
                    connection=email_backend,
                    reply_to=reply_to,
                )
                email.content_subtype = "html"
                email.send()
                logger.info(
                    "Password-reset email sent to %s (%s)",
                    recipient.get_full_name(),
                    recipient.email,
                )
            except Exception as exc:
                logger.error(
                    "Mail not sent to %s (%s): %s",
                    recipient.get_full_name(),
                    recipient.email,
                    exc,
                )

    def run(self) -> None:
        from django.db import connection as db_connection

        try:
            super().run()

            if self.type == "new_request":
                # Notify all ISO officers about the new password reset request
                owner = self.ticket.employee_id
                platform = self.pr_request.platform if self.pr_request else "N/A"

                iso_employees = self.get_iso_users()

                if iso_employees:
                    subject = "New Password Reset Request Submitted"
                    content = (
                        f"A new password reset request has been submitted by "
                        f"{owner.get_full_name()} for platform: {platform}. "
                        f"Please review the request and take appropriate action "
                        f"(approve or reject). You can view the details by clicking "
                        f"the link below."
                    )
                    self._send_email(subject, content, iso_employees, self.ticket.id)

                # Also send confirmation email to the requesting employee
                subject_owner = "Password Reset Request Submitted Successfully"
                content_owner = (
                    f"Your password reset request for {platform} has been "
                    f"successfully submitted and is pending ISO Officer review. "
                    f"You will be notified once the request has been reviewed."
                )
                self._send_email(subject_owner, content_owner, [owner], self.ticket.id)

            elif self.type == "iso_review":
                # Notify the requesting employee about approval/rejection
                owner = self.ticket.employee_id
                platform = self.pr_request.platform if self.pr_request else "N/A"
                reviewer = self.request_user

                try:
                    reviewer_name = reviewer.employee_get.get_full_name()
                except Exception:
                    if reviewer:
                        reviewer_name = reviewer.get_full_name() or reviewer.username
                    else:
                        reviewer_name = "An ISO Officer"

                if self.action == "approve":
                    subject = "Your Password Reset Request Has Been Approved"
                    content = (
                        f"Your password reset request for {platform} has been "
                        f"approved by {reviewer_name}. "
                        f"The necessary actions will be taken to reset your password. "
                        f"If you have any questions, please contact the IT/Helpdesk team."
                    )
                else:
                    subject = "Your Password Reset Request Has Been Rejected"
                    content = (
                        f"Your password reset request for {platform} has been "
                        f"rejected by {reviewer_name}."
                    )
                    if self.feedback:
                        content += f" Reason: {self.feedback}"
                    content += (
                        " If you believe this was done in error or have additional "
                        "information, please submit a new request or contact the "
                        "IT/Helpdesk team."
                    )

                self._send_email(subject, content, [owner], self.ticket.id)

                # Also notify other ISO officers about the review action
                try:
                    reviewer_employee = reviewer.employee_get if reviewer else None
                except Exception:
                    reviewer_employee = None

                iso_employees = [
                    emp for emp in self.get_iso_users()
                    if emp != owner and emp != reviewer_employee
                ]

                if iso_employees:
                    status_text = "approved" if self.action == "approve" else "rejected"
                    subject_iso = f"Password Reset Request {status_text.capitalize()}"
                    content_iso = (
                        f"The password reset request submitted by "
                        f"{owner.get_full_name()} for {platform} has been "
                        f"{status_text} by {reviewer_name}."
                    )
                    self._send_email(subject_iso, content_iso, iso_employees, self.ticket.id)

        except Exception as exc:
            logger.error("PasswordResetMailThread unhandled error: %s", exc, exc_info=True)
        finally:
            db_connection.close()
