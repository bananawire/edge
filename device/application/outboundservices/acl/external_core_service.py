"""ExternalCoreService — consumer-side ACL for clair-core over HTTP."""

from device.application.outboundservices.acl.core_context_facade import CoreContextFacade
from device.application.outboundservices.acl.delivery_result import DeliveryResult
from device.application.outboundservices.acl.http_core_context_facade import HttpCoreContextFacadeImpl


class ExternalCoreService:
    """Forwards device integration events to clair-core and reports the classified outcome."""

    def __init__(self, facade: CoreContextFacade | None = None) -> None:
        self._facade = facade or HttpCoreContextFacadeImpl()

    def publish_telemetry_recorded(self, payload: dict) -> DeliveryResult:
        return self._facade.publish_telemetry_recorded(payload)

    def publish_command_acknowledged(self, payload: dict) -> DeliveryResult:
        return self._facade.publish_command_acknowledged(payload)
