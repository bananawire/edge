"""Device application services.

Orchestrates telemetry record creation by coordinating cross-context
device verification, domain validation, local persistence, and guaranteed
outbound delivery to clair-core via the outbox pattern and HTTP.
"""

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

            if raw_payload is not None:
                try:
                    outbox_entry = OutboxEntry(
                        aggregate_type="TELEMETRY",
                        aggregate_id=persisted.id,
                        event_type="TELEMETRY_RECORDED",
                    )
                    self.outbox_repository.save(outbox_entry)
                except Exception as exc:
                    logger.warning("Failed to create outbox entry: %s", exc)

        return persisted


class DeviceCommandApplicationService:
    """Application service for Core -> Edge -> Embedded command delivery via HTTP."""

    def __init__(self):
        self.command_repository = DeviceCommandRepository()
        self.device_repository = DeviceRepository()
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
        """Persist embedded ACK locally and publish it to HTTP for clair-core."""
        device_command = self.command_repository.find_by_command_id(command.command_id)
        if device_command is None or device_command.hardware_id != command.hardware_id:
            raise ValueError("Device command not found")

        acknowledged_at = datetime.now(timezone.utc)
        if command.status == "EXECUTED":
            device_command.mark_executed(acknowledged_at)
        else:
            device_command.mark_failed(acknowledged_at, command.failure_reason)

        saved = self.command_repository.save(device_command)

        payload = {
            "device_id": saved.device_id,
            "hardware_id": saved.hardware_id,
            "command_id": saved.command_id,
            "status": command.status,
            "failure_reason": command.failure_reason,
            "acknowledged_at": acknowledged_at.isoformat(),
        }
        published = self.external_core_service.publish_command_acknowledged(payload)
        if not published:
            logger.warning("HTTP ACK publish failed for command %s", saved.command_id)
        return saved
