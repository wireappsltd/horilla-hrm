"""
Leave approval balance tests.

Run with:  python manage.py test leave.tests -v 2
"""

from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

from employee.models import Employee
from horilla.horilla_middlewares import _thread_locals
from leave.models import AvailableLeave, LeaveRequest, LeaveType


def _clear_request_local():
    # HorillaModel.save() reads the current request from a thread-local.
    _thread_locals.request = None


class ApproveUsesBalanceLeavesTests(TestCase):
    """
    Regression: 14 annual + 3 carryforward = 17. The employee used all 3 CF
    days and 12 annual days (15 total), then requested 2 more days. The leave
    card and request form showed a balance of 2, but approval failed because
    it checked the stored available_days/carryforward_days counters, which
    had drifted to 0.
    """

    @classmethod
    def setUpTestData(cls):
        _clear_request_local()
        cls.year = date.today().year
        cls.employee = Employee(
            badge_id="LV-1",
            employee_first_name="Leave",
            employee_last_name="Tester",
            email="leave.tester@example.com",
            phone="0700000001",
        )
        cls.employee.save()
        cls.leave_type = LeaveType(
            name="Annual Test",
            payment="paid",
            total_days=14,
            limit_leave=True,
            carryforward_type="carryforward",
            carryforward_max=3,
        )
        cls.leave_type.save()

        available, _ = AvailableLeave.objects.get_or_create(
            employee_id=cls.employee, leave_type_id=cls.leave_type
        )
        # Drifted stored counters: they should read 2 available / 0 CF.
        AvailableLeave.objects.filter(pk=available.pk).update(
            available_days=0, carryforward_days=0
        )

        def leave(day_from, day_to, days, status, cf=0, av=0):
            return LeaveRequest(
                employee_id=cls.employee,
                leave_type_id=cls.leave_type,
                start_date=date(cls.year, *day_from),
                end_date=date(cls.year, *day_to),
                start_date_breakdown="full_day",
                end_date_breakdown="full_day",
                requested_days=days,
                status=status,
                approved_carryforward_days=cf,
                approved_available_days=av,
                description="balance test",
            )

        # bulk_create skips save()/signals so the stored counters stay drifted.
        LeaveRequest.objects.bulk_create(
            [
                leave((2, 2), (2, 4), 3, "approved", cf=3),
                leave((5, 4), (5, 15), 12, "approved", av=12),
                leave((11, 5), (11, 6), 2, "requested"),
            ]
        )
        cls.pending = LeaveRequest.objects.get(
            employee_id=cls.employee, status="requested"
        )

    def setUp(self):
        _clear_request_local()
        self.available = AvailableLeave.objects.get(
            employee_id=self.employee, leave_type_id=self.leave_type
        )

    def test_balance_leaves_excludes_pending_request(self):
        # 14 + 0 CF left + 3 CF used - 15 taken - 2 pending
        self.assertEqual(self.available.balance_leaves(), 0)

    def test_approvable_balance_adds_back_the_request_itself(self):
        self.assertEqual(self.available.approvable_balance(self.pending), 2)

    def test_can_approve_despite_drifted_stored_counters(self):
        self.assertEqual(
            self.available.available_days + self.available.carryforward_days, 0
        )
        self.assertTrue(self.available.can_approve(self.pending))

    def test_cannot_approve_more_than_balance(self):
        self.pending.requested_days = 3
        self.assertFalse(self.available.can_approve(self.pending))

    def test_other_pending_requests_still_count(self):
        LeaveRequest.objects.bulk_create(
            [
                LeaveRequest(
                    employee_id=self.employee,
                    leave_type_id=self.leave_type,
                    start_date=date(self.year, 12, 1),
                    end_date=date(self.year, 12, 1),
                    start_date_breakdown="full_day",
                    end_date_breakdown="full_day",
                    requested_days=1,
                    status="requested",
                    description="other pending",
                )
            ]
        )
        self.assertEqual(self.available.approvable_balance(self.pending), 1)
        self.assertFalse(self.available.can_approve(self.pending))

    def test_unlimited_leave_type_always_approvable(self):
        LeaveType.objects.filter(pk=self.leave_type.pk).update(limit_leave=False)
        self.available.refresh_from_db()
        self.pending.requested_days = 50
        self.assertTrue(self.available.can_approve(self.pending))

    def test_approve_view_approves_the_request(self):
        admin = User.objects.create_superuser(
            "leave-admin", "leave.admin@example.com", "pw"
        )
        _clear_request_local()
        admin_employee = Employee(
            badge_id="LV-ADMIN",
            employee_first_name="Leave",
            employee_last_name="Admin",
            email="leave.admin@example.com",
            phone="0700000002",
            employee_user_id=admin,
        )
        admin_employee.save()
        _clear_request_local()
        self.client.force_login(admin)

        self.client.get(f"/leave/request-approve/{self.pending.id}")
        _clear_request_local()

        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, "approved")
