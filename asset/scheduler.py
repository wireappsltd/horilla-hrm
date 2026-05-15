"""
scheduler.py

This module is used to register scheduled tasks
"""

import logging
import sys
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from django.contrib.auth.models import Group
from django.urls import reverse

from notifications.signals import notify

logger = logging.getLogger(__name__)


def _resolve_notification_bot():
    """Return a User suitable for use as the `actor` on notify.send().

    Looks up `settings.NOTIFICATION_BOT_USERNAME` first; if that user doesn't
    exist, falls back to the first active superuser. Returning a non-None user
    is required by notify.send(), so without this fallback every job aborts
    silently on installs where the bot account was never seeded.
    """
    from django.conf import settings
    from django.contrib.auth.models import User

    bot_username = getattr(settings, "NOTIFICATION_BOT_USERNAME", None)
    bot = None
    if bot_username:
        bot = User.objects.filter(username=bot_username).first()
    if bot is None:
        bot = User.objects.filter(is_superuser=True, is_active=True).order_by("pk").first()
        if bot is not None:
            logger.info(
                "[asset.scheduler] notification bot user '%s' missing; falling back to superuser '%s'",
                bot_username,
                bot.username,
            )
    return bot


def notify_expiring_assets():
    """
    Finds all Expiring Assets and send a notification on the notify_before date.
    """
    from django.contrib.auth.models import User

    from asset.models import Asset
    print("Running")
    today = date.today()
    assets = Asset.objects.all()
    from django.conf import settings
    bot = User.objects.filter(username=settings.NOTIFICATION_BOT_USERNAME).first()
    for asset in assets:
        if asset.expiry_date and asset.owner:
            expiry_date = asset.expiry_date
            notify_date = expiry_date - timedelta(days=asset.notify_before)

            if notify_date == today:
                notify.send(
                    bot,
                    recipient=asset.owner.employee_user_id,
                    verb=f"The Asset ' {asset.asset_name} ' expires in {asset.notify_before} days",
                    verb_ar=f"تنتهي صلاحية الأصل ' {asset.asset_name} ' خلال {asset.notify_before}\
                    من الأيام",
                    verb_de=f"Das Asset {asset.asset_name} läuft in {asset.notify_before} Tagen\
                        ab.",
                    verb_es=f"El activo {asset.asset_name} caduca en {asset.notify_before} días.",
                    verb_fr=f"L'actif {asset.asset_name} expire dans {asset.notify_before} jours.",
                    redirect=reverse("asset-category-view"),
                    label="System",
                    icon="information",
                )


def notify_expiring_documents():
    """
    Finds all Expiring Documents and send a notification on the notify_before date.
    """
    from django.contrib.auth.models import User

    from horilla_documents.models import Document

    today = date.today()
    documents = Document.objects.all()
    from django.conf import settings
    bot = User.objects.filter(username=settings.NOTIFICATION_BOT_USERNAME).first()
    if not bot:
        print(
            f"Notification bot user '{settings.NOTIFICATION_BOT_USERNAME}' was not found; "
            "skipping expiring document notifications.",
            file=sys.stderr,
        )
        return
    for document in documents:
        if document.expiry_date:
            expiry_date = document.expiry_date
            notify_date = expiry_date - timedelta(days=document.notify_before)

            if notify_date == today:
                notify.send(
                    bot,
                    recipient=document.employee_id.employee_user_id,
                    verb=f"The document ' {document.title} ' expires in {document.notify_before}\
                        days",
                    verb_ar=f"تنتهي صلاحية المستند '{document.title}' خلال {document.notify_before}\
                    يوم",
                    verb_de=f"Das Dokument '{document.title}' läuft in {document.notify_before}\
                        Tagen ab.",
                    verb_es=f"El documento '{document.title}' caduca en {document.notify_before}\
                        días",
                    verb_fr=f"Le document '{document.title}' expire dans {document.notify_before}\
                        jours",
                    redirect=reverse("asset-category-view"),
                    label="System",
                    icon="information",
                )
            if today >= expiry_date:
                document.is_active = False
                document.save()


