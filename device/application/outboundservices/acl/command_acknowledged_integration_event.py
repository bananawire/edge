"""Outbound ACL DTO for command ACKs delivered asynchronously to clair-core.

The immutable event is persisted in the device outbox and delivered over HTTP
by the background outbox processor.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DeviceCommandAcknowledgedIntegrationEvent:
    """Immutable event queued after the embedded device acknowledges a command."""

    device_id: str
    hardware_id: str
    command_id: str
    status: str  # EXECUTED | FAILED
    failure_reason: Optional[str]
