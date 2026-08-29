"""ACL service that delegates edge events to clair-core over HTTP."""

from device.application.outboundservices.acl.core_context_facade import CoreContextFacade
from device.application.outboundservices.acl.http_core_context_facade import HttpCoreContextFacadeImpl


class ExternalCoreService:
    """Translate device integration events through an injectable core facade."""

    def __init__(self, facade: CoreContextFacade | None = None) -> None:
        self._facade = facade or HttpCoreContextFacadeImpl()

    def publish_telemetry_recorded(self, payload: dict) -> bool:
        return self._facade.publish_telemetry_recorded(payload)

    def publish_command_acknowledged(self, payload: dict) -> bool:
        return self._facade.publish_command_acknowledged(payload)
