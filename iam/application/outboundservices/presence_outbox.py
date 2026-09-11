"""Port: durable hand-off of presence transitions towards clair-core."""

from abc import ABC, abstractmethod

from iam.domain.events import DevicePresenceChangedEvent


class PresenceOutbox(ABC):
    @abstractmethod
    def enqueue(self, event: DevicePresenceChangedEvent) -> None:
        """Queue the transition; delivery happens asynchronously with retries."""
