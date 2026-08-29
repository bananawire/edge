"""DeviceChangedIntegrationEvent — inbound ACL DTO for provisioning updates.

Consumed by the Provisioning bounded context from HTTP topic
`clair.provisioning.devices.changed` published by clair-core.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceChangedIntegrationEvent:
    """Event consumed from Core representing a provisioning change."""

    device_id: str
    hardware_id: str
    api_key: str
    status: str
    change_type: str  # CREATED | UPDATED | DELETED
    changed_at: str
