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
    # Voided locally because the device was unlinked or reassigned; never delivered again.
    EXPIRED = "EXPIRED"


class DeviceTelemetry:
    """Aggregate root for one reading received from the embedded device.

    Attributes:
        id: Auto-incremented database ID (None before persistence).
        device_id: Hardware identifier of the source device.
        reading_id: Stable UUID of this sample; firmware-owned, edge-minted only for legacy clients.
        device_time: Device wall-clock string as sent (display only).
        uptime_seconds: System uptime in seconds.
        air_quality / particulate_matter / connectivity / location: value objects.
        health_status: Device health status percentage (0-100).
        status: Overall device status string.
        recorded_at: Measurement instant (UTC). Named for compatibility; it is ``measured_at``.
        received_at: When the edge accepted the reading (UTC). Metadata only.
        time_source: "device" when the firmware supplied the instant, "edge_receipt" otherwise.
    """

    TIME_SOURCE_DEVICE = "device"
    TIME_SOURCE_EDGE_RECEIPT = "edge_receipt"

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
        reading_id: str,
        received_at: Optional[datetime] = None,
        time_source: str = TIME_SOURCE_DEVICE,
        id: Optional[int] = None,
    ):
        if not device_id:
            raise ValueError("device_id is required")
        if not reading_id:
            raise ValueError("reading_id is required")
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
        if time_source not in (self.TIME_SOURCE_DEVICE, self.TIME_SOURCE_EDGE_RECEIPT):
            raise ValueError("time_source must be device or edge_receipt")

        self.id = id
        self.device_id = device_id
        self.reading_id = reading_id
        self.device_time = device_time
        self.uptime_seconds = uptime_seconds
        self.air_quality = air_quality
        self.particulate_matter = particulate_matter
        self.connectivity = connectivity
        self.location = location
        self.health_status = health_status
        self.status = status
        self.recorded_at = recorded_at
        self.received_at = received_at
        self.time_source = time_source

    @property
    def measured_at(self) -> datetime:
        return self.recorded_at

    def has_same_measurement(self, other: "DeviceTelemetry") -> bool:
        """Whether ``other`` is a byte-for-byte retry of this reading."""
        return (
            self.recorded_at == other.recorded_at
            and self.uptime_seconds == other.uptime_seconds
            and self.air_quality == other.air_quality
            and self.particulate_matter == other.particulate_matter
            and self.connectivity == other.connectivity
            and self.location == other.location
            and self.health_status == other.health_status
            and self.status == other.status
        )


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
        assignment_id: Optional[str] = None,
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
        self.assignment_id = assignment_id

    def mark_delivered_to_embedded(self, delivered_at: datetime) -> None:
        self.status = EdgeDeviceCommandStatus.DELIVERED_TO_EMBEDDED
        self.delivered_at = delivered_at

    def expire(self) -> None:
        """Void a command that can no longer be delivered; terminal states are left alone."""
        if self.status in (EdgeDeviceCommandStatus.RECEIVED, EdgeDeviceCommandStatus.DELIVERED_TO_EMBEDDED):
            self.status = EdgeDeviceCommandStatus.EXPIRED

    def mark_executed(self) -> None:
        self.status = EdgeDeviceCommandStatus.EXECUTED
        self.failure_reason = None

    def mark_failed(self, failure_reason: Optional[str]) -> None:
        self.status = EdgeDeviceCommandStatus.FAILED
        self.failure_reason = failure_reason
