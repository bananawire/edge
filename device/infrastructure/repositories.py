"""DeviceTelemetry repository — infrastructure layer.

Provides data access operations for the DeviceTelemetry aggregate root,
mapping between Peewee models and domain entities.
"""

from typing import Optional

from datetime import datetime, timedelta, timezone

from device.domain.entities import (
    DeviceCommand,
    DeviceCommandType,
    DeviceTelemetry,
    EdgeDeviceCommandStatus,
)
from device.domain.valueobjects import AirQuality, Connectivity, Location, ParticulateMatter
from device.infrastructure.models import DeviceCommandModel, DeviceTelemetryModel
from shared.infrastructure.database import db


class DeviceTelemetryRepository:
    """Repository for DeviceTelemetry aggregate root persistence."""

    def save(self, telemetry: DeviceTelemetry) -> DeviceTelemetry:
        """Persist a telemetry record to the database.

        Args:
            telemetry: A DeviceTelemetry domain entity to persist.

        Returns:
            A new DeviceTelemetry domain entity with the database-assigned ID.
        """
        model = DeviceTelemetryModel.create(
            device_id=telemetry.device_id,
            reading_id=telemetry.reading_id,
            device_time=telemetry.device_time,
            uptime_seconds=telemetry.uptime_seconds,
            co2=telemetry.air_quality.co2,
            temperature=telemetry.air_quality.temperature,
            humidity=telemetry.air_quality.humidity,
            pm1_0=telemetry.particulate_matter.pm1_0,
            pm2_5=telemetry.particulate_matter.pm2_5,
            pm10=telemetry.particulate_matter.pm10,
            wifi_status=telemetry.connectivity.status,
            network_name=telemetry.connectivity.network,
            signal_strength=telemetry.connectivity.signal_strength,
            country=telemetry.location.country,
            health_status=telemetry.health_status,
            status=telemetry.status,
            recorded_at=telemetry.recorded_at,
            received_at=telemetry.received_at,
            time_source=telemetry.time_source,
        )

        return self._model_to_entity(model)

    def find_by_device_and_reading_id(self, device_id: str, reading_id: str) -> Optional[DeviceTelemetry]:
        """Find the reading a device already sent under this identity, if any."""
        model = DeviceTelemetryModel.get_or_none(
            (DeviceTelemetryModel.device_id == device_id)
            & (DeviceTelemetryModel.reading_id == reading_id)
        )
        return self._model_to_entity(model) if model is not None else None

    def find_by_id(self, record_id: int) -> Optional[DeviceTelemetry]:
        """Find a telemetry record by its database ID.

        Args:
            record_id: The database ID to search for.

        Returns:
            DeviceTelemetry entity if found, None otherwise.
        """
        try:
            model = DeviceTelemetryModel.get(DeviceTelemetryModel.id == record_id)
            return self._model_to_entity(model)
        except DeviceTelemetryModel.DoesNotExist:
            return None

    def find_last_telemetry_by_hardware_id(self, hardware_id: str) -> Optional[DeviceTelemetry]:
        """Find the most recent telemetry record for a device.

        Args:
            hardware_id: The physical hardware identifier.

        Returns:
            The most recent DeviceTelemetry entity, or None if no records exist.
        """
        try:
            model = (
                DeviceTelemetryModel.select()
                .where(DeviceTelemetryModel.device_id == hardware_id)
                .order_by(DeviceTelemetryModel.recorded_at.desc())
                .limit(1)
                .get()
            )
            return self._model_to_entity(model)
        except DeviceTelemetryModel.DoesNotExist:
            return None

    def _model_to_entity(self, model: DeviceTelemetryModel) -> DeviceTelemetry:
        """Convert a Peewee model instance to a DeviceTelemetry domain entity.

        Args:
            model: A DeviceTelemetryModel instance from the database.

        Returns:
            A fully populated DeviceTelemetry domain entity.
        """
        air_quality = AirQuality(
            co2=model.co2,
            temperature=model.temperature,
            humidity=model.humidity,
        )

        particulate_matter = ParticulateMatter(
            pm1_0=model.pm1_0,
            pm2_5=model.pm2_5,
            pm10=model.pm10,
        )

        connectivity = Connectivity(
            status=model.wifi_status,
            network=model.network_name,
            signal_strength=model.signal_strength,
        )

        location = Location(
            country=model.country,
        )

        return DeviceTelemetry(
            id=model.id,
            device_id=model.device_id,
            reading_id=model.reading_id or f"legacy-{model.id}",
            device_time=model.device_time,
            uptime_seconds=model.uptime_seconds,
            air_quality=air_quality,
            particulate_matter=particulate_matter,
            connectivity=connectivity,
            location=location,
            health_status=model.health_status,
            status=model.status,
            recorded_at=_as_utc(model.recorded_at),
            received_at=_as_utc(model.received_at),
            time_source=model.time_source or DeviceTelemetry.TIME_SOURCE_DEVICE,
        )


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """libsql returns naive datetimes for values stored via isoformat; they are UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        from dateutil import parser as dateutil_parser
        value = dateutil_parser.isoparse(value)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class DeviceCommandRepository:
    """Repository for edge-local DeviceCommand aggregate persistence."""

    # A delivered command is leased to the embedded device. If the HTTP
    # response is lost, the lease eventually expires and the command is
    # delivered again; ACK remains the only terminal transition.
    DELIVERY_LEASE_SECONDS = 300

    def save(self, command: DeviceCommand) -> DeviceCommand:
        """Insert or update a device command and return the persisted entity."""
        DeviceCommandModel.insert(
            command_id=command.command_id,
            device_id=command.device_id,
            assignment_id=command.assignment_id,
            hardware_id=command.hardware_id,
            command_type=command.command_type.value,
            status=command.status.value,
            payload=command.payload,
            received_at=command.received_at,
            delivered_at=command.delivered_at,
            failure_reason=command.failure_reason,
        ).on_conflict(
            conflict_target=[DeviceCommandModel.command_id],
            update={
                DeviceCommandModel.status: command.status.value,
                DeviceCommandModel.payload: command.payload,
                DeviceCommandModel.delivered_at: command.delivered_at,
                DeviceCommandModel.failure_reason: command.failure_reason,
            },
        ).execute()
        return self.find_by_command_id(command.command_id)

    def find_by_command_id(self, command_id: str) -> Optional[DeviceCommand]:
        """Find a command by its clair-core command ID."""
        try:
            model = DeviceCommandModel.get(DeviceCommandModel.command_id == command_id)
            return self._model_to_entity(model)
        except DeviceCommandModel.DoesNotExist:
            return None

    def expire_outstanding_for_other_assignments(self, device_id: str, current_assignment_id) -> int:
        """Void cached commands issued under a generation other than ``current_assignment_id``.

        Called when the roster reports a new (or no) assignment for a device: whatever was queued
        for the previous owner must never reach the embedded unit. Unbound legacy rows are kept.
        """
        outstanding = (
            (DeviceCommandModel.device_id == device_id)
            & (DeviceCommandModel.status << [
                EdgeDeviceCommandStatus.RECEIVED.value,
                EdgeDeviceCommandStatus.DELIVERED_TO_EMBEDDED.value,
            ])
            & DeviceCommandModel.assignment_id.is_null(False)
        )
        if current_assignment_id is not None:
            outstanding &= DeviceCommandModel.assignment_id != str(current_assignment_id)
        return (
            DeviceCommandModel.update(status=EdgeDeviceCommandStatus.EXPIRED.value)
            .where(outstanding)
            .execute()
        )

    def find_pending_for_hardware_id(self, hardware_id: str, current_assignment_id=None) -> list[DeviceCommand]:
        """Find new commands and delivered commands whose lease expired, for the current generation only."""
        lease_expiry = datetime.now(timezone.utc) - timedelta(seconds=self.DELIVERY_LEASE_SECONDS)
        generation = DeviceCommandModel.assignment_id.is_null()
        if current_assignment_id is not None:
            generation = generation | (DeviceCommandModel.assignment_id == str(current_assignment_id))
        query = (
            DeviceCommandModel.select()
            .where(
                (DeviceCommandModel.hardware_id == hardware_id)
                & generation
                & (
                    (DeviceCommandModel.status == EdgeDeviceCommandStatus.RECEIVED.value)
                    | (
                        (DeviceCommandModel.status == EdgeDeviceCommandStatus.DELIVERED_TO_EMBEDDED.value)
                        & (
                            DeviceCommandModel.delivered_at.is_null()
                            | (DeviceCommandModel.delivered_at <= lease_expiry)
                        )
                    )
                )
            )
            .order_by(DeviceCommandModel.received_at.asc())
        )
        return [self._model_to_entity(model) for model in query]

    def mark_commands_delivered(self, commands: list[DeviceCommand]) -> list[DeviceCommand]:
        """Conditionally mark commands delivered, preserving concurrent terminal ACKs.

        Poll results are materialized before they are marked delivered.  The
        conditional update prevents that stale entity from overwriting an ACK
        which completed between those two operations.
        """
        delivered_at = datetime.now(timezone.utc)
        lease_expiry = delivered_at - timedelta(seconds=self.DELIVERY_LEASE_SECONDS)
        delivered = []
        # Claim the complete poll batch in one transaction.  This preserves
        # the conditional per-row claim while preventing a partial batch from
        # being exposed if persistence fails midway through the loop.
        with db.atomic():
            for command in commands:
                claim = (
                    (DeviceCommandModel.status == EdgeDeviceCommandStatus.RECEIVED.value)
                    | (
                        (DeviceCommandModel.status == EdgeDeviceCommandStatus.DELIVERED_TO_EMBEDDED.value)
                        & (
                            DeviceCommandModel.delivered_at.is_null()
                            | (DeviceCommandModel.delivered_at <= lease_expiry)
                        )
                    )
                )
                claimed = (
                    DeviceCommandModel.update(
                        status=EdgeDeviceCommandStatus.DELIVERED_TO_EMBEDDED.value,
                        delivered_at=delivered_at,
                    )
                    .where((DeviceCommandModel.command_id == command.command_id) & claim)
                    .execute()
                )
                # Return the claimed snapshot, never reread the row: an ACK can
                # legitimately win immediately after the claim and must not turn
                # this response into a terminal command response.
                if claimed == 1:
                    command.mark_delivered_to_embedded(delivered_at)
                    delivered.append(command)
        return delivered

    def _model_to_entity(self, model: DeviceCommandModel) -> DeviceCommand:
        """Convert a Peewee command model into a domain entity."""
        return DeviceCommand(
            command_id=model.command_id,
            device_id=model.device_id,
            hardware_id=model.hardware_id,
            command_type=DeviceCommandType(model.command_type),
            status=EdgeDeviceCommandStatus(model.status),
            payload=model.payload,
            received_at=model.received_at,
            delivered_at=model.delivered_at,
            failure_reason=model.failure_reason,
            assignment_id=model.assignment_id,
        )
