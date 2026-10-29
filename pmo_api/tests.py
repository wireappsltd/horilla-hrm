from django.core.cache import cache
from django.test import TestCase, override_settings

KEY = {"HTTP_X_API_KEY": "test-key"}


@override_settings(PMO_API_KEY="test-key", PMO_THROTTLE_RATE="3/min")
class PMOApiErrorTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_unauthorised_is_json_401(self):
        r = self.client.get("/api/pmo/v1/employees/")
        self.assertEqual(r.status_code, 401)
        self.assertIn("application/json", r["Content-Type"])

    def test_unknown_path_is_json_404(self):
        r = self.client.get("/api/pmo/v1/nope/")
        self.assertEqual(r.status_code, 404)
        self.assertIn("application/json", r["Content-Type"])

    def test_throttle_returns_json_429_with_retry_after(self):
        for _ in range(3):
            self.client.get("/api/pmo/v1/leave-types/", **KEY)
        r = self.client.get("/api/pmo/v1/leave-types/", **KEY)
        self.assertEqual(r.status_code, 429)
        self.assertIn("application/json", r["Content-Type"])
        self.assertIn("Retry-After", r)
        self.assertEqual(r.json()["code"], "throttled")

    def test_deleted_feed_ok(self):
        r = self.client.get("/api/pmo/v1/deleted/", **KEY)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["results"], [])


@override_settings(PMO_API_KEY="test-key", PMO_THROTTLE_RATE="1000/min")
class PMOEmployeeQueryTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_employee_list_and_search_run(self):
        for qs in ("", "?include_inactive=true", "?search=a%20b"):
            r = self.client.get("/api/pmo/v1/employees/" + qs, **KEY)
            self.assertEqual(r.status_code, 200, r.content)

    def test_deletion_triggers_installed(self):
        from django.db import connection

        with connection.cursor() as cur:
            if connection.vendor == "postgresql":
                cur.execute(
                    "SELECT tgname FROM pg_trigger WHERE tgname LIKE 'pmo_tombstone_%'"
                )
            elif connection.vendor == "sqlite":
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND name LIKE 'pmo_tombstone_%'"
                )
            else:
                self.skipTest("triggers only on sqlite/postgresql")
            names = {row[0] for row in cur.fetchall()}
        self.assertEqual(names, {"pmo_tombstone_employee", "pmo_tombstone_leave"})

    def test_tombstone_is_unique(self):
        from django.db import IntegrityError, transaction

        from pmo_api.models import PMODeletedRecord

        PMODeletedRecord.objects.create(resource="leave", record_id=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PMODeletedRecord.objects.create(resource="leave", record_id=1)
