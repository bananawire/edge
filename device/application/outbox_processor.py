"""Outbox worker for guaranteed asynchronous HTTP delivery.

Implements the outbox pattern with exponential backoff and circuit breaker
protection for publishing telemetry and command ACK integration events.
"""

import json
import logging
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from device.application.outboundservices.acl.external_core_service import (
    ExternalCoreService,
)
from device.application.outboundservices.acl.delivery_result import DeliveryOutcome, DeliveryResult
from device.domain.outbox_entry import OutboxEntry
from device.infrastructure.outbox.outbox_repository import OutboxRepository
from device.infrastructure.repositories import DeviceCommandRepository, DeviceTelemetryRepository
from device.infrastructure.reliability.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenException,
)
from shared.infrastructure.database import db
from shared.infrastructure.environment import get_outbox_dead_letter_retention_hours, get_positive_interval

logger = logging.getLogger(__name__)


class LegacyOutboxPayloadUnavailableError(ValueError):
    """A legacy row has no immutable event snapshot to deliver safely."""


class DeliveryRetryableError(RuntimeError):
    """Raised inside the breaker so transient and blocked outcomes count as failures."""

    def __init__(self, result: DeliveryResult):
        super().__init__(result.reason)
        self.result = result


class TelemetryOutboxProcessor:
    """Polls the outbox and publishes integration events to clair-core over HTTP.

    Delivery outcomes are classified (see ``DeliveryResult``):

    - DELIVERED: mark sent.
    - RETRY: exponential backoff with jitter, capped, retried indefinitely. An
      extended core outage therefore never turns readings into dead letters.
    - BLOCKED: auth or configuration failure; kept pending with a long backoff
      and logged at ERROR so an operator notices.
    - REJECTED: core validated and refused the record; quarantined (dead letter)
      with the reason so it can be inspected or replayed with the tooling.

    The circuit breaker observes RETRY and BLOCKED as failures, so a dead core
    opens it and the loop pauses instead of hammering it.
    """

    BASE_DELAY_SECONDS = 5
    MAX_DELAY_SECONDS = 300
    BLOCKED_DELAY_SECONDS = 300
    # DEVICE_NOT_FOUND is retried this many times (roster lag) before quarantine.
    NOT_FOUND_RETRY_BUDGET = 12
    POLL_INTERVAL_SECONDS = 5
    CLEANUP_INTERVAL_SECONDS = 300  # 5 minutes
    BATCH_SIZE = 10

    def __init__(self) -> None:
        self.outbox_repository = OutboxRepository()
        self.telemetry_repository = DeviceTelemetryRepository()
        self.command_repository = DeviceCommandRepository()
        self.external_core_service = ExternalCoreService()
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=3, recovery_timeout=30.0
        )
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._cycles = 0

    def start(self) -> None:
        """Start the background processor thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="telemetry-outbox")
        self._thread.start()
        logger.info("TelemetryOutboxProcessor started")

    def stop(self) -> None:
        """Signal the processor to stop."""
        self._running = False

    def _run(self) -> None:
        """Main loop with per-iteration DB connection management."""
        interval = get_positive_interval(
            "EDGE_OUTBOX_POLL_INTERVAL_SECONDS", self.POLL_INTERVAL_SECONDS
        )
        while self._running:
            try:
                if db.is_closed():
                    db.connect()
                self._process_batch()
                self._cycles += 1
                cleanup_every = self.CLEANUP_INTERVAL_SECONDS // self.POLL_INTERVAL_SECONDS
                if cleanup_every > 0 and self._cycles % cleanup_every == 0:
                    self._cleanup_sent()
            except Exception:
                logger.exception("Outbox processor loop error")
            finally:
                if not db.is_closed():
                    db.close()
            time.sleep(interval)

    def _process_batch(self) -> None:
        """Fetch and attempt to publish pending outbox entries."""
        entries = self.outbox_repository.find_pending(limit=self.BATCH_SIZE)
        if not entries:
            return

        for entry in entries:
            try:
                self._send_entry(entry)
            except CircuitBreakerOpenException:
                logger.warning(
                    "Circuit breaker OPEN; pausing outbox processing until recovery"
                )
                return
            except Exception as exc:
                logger.warning(
                    "Failed to process outbox entry %s: %s", entry.id, exc
                )

    def _send_entry(self, entry: OutboxEntry) -> bool:
        """Attempt one delivery of an outbox entry and record the classified outcome."""
        try:
            payload = self._build_payload(entry)
        except LegacyOutboxPayloadUnavailableError as exc:
            # Legacy rows predate immutable snapshots. Never rebuild an event from mutable
            # aggregate state: quarantine so an operator can replay from a trusted source.
            self.outbox_repository.mark_dead_letter(entry.id, str(exc))
            logger.error("Outbox entry %s quarantined: %s", entry.id, exc)
            return False

        publisher_names = {
            "COMMAND_ACKNOWLEDGED": "publish_command_acknowledged",
            "PRESENCE_CHANGED": "publish_presence_changed",
            "TELEMETRY_RECORDED": "publish_telemetry_recorded",
        }
        publisher = getattr(self.external_core_service, publisher_names[entry.event_type])
        try:
            result = self.circuit_breaker.call(self._deliver, publisher, payload)
        except CircuitBreakerOpenException:
            raise
        except DeliveryRetryableError as exc:
            result = exc.result
        except Exception as exc:  # adapter bug or unexpected transport error: retry, don't lose
            result = DeliveryResult.retry(f"unexpected delivery error: {exc}")

        if result.outcome is DeliveryOutcome.DELIVERED:
            self.outbox_repository.mark_sent(entry.id)
            logger.info("Outbox entry %s delivered to core", entry.id)
            return True
        if result.outcome is DeliveryOutcome.REJECTED:
            self.outbox_repository.mark_dead_letter(entry.id, result.reason)
            logger.error("Outbox entry %s quarantined: %s", entry.id, result.reason)
            return False
        if result.outcome is DeliveryOutcome.BLOCKED:
            next_retry = datetime.now(timezone.utc) + timedelta(seconds=self.BLOCKED_DELAY_SECONDS)
            self.outbox_repository.mark_retry(entry.id, next_retry, f"BLOCKED: {result.reason}")
            logger.error("Outbox entry %s blocked by core (%s); will retry at %s",
                         entry.id, result.reason, next_retry.isoformat())
            return False
        # RETRY
        if result.reason == "core: DEVICE_NOT_FOUND" and entry.retry_count >= self.NOT_FOUND_RETRY_BUDGET:
            self.outbox_repository.mark_dead_letter(
                entry.id, f"{result.reason} after {entry.retry_count} retries")
            logger.error("Outbox entry %s quarantined: device unknown to core", entry.id)
            return False
        next_retry = self._calculate_next_retry(entry.retry_count)
        self.outbox_repository.mark_retry(entry.id, next_retry, result.reason)
        logger.info("Outbox entry %s scheduled for retry %s at %s (%s)",
                    entry.id, entry.retry_count + 1, next_retry.isoformat(), result.reason)
        return False

    @staticmethod
    def _deliver(publisher, payload: dict) -> DeliveryResult:
        """Run inside the breaker: anything that is not a final answer counts as a failure."""
        result = publisher(payload)
        if not isinstance(result, DeliveryResult):
            # Backwards compatibility with boolean publishers.
            result = DeliveryResult.delivered_ok() if result else DeliveryResult.retry("delivery returned False")
        if result.outcome in (DeliveryOutcome.RETRY, DeliveryOutcome.BLOCKED):
            raise DeliveryRetryableError(result)
        return result

    def _build_payload(self, entry: OutboxEntry) -> dict:
        """Return the immutable snapshot, rejecting legacy rows explicitly."""
        expected = {"COMMAND": "COMMAND_ACKNOWLEDGED", "TELEMETRY": "TELEMETRY_RECORDED", "PRESENCE": "PRESENCE_CHANGED"}
        if expected.get(entry.aggregate_type) != entry.event_type:
            raise ValueError(f"Unsupported outbox event: {entry.aggregate_type}/{entry.event_type}")
        if not getattr(entry, "payload", None):
            raise LegacyOutboxPayloadUnavailableError(
                f"Legacy outbox entry {entry.id} has no immutable payload; manual replay required"
            )
        return json.loads(entry.payload)

    def _calculate_next_retry(self, retry_count: int) -> datetime:
        """Exponential backoff, capped, with up to 25% jitter so retries do not synchronise."""
        delay = min(self.BASE_DELAY_SECONDS * (2 ** min(retry_count, 16)), self.MAX_DELAY_SECONDS)
        delay = delay * (1 + random.uniform(0, 0.25))
        return datetime.now(timezone.utc) + timedelta(seconds=delay)

    def _cleanup_sent(self) -> None:
        """Retention: drop sent rows after a day and quarantined rows after the configured window."""
        now = datetime.now(timezone.utc)
        deleted = self.outbox_repository.delete_sent_older_than(now - timedelta(hours=24))
        if deleted:
            logger.info("Cleaned up %s old sent outbox records", deleted)
        expired = self.outbox_repository.delete_dead_letters_older_than(
            now - timedelta(hours=get_outbox_dead_letter_retention_hours()))
        if expired:
            logger.warning("Purged %s quarantined outbox records past retention", expired)
