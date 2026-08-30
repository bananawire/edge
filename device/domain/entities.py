"""DeviceTelemetry entity — aggregate root of the Device bounded context.

Represents the optimized telemetry reading received from the embedded device.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from device.domain.valueobjects import AirQuality, Connectivity, Location, ParticulateMatter


class DeviceCommandType(str, Enum):
    """Commands that the embedded device can execute."""

    STANDBY = "STANDBY"
    WAKE = "WAKE"
    RESTART = "RESTART"


class EdgeDeviceCommandStatus(str, Enum):
    """Edge-local command delivery status."""

    RECEIVED = "RECEIVED"
    DELIVERED_TO_EMBEDDED = "DELIVERED_TO_EMBEDDED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


class DeviceTelemetry:
    """Aggregate root entity representing an optimized device telemetry reading.

    Encapsulates only the data sent by the embedded device in the lightweight
    payload: environmental sensors, connectivity status, location, health status, and overall state.

    Attributes:
        id: Auto-incremented database ID (None before persistence).
        device_id: Logical identifier of the source device.
        device_time: Device local time as string (e.g., "14:30:25").
        uptime_seconds: System uptime in seconds (parsed from HH:MM:SS).
        air_quality: SCD41 CO2/temperature/humidity readings (value object).
        particulate_matter: PMS5003 PM1.0/PM2.5/PM10 readings (value object).
        connectivity: WiFi connection status (value object).
        location: Device geographical location (value object).
        health_status: Device health status percentage (0-100).
        status: Overall device status string.
        recorded_at: UTC timestamp when the reading was recorded by edge.
    """

    def __init__(
        self,
        device_id: str,
        device_time: str,
        uptime_seconds: int,
        air_quality: AirQuality,
        particulate_matter: ParticulateMatter,
        connectivity: Connectivity,
        location: Location,
        health_status: int,
        status: str,
        recorded_at: datetime,
        id: Optional[int] = None,
    ):
        if not device_id:
            raise ValueError("device_id is required")
        if not device_time:
            raise ValueError("device_time is required")
        if air_quality is None:
            raise ValueError("air_quality is required")
        if particulate_matter is None:
            raise ValueError("particulate_matter is required")
        if connectivity is None:
            raise ValueError("connectivity is required")
        if location is None:
            raise ValueError("location is required")
        if health_status is None:
            raise ValueError("health_status is required")
        if not status:
            raise ValueError("status is required")
        if recorded_at is None:
            raise ValueError("recorded_at is required")

        self.id = id
        self.device_id = device_id
        self.device_time = device_time
        self.uptime_seconds = uptime_seconds
        self.air_quality = air_quality
        self.particulate_matter = particulate_matter
        self.connectivity = connectivity
        self.location = location
        self.health_status = health_status
        self.status = status
        self.recorded_at = recorded_at




class DeviceCommand:
    """Aggregate root representing a command received from clair-core."""

    def __init__(
        self,
        command_id: str,
        device_id: str,
        hardware_id: str,
        command_type: DeviceCommandType,
        status: EdgeDeviceCommandStatus,
        payload: Optional[str],
        received_at: datetime,
        delivered_at: Optional[datetime] = None,
        failure_reason: Optional[str] = None,
    ):
        if not command_id:
            raise ValueError("command_id is required")
        if not device_id:
            raise ValueError("device_id is required")
        if not hardware_id:
            raise ValueError("hardware_id is required")
        if command_type is None:
            raise ValueError("command_type is required")
        if status is None:
            raise ValueError("status is required")
        if received_at is None:
            raise ValueError("received_at is required")

        self.command_id = command_id
        self.device_id = device_id
        self.hardware_id = hardware_id
        self.command_type = command_type
        self.status = status
        self.payload = payload
        self.received_at = received_at
        self.delivered_at = delivered_at
        self.failure_reason = failure_reason

    def mark_delivered_to_embedded(self, delivered_at: datetime) -> None:
        self.status = EdgeDeviceCommandStatus.DELIVERED_TO_EMBEDDED
        self.delivered_at = delivered_at

    def mark_executed(self) -> None:
        self.status = EdgeDeviceCommandStatus.EXECUTED
        self.failure_reason = None

    def mark_failed(self, failure_reason: Optional[str]) -> None:
        self.status = EdgeDeviceCommandStatus.FAILED
        self.failure_reason = failure_reason
