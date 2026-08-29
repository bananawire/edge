"""Transport-neutral ACL interface for communication with clair-core."""

from abc import ABC, abstractmethod


class CoreContextFacade(ABC):
    """Anti-corruption layer facade for core integration."""

    @abstractmethod
    def publish_telemetry_recorded(self, payload: dict) -> bool:
        """Publish a telemetry integration event to clair-core."""
        ...

    @abstractmethod
    def publish_command_acknowledged(self, payload: dict) -> bool:
        """Publish a command acknowledgement integration event to clair-core."""
        ...
