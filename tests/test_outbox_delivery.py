"""Delivery classification, breaker behaviour and retention of the telemetry outbox."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

from device.application.outboundservices.acl.delivery_result import DeliveryOutcome, DeliveryResult
from device.application.outboundservices.acl.http_core_context_facade import HttpCoreContextFacadeImpl
from device.application.outbox_processor import TelemetryOutboxProcessor
from device.domain.outbox_entry import OutboxEntry
from device.infrastructure.outbox.outbox_record_model import OutboxRecordModel
from device.infrastructure.outbox.outbox_repository import OutboxRepository
from device.infrastructure.reliability.circuit_breaker import CircuitBreaker, CircuitBreakerOpenException


class Response:
    def __init__(self, status, body=b"{}"): self.status, self.body = status, body
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return self.body


def facade(reply):
    def opener(request, timeout):
        if isinstance(reply, Exception):
            raise reply
        return reply
    return HttpCoreContextFacadeImpl("http://localhost:49220", "t", opener=opener)


class TestClassification:
    def test_per_record_results_decide_the_outcome(self):
        created = Response(200, json.dumps({"results": [{"status": "CREATED"}]}).encode())
        assert facade(created).publish_telemetry_recorded({}).outcome is DeliveryOutcome.DELIVERED
        invalid = Response(200, json.dumps({"results": [{"status": "ERROR", "reason": "VALIDATION_ERROR"}]}).encode())
        result = facade(invalid).publish_telemetry_recorded({})
        assert result.outcome is DeliveryOutcome.REJECTED and "VALIDATION_ERROR" in result.reason
        unknown = Response(200, json.dumps({"results": [{"status": "ERROR", "reason": "DEVICE_NOT_FOUND"}]}).encode())
        assert facade(unknown).publish_telemetry_recorded({}).outcome is DeliveryOutcome.RETRY

    def test_transport_and_http_statuses_map_to_retry_blocked_or_rejected(self):
        assert facade(URLError("refused")).publish_telemetry_recorded({}).outcome is DeliveryOutcome.RETRY
        assert facade(TimeoutError()).publish_telemetry_recorded({}).outcome is DeliveryOutcome.RETRY
        assert facade(HTTPError("u", 503, "down", {}, None)).publish_telemetry_recorded({}).outcome is DeliveryOutcome.RETRY
        assert facade(HTTPError("u", 429, "slow", {}, None)).publish_telemetry_recorded({}).outcome is DeliveryOutcome.RETRY
        blocked = facade(HTTPError("u", 401, "nope", {}, None)).publish_telemetry_recorded({})
        assert blocked.outcome is DeliveryOutcome.BLOCKED and "EDGE_TO_CORE_TOKEN" in blocked.reason
        assert facade(HTTPError("u", 400, "bad", {}, None)).publish_telemetry_recorded({}).outcome is DeliveryOutcome.REJECTED

    def test_command_ack_conflict_is_delivered_and_not_found_is_rejected(self):
        payload = {"command_id": "c", "hardware_id": "h", "status": "EXECUTED"}
        assert facade(HTTPError("u", 409, "terminal", {}, None)).publish_command_acknowledged(payload).delivered
        assert facade(HTTPError("u", 404, "unknown", {}, None)).publish_command_acknowledged(payload).outcome is DeliveryOutcome.REJECTED
        assert facade(HTTPError("u", 401, "nope", {}, None)).publish_command_acknowledged(payload).outcome is DeliveryOutcome.BLOCKED


def _processor(result, breaker=None):
    processor = TelemetryOutboxProcessor.__new__(TelemetryOutboxProcessor)
    processor.circuit_breaker = breaker or CircuitBreaker(failure_threshold=3, recovery_timeout=30)
    processor.external_core_service = type("S", (), {
        "publish_telemetry_recorded": lambda self, payload: result,
        "publish_command_acknowledged": lambda self, payload: result,
    })()
    processor.outbox_repository = OutboxRepository()
    return processor


def _entry(retry_count=0):
    return OutboxRepository().save(OutboxEntry("TELEMETRY", 1, "TELEMETRY_RECORDED",
                                               retry_count=retry_count, payload='{"reading_id":"r"}'))


class TestProcessorOutcomes:
    def test_delivered_marks_sent(self, db):
        entry = _entry()
        assert _processor(DeliveryResult.delivered_ok())._send_entry(entry) is True
        assert OutboxRecordModel.get().status == "sent"

    def test_rejected_is_quarantined_with_reason(self, db):
        entry = _entry()
        assert _processor(DeliveryResult.rejected("core: VALIDATION_ERROR"))._send_entry(entry) is False
        row = OutboxRecordModel.get()
        assert row.status == "dead_letter" and "VALIDATION_ERROR" in row.error_message

    def test_retry_never_dead_letters_even_after_many_attempts(self, db):
        entry = _entry(retry_count=500)
        processor = _processor(DeliveryResult.retry("core unreachable"))
        assert processor._send_entry(entry) is False
        row = OutboxRecordModel.get()
        assert row.status == "pending" and row.retry_count == 501
        delay = (row.next_retry_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()
        assert 290 <= delay <= 300 * 1.25 + 1  # capped, with jitter

    def test_blocked_keeps_the_entry_with_a_long_backoff(self, db):
        entry = _entry()
        processor = _processor(DeliveryResult.blocked("HTTP 401"))
        assert processor._send_entry(entry) is False
        row = OutboxRecordModel.get()
        assert row.status == "pending" and row.error_message.startswith("BLOCKED")
        delay = (row.next_retry_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()
        assert delay >= 290

    def test_device_not_found_is_retried_then_quarantined(self, db):
        processor = _processor(DeliveryResult.retry("core: DEVICE_NOT_FOUND"))
        assert processor._send_entry(_entry(retry_count=0)) is False
        assert OutboxRecordModel.get().status == "pending"
        OutboxRecordModel.delete().execute()
        assert processor._send_entry(_entry(retry_count=TelemetryOutboxProcessor.NOT_FOUND_RETRY_BUDGET)) is False
        assert OutboxRecordModel.get().status == "dead_letter"

    def test_breaker_opens_after_repeated_transient_failures(self, db):
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        processor = _processor(DeliveryResult.retry("core unreachable"), breaker)
        for _ in range(3):
            processor._send_entry(_entry())
        assert breaker.state == CircuitBreaker.OPEN
        try:
            processor._send_entry(_entry())
            raised = False
        except CircuitBreakerOpenException:
            raised = True
        assert raised, "an open breaker must stop the batch instead of retrying blindly"

    def test_successful_delivery_resets_the_breaker(self, db):
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        _processor(DeliveryResult.retry("x"), breaker)._send_entry(_entry())
        _processor(DeliveryResult.delivered_ok(), breaker)._send_entry(_entry())
        assert breaker.state == CircuitBreaker.CLOSED


class TestToolingAndRetention:
    def test_requeue_restores_a_quarantined_entry_and_purge_respects_age(self, db):
        repository = OutboxRepository()
        entry = _entry()
        repository.mark_dead_letter(entry.id, "core: VALIDATION_ERROR")
        assert repository.count_by_status()["dead_letter"] == 1
        assert [e.id for e in repository.find_dead_letters()] == [entry.id]
        assert repository.requeue(entry.id) is True
        assert repository.requeue(entry.id) is False
        assert OutboxRecordModel.get().status == "pending" and OutboxRecordModel.get().retry_count == 0
        repository.mark_dead_letter(entry.id, "again")
        assert repository.delete_dead_letters_older_than(datetime.now(timezone.utc) - timedelta(days=1)) == 0
        assert repository.delete_dead_letters_older_than(datetime.now(timezone.utc) + timedelta(seconds=1)) == 1
        assert OutboxRecordModel.select().count() == 0
