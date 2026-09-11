"""IAM application services: device authentication and presence."""

import json
import logging
from datetime import datetime, timezone

from iam.application.outboundservices.presence_outbox import PresenceOutbox
from iam.domain.events import DevicePresenceChangedEvent
from iam.domain.services import AuthService
from iam.infrastructure.outbox_presence_outbox import OutboxPresenceOutbox
from iam.infrastructure.repositories import DeviceRepository

logger = logging.getLogger(__name__)


class AuthApplicationService:
    """Authenticates a physical device against the roster cache."""

    def __init__(self):
        self.device_repository = DeviceRepository()
        self.auth_service = AuthService()

    def authenticate(self, hardware_id, api_key):
        device = self.device_repository.find_by_hardware_id_and_api_key(hardware_id, api_key)
        return self.auth_service.authenticate(device)


class DevicePresenceApplicationService:
    """Edge-observed presence, delivered to core through the durable outbox.

    Two kinds of evidence:

    - ``touch``: any authenticated request (command or incident poll). It proves the unit is
      reachable and refreshes ``last_seen_at`` so the offline monitor stays quiet, but it does not
      change the status: a device in STANDBY polls without sending telemetry.
    - ``mark_seen``: telemetry. Moves the unit to ONLINE and, if that is a change, queues a
      presence transition for core.
    """

    def __init__(self, presence_outbox: PresenceOutbox | None = None):
        self.device_repository = DeviceRepository()
        self.presence_outbox = presence_outbox or OutboxPresenceOutbox()

    def touch(self, hardware_id: str) -> None:
        self.device_repository.touch(hardware_id)

    def mark_seen(self, hardware_id: str) -> None:
        device = self.device_repository.update_last_seen(hardware_id)
        if device is not None:
            self._queue_presence(device)

    def mark_stale_devices_offline(self, offline_before) -> int:
        devices = self.device_repository.mark_offline_stale_devices(offline_before)
        occurred_at = datetime.now(timezone.utc)
        for device in devices:
            if device is not None:
                self._queue_presence(device, occurred_at=occurred_at)
        return len(devices)

    def _queue_presence(self, device, occurred_at=None) -> None:
        occurred_at = occurred_at or device.last_seen_at or datetime.now(timezone.utc)
        event = DevicePresenceChangedEvent(
            device_id=device.device_id,
            hardware_id=device.hardware_id,
            status=device.status,
            occurred_at=occurred_at,
        )
        self.presence_outbox.enqueue(event)
