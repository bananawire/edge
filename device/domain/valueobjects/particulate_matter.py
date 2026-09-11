"""ParticulateMatter value object — PMS5003 readings as decimals.

Values travel as decimals end to end (core stores double precision). A missing
or non-finite reading is a validation error, never a silent zero.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ParticulateMatter:
    """PM1.0, PM2.5 and PM10 concentrations in µg/m³."""

    pm1_0: float
    pm2_5: float
    pm10: float

    def __post_init__(self):
        for name in ("pm1_0", "pm2_5", "pm10"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number, got {value!r}")
            if value < 0 or value > 1000:
                raise ValueError(f"{name} must be between 0 and 1000 µg/m³, got {value}")
            object.__setattr__(self, name, float(value))

    @classmethod
    def from_dict(cls, data: dict) -> "ParticulateMatter":
        """Create from a payload dict; every field is required."""
        missing = [k for k in ("pm1_0", "pm2_5", "pm10") if data.get(k) is None]
        if missing:
            raise ValueError(f"Missing particulate matter field(s): {', '.join(missing)}")
        return cls(pm1_0=_number(data["pm1_0"], "pm1_0"),
                   pm2_5=_number(data["pm2_5"], "pm2_5"),
                   pm10=_number(data["pm10"], "pm10"))


def _number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    return float(value)
