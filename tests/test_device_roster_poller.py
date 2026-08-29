import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from iam.application.services import AuthApplicationService
from iam.infrastructure.models import DeviceModel
from provisioning.application.services.device_provisioning_application_service import DeviceProvisioningApplicationService
from provisioning.infrastructure.device_cache_repository import DeviceCacheRepository
from shared.infrastructure.database import db

from provisioning.application.device_roster_poller import DeviceRosterPoller


class DeviceRosterPollerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.connect(reuse_if_open=True)
        db.create_tables([DeviceModel], safe=True)

    @classmethod
    def tearDownClass(cls):
        DeviceModel.delete().execute()
        db.close()

    def setUp(self):
        DeviceModel.delete().execute()

    def test_failed_page_does_not_advance_watermark(self):
        client = Mock()
        client.get.side_effect = [
            {"devices": [{"device_id": "a"}], "has_more": True, "next_since": "s1", "next_after_id": "a"},
            None,
        ]
        service = Mock()
        poller = DeviceRosterPoller(client, service)
        poller.watermark = "start"
        self.assertFalse(poller.sync_once())
        self.assertEqual(poller.watermark, "start")
        self.assertEqual(service.sync_from_roster.call_count, 1)

    def test_replaying_roster_is_safe_for_idempotent_service(self):
        client = Mock()
        page = {"devices": [{"device_id": "a"}], "has_more": False, "watermark": "w1"}
        client.get.return_value = page
        service = Mock()
        poller = DeviceRosterPoller(client, service)
        self.assertTrue(poller.sync_once())
        self.assertTrue(poller.sync_once())
        self.assertEqual(service.sync_from_roster.call_count, 2)
        self.assertEqual(poller.watermark, "w1")

    def test_tombstone_is_forwarded_to_application_service(self):
        client = Mock()
        client.get.return_value = {
            "devices": [{"device_id": "a", "deleted": True, "updated_at": "w2"}],
            "has_more": False,
            "watermark": "w2",
        }
        service = Mock()
        poller = DeviceRosterPoller(client, service)
        self.assertTrue(poller.sync_once())
        self.assertEqual(service.sync_from_roster.call_args.args[0][0]["deleted"], True)

    def test_real_service_persists_unknown_tombstone_and_rejects_stale_active_record(self):
        service = DeviceProvisioningApplicationService()
        tombstone = {"device_id": "a", "hardware_id": "h", "api_key": "k", "status": "OFFLINE", "deleted": True, "updated_at": "2026-01-02T00:00:00+00:00"}
        service.sync_from_roster([tombstone])
        stale = dict(tombstone, deleted=False, status="ONLINE", updated_at="2026-01-01T00:00:00+00:00")
        service.sync_from_roster([stale])
        row = DeviceModel.get_by_id("a")
        self.assertTrue(row.deleted)
        self.assertEqual(row.status, "OFFLINE")
        self.assertFalse(AuthApplicationService().authenticate("h", "k"))


if __name__ == "__main__":
    unittest.main()
