"""Application service for provisioning the local device cache via HTTP.

Coordinates device cache updates driven entirely by HTTP events from
clair-core.  No HTTP communication remains in this bounded context.
"""

from provisioning.domain.services.device_cache_service import DeviceCacheService
from provisioning.infrastructure.device_cache_repository import DeviceCacheRepository


class DeviceProvisioningApplicationService:
    """Coordinates HTTP-driven device cache synchronization.

    Receives DeviceChanged integration events and upserts validated device
    records into the local SQLite cache.
    """

    def __init__(self):
        self.device_cache_repository = DeviceCacheRepository()
        self.device_cache_service = DeviceCacheService()

    def handle_device_changed_event(self, payload: dict) -> int:
        """Process a single DeviceChanged integration event from HTTP.

        Args:
            payload: Dict with device_id, hardware_id, api_key, status.

        Returns:
            Number of records updated (0 or 1).
        """
        record = self._normalize_payload(payload)
        self.device_cache_service.validate_device_record(record)
        return self.device_cache_repository.upsert_many([record])

    def sync_from_roster(self, payloads: list[dict]) -> int:
        """Apply one authoritative roster page after validating every record."""
        records = [self._normalize_payload(payload) for payload in payloads]
        for record in records:
            # Tombstones carry the complete roster record and are persisted too;
            # this prevents an older active record from resurrecting the device.
            self.device_cache_service.validate_device_record(record)
        return self.device_cache_repository.upsert_many(records)

    @staticmethod
    def _normalize_payload(payload: dict) -> dict:
        """Normalize HTTP payload keys to the local cache schema."""
        return {
            # Core emits snake_case after the latest update, but accept both casings.
            "device_id": str(
                payload.get("device_id")
                or payload.get("deviceId")
                or payload.get("id")
            ),
            "hardware_id": payload.get("hardware_id") or payload.get("hardwareId"),
            "api_key": payload.get("api_key") or payload.get("apiKey") or "",
            "status": payload.get("status"),
            "deleted": bool(payload.get("deleted", False)),
            "updated_at": payload.get("updated_at") or payload.get("updatedAt"),
        }
