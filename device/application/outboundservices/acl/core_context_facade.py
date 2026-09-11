"""CoreContextFacade — port the Device context uses to reach clair-core."""

from abc import ABC, abstractmethod

from device.application.outboundservices.acl.delivery_result import DeliveryResult


class CoreContextFacade(ABC):
    """Outbound port; implementations classify every attempt as a DeliveryResult."""

    @abstractmethod
    def publish_telemetry_recorded(self, payload: dict) -> DeliveryResult:
        """Deliver one immutable telemetry record (contract v1 batch of one)."""

    @abstractmethod
    def publish_command_acknowledged(self, payload: dict) -> DeliveryResult:
        """Deliver one command acknowledgement."""
