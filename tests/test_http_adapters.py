import json
import threading
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from peewee import IntegrityError, SqliteDatabase

from device.application.outboundservices.acl.http_core_context_facade import HttpCoreContextFacadeImpl
from device.application.outbox_processor import TelemetryOutboxProcessor
from device.application.services import DeviceCommandApplicationService, DeviceTelemetryAppService
from device.infrastructure.repositories import DeviceCommandRepository
from device.domain.entities import DeviceCommand, DeviceCommandType, EdgeDeviceCommandStatus
from device.domain.commands import (
    AcknowledgeEmbeddedDeviceCommandCommand,
    CreateFullTelemetryRecordCommand,
)
from device.domain.outbox_entry import OutboxEntry
from iam.application.outboundservices.acl.core_presence_http_publisher import CorePresenceHttpPublisher
from provisioning.application.device_roster_poller import DeviceRosterPoller
from alerting.application.alert_poller import AlertIncidentPoller
from shared.infrastructure.core_http_client import CoreHttpClient
from shared.infrastructure.environment import get_positive_interval
from device.infrastructure.outbox.outbox_repository import OutboxRepository
from device.infrastructure.outbox.outbox_record_model import OutboxRecordModel
from device.infrastructure.outbox.outbox_payload_snapshot_model import OutboxPayloadSnapshotModel

class Response:
    def __init__(self, status, body=b"{}"): self.status, self.body = status, body
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return self.body

