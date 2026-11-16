"""
Tests for the PMO (Meridian) integration API under /api/pmo/.

Run with:  python manage.py test horilla_api.test_pmo -v 2
"""

import os
from datetime import date, datetime, timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from base.models import Department, Holidays, JobPosition
from employee.models import Employee, EmployeeWorkInformation
from horilla.horilla_middlewares import _thread_locals
from leave.models import LeaveRequest, LeaveType
from offboarding.models import OffboardingEmployee

EMPLOYEES = "/api/pmo/employees/"
LEAVES = "/api/pmo/leaves/"
LEAVE_TYPES = "/api/pmo/leave-types/"
HOLIDAYS = "/api/pmo/holidays/"

KEYS = "old-key, new-key"


def _clear_request_local():
    # Horilla stores the current request in a thread-local; after an
    # APIClient call it still holds a request whose user is None (API-key
    # auth), which breaks HorillaModel.save() in the same thread.
    _thread_locals.request = None


def _make_employee(n, *, active=True):
    _clear_request_local()
    employee = Employee(
        badge_id=f"PMO-{n}",
        employee_first_name=f"First{n}",
        employee_last_name=f"Last{n}",
        email=f"pmo.test{n}@example.com",
        phone=f"07000000{n:02d}",
    )
    employee.save()
    if not active:
        Employee.objects.entire().filter(pk=employee.pk).update(is_active=False)
        employee.refresh_from_db()
    return employee


@override_settings(PMO_API_KEY=KEYS, ALLOWED_HOSTS=["testserver"])
class PMOTestCase(TestCase):
    """Base class: env key patched, thread-local request cleared."""

    def setUp(self):
        _clear_request_local()
        patcher = mock.patch.dict(os.environ, {"PMO_API_KEY": KEYS})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = APIClient()
        self.auth = {"HTTP_X_API_KEY": "new-key"}

    def get(self, url, params=None, **headers):
        response = self.client.get(url, params or {}, **(headers or self.auth))
        _clear_request_local()
        return response


class PMOAuthTests(PMOTestCase):
    def test_missing_key_is_rejected(self):
        for url in (EMPLOYEES, LEAVE_TYPES, HOLIDAYS + "?year=2026"):
            self.assertEqual(self.client.get(url).status_code, 401, url)

    def test_wrong_key_is_rejected(self):
        response = self.get(EMPLOYEES, HTTP_X_API_KEY="nope")
        self.assertEqual(response.status_code, 401)

    def test_both_rotation_keys_are_accepted(self):
        self.assertEqual(self.get(EMPLOYEES, HTTP_X_API_KEY="old-key").status_code, 200)
        self.assertEqual(
            self.get(EMPLOYEES, HTTP_AUTHORIZATION="Api-Key new-key").status_code, 200
        )

    def test_partial_key_is_rejected(self):
        self.assertEqual(self.get(EMPLOYEES, HTTP_X_API_KEY="old").status_code, 401)


