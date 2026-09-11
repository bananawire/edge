"""Device application services.

Orchestrates telemetry record creation by coordinating cross-context
device verification, domain validation, local persistence, and guaranteed
outbound delivery to clair-core via the outbox pattern and HTTP.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from peewee import IntegrityError

from device.domain.commands import (
    AcknowledgeEmbeddedDeviceCommandCommand,
    CreateFullTelemetryRecordCommand,
)
from device.domain.entities import (
    DeviceCommand,
    DeviceCommandType,
    DeviceTelemetry,
    EdgeDeviceCommandStatus,
)
from device.domain.errors import TelemetryConflictError
from device.domain.outbox_entry import OutboxEntry
from device.domain.services import DeviceTelemetryService
from device.application.outboundservices.acl.external_core_service import ExternalCoreService
from device.infrastructure.outbox.outbox_repository import OutboxRepository
from device.infrastructure.models import DeviceCommandModel
from device.infrastructure.repositories import DeviceCommandRepository, DeviceTelemetryRepository
from iam.infrastructure.repositories import DeviceRepository
from shared.infrastructure.database import db
from shared.infrastructure.environment import get_edge_require_measured_at

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelemetryIngestionResult:
    """What the device hop produced: the stored reading and whether it already existed."""

    record: DeviceTelemetry
    duplicate: bool


class DeviceTelemetryAppService:
    """Application service for device telemetry workflows.

    Coordinates device verification (IAM cache), domain validation, local
    persistence and the outbox row that guarantees delivery to clair-core.
    The device hop is idempotent on ``(hardware_id, reading_id)``: an exact
    retry returns the stored reading, a changed payload under the same
    identity is a conflict.
    """

    def __init__(self):
        self.telemetry_repository = DeviceTelemetryRepository()
        self.telemetry_service = DeviceTelemetryService(
            require_measured_at=get_edge_require_measured_at()
        )
        self.device_repository = DeviceRepository()
        self.outbox_repository = OutboxRepository()

    def create_full_telemetry_record(
        self,
        command: CreateFullTelemetryRecordCommand,
        raw_payload: dict | None = None,
    ) -> DeviceTelemetry:
        """Create, persist locally and queue for core delivery; returns the stored reading."""
        return self.ingest(command).record

    def ingest(self, command: CreateFullTelemetryRecordCommand) -> TelemetryIngestionResult:
        """Idempotent ingestion of one reading.

        Raises:
            ValueError: unknown device or invalid measurement.
            TelemetryConflictError: same reading identity, different measurement data.
        """
        device = self.device_repository.find_by_hardware_id(command.hardware_id)
        if device is None:
            raise ValueError(f"Device not found: {command.hardware_id}")

        record = self.telemetry_service.create_record_from_command(command)

        with db.atomic():
            existing = self.telemetry_repository.find_by_device_and_reading_id(
                record.device_id, record.reading_id
            )
            if existing is None:
                try:
                    persisted = self._store(record)
                    return TelemetryIngestionResult(record=persisted, duplicate=False)
                except IntegrityError:
                    # Two identical retries raced past the lookup; the unique index kept one row.
                    existing = self.telemetry_repository.find_by_device_and_reading_id(
                        record.device_id, record.reading_id
                    )
                    if existing is None:
                        raise
            if not existing.has_same_measurement(record):
                raise TelemetryConflictError(
                    f"reading_id {record.reading_id} already stored with different measurement data"
                )
            return TelemetryIngestionResult(record=existing, duplicate=True)

    def _store(self, record: DeviceTelemetry) -> DeviceTelemetry:
        # Savepoint: an IntegrityError must not poison the enclosing transaction.
        with db.atomic():
            persisted = self.telemetry_repository.save(record)
            # The outbox row is part of the same transaction as the reading. Never make durable
            # delivery depend on request details, and never swallow a persistence failure here.
            self.outbox_repository.save(OutboxEntry(
                aggregate_type="TELEMETRY",
                aggregate_id=persisted.id,
                event_type="TELEMETRY_RECORDED",
                payload=json.dumps(_telemetry_payload(persisted), separators=(",", ":"), sort_keys=True),
            ))
            return persisted


def _telemetry_payload(record: DeviceTelemetry) -> dict:
    """The immutable core batch record for a reading (contract v1, core-edge/telemetry.batch.request)."""
    return {
        "client_ref": str(record.id),
        "reading_id": record.reading_id,
        "device_id": record.device_id,
        "occurred_at": record.recorded_at.isoformat(),
        "uptime_seconds": record.uptime_seconds,
        "co2": record.air_quality.co2,
        "temperature": record.air_quality.temperature,
        "humidity": record.air_quality.humidity,
        "pm1_0": record.particulate_matter.pm1_0,
        "pm2_5": record.particulate_matter.pm2_5,
        "pm10": record.particulate_matter.pm10,
        "wifi_status": record.connectivity.status,
        "network_name": record.connectivity.network,
        "signal_strength": record.connectivity.signal_strength,
        "country": record.location.country,
        "health_status": record.health_status,
        "status": record.status,
        # Kept for readers of older snapshots; core ignores it.
        "recorded_at": record.recorded_at.isoformat(),
    }


class DeviceCommandApplicationService:
    """Application service for Core -> Edge -> Embedded command delivery via HTTP."""

    def __init__(self):
        self.command_repository = DeviceCommandRepository()
        self.device_repository = DeviceRepository()
        self.outbox_repository = OutboxRepository()
        self.external_core_service = ExternalCoreService()

    def ingest_command_messages(self, messages: list[dict]) -> list[DeviceCommand]:
        """Persist command integration events from HTTP into the local cache.

        Args:
            messages: Raw dict payloads from the HTTP consumer.

        Returns:
            List of persisted or existing DeviceCommand entities.
        """
        persisted: list[DeviceCommand] = []

        with db.atomic():
            for item in messages:
                device_id = item.get("deviceId") or item.get("device_id")
                command_id = item.get("id") or item.get("commandId") or item.get("command_id")
                command_type = item.get("type") or item.get("commandType") or item.get("command_type")
                payload = item.get("payload")

                if not device_id or not command_id or not command_type:
                    logger.warning("Skipping malformed command from HTTP: %s", item)
                    continue

                device = self.device_repository.find_by_device_id(device_id)
                if device is None:
                    logger.warning("Skipping command %s for unknown device %s", command_id, device_id)
                    continue

                existing = self.command_repository.find_by_command_id(command_id)
                if existing is not None:
                    persisted.append(existing)
                    continue

                device_command = DeviceCommand(
                    command_id=command_id,
                    device_id=device_id,
                    hardware_id=device.hardware_id,
                    command_type=DeviceCommandType(command_type),
                    status=EdgeDeviceCommandStatus.RECEIVED,
                    payload=payload,
                    received_at=datetime.now(timezone.utc),
                )
                persisted.append(self.command_repository.save(device_command))

        return persisted

    def get_pending_commands_for_embedded(self, hardware_id: str) -> list[DeviceCommand]:
        """Return commands pending for an embedded device and mark them delivered."""
        commands = self.command_repository.find_pending_for_hardware_id(hardware_id)
        return self.command_repository.mark_commands_delivered(commands)

    def acknowledge_embedded_command(self, command: AcknowledgeEmbeddedDeviceCommandCommand) -> DeviceCommand:
        """Persist an ACK exactly once and queue its immutable event in the outbox.

        Clair-core delivery is asynchronous and handled by the background outbox
        processor after this local transaction commits.

        Turso/libSQL serializes writes server-side and the ``BEGIN IMMEDIATE``
        SQLite modifier is not part of the remote protocol. We replace the
        client-side write lock with a conditional UPDATE: only the row in
        ``DELIVERED_TO_EMBEDDED`` state is promoted to a terminal status, so
        concurrent ACK requests cannot both observe a non-terminal command and
        double-enqueue an outbox event.
        """
        new_status = (
            EdgeDeviceCommandStatus.EXECUTED
            if command.status == "EXECUTED"
            else EdgeDeviceCommandStatus.FAILED
        )

        with db.atomic():
            existing = self.command_repository.find_by_command_id(command.command_id)
            if existing is None or existing.hardware_id != command.hardware_id:
                raise ValueError("Device command not found")
            if existing.status in (
                EdgeDeviceCommandStatus.EXECUTED,
                EdgeDeviceCommandStatus.FAILED,
            ):
                return existing
            if existing.status != EdgeDeviceCommandStatus.DELIVERED_TO_EMBEDDED:
                raise ValueError("Device command has not been delivered")

            failure_reason = (
                None if new_status == EdgeDeviceCommandStatus.EXECUTED else command.failure_reason
            )
            updated = (
                DeviceCommandModel
                .update(
                    status=new_status.value,
                    failure_reason=failure_reason,
                )
                .where(
                    (DeviceCommandModel.command_id == command.command_id)
                    & (DeviceCommandModel.hardware_id == command.hardware_id)
                    & (DeviceCommandModel.status == EdgeDeviceCommandStatus.DELIVERED_TO_EMBEDDED.value)
                )
                .execute()
            )
            if updated == 0:
                # Another worker transitioned the command after our snapshot
                # read; honor the terminal state it produced.
                terminal = self.command_repository.find_by_command_id(command.command_id)
                if terminal is not None and terminal.status in (
                    EdgeDeviceCommandStatus.EXECUTED,
                    EdgeDeviceCommandStatus.FAILED,
                ):
                    return terminal
                raise ValueError("Device command state changed concurrently")

            # Reflect the new state on the in-memory entity so subsequent
            # in-process reads (e.g. a duplicated ACK landing moments later)
            # see the terminal status without an extra round-trip. The DB
            # row has already been updated by the conditional UPDATE above.
            if new_status == EdgeDeviceCommandStatus.EXECUTED:
                existing.mark_executed()
            else:
                existing.mark_failed(failure_reason)
            saved = existing
            payload = json.dumps({
                "device_id": saved.device_id,
                "hardware_id": saved.hardware_id,
                "command_id": saved.command_id,
                "status": saved.status.value,
                "failure_reason": saved.failure_reason,
            }, separators=(",", ":"), sort_keys=True)
            self.outbox_repository.save(OutboxEntry(
                aggregate_type="COMMAND",
                aggregate_id=saved.command_id,
                event_type="COMMAND_ACKNOWLEDGED",
                payload=payload,
            ))
            return saved
