"""REST resources for edge device command endpoints."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AcknowledgeDeviceCommandRequest:
    """Request body sent by embedded devices after command execution."""

    status: str
    failure_reason: Optional[str] = None

    @staticmethod
    def from_dict(data: dict) -> "AcknowledgeDeviceCommandRequest":
        if data is None:
            raise ValueError("JSON body is required")
        status = data.get("status")
        failure_reason = data.get("failureReason") or data.get("failure_reason")
        if status not in ("EXECUTED", "FAILED"):
            raise ValueError("status must be EXECUTED or FAILED")
        return AcknowledgeDeviceCommandRequest(status=status, failure_reason=failure_reason)


def device_command_to_dict(command) -> dict:
    """Transform a DeviceCommand domain entity into a response dict."""
    return {
        "commandId": command.command_id,
        "deviceId": command.device_id,
        "hardwareId": command.hardware_id,
        "type": command.command_type.value,
        "status": command.status.value,
        "payload": command.payload,
        "receivedAt": command.received_at.isoformat() if command.received_at else None,
        "deliveredAt": command.delivered_at.isoformat() if command.delivered_at else None,
        "failureReason": command.failure_reason,
    }
