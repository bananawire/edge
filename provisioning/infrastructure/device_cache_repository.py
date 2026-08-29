"""Repository for updating the local IAM device cache."""

from datetime import datetime, timezone

from iam.infrastructure.models import DeviceModel


class DeviceCacheRepository:
    """Persists clair-core device records in the local SQLite cache."""

    @staticmethod
    def _is_current_or_newer(incoming, current):
        """Reject stale roster records while tolerating missing legacy versions."""
        if incoming is None or current is None:
            return True
        try:
            from datetime import datetime
            parse = lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parse(incoming) >= parse(current)
        except (TypeError, ValueError):
            return str(incoming) >= str(current)

    def delete_by_device_id(self, device_id: str, updated_at=None) -> None:
        """Apply a tombstone without physically removing the cache row."""
        current = DeviceModel.get_or_none(DeviceModel.device_id == device_id)
        if current is not None and self._is_current_or_newer(updated_at, current.updated_at):
            DeviceModel.update(deleted=True, updated_at=updated_at).where(
                DeviceModel.device_id == device_id
            ).execute()

    def upsert_many(self, devices):
        """Upsert synchronized devices and return the number of cached records."""
        now = datetime.now(timezone.utc)
        count = 0
        for device in devices:
            current = DeviceModel.get_or_none(DeviceModel.device_id == device["device_id"])
            if current is not None and not self._is_current_or_newer(
                device.get("updated_at"), current.updated_at
            ):
                continue
            update = {
                DeviceModel.hardware_id: device["hardware_id"],
                DeviceModel.api_key: device["api_key"],
                DeviceModel.status: device["status"],
                DeviceModel.deleted: device.get("deleted", False),
            }
            if device.get("updated_at") is not None:
                update[DeviceModel.updated_at] = device["updated_at"]
            DeviceModel.insert(
                device_id=device["device_id"],
                hardware_id=device["hardware_id"],
                api_key=device["api_key"],
                status=device["status"],
                created_at=now,
                last_seen_at=None,
                deleted=device.get("deleted", False),
                updated_at=device.get("updated_at"),
            ).on_conflict(
                conflict_target=[DeviceModel.device_id],
                preserve=[DeviceModel.created_at, DeviceModel.last_seen_at],
                update=update,
            ).execute()
            count += 1
        return count
