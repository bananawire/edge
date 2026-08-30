"""Device application services.

Orchestrates telemetry record creation by coordinating cross-context
device verification, domain validation, local persistence, and guaranteed
outbound delivery to clair-core via the outbox pattern and HTTP.
"""

import json
import logging
from datetime import datetime, timezone

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
from device.domain.outbox_entry import OutboxEntry
from device.domain.services import DeviceTelemetryService
from device.application.outboundservices.acl.external_core_service import ExternalCoreService
from device.infrastructure.outbox.outbox_repository import OutboxRepository
from device.infrastructure.repositories import DeviceCommandRepository, DeviceTelemetryRepository
from iam.infrastructure.repositories import DeviceRepository
from shared.infrastructure.database import db

logger = logging.getLogger(__name__)


class DeviceTelemetryAppService:
    """Application service for device telemetry workflows.

    Coordinates between IAM (device verification), Device domain
    (telemetry validation), Device infrastructure (local persistence),
    and the outbox (guaranteed forward to clair-core via HTTP).
    """

    def __init__(self):
        self.telemetry_repository = DeviceTelemetryRepository()
        self.telemetry_service = DeviceTelemetryService()
        self.device_repository = DeviceRepository()
        self.outbox_repository = OutboxRepository()

    def create_full_telemetry_record(
        self,
        command: CreateFullTelemetryRecordCommand,
        raw_payload: dict | None = None,
    ) -> DeviceTelemetry:
        """Create, persist locally, and queue for core delivery a telemetry record.

        The outbox entry is written within the same database transaction
        as the telemetry record, ensuring at-least-once HTTP delivery
        without blocking the device response.

        Args:
            command: CreateFullTelemetryRecordCommand with device telemetry data.
            raw_payload: Optional original device payload dict to forward to Core.

        Returns:
            The persisted DeviceTelemetry domain entity with assigned ID.

        Raises:
            ValueError: If device not found, or any validation fails.
        """
        device = self.device_repository.find_by_hardware_id(command.hardware_id)
        if device is None:
            raise ValueError(f"Device not found: {command.hardware_id}")

        record = self.telemetry_service.create_record_from_command(command)

        with db.atomic():
            persisted = self.telemetry_repository.save(record)

            # The outbox is part of the same transaction as the record. Never
            # make durable delivery depend on whether the HTTP payload happened
            # to be supplied, and never swallow a persistence failure here.
            outbox_entry = OutboxEntry(
                aggregate_type="TELEMETRY",
                aggregate_id=persisted.id,
                event_type="TELEMETRY_RECORDED",
                payload=json.dumps(_telemetry_payload(persisted), separators=(",", ":"), sort_keys=True),
            )
            self.outbox_repository.save(outbox_entry)

        return persisted


def _telemetry_payload(record: DeviceTelemetry) -> dict:
    """Create the immutable integration snapshot for a telemetry record."""
    return {
        "client_ref": str(record.id),
        "device_id": record.device_id,
        "device_time": record.device_time,
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
        "recorded_at": record.recorded_at.isoformat(),
        # Use the persisted event time, not processing time, so retries are
        # byte-for-byte stable.
        "occurred_at": record.recorded_at.isoformat(),
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
        """
        # Keep the read and conditional terminal transition in one transaction.
        # A repeated ACK returns the first terminal result and does not enqueue
        # another event, preserving both idempotency and the original snapshot.
        # IMMEDIATE obtains SQLite's write lock before the state check, so two
        # concurrent ACK requests cannot both observe a non-terminal command.
        with db.atomic("IMMEDIATE"):
            device_command = self.command_repository.find_by_command_id(command.command_id)
            if device_command is None or device_command.hardware_id != command.hardware_id:
                raise ValueError("Device command not found")
            if device_command.status in (
                EdgeDeviceCommandStatus.EXECUTED,
                EdgeDeviceCommandStatus.FAILED,
            ):
                return device_command
            if device_command.status != EdgeDeviceCommandStatus.DELIVERED_TO_EMBEDDED:
                raise ValueError("Device command has not been delivered")

            if command.status == "EXECUTED":
                device_command.mark_executed()
            else:
                device_command.mark_failed(command.failure_reason)
            saved = self.command_repository.save(device_command)
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
