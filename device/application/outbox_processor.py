"""Outbox worker for guaranteed asynchronous HTTP delivery.

Implements the outbox pattern with exponential backoff and circuit breaker
protection for publishing telemetry and command ACK integration events.
"""

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from device.application.outboundservices.acl.external_core_service import (
    ExternalCoreService,
)
from device.domain.outbox_entry import OutboxEntry
from device.infrastructure.outbox.outbox_repository import OutboxRepository
from device.infrastructure.repositories import DeviceCommandRepository, DeviceTelemetryRepository
from device.infrastructure.reliability.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenException,
)
from shared.infrastructure.database import db
from shared.infrastructure.environment import get_positive_interval

logger = logging.getLogger(__name__)


class LegacyOutboxPayloadUnavailableError(ValueError):
    """A legacy row has no immutable event snapshot to deliver safely."""


class TelemetryOutboxProcessor:
    """Polls the outbox and asynchronously publishes integration events to HTTP.

    Guarantees at-least-once delivery by retrying with exponential backoff.
    Protects HTTP from overload via circuit breaker.
    """

    MAX_RETRIES = 5
    BASE_DELAY_SECONDS = 5
    MAX_DELAY_SECONDS = 300
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
        self._thread = threading.Thread(target=self._run, daemon=True)
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
        """Fetch and attempt to publish pending outbox entries to HTTP."""
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
        """Attempt to publish a single outbox entry to HTTP.

        Args:
            entry: OutboxEntry to publish.

        Returns:
            True if published successfully, False otherwise.
        """
        try:
            payload = self._build_payload(entry)
            publisher = (self.external_core_service.publish_command_acknowledged
                         if entry.event_type == "COMMAND_ACKNOWLEDGED"
                         else self.external_core_service.publish_telemetry_recorded)
            published = self.circuit_breaker.call(publisher, payload)
            if not published:
                raise RuntimeError("Core rejected telemetry delivery")
            self.outbox_repository.mark_sent(entry.id)
            logger.info("Outbox entry %s published to HTTP", entry.id)
            return True
        except CircuitBreakerOpenException:
            raise
        except LegacyOutboxPayloadUnavailableError as exc:
            # Legacy rows predate immutable snapshots.  Do not rebuild an ACK
            # or telemetry event from mutable aggregate state: that could send
            # a different event than the one originally enqueued.  Quarantine
            # the row explicitly so operators can replay it from a trusted
            # historical payload if one exists.
            self.outbox_repository.mark_dead_letter(entry.id, str(exc))
            logger.error("Outbox entry %s quarantined: %s", entry.id, exc)
            return False
        except Exception as exc:
            error = str(exc)
            if entry.retry_count >= self.MAX_RETRIES:
                self.outbox_repository.mark_dead_letter(entry.id, error)
                logger.error(
                    "Outbox entry %s moved to dead letter after %s retries: %s",
                    entry.id,
                    entry.retry_count,
                    error,
                )
            else:
                next_retry = self._calculate_next_retry(entry.retry_count)
                self.outbox_repository.mark_retry(entry.id, next_retry, error)
                logger.info(
                    "Outbox entry %s scheduled for retry %s at %s",
                    entry.id,
                    entry.retry_count + 1,
                    next_retry.isoformat(),
                )
            return False

    def _build_payload(self, entry: OutboxEntry) -> dict:
        """Return the immutable snapshot, rejecting legacy rows explicitly.

        Rows created before the payload column cannot be reconstructed safely:
        the command or telemetry aggregate may have changed since enqueue. They
        are quarantined by ``_send_entry`` instead of publishing mutable state.
        """
        if entry.aggregate_type not in {"COMMAND", "TELEMETRY"}:
            raise ValueError(f"Unsupported outbox event: {entry.aggregate_type}/{entry.event_type}")
        expected_event = ("COMMAND_ACKNOWLEDGED" if entry.aggregate_type == "COMMAND"
                          else "TELEMETRY_RECORDED")
        if entry.event_type != expected_event:
            raise ValueError(f"Unsupported outbox event: {entry.aggregate_type}/{entry.event_type}")
        if not getattr(entry, "payload", None):
            raise LegacyOutboxPayloadUnavailableError(
                f"Legacy outbox entry {entry.id} has no immutable payload; manual replay required"
            )
        return json.loads(entry.payload)

    def _calculate_next_retry(self, retry_count: int) -> datetime:
        """Calculate next retry timestamp using exponential backoff.

        Args:
            retry_count: Current number of failed attempts.

        Returns:
            UTC datetime for the next retry attempt.
        """
        delay = min(
            self.BASE_DELAY_SECONDS * (2 ** retry_count),
            self.MAX_DELAY_SECONDS,
        )
        return datetime.now(timezone.utc) + timedelta(seconds=delay)

    def _cleanup_sent(self) -> None:
        """Delete old sent outbox records to prevent table bloat."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        deleted = self.outbox_repository.delete_sent_older_than(cutoff)
        if deleted:
            logger.info("Cleaned up %s old sent outbox records", deleted)