class HttpAdapterTests(unittest.TestCase):
    def test_legacy_outbox_is_written_without_snapshot_column(self):
        entry = OutboxEntry("TELEMETRY", 7, "TELEMETRY_RECORDED", payload='{"v":1}')
        repository = OutboxRepository()
        fake_model = MagicMock(id=1, aggregate_type="TELEMETRY", aggregate_id="7",
                               event_type="TELEMETRY_RECORDED", status="pending",
                               retry_count=0, next_retry_at=entry.next_retry_at,
                               created_at=entry.created_at, sent_at=None,
                               error_message=None, payload=None)
        with patch("device.infrastructure.outbox.outbox_repository.db") as database, \
             patch.object(OutboxRecordModel, "create", return_value=fake_model) as create, \
             patch.object(OutboxPayloadSnapshotModel, "create", return_value=MagicMock(payload=entry.payload)) as snapshot, \
             patch.object(OutboxPayloadSnapshotModel, "delete") as delete_snapshot, \
             patch.object(OutboxPayloadSnapshotModel, "get_or_none", return_value=MagicMock(payload=entry.payload)):
            database.get_columns.return_value = [SimpleNamespace(name="id")]
            delete_snapshot.return_value.where.return_value.execute.return_value = 0
            repository.save(entry)
        self.assertNotIn("payload", create.call_args.kwargs)
        snapshot.assert_called_once_with(outbox_id=1, payload=entry.payload)
        delete_snapshot.assert_called_once()

    def test_sent_cleanup_removes_snapshots_and_orphans(self):
        repository = OutboxRepository()
        sent = SimpleNamespace(id=12)
        snapshot_delete = MagicMock()
        snapshot_delete.where.return_value.execute.return_value = 2
        record_delete = MagicMock()
        record_delete.where.return_value.execute.return_value = 1
        sent_query = MagicMock()
        sent_query.where.return_value = [sent]
        orphan_query = MagicMock()
        orphan_query.join.return_value = orphan_query
        orphan_query.where.return_value = orphan_query
        with patch.object(OutboxRecordModel, "select", return_value=sent_query), \
             patch.object(OutboxPayloadSnapshotModel, "select", return_value=orphan_query), \
             patch.object(OutboxPayloadSnapshotModel, "delete", return_value=snapshot_delete) as delete_snapshot, \
             patch.object(OutboxRecordModel, "delete", return_value=record_delete), \
             patch("device.infrastructure.outbox.outbox_repository.db.atomic"):
            deleted = repository.delete_sent_older_than(datetime.now(timezone.utc))
        self.assertEqual(deleted, 1)
        self.assertEqual(delete_snapshot.call_count, 2)
        self.assertEqual(snapshot_delete.where.return_value.execute.call_count, 2)

    def test_snapshot_rejects_null_id_and_cleanup_removes_orphan(self):
        test_db = SqliteDatabase(":memory:")
        models = [OutboxRecordModel, OutboxPayloadSnapshotModel]
        with test_db.bind_ctx(models):
            test_db.create_tables(models)
            with self.assertRaises(IntegrityError):
                OutboxPayloadSnapshotModel.create(outbox_id=None, payload="invalid")

            sent = OutboxRecordModel.create(
                aggregate_type="TELEMETRY", aggregate_id="7",
                event_type="TELEMETRY_RECORDED", status="sent",
                retry_count=0, next_retry_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                sent_at=datetime.now(timezone.utc),
            )
            OutboxPayloadSnapshotModel.create(outbox_id=sent.id, payload="sent")
            orphan = OutboxPayloadSnapshotModel.create(outbox_id=999, payload="orphan")

            with patch("device.infrastructure.outbox.outbox_repository.db", test_db):
                self.assertEqual(
                    OutboxRepository().delete_sent_older_than(datetime.now(timezone.utc)),
                    1,
                )
            self.assertIsNone(OutboxRecordModel.get_or_none(OutboxRecordModel.id == sent.id))
            self.assertIsNone(OutboxPayloadSnapshotModel.get_or_none(OutboxPayloadSnapshotModel.id == orphan.id))
            test_db.close()

    def test_save_replaces_snapshot_when_sqlite_reuses_outbox_id(self):
        entry = OutboxEntry("COMMAND", "command-1", "COMMAND_ACKNOWLEDGED", payload='{"v":2}')
        model = MagicMock(id=4, aggregate_type="COMMAND", aggregate_id="command-1",
                          event_type="COMMAND_ACKNOWLEDGED", status="pending", retry_count=0,
                          next_retry_at=entry.next_retry_at, created_at=entry.created_at,
                          sent_at=None, error_message=None, payload=None)
        snapshot_delete = MagicMock()
        with patch.object(OutboxRecordModel, "create", return_value=model), \
             patch.object(OutboxPayloadSnapshotModel, "delete", return_value=snapshot_delete), \
             patch.object(OutboxPayloadSnapshotModel, "create") as create_snapshot, \
             patch("device.infrastructure.outbox.outbox_repository.db.atomic"):
            snapshot_delete.where.return_value.execute.return_value = 1
            OutboxRepository().save(entry)
        snapshot_delete.where.return_value.execute.assert_called_once()
        create_snapshot.assert_called_once_with(outbox_id=4, payload=entry.payload)

    def test_telemetry_posts_batch_record(self):
        calls=[]
        def opener(request, timeout): calls.append(request); return Response(200, b'{"results":[{"status":"CREATED"}]}')
        self.assertTrue(HttpCoreContextFacadeImpl("http://localhost:8080", "secret", opener=opener).publish_telemetry_recorded({"device_id":"d"}))
        self.assertEqual(json.loads(calls[0].data), {"records":[{"device_id":"d"}]})

    def test_telemetry_always_enqueues_immutable_snapshot(self):
        recorded_at = datetime(2024, 1, 2, tzinfo=timezone.utc)
        record = SimpleNamespace(
            id=7, device_id="d", device_time="2024-01-02T00:00:00Z",
            uptime_seconds=3,
            air_quality=SimpleNamespace(co2=1, temperature=2, humidity=3),
            particulate_matter=SimpleNamespace(pm1_0=4, pm2_5=5, pm10=6),
            connectivity=SimpleNamespace(status="up", network="net", signal_strength=-1),
            location=SimpleNamespace(country="US"), health_status="ok", status="valid",
            recorded_at=recorded_at,
        )
        service = DeviceTelemetryAppService()
        command = CreateFullTelemetryRecordCommand(
            hardware_id="hw-1",
            device_time="2024-01-02T00:00:00Z",
            uptime="00:00:03",
            air_quality={"co2": 1, "temperature": 2, "humidity": 3},
            particulate_matter={"pm1_0": 4, "pm2_5": 5, "pm10": 6},
            connectivity={"status": "up", "network": "net", "signalStrength": -1},
            location={"country": "US"},
            health_status=100,
            status="valid",
        )
        service.device_repository = type("Devices", (), {"find_by_hardware_id": lambda *_: object()})()
        service.telemetry_service = type("Telemetry", (), {"create_record_from_command": lambda *_: record})()
        service.telemetry_repository = type("TelemetryRepo", (), {"save": lambda *_: record})()
        entries = []
        service.outbox_repository = type("Outbox", (), {"save": lambda _, entry: entries.append(entry)})()
        with patch("device.application.services.db.atomic"):
            service.create_full_telemetry_record(command, raw_payload=None)
        self.assertEqual(len(entries), 1)
        snapshot = json.loads(entries[0].payload)
        self.assertEqual(snapshot["recorded_at"], recorded_at.isoformat())
        self.assertEqual(snapshot["occurred_at"], recorded_at.isoformat())

    def test_duplicate_ack_is_idempotent_and_keeps_snapshot(self):
        command = DeviceCommand("c", "d", "h", DeviceCommandType.STANDBY, EdgeDeviceCommandStatus.DELIVERED_TO_EMBEDDED, None, datetime.now(timezone.utc))
        service = DeviceCommandApplicationService()
        service.command_repository = type("Repo", (), {
            "find_by_command_id": lambda self, _: command,
            "save": lambda self, value: value,
        })()
        entries = []
        service.outbox_repository = type("Outbox", (), {"save": lambda self, entry: entries.append(entry)})()
        first = service.acknowledge_embedded_command(AcknowledgeEmbeddedDeviceCommandCommand("h", "c", "EXECUTED", None))
        second = service.acknowledge_embedded_command(AcknowledgeEmbeddedDeviceCommandCommand("h", "c", "FAILED", "late"))
        self.assertEqual(first.status, EdgeDeviceCommandStatus.EXECUTED)
        self.assertEqual(second.status, EdgeDeviceCommandStatus.EXECUTED)
        self.assertEqual(len(entries), 1)
        self.assertIn('"status":"EXECUTED"', entries[0].payload)
        self.assertNotIn("acknowledged_at", entries[0].payload)

    def test_stale_poll_does_not_overwrite_concurrent_terminal_ack(self):
        stale = DeviceCommand(
            "c", "d", "h", DeviceCommandType.STANDBY,
            EdgeDeviceCommandStatus.DELIVERED_TO_EMBEDDED, None, datetime.now(timezone.utc),
        )
        terminal = DeviceCommand(
            "c", "d", "h", DeviceCommandType.STANDBY,
            EdgeDeviceCommandStatus.EXECUTED, None, stale.received_at,
        )
        update = MagicMock()
        update.where.return_value = update
        update.execute.return_value = 0  # ACK won the race before this update.
        repository = object.__new__(DeviceCommandRepository)
        repository.find_by_command_id = MagicMock(return_value=terminal)
        with patch("device.infrastructure.repositories.DeviceCommandModel.update", return_value=update) as conditional_update, \
             patch("device.infrastructure.repositories.db.atomic"):
            result = repository.mark_commands_delivered([stale])

        self.assertEqual(result, [])
        conditional_update.assert_called_once()
        update.execute.assert_called_once_with()

    def test_ack_before_embedded_delivery_is_rejected(self):
        command = DeviceCommand("c", "d", "h", DeviceCommandType.STANDBY, EdgeDeviceCommandStatus.RECEIVED, None, datetime.now(timezone.utc))
        service = DeviceCommandApplicationService()
        service.command_repository = type("Repo", (), {
            "find_by_command_id": lambda self, _: command,
            "save": lambda self, value: value,
        })()
        entries = []
        service.outbox_repository = type("Outbox", (), {"save": lambda self, entry: entries.append(entry)})()
        with self.assertRaises(ValueError):
            service.acknowledge_embedded_command(AcknowledgeEmbeddedDeviceCommandCommand("h", "c", "EXECUTED", None))
        self.assertEqual(command.status, EdgeDeviceCommandStatus.RECEIVED)
        self.assertEqual(entries, [])

    def test_command_ack_maps_core_contract(self):
        calls=[]
        def opener(request, timeout): calls.append(request); return Response(200)
        self.assertTrue(HttpCoreContextFacadeImpl(opener=opener).publish_command_acknowledged({"command_id":"a", "hardware_id":"h", "status":"EXECUTED", "failure_reason":None}))
        body=json.loads(calls[0].data); self.assertEqual(body["result"], "OK"); self.assertNotIn("status", body)

    def test_command_ack_treats_conflict_as_success(self):
        def opener(request, timeout): raise HTTPError(request.full_url, 409, "already acknowledged", {}, None)
        self.assertTrue(HttpCoreContextFacadeImpl(opener=opener).publish_command_acknowledged({"command_id":"a"}))

    def test_core_client_uses_core_token_for_pull(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            return Response(200, b'[]')
        self.assertEqual(CoreHttpClient("http://localhost:8080", "secret", opener=opener).get("/pending"), [])
        self.assertEqual(calls[0].headers["X-core-token"], "secret")

    def test_command_poller_ingests_without_acknowledging(self):
        class Client:
            def __init__(self): self.acks = []
            def get(self, path):
                return [{"command_id": "c", "device_id": "d", "hardware_id": "h", "command_type": "PING"}]
            def post(self, path, body, accept_conflict=False):
                self.acks.append((path, body, accept_conflict))
                return {}
        class Command:
            command_id = "c"
            hardware_id = "h"
        class Service:
            def ingest_command_messages(self, messages): return [Command()]
        client = Client()
        from device.application.command_poller import DeviceCommandPoller
        self.assertEqual(DeviceCommandPoller(client, Service()).poll_once(), 1)
        self.assertEqual(client.acks, [])

    def test_alert_poller_ingests_each_payload(self):
        class Client:
            def get(self, path):
                self.path = path
                return [{"alert_id": "a", "device_id": "d", "hardware_id": "h", "occurred_at": "2024-01-01T00:00:00Z"}]
            def post(self, path, body, accept_conflict=False):
                self.ack = (path, body, accept_conflict)
                return {}
        class Service:
            def ingest_alert_incident_changed_event(self, payload):
                self.payload = payload
                return type("Result", (), {"stored": True})()
        service = Service()
        client = Client()
        self.assertEqual(AlertIncidentPoller(client, service).poll_once(), 1)
        self.assertEqual(service.payload["alert_id"], "a")
        self.assertEqual(client.ack[0], "/api/v1/edge/alerts/a/ack")
        self.assertEqual(client.ack[1]["hardware_id"], "h")
        self.assertTrue(client.ack[2])

    def test_notify_auth_validation_and_async_resource_triggers(self):
        import os
        import app as edge_app
        class Spy:
            def __init__(self): self.calls = 0
            def trigger(self): self.calls += 1
        original = (edge_app._initialized, edge_app._device_roster_poller,
                    edge_app._command_poller, edge_app._alert_poller)
        device, command, alert = Spy(), Spy(), Spy()
        edge_app._initialized = True
        edge_app._device_roster_poller, edge_app._command_poller, edge_app._alert_poller = device, command, alert
        previous = os.environ.get("EDGE_TOKEN")
        os.environ["EDGE_TOKEN"] = "edge-secret"
        try:
            client = edge_app.app.test_client()
            self.assertEqual(client.post("/api/v1/edge/notify", json={"resource": "device"}).status_code, 401)
            self.assertEqual(client.post("/api/v1/edge/notify", headers={"X-Edge-Token": "edge-secret"}, json={"resource": "devices"}).status_code, 400)
            response = client.post("/api/v1/edge/notify", headers={"X-Edge-Token": "edge-secret"}, json={"resource": "command"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(command.calls, 1)
            self.assertEqual(alert.calls, 0)
        finally:
            if previous is None: os.environ.pop("EDGE_TOKEN", None)
            else: os.environ["EDGE_TOKEN"] = previous
            (edge_app._initialized, edge_app._device_roster_poller,
             edge_app._command_poller, edge_app._alert_poller) = original

    def test_core_client_normal_409_is_idempotent_success(self):
        def opener(request, timeout):
            return Response(409)
        self.assertEqual(CoreHttpClient("http://localhost:8080", opener=opener).post("/ack", accept_conflict=True), {})

    def test_invalid_worker_interval_falls_back_and_clamps(self):
        import os
        previous = os.environ.get("EDGE_TEST_INTERVAL")
        try:
            os.environ["EDGE_TEST_INTERVAL"] = "not-a-number"
            self.assertEqual(get_positive_interval("EDGE_TEST_INTERVAL", 5), 5)
            os.environ["EDGE_TEST_INTERVAL"] = "0"
            self.assertEqual(get_positive_interval("EDGE_TEST_INTERVAL", 5), 0.1)
            for invalid in ("nan", "inf", "-inf"):
                os.environ["EDGE_TEST_INTERVAL"] = invalid
                self.assertEqual(get_positive_interval("EDGE_TEST_INTERVAL", 5), 5)
        finally:
            if previous is None:
                os.environ.pop("EDGE_TEST_INTERVAL", None)
            else:
                os.environ["EDGE_TEST_INTERVAL"] = previous

    def test_presence_failure_is_best_effort(self):
        def opener(request, timeout): raise TimeoutError("offline")
        self.assertFalse(CorePresenceHttpPublisher(opener=opener).publish_device_presence_changed({"status":"ONLINE"}))

    def test_roster_uses_server_cursor_for_next_page(self):
        class Client:
            def __init__(self): self.calls=[]
            def get(self, path, params):
                self.calls.append(params)
                return ({"devices":[{"device_id":"1"}],"has_more":True,"next_since":"s1","next_after_id":"id1"} if len(self.calls)==1 else {"devices":[{"device_id":"2"}],"has_more":False,"watermark":"final"})
        class Service:
            def __init__(self): self.pages=[]
            def sync_from_roster(self, page): self.pages.append(page)
        client, service = Client(), Service(); poller=DeviceRosterPoller(client, service)
        self.assertTrue(poller.sync_once()); self.assertEqual(client.calls[1]["since"],"s1"); self.assertEqual(client.calls[1]["afterId"],"id1"); self.assertEqual(poller.watermark,"final")

    def test_roster_thread_survives_sync_exception(self):
        poller=DeviceRosterPoller(object(), object()); poller.sync_once=lambda: (_ for _ in ()).throw(RuntimeError("db")); poller.POLL_INTERVAL_SECONDS=0; poller._running=True
        thread=threading.Thread(target=poller._run); thread.start(); threading.Event().wait(.02); poller.stop(); thread.join(1)
        self.assertFalse(thread.is_alive())

    def test_legacy_command_outbox_is_quarantined_without_reconstructing(self):
        processor = TelemetryOutboxProcessor.__new__(TelemetryOutboxProcessor)
        processor.command_repository = type("Repo", (), {"find_by_command_id": lambda *_: (_ for _ in ()).throw(AssertionError("mutable state read"))})()
        processor.outbox_repository = type("R", (), {"mark_dead_letter": lambda self, *args: setattr(self, "dead_lettered", args)})()
        entry = type("E", (), {"id": 1, "retry_count": 0, "aggregate_type": "COMMAND", "aggregate_id": "missing", "event_type": "COMMAND_ACKNOWLEDGED", "payload": None})()
        self.assertFalse(processor._send_entry(entry))
        self.assertEqual(processor.outbox_repository.dead_lettered[0], 1)

    def test_delivery_claims_batch_in_one_transaction(self):
        repository = object.__new__(DeviceCommandRepository)
        with patch("device.infrastructure.repositories.db.atomic") as atomic:
            repository.mark_commands_delivered([])
        atomic.assert_called_once_with()

    def test_poll_returns_claimed_snapshot_when_ack_interleaves(self):
        command = DeviceCommand(
            "c", "d", "h", DeviceCommandType.STANDBY,
            EdgeDeviceCommandStatus.RECEIVED, None, datetime.now(timezone.utc),
        )
        update = MagicMock()
        update.where.return_value = update
        update.execute.return_value = 1
        repository = object.__new__(DeviceCommandRepository)
        with patch("device.infrastructure.repositories.DeviceCommandModel.update", return_value=update), \
             patch("device.infrastructure.repositories.db.atomic"):
            result = repository.mark_commands_delivered([command])

        self.assertEqual(result, [command])
        self.assertEqual(result[0].status, EdgeDeviceCommandStatus.DELIVERED_TO_EMBEDDED)
        # No post-claim read can expose an ACK that interleaved with this claim.

    def test_legacy_telemetry_outbox_is_quarantined_without_reconstructing(self):
        processor = TelemetryOutboxProcessor.__new__(TelemetryOutboxProcessor)
        processor.telemetry_repository = type("Repo", (), {"find_by_id": lambda *_: (_ for _ in ()).throw(AssertionError("mutable state read"))})()
        processor.outbox_repository = type("R", (), {"mark_dead_letter": lambda self, *args: setattr(self, "dead_lettered", args)})()
        entry = type("E", (), {"id": 9, "aggregate_type": "TELEMETRY", "aggregate_id": "7", "event_type": "TELEMETRY_RECORDED", "payload": None})()
        self.assertFalse(processor._send_entry(entry))
        self.assertEqual(processor.outbox_repository.dead_lettered[0], 9)


    def test_outbox_snapshot_is_not_rebuilt_from_command(self):
        processor = TelemetryOutboxProcessor.__new__(TelemetryOutboxProcessor)
        expected = {"command_id": "c", "status": "EXECUTED"}
        entry = OutboxEntry("COMMAND", "c", "COMMAND_ACKNOWLEDGED", payload=json.dumps(expected))
        processor.command_repository = type("Repo", (), {"find_by_command_id": lambda *_: (_ for _ in ()).throw(AssertionError("mutable state read"))})()
        self.assertEqual(processor._build_payload(entry), expected)

    def test_outbox_false_delivery_is_retry(self):
        processor=TelemetryOutboxProcessor.__new__(TelemetryOutboxProcessor)
        from device.infrastructure.reliability.circuit_breaker import CircuitBreaker
        processor.circuit_breaker=CircuitBreaker(); processor.external_core_service=type("S",(),{"publish_telemetry_recorded":lambda *_:False})(); processor.outbox_repository=type("R",(),{"mark_retry":lambda self,*args:setattr(self,"retried",args),"mark_dead_letter":lambda *_:None})(); processor._build_payload=lambda _: {}; processor._calculate_next_retry=lambda _:datetime.now(timezone.utc)
        self.assertFalse(processor._send_entry(type("E",(),{"id":1,"retry_count":0})())); self.assertTrue(hasattr(processor.outbox_repository,"retried"))

if __name__ == "__main__": unittest.main()
