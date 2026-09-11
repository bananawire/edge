"""Device domain services: telemetry validation and aggregate creation."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from dateutil import parser as dateutil_parser

from device.domain.commands import CreateFullTelemetryRecordCommand
from device.domain.entities import DeviceTelemetry
from device.domain.valueobjects import AirQuality, Connectivity, Location, ParticulateMatter


class DeviceTelemetryService:
    """Turns a validated command into a DeviceTelemetry aggregate.

    Identity and time follow contract v1: the firmware owns ``reading_id`` and
    ``measured_at``. A client that sends neither is a legacy client; the edge
    mints a UUID once and, unless ``require_measured_at`` is set, uses its own
    receipt time flagged as such. It never substitutes upload time silently.
    """

    def __init__(self, require_measured_at: bool = False, clock=None):
        self.require_measured_at = require_measured_at
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create_record_from_command(self, command: CreateFullTelemetryRecordCommand) -> DeviceTelemetry:
        """Validate command data and create a DeviceTelemetry entity.

        Raises:
            ValueError: If any field is missing, malformed or out of range.
        """
        air_quality = AirQuality.from_dict(command.air_quality)
        particulate_matter = ParticulateMatter.from_dict(command.particulate_matter)
        connectivity = Connectivity.from_dict(command.connectivity)
        location = Location.from_dict(command.location)
        uptime_seconds = self._parse_uptime(command.uptime)
        health_status = self._parse_health_status(command.health_status)
        received_at = self._clock()
        reading_id = self._resolve_reading_id(command.reading_id)

        if command.measured_at:
            recorded_at = self._parse_instant(command.measured_at)
            time_source = DeviceTelemetry.TIME_SOURCE_DEVICE
        elif self.require_measured_at:
            raise ValueError("measured_at is required")
        else:
            recorded_at = received_at
            time_source = DeviceTelemetry.TIME_SOURCE_EDGE_RECEIPT

        return DeviceTelemetry(
            device_id=command.hardware_id,
            device_time=command.device_time,
            uptime_seconds=uptime_seconds,
            air_quality=air_quality,
            particulate_matter=particulate_matter,
            connectivity=connectivity,
            location=location,
            health_status=health_status,
            status=command.status,
            recorded_at=recorded_at,
            reading_id=reading_id,
            received_at=received_at,
            time_source=time_source,
        )

    @staticmethod
    def _resolve_reading_id(value: Optional[str]) -> str:
        if value is None or str(value).strip() == "":
            # Legacy client: mint once here; the caller persists it with the row.
            return str(uuid.uuid4())
        try:
            return str(uuid.UUID(str(value).strip()))
        except ValueError as exc:
            raise ValueError(f"reading_id must be a UUID, got {value!r}") from exc

    @staticmethod
    def _parse_health_status(value) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"health_status must be a number, got {value!r}")
        if value < 0 or value > 100:
            raise ValueError(f"health_status must be between 0 and 100, got {value}")
        return int(value)

    @staticmethod
    def _parse_uptime(uptime: str) -> int:
        """Parse "HH:MM:SS" or plain integer seconds."""
        text = str(uptime).strip()
        if text.isdigit():
            return int(text)
        parts = text.split(":")
        if len(parts) == 3:
            try:
                hours, minutes, seconds = (int(p) for p in parts)
                return hours * 3600 + minutes * 60 + seconds
            except ValueError as exc:
                raise ValueError(f"Invalid uptime format: {uptime}") from exc
        raise ValueError(f"Invalid uptime format: {uptime}. Expected HH:MM:SS or integer seconds.")

    @staticmethod
    def _parse_instant(value: str) -> datetime:
        """Parse an ISO-8601 instant that carries an offset; naive values are rejected."""
        try:
            parsed = dateutil_parser.isoparse(str(value))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid measured_at format: {value}") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"measured_at must include a UTC offset: {value}")
        return parsed.astimezone(timezone.utc)