def notify_upcoming_checkups():
    """
    Sends a notification once when a yearly check-up enters the 30-day
    upcoming window. Notifies the assigned employee and all users with
    asset.view_assetassignment permission (HR/Ops).

    Idempotency: an assignment is only picked up while
    `last_upcoming_notification_date` is NULL. The flag is set after the
    first fire and cleared again when the check-up is marked Complete, so
    the next yearly cycle will re-trigger.
    """
    logger.info("[notify_upcoming_checkups] job started")

    from django.contrib.auth.models import User

    from asset.models import AssetAssignment
    from horilla.methods import horilla_users_with_perms

    today = date.today()
    window_end = today + timedelta(days=30)
    bot = _resolve_notification_bot()
    if not bot:
        logger.warning(
            "[notify_upcoming_checkups] no usable bot/superuser found; aborting"
        )
        return
    assignments = AssetAssignment.objects.filter(
        yearly_checkup_date__gt=today,
        yearly_checkup_date__lte=window_end,
        checkup_completed=False,
        return_date__isnull=True,
        last_upcoming_notification_date__isnull=True,
    )
    logger.info(
        "[notify_upcoming_checkups] matched %s assignment(s) in window today=%s..%s",
        assignments.count(),
        today,
        window_end,
    )
    for assignment in assignments:
        logger.info(
            "[notify_upcoming_checkups] processing assignment id=%s asset=%s employee=%s checkup_date=%s",
            assignment.pk,
            getattr(assignment.asset_id, "asset_name", None),
            getattr(assignment.assigned_to_employee_id, "pk", None),
            assignment.yearly_checkup_date,
        )
        asset = assignment.asset_id
        employee = assignment.assigned_to_employee_id
        shop = assignment.service_shop_name or "N/A"
        checkup_date = assignment.yearly_checkup_date.strftime("%Y-%m-%d")

        message = (
            f"Yearly check-up for asset '{asset.asset_name}' "
            f"({asset.asset_tracking_id}) is due on {checkup_date}. "
            f"Service shop: {shop}."
        )
        message_ar = (
            f"الفحص السنوي للأصل '{asset.asset_name}' "
            f"({asset.asset_tracking_id}) مستحق في {checkup_date}. "
            f"ورشة الخدمة: {shop}."
        )
        message_de = (
            f"Jährliche Überprüfung für Asset '{asset.asset_name}' "
            f"({asset.asset_tracking_id}) fällig am {checkup_date}. "
            f"Servicewerkstatt: {shop}."
        )
        message_es = (
            f"Revisión anual del activo '{asset.asset_name}' "
            f"({asset.asset_tracking_id}) programada para {checkup_date}. "
            f"Taller de servicio: {shop}."
        )
        message_fr = (
            f"Contrôle annuel de l'actif '{asset.asset_name}' "
            f"({asset.asset_tracking_id}) prévu le {checkup_date}. "
            f"Atelier de service : {shop}."
        )

        # Notify the assigned employee
        logger.info(
            "[notify_upcoming_checkups] in-app notify assigned_employee user_id=%s",
            getattr(employee.employee_user_id, "pk", None),
        )
        notify.send(
            bot,
            recipient=employee.employee_user_id,
            verb=message,
            verb_ar=message_ar,
            verb_de=message_de,
            verb_es=message_es,
            verb_fr=message_fr,
            redirect=reverse("asset-request-allocation-view"),
            label="System",
            icon="calendar",
        )

        # Notify HR/Ops (users with asset view permission)
        permed_users = horilla_users_with_perms("asset.view_assetassignment")
        logger.info(
            "[notify_upcoming_checkups] permed users (asset.view_assetassignment) count=%s ids=%s",
            permed_users.count(),
            list(permed_users.values_list("pk", flat=True)),
        )
        if permed_users.exists():
            notify.send(
                bot,
                recipient=permed_users,
                verb=message,
                verb_ar=message_ar,
                verb_de=message_de,
                verb_es=message_es,
                verb_fr=message_fr,
                redirect=reverse("asset-request-allocation-view"),
                label="System",
                icon="calendar",
            )

        # Notify HR and OPS group users
        notified_pks = set(permed_users.values_list("pk", flat=True))
        notified_pks.add(employee.employee_user_id.pk)

        for group_name in ("HR", "OPS"):
            try:
                group = Group.objects.get(name=group_name)
                group_users = group.user_set.exclude(pk__in=notified_pks)
                logger.info(
                    "[notify_upcoming_checkups] group=%s additional user_ids=%s",
                    group_name,
                    list(group_users.values_list("pk", flat=True)),
                )
                if group_users.exists():
                    notify.send(
                        bot,
                        recipient=group_users,
                        verb=message,
                        verb_ar=message_ar,
                        verb_de=message_de,
                        verb_es=message_es,
                        verb_fr=message_fr,
                        redirect=reverse("asset-request-allocation-view"),
                        label="System",
                        icon="calendar",
                    )
                    notified_pks.update(group_users.values_list("pk", flat=True))
            except Group.DoesNotExist:
                logger.info(
                    "[notify_upcoming_checkups] group=%s does not exist; skipping",
                    group_name,
                )

        # Send email notifications
        from asset.threading import CheckupMailThread
        from employee.models import Employee

        email_context = {
            "asset_name": asset.asset_name,
            "tracking_id": asset.asset_tracking_id,
            "assigned_to": employee.get_full_name(),
            "checkup_date": checkup_date,
            "service_shop": shop,
            "message": message,
        }
        all_notified_users = User.objects.filter(pk__in=notified_pks).exclude(
            pk=employee.employee_user_id.pk
        )
        email_recipients = [employee]
        if all_notified_users.exists():
            extra_employees = Employee.objects.filter(
                employee_user_id__in=all_notified_users
            )
            email_recipients.extend(list(extra_employees))
        logger.info(
            "[notify_upcoming_checkups] dispatching email thread assignment=%s "
            "recipient_employees=%s emails=%s",
            assignment.pk,
            [str(e) for e in email_recipients],
            [e.get_mail() for e in email_recipients],
        )
        CheckupMailThread(email_recipients, email_context, is_overdue=False).start()

        assignment.last_upcoming_notification_date = today
        assignment.save(update_fields=["last_upcoming_notification_date"])
        logger.info(
            "[notify_upcoming_checkups] flag set last_upcoming_notification_date=%s assignment=%s",
            today,
            assignment.pk,
        )


