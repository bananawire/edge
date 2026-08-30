"""DevicePresenceChangedIntegrationEvent — outbound ACL DTO for presence transitions.

Published by the IAM bounded context to HTTP topic
`clair.device.presence.changed` for consumption by clair-core.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DevicePresenceChangedIntegrationEvent:
    """Event published when the edge detects an ONLINE/OFFLINE transition."""

    device_id: str
    hardware_id: str
    status: str  # ONLINE | OFFLINE | STANDBY | ERROR
    occurred_at: str
