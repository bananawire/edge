"""DevicesSyncRequestedIntegrationEvent — outbound ACL DTO for sync requests.

Published by the Provisioning bounded context to HTTP topic
`clair.provisioning.devices.sync.requested` for consumption by clair-core.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DevicesSyncRequestedIntegrationEvent:
    """Event published by the Edge on startup to request a full device sync."""

    edge_instance_id: str
    requested_at: str
