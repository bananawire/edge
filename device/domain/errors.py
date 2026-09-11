"""Domain errors raised by the Device bounded context."""


class TelemetryConflictError(ValueError):
    """The same reading identity was presented with different measurement data."""