class PMOEmployeeTests(PMOTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.active = _make_employee(1)
        cls.leaver = _make_employee(2, active=False)
        _clear_request_local()
        dept = Department(department="PMO Engineering")
        dept.save()
        job = JobPosition(job_position="PMO Backend Engineer", department_id=dept)
        job.save()
        EmployeeWorkInformation.objects.filter(employee_id=cls.active).delete()
        EmployeeWorkInformation.objects.bulk_create(
            [
                EmployeeWorkInformation(
                    employee_id=cls.active,
                    department_id=dept,
                    job_position_id=job,
                    date_joining=date(2024, 3, 1),
                )
            ]
        )
        OffboardingEmployee.objects.bulk_create(
            [OffboardingEmployee(employee_id=cls.leaver, last_working_date=date(2026, 10, 31))]
        )

    def test_active_employee_detail(self):
        response = self.get(f"{EMPLOYEES}{self.active.id}/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["is_active"])
        self.assertEqual(body["department"], "PMO Engineering")
        self.assertEqual(body["job_title"], "PMO Backend Engineer")
        self.assertEqual(body["date_joined"], "2024-03-01")
        self.assertIsNone(body["leaving_date"])
        self.assertIsNone(body["updated_at"])

    def test_inactive_employee_detail_returns_200_with_leaving_date(self):
        response = self.get(f"{EMPLOYEES}{self.leaver.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["is_active"])
        self.assertEqual(response.json()["leaving_date"], "2026-10-31")

    def test_unknown_employee_returns_404(self):
        missing = Employee.objects.entire().order_by("-id").first().id + 1000
        self.assertEqual(self.get(f"{EMPLOYEES}{missing}/").status_code, 404)

    def test_list_excludes_inactive_by_default(self):
        ids = {e["id"] for e in self.get(EMPLOYEES, {"limit": 100}).json()["results"]}
        self.assertIn(self.active.id, ids)
        self.assertNotIn(self.leaver.id, ids)

    def test_list_include_inactive(self):
        body = self.get(EMPLOYEES, {"include_inactive": "true", "limit": 100}).json()
        ids = {e["id"] for e in body["results"]}
        self.assertIn(self.leaver.id, ids)
        self.assertEqual(body["count"], Employee.objects.entire().count())
        self.assertTrue(
            {"is_active", "leaving_date", "updated_at", "date_joined"}
            <= set(body["results"][0])
        )

    def test_updated_since_not_supported_on_employees(self):
        response = self.get(EMPLOYEES, {"updated_since": "2026-01-01T00:00:00Z"})
        self.assertEqual(response.status_code, 400)

    def test_limit_clamped_and_offset_validated(self):
        self.assertEqual(self.get(EMPLOYEES, {"limit": 500}).json()["limit"], 100)
        self.assertEqual(self.get(EMPLOYEES, {"offset": -1}).status_code, 400)
        self.assertEqual(self.get(EMPLOYEES, {"limit": "x"}).status_code, 400)

    def test_search(self):
        body = self.get(EMPLOYEES, {"search": "PMO Backend"}).json()
        self.assertEqual([e["id"] for e in body["results"]], [self.active.id])


class PMOLeaveTests(PMOTestCase):
    WINDOW = {"start_date": "2026-10-01", "end_date": "2026-10-31", "limit": 100}

    @classmethod
    def setUpTestData(cls):
        cls.emp = _make_employee(11)
        cls.leaver = _make_employee(12, active=False)
        _clear_request_local()
        cls.paid = LeaveType(name="PMO Annual", payment="paid")
        cls.paid.save()
        cls.unpaid = LeaveType(name="PMO Unpaid", payment="unpaid")
        cls.unpaid.save()

        def mk(emp, start, end, status="approved", sb="full_day", eb="full_day", lt=None):
            _clear_request_local()
            leave = LeaveRequest(
                employee_id=emp,
                leave_type_id=lt or cls.paid,
                start_date=start,
                end_date=end,
                start_date_breakdown=sb,
                end_date_breakdown=eb,
                status=status,
                description="pmo test",
            )
            leave.save()
            return leave

        cls.half_day = mk(cls.emp, date(2026, 10, 20), date(2026, 10, 22), eb="first_half")
        cls.of_leaver = mk(cls.leaver, date(2026, 10, 1), date(2026, 10, 5))
        cls.ends_on_start = mk(cls.emp, date(2026, 9, 28), date(2026, 10, 1), status="rejected")
        cls.starts_on_end = mk(
            cls.emp, date(2026, 10, 31), date(2026, 11, 2), status="requested", lt=cls.unpaid
        )
        cls.spanning = mk(cls.emp, date(2026, 9, 1), date(2026, 11, 30))
        cls.outside = mk(cls.emp, date(2026, 11, 5), date(2026, 11, 6))
        cls.to_cancel = mk(cls.emp, date(2026, 10, 10), date(2026, 10, 10))

    def ids(self, params):
        response = self.get(LEAVES, params)
        self.assertEqual(response.status_code, 200, response.content)
        return {r["id"] for r in response.json()["results"]}

    def test_overlap_window(self):
        self.assertEqual(
            self.ids(self.WINDOW),
            {
                self.half_day.id,
                self.of_leaver.id,
                self.ends_on_start.id,
                self.starts_on_end.id,
                self.spanning.id,
                self.to_cancel.id,
            },
        )

    def test_leave_of_inactive_employee_is_returned(self):
        self.assertIn(self.of_leaver.id, self.ids(self.WINDOW))

    def test_payload_fields(self):
        rows = self.get(LEAVES, self.WINDOW).json()["results"]
        row = next(r for r in rows if r["id"] == self.half_day.id)
        self.assertEqual(row["employee_id"], self.emp.id)
        self.assertEqual(row["leave_type_id"], self.paid.id)
        self.assertEqual(row["leave_type"], "PMO Annual")
        self.assertEqual(row["start_date"], "2026-10-20")
        self.assertEqual(row["end_date"], "2026-10-22")
        self.assertEqual(row["start_breakdown"], "full_day")
        self.assertEqual(row["end_breakdown"], "first_half")
        self.assertEqual(row["status"], "approved")
        self.assertIsNotNone(datetime.fromisoformat(row["updated_at"]).tzinfo)

    def test_status_filters(self):
        self.assertEqual(
            self.ids({**self.WINDOW, "status": "requested,rejected"}),
            {self.ends_on_start.id, self.starts_on_end.id},
        )
        self.assertEqual(self.get(LEAVES, {**self.WINDOW, "status": "bogus"}).status_code, 400)

    def test_employee_filter(self):
        self.assertEqual(
            self.ids({**self.WINDOW, "employee_id": self.leaver.id}), {self.of_leaver.id}
        )
        self.assertEqual(self.get(LEAVES, {**self.WINDOW, "employee_id": "x"}).status_code, 400)

    def test_date_validation(self):
        self.assertEqual(self.get(LEAVES, {"start_date": "2026-10-01"}).status_code, 400)
        self.assertEqual(
            self.get(LEAVES, {"start_date": "2026-10-31", "end_date": "2026-10-01"}).status_code,
            400,
        )
        self.assertEqual(
            self.get(LEAVES, {"start_date": "2026-13-01", "end_date": "2026-10-31"}).status_code,
            400,
        )

    def test_updated_since_returns_cancelled_leave(self):
        cutoff = timezone.now()
        _clear_request_local()
        self.to_cancel.status = "cancelled"
        self.to_cancel.save()
        response = self.get(LEAVES, {**self.WINDOW, "updated_since": cutoff.isoformat()})
        rows = response.json()["results"]
        self.assertEqual([r["id"] for r in rows], [self.to_cancel.id])
        self.assertEqual(rows[0]["status"], "cancelled")

    def test_updated_since_validation(self):
        naive = {**self.WINDOW, "updated_since": "2026-10-01T00:00:00"}
        self.assertEqual(self.get(LEAVES, naive).status_code, 400)
        old = (timezone.now() - timedelta(days=1)).isoformat()
        self.assertEqual(len(self.ids({**self.WINDOW, "updated_since": old})), 6)

    def test_leave_types(self):
        rows = {r["id"]: r for r in self.get(LEAVE_TYPES).json()["results"]}
        self.assertTrue(rows[self.paid.id]["is_paid"])
        self.assertFalse(rows[self.unpaid.id]["is_paid"])
        self.assertEqual(rows[self.paid.id]["name"], "PMO Annual")


class PMOHolidayTests(PMOTestCase):
    @classmethod
    def setUpTestData(cls):
        _clear_request_local()

        def mk(name, start, end, recurring=False):
            holiday = Holidays(name=name, start_date=start, end_date=end, recurring=recurring)
            holiday.save()
            return holiday

        cls.xmas = mk("PMO Christmas", date(2020, 12, 25), date(2020, 12, 25), recurring=True)
        cls.leap = mk("PMO Leap", date(2024, 2, 29), date(2024, 2, 29), recurring=True)
        cls.this_year = mk("PMO Vesak", date(2026, 5, 1), date(2026, 5, 2))
        cls.other_year = mk("PMO 2025", date(2025, 5, 1), date(2025, 5, 1))
        cls.spans_new_year = mk("PMO NYE", date(2025, 12, 31), date(2026, 1, 1))
        cls.future_recurring = mk("PMO Future", date(2027, 1, 14), date(2027, 1, 14), True)

    def test_holidays_for_year(self):
        response = self.get(HOLIDAYS, {"year": 2026})
        self.assertEqual(response.status_code, 200)
        rows = {r["id"]: r for r in response.json()["results"]}
        self.assertEqual(rows[self.xmas.id]["start_date"], "2026-12-25")
        self.assertEqual(rows[self.xmas.id]["original_start_date"], "2020-12-25")
        self.assertEqual(rows[self.leap.id]["start_date"], "2026-02-28")
        self.assertEqual(rows[self.this_year.id]["end_date"], "2026-05-02")
        self.assertIn(self.spans_new_year.id, rows)
        self.assertNotIn(self.other_year.id, rows)
        self.assertNotIn(self.future_recurring.id, rows)
        self.assertTrue(all("company_id" in r and "company" in r for r in rows.values()))

    def test_year_validation(self):
        self.assertEqual(self.get(HOLIDAYS).status_code, 400)
        self.assertEqual(self.get(HOLIDAYS, {"year": "abc"}).status_code, 400)
