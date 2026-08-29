import json
import threading
import unittest
from datetime import datetime, timezone
from urllib.error import HTTPError

from device.application.outboundservices.acl.http_core_context_facade import HttpCoreContextFacadeImpl
from device.application.outbox_processor import TelemetryOutboxProcessor
from iam.application.outboundservices.acl.core_presence_http_publisher import CorePresenceHttpPublisher
from provisioning.application.device_roster_poller import DeviceRosterPoller
from alerting.application.alert_poller import AlertIncidentPoller
from shared.infrastructure.core_http_client import CoreHttpClient
from shared.infrastructure.environment import get_positive_interval

class Response:
    def __init__(self, status, body=b"{}"): self.status, self.body = status, body
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return self.body

class HttpAdapterTests(unittest.TestCase):
    def test_telemetry_posts_batch_record(self):
        calls=[]
        def opener(request, timeout): calls.append(request); return Response(200, b'{"results":[{"status":"CREATED"}]}')
        self.assertTrue(HttpCoreContextFacadeImpl("http://localhost:8080", "secret", opener=opener).publish_telemetry_recorded({"device_id":"d"}))
        self.assertEqual(json.loads(calls[0].data), {"records":[{"device_id":"d"}]})

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

    def test_command_poller_acknowledges_each_command(self):
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
        self.assertEqual(client.acks[0][0], "/api/v1/edge/commands/c/ack")
        self.assertTrue(client.acks[0][2])

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

    def test_outbox_false_delivery_is_retry(self):
        processor=TelemetryOutboxProcessor.__new__(TelemetryOutboxProcessor)
        from device.infrastructure.reliability.circuit_breaker import CircuitBreaker
        processor.circuit_breaker=CircuitBreaker(); processor.external_core_service=type("S",(),{"publish_telemetry_recorded":lambda *_:False})(); processor.outbox_repository=type("R",(),{"mark_retry":lambda self,*args:setattr(self,"retried",args),"mark_dead_letter":lambda *_:None})(); processor._build_payload=lambda _: {}; processor._calculate_next_retry=lambda _:datetime.now(timezone.utc)
        self.assertFalse(processor._send_entry(type("E",(),{"id":1,"retry_count":0})())); self.assertTrue(hasattr(processor.outbox_repository,"retried"))

if __name__ == "__main__": unittest.main()
