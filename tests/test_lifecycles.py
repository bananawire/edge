"""Phase 3 lifecycles: alert delivery to the device, presence via outbox, command generations, heartbeat."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from alerting.application.alert_poller import AlertIncidentPoller
from alerting.application.services.alert_incident_event_application_service import (
    AlertIncidentEventApplicationService,
)
from alerting.infrastructure.models import AlertIncidentEventModel
from device.application.services import DeviceCommandApplicationService
from device.domain.commands import AcknowledgeEmbeddedDeviceCommandCommand
from device.domain.entities import EdgeDeviceCommandStatus
from device.infrastructure.models import DeviceCommandModel
from device.infrastructure.outbox.outbox_record_model import OutboxRecordModel
from device.infrastructure.outbox.outbox_payload_snapshot_model import OutboxPayloadSnapshotModel
from iam.application.services import DevicePresenceApplicationService
from iam.infrastructure.models import DeviceModel
from iam.infrastructure.repositories import DeviceRepository
from provisioning.application.services.device_provisioning_application_service import (
    DeviceProvisioningApplicationService,
)
from shared.infrastructure.models import SyncWatermarkModel


def _device(assignment_id="gen-1", status="ONLINE"):
    return DeviceModel.create(device_id="dev-1", hardware_id="CLAIR-0001", api_key="k", status=status,
                              created_at=datetime.now(timezone.utc), assignment_id=assignment_id,
                              last_seen_at=datetime.now(timezone.utc))


def _transition(sequence, status="ACTIVE"):
    return {"alert_id": "alert-1", "sequence": sequence, "device_id": "dev-1", "hardware_id": "CLAIR-0001",
            "metric": "CO2", "status": status, "occurred_at": "2026-09-11T14:35:00Z"}


class TestAlertDeliveryToDevice:
    def test_transitions_are_redelivered_until_acked_and_acks_are_per_transition(self, db, monkeypatch):
        monkeypatch.setenv("EDGE_ALERT_DELIVERY_LEASE_SECONDS", "5")
        service = AlertIncidentEventApplicationService()
        service.ingest_alert_incident_changed_event(_transition(5))
        service.ingest_alert_incident_changed_event(_transition(9, "RESOLVED"))
        first = service.get_pending_for_embedded("CLAIR-0001")
        assert [e["sequence"] for e in first] == [5, 9]
        # Within the lease nothing is offered again...
        assert service.get_pending_for_embedded("CLAIR-0001") == []
        # ...after it, both come back because the device never acked.
        AlertIncidentEventModel.update(delivered_at=datetime.now(timezone.utc) - timedelta(seconds=10)).execute()
        again = service.get_pending_for_embedded("CLAIR-0001")
        assert [e["sequence"] for e in again] == [5, 9]
        # Acking the old transition does not hide the newer one.
        service.acknowledge_for_embedded(first[0]["id"], "CLAIR-0001")
        AlertIncidentEventModel.update(delivered_at=datetime.now(timezone.utc) - timedelta(seconds=10)).execute()
        assert [e["sequence"] for e in service.get_pending_for_embedded("CLAIR-0001")] == [9]
        service.acknowledge_for_embedded(first[1]["id"], "CLAIR-0001")
        service.acknowledge_for_embedded(first[1]["id"], "CLAIR-0001")  # idempotent
        AlertIncidentEventModel.update(delivered_at=datetime.now(timezone.utc) - timedelta(seconds=10)).execute()
        assert service.get_pending_for_embedded("CLAIR-0001") == []

    def test_poller_cursor_survives_restart_and_a_failed_receipt_does_not_advance_it(self, db):
        class Client:
            def __init__(self, fail_receipt=False): self.fail_receipt = fail_receipt; self.calls = []
            def get(self, path, params=None):
                self.calls.append(params["after_sequence"])
                return [_transition(3), _transition(4, "RESOLVED")] if params["after_sequence"] is None else []
            def post(self, path, body, accept_conflict=False):
                return None if self.fail_receipt else {}
        service = AlertIncidentEventApplicationService()
        failing = AlertIncidentPoller(Client(fail_receipt=True), service)
        failing.poll_once()
        assert failing.cursor is None, "no receipt reached core, so core will offer these again"
        assert AlertIncidentEventModel.select().count() == 2, "stored anyway; the re-offer is deduplicated"
        healthy = AlertIncidentPoller(Client(), service)
        healthy.poll_once()
        assert healthy.cursor == 4
        assert SyncWatermarkModel.get(SyncWatermarkModel.resource == "alerts").value == "4"
        assert AlertIncidentEventModel.select().count() == 2
        restarted = AlertIncidentPoller(Client(), service)
        assert restarted.cursor == 4


class TestPresenceOutbox:
    def test_presence_transitions_are_queued_and_coalesced_per_device(self, db):
        _device(status="OFFLINE")
        service = DevicePresenceApplicationService()
        service.mark_seen("CLAIR-0001")               # OFFLINE -> ONLINE: queued
        service.mark_seen("CLAIR-0001")               # already ONLINE: nothing new
        rows = list(OutboxRecordModel.select().where(OutboxRecordModel.aggregate_type == "PRESENCE"))
        assert len(rows) == 1
        payload = json.loads(OutboxPayloadSnapshotModel.get(OutboxPayloadSnapshotModel.outbox_id == rows[0].id).payload)
        assert payload["status"] == "ONLINE" and payload["device_id"] == "dev-1" and payload["hardware_id"] == "CLAIR-0001"
        service.mark_stale_devices_offline(datetime.now(timezone.utc) + timedelta(seconds=1))
        rows = list(OutboxRecordModel.select().where(OutboxRecordModel.aggregate_type == "PRESENCE"))
        assert len(rows) == 1, "the undelivered ONLINE is replaced by the newer OFFLINE"
        payload = json.loads(OutboxPayloadSnapshotModel.get(OutboxPayloadSnapshotModel.outbox_id == rows[0].id).payload)
        assert payload["status"] == "OFFLINE"

    def test_a_poll_heartbeat_refreshes_last_seen_without_changing_status(self, db):
        device = _device(status="STANDBY")
        stale = datetime.now(timezone.utc) - timedelta(minutes=10)
        DeviceModel.update(last_seen_at=stale).execute()
        DevicePresenceApplicationService().touch("CLAIR-0001")
        row = DeviceModel.get()
        assert row.status == "STANDBY"
        assert row.last_seen_at.replace(tzinfo=timezone.utc) > stale
        assert OutboxRecordModel.select().count() == 0
        # A standby unit that keeps polling is never declared OFFLINE.
        offline_before = datetime.now(timezone.utc) - timedelta(seconds=30)
        assert DevicePresenceApplicationService().mark_stale_devices_offline(offline_before) == 0


class TestCommandGenerations:
    def _cached(self, command_id, assignment_id, status=EdgeDeviceCommandStatus.RECEIVED):
        DeviceCommandModel.create(command_id=command_id, device_id="dev-1", hardware_id="CLAIR-0001",
                                  assignment_id=assignment_id, command_type="STANDBY", status=status.value,
                                  received_at=datetime.now(timezone.utc))

    def test_roster_reporting_a_new_generation_voids_cached_commands_of_the_old_one(self, db):
        _device(assignment_id="gen-1")
        self._cached("old-received", "gen-1")
        self._cached("old-delivered", "gen-1", EdgeDeviceCommandStatus.DELIVERED_TO_EMBEDDED)
        self._cached("old-done", "gen-1", EdgeDeviceCommandStatus.EXECUTED)
        self._cached("legacy-unbound", None)
        DeviceProvisioningApplicationService().sync_from_roster([{
            "device_id": "dev-1", "hardware_id": "CLAIR-0001", "api_key": "k", "status": "OFFLINE",
            "assignment_id": "gen-2", "updated_at": "2026-09-11T15:00:00+00:00",
        }])
        statuses = {row.command_id: row.status for row in DeviceCommandModel.select()}
        assert statuses["old-received"] == "EXPIRED" and statuses["old-delivered"] == "EXPIRED"
        assert statuses["old-done"] == "EXECUTED" and statuses["legacy-unbound"] == "RECEIVED"
        assert DeviceModel.get().assignment_id == "gen-2"

    def test_only_current_generation_commands_reach_the_device_and_expired_acks_are_not_forwarded(self, db):
        _device(assignment_id="gen-2")
        self._cached("stale", "gen-1")
        self._cached("current", "gen-2")
        service = DeviceCommandApplicationService()
        delivered = service.get_pending_commands_for_embedded("CLAIR-0001")
        assert [c.command_id for c in delivered] == ["current"]
        DeviceCommandModel.update(status="EXPIRED").where(DeviceCommandModel.command_id == "stale").execute()
        result = service.acknowledge_embedded_command(
            AcknowledgeEmbeddedDeviceCommandCommand("CLAIR-0001", "stale", "EXECUTED", None))
        assert result.status == EdgeDeviceCommandStatus.EXPIRED
        assert OutboxRecordModel.select().where(OutboxRecordModel.aggregate_type == "COMMAND").count() == 0

    def test_ingest_drops_a_command_for_a_generation_the_roster_no_longer_reports(self, db):
        _device(assignment_id="gen-2")
        ingested = DeviceCommandApplicationService().ingest_command_messages([
            {"command_id": "c1", "device_id": "dev-1", "command_type": "WAKE", "assignment_id": "gen-1"},
            {"command_id": "c2", "device_id": "dev-1", "command_type": "WAKE", "assignment_id": "gen-2"},
        ])
        assert [c.command_id for c in ingested] == ["c2"]
        assert DeviceCommandModel.get().assignment_id == "gen-2"
