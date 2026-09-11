"""Presence transitions ride the same outbox as telemetry and ACKs.

Coalescing: only the newest pending transition per device matters to core,
which applies presence monotonically, so older undelivered ones are dropped
when a newer one is queued.
"""

import json

from device.domain.outbox_entry import OutboxEntry
from device.infrastructure.outbox.outbox_repository import OutboxRepository
from iam.application.outboundservices.presence_outbox import PresenceOutbox
from iam.domain.events import DevicePresenceChangedEvent
from shared.infrastructure.database import db

AGGREGATE_TYPE = "PRESENCE"
EVENT_TYPE = "PRESENCE_CHANGED"


class OutboxPresenceOutbox(PresenceOutbox):
    def __init__(self, repository: OutboxRepository | None = None) -> None:
        self._repository = repository or OutboxRepository()

    def enqueue(self, event: DevicePresenceChangedEvent) -> None:
        payload = json.dumps({
            "device_id": event.device_id,
            "hardware_id": event.hardware_id,
            "status": event.status,
            "occurred_at": event.occurred_at.isoformat(),
        }, separators=(",", ":"), sort_keys=True)
        with db.atomic():
            self._repository.discard_pending(AGGREGATE_TYPE, event.device_id)
            self._repository.save(OutboxEntry(
                aggregate_type=AGGREGATE_TYPE,
                aggregate_id=event.device_id,
                event_type=EVENT_TYPE,
                payload=payload,
            ))
