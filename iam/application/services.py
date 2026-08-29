"""IAM application services.

Orchestrates device authentication by coordinating domain services
with infrastructure repositories.
"""

import logging
from datetime import datetime, timezone

from iam.application.outboundservices.acl.core_presence_http_publisher import CorePresenceHttpPublisher
from iam.domain.events import DevicePresenceChangedEvent
from iam.domain.services import AuthService
from iam.infrastructure.repositories import DeviceRepository

logger = logging.getLogger(__name__)


class AuthApplicationService:
    """Application service that orchestrates IAM authentication workflows.

    Coordinates between the AuthService (domain logic) and
    DeviceRepository (infrastructure) to authenticate devices.
    """

    def __init__(self):
        self.device_repository = DeviceRepository()
        self.auth_service = AuthService()

    def authenticate(self, hardware_id, api_key):
        """Authenticate a physical device by its hardware ID and api key.

        Args:
            hardware_id: The physical hardware identifier.
            api_key: The secret key provided by the physical embedded device.

        Returns:
            True if the device exists and is allowed by its synchronized status.
        """
        device = self.device_repository.find_by_hardware_id_and_api_key(hardware_id, api_key)
        return self.auth_service.authenticate(device)


class DevicePresenceApplicationService:
    """Application service for publishing edge-detected presence transitions via HTTP."""

    def __init__(self):
        self.device_repository = DeviceRepository()
        self.core_presence_publisher = CorePresenceHttpPublisher()

    def mark_seen(self, hardware_id: str) -> None:
        """Mark a device ONLINE from telemetry and publish the transition if needed."""
        device = self.device_repository.update_last_seen(hardware_id)
        if device is not None:
            self._publish_presence(device)

    def mark_stale_devices_offline(self, offline_before) -> int:
        """Mark stale devices OFFLINE and publish one event per transition."""
        devices = self.device_repository.mark_offline_stale_devices(offline_before)
        occurred_at = datetime.now(timezone.utc)
        for device in devices:
            if device is not None:
                self._publish_presence(device, occurred_at=occurred_at)
        return len(devices)

    def _publish_presence(self, device, occurred_at=None) -> None:
        occurred_at = occurred_at or device.last_seen_at or datetime.now(timezone.utc)
        event = DevicePresenceChangedEvent(
            device_id=device.device_id,
            hardware_id=device.hardware_id,
            status=device.status,
            occurred_at=occurred_at,
        )
        payload = {
            "device_id": event.device_id,
            "hardware_id": event.hardware_id,
            "status": event.status,
            "occurred_at": event.occurred_at.isoformat(),
        }
        if not self.core_presence_publisher.publish_device_presence_changed(payload):
            logger.warning(
                "Failed to publish %s presence event for hardware_id=%s",
                event.status,
                event.hardware_id,
            )