def notify_overdue_checkups():
    """
    Sends a first overdue notification once when a yearly check-up has
    passed its due date without being marked as completed. Notifies the
    assigned employee and all users with asset.view_assetassignment
    permission (HR/Ops).

    Idempotency: an assignment is only picked up while
    `last_overdue_notification_date` is NULL, so the 4h scheduler tick
    fires it once and not 6×/day. Catches up missed days even if the
    scheduler was down on the exact day the check-up became overdue.
    The flag is cleared again when the check-up is marked Complete, so
    the next yearly cycle's overdue state will re-trigger.
    """
    logger.info("[notify_overdue_checkups] job started")

    from django.contrib.auth.models import User

    from asset.models import AssetAssignment
    from horilla.methods import horilla_users_with_perms

    today = date.today()
    bot = _resolve_notification_bot()
    if not bot:
        logger.warning(
            "[notify_overdue_checkups] no usable bot/superuser found; aborting"
        )
        return

    overdue_assignments = AssetAssignment.objects.filter(
        yearly_checkup_date__lt=today,
        checkup_completed=False,
        return_date__isnull=True,
        last_overdue_notification_date__isnull=True,
    )
    logger.info(
        "[notify_overdue_checkups] matched %s assignment(s) overdue as of %s",
        overdue_assignments.count(),
        today,
    )
    for assignment in overdue_assignments:
        logger.info(
            "[notify_overdue_checkups] processing assignment id=%s asset=%s employee=%s checkup_date=%s",
            assignment.pk,
            getattr(assignment.asset_id, "asset_name", None),
            getattr(assignment.assigned_to_employee_id, "pk", None),
            assignment.yearly_checkup_date,
        )
        asset = assignment.asset_id
        employee = assignment.assigned_to_employee_id
        shop = assignment.service_shop_name or "N/A"
        checkup_date = assignment.yearly_checkup_date.strftime("%Y-%m-%d")

        message = (
            f"OVERDUE: Yearly check-up for asset '{asset.asset_name}' "
            f"({asset.asset_tracking_id}) was due on {checkup_date}. "
            f"Service shop: {shop}. Please schedule immediately."
        )
        message_ar = (
            f"متأخر: الفحص السنوي للأصل '{asset.asset_name}' "
            f"({asset.asset_tracking_id}) كان مستحقاً في {checkup_date}. "
            f"ورشة الخدمة: {shop}. يرجى الجدولة فوراً."
        )
        message_de = (
            f"ÜBERFÄLLIG: Jährliche Überprüfung für Asset '{asset.asset_name}' "
            f"({asset.asset_tracking_id}) war fällig am {checkup_date}. "
            f"Servicewerkstatt: {shop}. Bitte sofort planen."
        )
        message_es = (
            f"VENCIDO: Revisión anual del activo '{asset.asset_name}' "
            f"({asset.asset_tracking_id}) estaba programada para {checkup_date}. "
            f"Taller de servicio: {shop}. Por favor programe inmediatamente."
        )
        message_fr = (
            f"EN RETARD : Contrôle annuel de l'actif '{asset.asset_name}' "
            f"({asset.asset_tracking_id}) était prévu le {checkup_date}. "
            f"Atelier de service : {shop}. Veuillez planifier immédiatement."
        )

        # Notify the assigned employee
        logger.info(
            "[notify_overdue_checkups] in-app notify assigned_employee user_id=%s",
            getattr(employee.employee_user_id, "pk", None),
        )
        notify.send(
            bot,
            recipient=employee.employee_user_id,
            verb=message,
            verb_ar=message_ar,
            verb_de=message_de,
            verb_es=message_es,
            verb_fr=message_fr,
            redirect=reverse("asset-request-allocation-view"),
            label="System",
            icon="alert-circle",
        )

        # Notify HR/Ops
        permed_users = horilla_users_with_perms("asset.view_assetassignment")
        logger.info(
            "[notify_overdue_checkups] permed users (asset.view_assetassignment) count=%s ids=%s",
            permed_users.count(),
            list(permed_users.values_list("pk", flat=True)),
        )
        if permed_users.exists():
            notify.send(
                bot,
                recipient=permed_users,
                verb=message,
                verb_ar=message_ar,
                verb_de=message_de,
                verb_es=message_es,
                verb_fr=message_fr,
                redirect=reverse("asset-request-allocation-view"),
                label="System",
                icon="alert-circle",
            )

        # Notify HR and OPS group users
        notified_pks = set(permed_users.values_list("pk", flat=True))
        notified_pks.add(employee.employee_user_id.pk)

        for group_name in ("HR", "OPS"):
            try:
                group = Group.objects.get(name=group_name)
                group_users = group.user_set.exclude(pk__in=notified_pks)
                logger.info(
                    "[notify_overdue_checkups] group=%s additional user_ids=%s",
                    group_name,
                    list(group_users.values_list("pk", flat=True)),
                )
                if group_users.exists():
                    notify.send(
                        bot,
                        recipient=group_users,
                        verb=message,
                        verb_ar=message_ar,
                        verb_de=message_de,
                        verb_es=message_es,
                        verb_fr=message_fr,
                        redirect=reverse("asset-request-allocation-view"),
                        label="System",
                        icon="alert-circle",
                    )
                    notified_pks.update(group_users.values_list("pk", flat=True))
            except Group.DoesNotExist:
                logger.info(
                    "[notify_overdue_checkups] group=%s does not exist; skipping",
                    group_name,
                )

        # Send email notifications
        from asset.threading import CheckupMailThread
        from employee.models import Employee

        email_context = {
            "asset_name": asset.asset_name,
            "tracking_id": asset.asset_tracking_id,
            "assigned_to": employee.get_full_name(),
            "checkup_date": checkup_date,
            "service_shop": shop,
            "message": message,
        }
        all_notified_users = User.objects.filter(pk__in=notified_pks).exclude(
            pk=employee.employee_user_id.pk
        )
        email_recipients = [employee]
        if all_notified_users.exists():
            extra_employees = Employee.objects.filter(
                employee_user_id__in=all_notified_users
            )
            email_recipients.extend(list(extra_employees))
        logger.info(
            "[notify_overdue_checkups] dispatching email thread assignment=%s "
            "recipient_employees=%s emails=%s",
            assignment.pk,
            [str(e) for e in email_recipients],
            [e.get_mail() for e in email_recipients],
        )
        CheckupMailThread(email_recipients, email_context, is_overdue=True).start()

        assignment.last_overdue_notification_date = today
        assignment.save(update_fields=["last_overdue_notification_date"])
        logger.info(
            "[notify_overdue_checkups] flag set last_overdue_notification_date=%s assignment=%s",
            today,
            assignment.pk,
        )


