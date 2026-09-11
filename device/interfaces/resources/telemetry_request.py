"""Device telemetry request/response DTOs.

Resources define API contracts for the optimized device telemetry endpoint.
These are pure transport classes that map the lightweight embedded device payload.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class AirQualityData:
    """Air quality sensor data, passed through as sent; the domain validates."""
    co2: Optional[float]
    temperature: Optional[float]
    humidity: Optional[float]

    @classmethod
    def from_dict(cls, data: dict) -> "AirQualityData":
        return cls(co2=data.get("co2"), temperature=data.get("temperature"), humidity=data.get("humidity"))


@dataclass
class ParticulateMatterData:
    """Particulate matter sensor data, passed through as sent (decimals preserved)."""
    pm1_0: Optional[float]
    pm2_5: Optional[float]
    pm10: Optional[float]

    @classmethod
    def from_dict(cls, data: dict) -> "ParticulateMatterData":
        return cls(pm1_0=data.get("pm1_0"), pm2_5=data.get("pm2_5"), pm10=data.get("pm10"))


@dataclass
class ConnectivityData:
    """WiFi connectivity status."""
    status: str
    network: str = ""
    signal_strength: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> "ConnectivityData":
        return cls(
            status=str(data.get("status", "unknown")),
            network=str(data.get("network", "")),
            signal_strength=int(data.get("signalStrength", 0)),
        )


@dataclass
class LocationData:
    """Device geographical location."""
    country: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "LocationData":
        return cls(
            country=str(data.get("country", "")),
        )


@dataclass
class TelemetryRequest:
    """Optimized telemetry request from embedded device.

    Maps the lightweight JSON payload:
    {
      "deviceId": "CLAIR-0001",
      "timestamp": "16:57:17",
      "uptime": "00:00:15",
      "airQuality": {"co2": 420, "temperature": 24.99893, "humidity": 50},
      "particulateMatter": {"pm1_0": 12, "pm2_5": 20, "pm10": 32},
      "connectivity": {"status": "connected", "network": "Wokwi-GUEST", "signalStrength": -65},
      "location": {"country": "PERU"},
      "healthStatus": 100,
      "status": "Optimal",
      "reading_id": "40e67f87-2c0a-47ef-a3ed-7999e106cc9c",
      "measured_at": "2026-09-11T14:30:25.123Z"
    }
    """
    device_id: str
    timestamp: str
    uptime: str
    air_quality: AirQualityData
    particulate_matter: ParticulateMatterData
    connectivity: ConnectivityData
    location: LocationData
    health_status: int
    status: str
    reading_id: Optional[str] = None
    measured_at: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "TelemetryRequest":
        device_id = data.get("deviceId") or data.get("device_id")
        if not device_id:
            raise KeyError("deviceId or device_id is required")

        return cls(
            device_id=str(device_id),
            timestamp=str(data.get("timestamp", "")),
            uptime=str(data.get("uptime", "")),
            air_quality=AirQualityData.from_dict(data.get("airQuality", {})),
            particulate_matter=ParticulateMatterData.from_dict(data.get("particulateMatter", {})),
            connectivity=ConnectivityData.from_dict(data.get("connectivity", {})),
            location=LocationData.from_dict(data.get("location", {})),
            health_status=data.get("healthStatus"),
            status=str(data.get("status", "unknown")),
            reading_id=_first(data, "reading_id", "readingId"),
            # ``created_at`` is the pre-v1 name for the same instant.
            measured_at=_first(data, "measured_at", "measuredAt", "created_at"),
        )


def _first(data: dict, *keys: str) -> Optional[str]:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return None
