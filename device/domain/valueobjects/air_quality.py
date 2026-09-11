"""AirQuality value object — SCD41 readings.

A missing or non-finite reading is a validation error, never a silent zero.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class AirQuality:
    """CO2 in ppm, temperature in °C, relative humidity in %."""

    co2: float
    temperature: float
    humidity: float

    def __post_init__(self):
        for name, low, high in (("co2", 0, 10000), ("temperature", -40, 85), ("humidity", 0, 100)):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number, got {value!r}")
            if value < low or value > high:
                raise ValueError(f"{name} must be between {low} and {high}, got {value}")
            object.__setattr__(self, name, float(value))

    @classmethod
    def from_dict(cls, data: dict) -> "AirQuality":
        """Create from a payload dict; every field is required."""
        missing = [k for k in ("co2", "temperature", "humidity") if data.get(k) is None]
        if missing:
            raise ValueError(f"Missing air quality field(s): {', '.join(missing)}")
        values = {}
        for key in ("co2", "temperature", "humidity"):
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be a number, got {value!r}")
            values[key] = float(value)
        return cls(**values)