def notify_overdue_checkups_recurring():
    """
    Sends a recurring 3-month follow-up notification for any asset assignment
    whose yearly check-up is overdue and has not been marked complete.

    A record receives the next reminder once 90 days have elapsed since its
    last reminder (or its check-up due date, if never reminded). The job
    stops sending reminders for an assignment as soon as it is marked
    Complete or returned.
    """
    logger.info("[notify_overdue_checkups_recurring] job started")

    from django.contrib.auth.models import User

    from asset.models import AssetAssignment
    from horilla.methods import horilla_users_with_perms

    today = date.today()
    threshold = today - timedelta(days=90)
    bot = _resolve_notification_bot()
    if not bot:
        logger.warning(
            "[notify_overdue_checkups_recurring] no usable bot/superuser found; aborting"
        )
        return

    from django.db.models import Q

    overdue_assignments = AssetAssignment.objects.filter(
        yearly_checkup_date__lte=today,
        checkup_completed=False,
        return_date__isnull=True,
    ).filter(
        Q(last_overdue_notification_date__isnull=True)
        | Q(last_overdue_notification_date__lte=threshold)
    )

    logger.info(
        "[notify_overdue_checkups_recurring] matched %s assignment(s)",
        overdue_assignments.count(),
    )

    for assignment in overdue_assignments:
        asset = assignment.asset_id
        employee = assignment.assigned_to_employee_id
        shop = assignment.service_shop_name or "N/A"
        checkup_date = assignment.yearly_checkup_date.strftime("%Y-%m-%d")

        message = (
            f"REMINDER: Yearly check-up for asset '{asset.asset_name}' "
            f"({asset.asset_tracking_id}) was due on {checkup_date} and is "
            f"still overdue. Service shop: {shop}."
        )

        notify.send(
            bot,
            recipient=employee.employee_user_id,
            verb=message,
            redirect=reverse("asset-request-allocation-view"),
            label="System",
            icon="alert-circle",
        )

        permed_users = horilla_users_with_perms("asset.view_assetassignment")
        if permed_users.exists():
            notify.send(
                bot,
                recipient=permed_users,
                verb=message,
                redirect=reverse("asset-request-allocation-view"),
                label="System",
                icon="alert-circle",
            )

        notified_pks = set(permed_users.values_list("pk", flat=True))
        notified_pks.add(employee.employee_user_id.pk)

        for group_name in ("HR", "OPS"):
            try:
                group = Group.objects.get(name=group_name)
                group_users = group.user_set.exclude(pk__in=notified_pks)
                if group_users.exists():
                    notify.send(
                        bot,
                        recipient=group_users,
                        verb=message,
                        redirect=reverse("asset-request-allocation-view"),
                        label="System",
                        icon="alert-circle",
                    )
                    notified_pks.update(
                        group_users.values_list("pk", flat=True)
                    )
            except Group.DoesNotExist:
                pass

        assignment.last_overdue_notification_date = today
        assignment.save(update_fields=["last_overdue_notification_date"])


if not any(
    cmd in sys.argv
    for cmd in ["makemigrations", "migrate", "compilemessages", "flush", "shell"]
):
    logger.info(
        "[asset.scheduler] registering background jobs (argv=%s)", sys.argv
    )
    try:
        scheduler = BackgroundScheduler()
        scheduler.add_job(notify_expiring_assets, "interval", hours=4)
        scheduler.add_job(notify_expiring_documents, "interval", hours=4)
        scheduler.add_job(notify_upcoming_checkups, "interval", hours=4)
        scheduler.add_job(notify_overdue_checkups, "interval", hours=4)
        scheduler.add_job(notify_overdue_checkups_recurring, "interval", hours=24)
        scheduler.start()
        logger.info(
            "[asset.scheduler] BackgroundScheduler started with %s job(s): %s",
            len(scheduler.get_jobs()),
            [j.func_ref or j.id for j in scheduler.get_jobs()],
        )
    except Exception:
        logger.exception("[asset.scheduler] failed to start BackgroundScheduler")
else:
    logger.info(
        "[asset.scheduler] skipping scheduler start due to management command (argv=%s)",
        sys.argv,
    )
