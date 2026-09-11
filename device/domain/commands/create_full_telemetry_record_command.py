"""CreateFullTelemetryRecordCommand — intention to persist one device reading.

Immutable. ``reading_id`` and ``measured_at`` are the firmware-owned identity and
measurement instant from contract v1; both are optional only for legacy
clients, which the domain service handles explicitly.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CreateFullTelemetryRecordCommand:
    """Command to create a device telemetry record.

    Attributes:
        hardware_id: Physical hardware identifier from X-Hardware-Id header.
        device_time: Device wall-clock string (display only, e.g. "14:30:25").
        uptime: System uptime as "HH:MM:SS" or integer seconds string.
        air_quality: Dict with co2, temperature, humidity.
        particulate_matter: Dict with pm1_0, pm2_5, pm10.
        connectivity: Dict with status, network, signalStrength.
        location: Dict with country.
        health_status: Device health status percentage (0-100).
        status: Overall device status string.
        reading_id: Firmware UUID for this sample, or None for legacy clients.
        measured_at: ISO-8601 instant with offset, or None for legacy clients.
    """

    hardware_id: str
    device_time: str
    uptime: str
    air_quality: dict
    particulate_matter: dict
    connectivity: dict
    location: dict
    health_status: int
    status: str
    reading_id: Optional[str] = None
    measured_at: Optional[str] = None

    def __post_init__(self):
        if not self.hardware_id:
            raise ValueError("hardware_id is required")
        if not self.device_time:
            raise ValueError("device_time is required")
        if not self.uptime:
            raise ValueError("uptime is required")
        for name in ("air_quality", "particulate_matter", "connectivity", "location"):
            if not isinstance(getattr(self, name), dict):
                raise ValueError(f"{name} must be a dict")
        if self.health_status is None:
            raise ValueError("health_status is required")
        if not self.status:
            raise ValueError("status is required")
