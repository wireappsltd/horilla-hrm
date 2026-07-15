import contextlib
import logging
from threading import Thread
from django.contrib.auth.models import Group
from django.contrib import messages
from django.core.mail import EmailMessage
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils.translation import gettext as _

from base.backends import ConfiguredEmailBackend

logger = logging.getLogger(__name__)


class LeaveMailSendThread(Thread):

    def __init__(self, request, leave_request, type):
        Thread.__init__(self)
        self.request = request
        self.leave_request = leave_request
        self.type = type
        if request is not None:
            self.host = request.get_host()
            self.protocol = "https" if request.is_secure() else "http"
        else:
            from django.conf import settings

            origin = (
                getattr(settings, "CSRF_TRUSTED_ORIGINS", None)
                or ["http://localhost:8000"]
            )[0]
            self.protocol = "https" if origin.startswith("https") else "http"
            self.host = origin.split("://")[-1].rstrip("/")

    def get_hr_users(self):
        try:
            hr_group = Group.objects.get(name="HR")
            return [
                user.employee_get
                for user in hr_group.user_set.all()
                if hasattr(user, "employee_get")
                and user.employee_get
                and user.employee_get.is_active
            ]
        except Group.DoesNotExist:
            return []

    def get_pending_approvers(self):
        """
        Return the list of employees who still need to approve this leave
        request. For a multi-level (conditional) approval flow only the
        managers that have not approved yet are returned; otherwise the
        employee's reporting manager is used. Falls back to the HR users when
        no approver can be resolved so a reminder is never silently dropped.
        """
        leave = self.leave_request
        approvers = []
        multiple = leave.multiple_approvals()
        if multiple:
            for condition_approval in multiple["requested"]:
                manager = condition_approval.manager_id
                if manager and manager.is_active:
                    approvers.append(manager)
        else:
            reporting_manager = leave.employee_id.get_reporting_manager()
            if reporting_manager and reporting_manager.is_active:
                approvers.append(reporting_manager)

        if not approvers:
            approvers.extend(self.get_hr_users())

        return list(set(approvers))

    def send_email(self, subject, content, recipients, leave_request_id="#"):
        email_backend = ConfiguredEmailBackend()
        display_email_name = email_backend.dynamic_from_email_with_display_name

        host = self.host
        protocol = self.protocol
        link = leave_request_id
        if leave_request_id != "#":
            link = int(leave_request_id)
        for recipient in recipients:
            if recipient:
                html_message = render_to_string(
                    "base/mail_templates/leave_request_template.html",
                    {
                        "link": link,
                        "instance": recipient,
                        "host": host,
                        "protocol": protocol,
                        "subject": subject,
                        "content": content,
                    },
                    request=self.request,
                )

                email = EmailMessage(
                    subject=subject,
                    body=html_message,
                    from_email=display_email_name,
                    to=[recipient.get_mail()],
                    reply_to=[display_email_name],
                )
                email.content_subtype = "html"
                try:
                    email.send()
                except:
                    if self.request is not None:
                        messages.error(
                            self.request,
                            f"Mail not sent to {recipient.get_full_name()}",
                        )
                    else:
                        logger.error(
                            "Mail not sent to %s", recipient.get_full_name()
                        )

    def run(self) -> None:
        super().run()
        if self.type == "request":
            leave = self.leave_request
            owner = self.leave_request.employee_id
            reporting_manager = self.leave_request.employee_id.get_reporting_manager()

            leave_details = {
                "employee_name": owner.get_full_name(),
                "leave_type": leave.leave_type_id.name,
                "from_date": leave.start_date,
                "to_date": leave.end_date,
                "total_days": leave.requested_days,
                "status": leave.status.capitalize(),
            }

            content_manager = (
                f"A new leave request has been submitted by {leave_details['employee_name']} "
                f"from {leave_details['from_date']} to {leave_details['to_date']} for"
                f" {leave_details['total_days']} day(s). "
                f"The leave type is {leave_details['leave_type']}."
            )

            subject_manager = f"Leave request has been requested by {owner}"

            recipients = []

            if reporting_manager and reporting_manager.is_active:
                recipients.append(reporting_manager)

            hr_users = self.get_hr_users()
            recipients.extend(hr_users)

            recipients = list(set(recipients))

            self.send_email(
                subject_manager,
                content_manager,
                recipients,
                self.leave_request.id,
            )

            content_owner = (
                f"Your leave request has been recorded in our system. "
                f"Our HR will now review it and take necessary action. "
                f"If you need to share any additional information or updates, "
                f"please contact {reporting_manager} directly."
                if reporting_manager
                else (
                    "Your leave request has been recorded in our system. "
                    "Our HR will now review it and take necessary action. "
                    "If you need to share any additional information or updates, "
                    "please contact our HR team directly."
                )
            )
            subject_owner = "your leave request has been submitted successfully"

            self.send_email(
                subject_owner, content_owner, [owner], self.leave_request.id
            )

        elif self.type == "approve":
            owner = self.leave_request.employee_id

            subject = "your leave request has been approved"
            content = ""

            self.send_email(subject, content, [owner], self.leave_request.id)

        elif self.type == "reminder":
            owner = self.leave_request.employee_id
            approvers = self.get_pending_approvers()

            # Reminder to the employee: keep them informed that the request is
            # still awaiting approval.
            subject_owner = "your leave request is awaiting approval"
            content_owner = (
                "This is a friendly reminder that your leave request is still "
                "awaiting approval. You will be notified as soon as it has been "
                "reviewed. If you need to share any additional information or "
                "updates, please contact your approver directly."
            )
            self.send_email(
                subject_owner, content_owner, [owner], self.leave_request.id
            )

            # Daily reminder to the approver(s) until the request is approved.
            subject_approver = f"Reminder: Leave request pending approval from {owner}"
            content_approver = (
                f"This is a daily reminder that a leave request submitted by "
                f"{owner} from {self.leave_request.start_date} to "
                f"{self.leave_request.end_date} is still pending your approval. "
                f"Please review and take the necessary action at your earliest "
                f"convenience."
            )
            self.send_email(
                subject_approver,
                content_approver,
                approvers,
                self.leave_request.id,
            )

            # In-app notifications mirroring the email reminders.
            with contextlib.suppress(Exception):
                from notifications.signals import notify

                for approver in approvers:
                    if getattr(approver, "employee_user_id", None):
                        notify.send(
                            owner,
                            recipient=approver.employee_user_id,
                            verb="You have a leave request pending your approval.",
                            verb_ar="لديك طلب إجازة في انتظار موافقتك.",
                            verb_de="Sie haben eine Urlaubsanfrage, die auf Ihre Genehmigung wartet.",
                            verb_es="Tiene una solicitud de permiso pendiente de su aprobación.",
                            verb_fr="Vous avez une demande de congé en attente de votre approbation.",
                            icon="people-circle",
                            redirect=f"/leave/request-view?id={self.leave_request.id}",
                        )
                if getattr(owner, "employee_user_id", None):
                    notify.send(
                        owner,
                        recipient=owner.employee_user_id,
                        verb="Your leave request is awaiting approval.",
                        verb_ar="طلب الإجازة الخاص بك في انتظار الموافقة.",
                        verb_de="Ihr Urlaubsantrag wartet auf Genehmigung.",
                        verb_es="Su solicitud de permiso está pendiente de aprobación.",
                        verb_fr="Votre demande de congé est en attente d'approbation.",
                        icon="people-circle",
                        redirect=f"/leave/user-request-view?id={self.leave_request.id}",
                    )


        elif self.type == "reject":
            owner = self.leave_request.employee_id
            reporting_manager = self.leave_request.employee_id.get_reporting_manager()

            subject = "The Leave request has been rejected"
            content = f"This is to inform you that the leave request has been rejected. If you have any questions or require further information, feel free to reach out to the {reporting_manager}."

            self.send_email(subject, content, [owner], self.leave_request.id)


        elif self.type == "manager_reject_mail":

            employee = self.leave_request.employee_id

            manager = self.leave_request.manager

            if not manager or not manager.employee_user_id:
                return

            subject = f"Leave Request Rejected – {employee}"

            content = (

                f"This is to inform you that the leave request submitted by "

                f"{employee} has been rejected.\n\n"

                f"Leave Period: {self.leave_request.start_date} to {self.leave_request.end_date}\n"

                f"Rejected By: {self.request.user.employee_get}\n"

                f"Reason: {self.leave_request.reject_reason}"

            )

            self.send_email(

                subject,

                content,

                [manager],

                self.leave_request.id,

            )


        elif self.type == "cancel":
                owner = self.leave_request.employee_id
                reporting_manager = self.leave_request.employee_id.get_reporting_manager()

                content_manager = f"This is to inform you that a leave request has been requested to cancel by {owner}. Take the necessary actions for the leave request. Should you have any additional information or updates, please feel free to communicate directly with the {owner}."
                subject_manager = f"Leave request cancellation"

                self.send_email(
                    subject_manager,
                    content_manager,
                    [reporting_manager],
                    self.leave_request.id,
                )

                content_owner = f"This is to inform you that a cancellation request created for your leave request has been successfully logged into our system. The manager will now take the necessary actions to address the leave request. Should you have any additional information or updates, please feel free to communicate directly with the {reporting_manager}."
                subject_owner = "Leave request cancellation requested"

                self.send_email(
                    subject_owner, content_owner, [owner], self.leave_request.id
                )

                return


class LeaveClashThread(Thread):

    def __init__(self, leave_request):
        Thread.__init__(self)
        self.leave_request = leave_request

    def count_leave_clashes(self):
        from leave.models import LeaveRequest

        """
        Method to count leave clashes where this employee's leave request overlaps
        with other employees' requested dates.
        """
        overlapping_requests = LeaveRequest.objects.exclude(
            id=self.leave_request.id
        ).filter(
            Q(
                employee_id__employee_work_info__department_id=self.leave_request.employee_id.employee_work_info.department_id
            )
            | Q(
                employee_id__employee_work_info__job_position_id=self.leave_request.employee_id.employee_work_info.job_position_id
            ),
            start_date__lte=self.leave_request.end_date,
            end_date__gte=self.leave_request.start_date,
        )

        return overlapping_requests.count()

    def run(self) -> None:
        from leave.models import LeaveRequest

        super().run()
        dates = self.leave_request.requested_dates()
        leave_requests_to_update = LeaveRequest.objects.filter(
            Q(start_date__in=dates) | Q(end_date__in=dates)
        )

        for leave_request in leave_requests_to_update:
            leave_request.leave_clashes_count = self.count_leave_clashes()
            leave_request.save()
