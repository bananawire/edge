from dataclasses import dataclass


@dataclass
class DeviceCacheResource:
    """Resource received from clair-core via HTTP."""

    device_id: str
    hardware_id: str
    api_key: str
    status: str
    deleted: bool = False
    updated_at: str | None = None

    @staticmethod
    def from_dict(payload):
        return DeviceCacheResource(
            device_id=str(payload.get("device_id") or payload.get("id")),
            hardware_id=payload.get("hardware_id") or payload.get("hardwareId"),
            api_key=payload.get("api_key") or payload.get("apiKey") or "",
            status=payload.get("status"),
            deleted=bool(payload.get("deleted", False)),
            updated_at=payload.get("updated_at") or payload.get("updatedAt"),
        )

    def to_command_record(self):
        return {
            "device_id": self.device_id,
            "hardware_id": self.hardware_id,
            "api_key": self.api_key,
            "status": self.status,
            "deleted": self.deleted,
            "updated_at": self.updated_at,
        }
