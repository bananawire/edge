"""Device repository — infrastructure layer.

Provides data access operations for the Device aggregate root,
mapping between Peewee models and domain entities.
"""

from datetime import datetime, timezone
from typing import Optional

from iam.domain.entities import Device
from iam.infrastructure.models import DeviceModel


class DeviceRepository:
    """Repository for Device aggregate root persistence.

    Handles all database operations for devices, translating between
    Peewee ORM models (infrastructure) and Device domain entities.
    """

    def find_by_hardware_id_and_api_key(self, hardware_id, api_key):
        """Find a device by its hardware ID and api key combination.

        Args:
            hardware_id: The physical hardware identifier.
            api_key: The secret key provided by the physical embedded device.

        Returns:
            A Device domain entity if found, None otherwise.
        """
        try:
            model = DeviceModel.get(
                (DeviceModel.hardware_id == hardware_id) &
                (DeviceModel.api_key == api_key)
            )
            return Device(
                device_id=model.device_id,
                hardware_id=model.hardware_id,
                api_key=model.api_key,
                status=model.status,
                created_at=model.created_at,
                last_seen_at=model.last_seen_at,
                deleted=model.deleted,
            )
        except DeviceModel.DoesNotExist:
            return None

    def update_last_seen(self, hardware_id):
        """Update last_seen_at and mark the device ONLINE.

        Returns the updated device only when the status changed to ONLINE.

        Args:
            hardware_id: The physical hardware identifier to update.
        """
        device = self.find_by_hardware_id(hardware_id)
        if device is None:
            return None

        now = datetime.now(timezone.utc)
        DeviceModel.update(last_seen_at=now, status="ONLINE").where(
            DeviceModel.hardware_id == hardware_id
        ).execute()

        if device.status == "ONLINE":
            return None
        return self.find_by_hardware_id(hardware_id)

    def mark_offline_stale_devices(self, offline_before: datetime) -> list[Device]:
        """Mark devices as OFFLINE when their last_seen_at is stale.

        Args:
            offline_before: Devices seen before this UTC timestamp become OFFLINE.

        Returns:
            Devices whose status changed to OFFLINE.
        """
        stale_models = list(
            DeviceModel.select().where(
                (DeviceModel.last_seen_at.is_null(False))
                & (DeviceModel.last_seen_at < offline_before)
                & (DeviceModel.status != "OFFLINE")
            )
        )
        if not stale_models:
            return []

        hardware_ids = [model.hardware_id for model in stale_models]
        DeviceModel.update(status="OFFLINE").where(
            DeviceModel.hardware_id.in_(hardware_ids)
        ).execute()

        return [self.find_by_hardware_id(hardware_id) for hardware_id in hardware_ids]

    def find_by_hardware_id(self, hardware_id):
        """Find a device by its hardware ID (without validating credentials)."""
        try:
            model = DeviceModel.get(DeviceModel.hardware_id == hardware_id)
            return Device(
                device_id=model.device_id,
                hardware_id=model.hardware_id,
                api_key=model.api_key,
                status=model.status,
                created_at=model.created_at,
                last_seen_at=model.last_seen_at,
                deleted=model.deleted,
            )
        except DeviceModel.DoesNotExist:
            return None

    def find_by_device_id(self, device_id):
        """Find a device by its clair-core device ID."""
        try:
            model = DeviceModel.get(DeviceModel.device_id == device_id)
            return Device(
                device_id=model.device_id,
                hardware_id=model.hardware_id,
                api_key=model.api_key,
                status=model.status,
                created_at=model.created_at,
                last_seen_at=model.last_seen_at,
                deleted=model.deleted,
            )
        except DeviceModel.DoesNotExist:
            return None
