"""Device entity — aggregate root of the IAM bounded context.

A Device represents a registered IoT edge device with authentication
credentials and lifecycle status synchronized from clair-core.
"""


class Device:
    """Aggregate root entity representing a registered IoT device.

    Attributes:
        device_id: clair-core master device UUID cached locally.
        hardware_id: Physical hardware ID — prevents device cloning.
        api_key: Secret key for physical device -> edge authentication.
        status: Device lifecycle state synchronized from clair-core.
        created_at: UTC timestamp of when the device was registered (audit trail).
        last_seen_at: Last time the device sent data — detects offline sensors.
    """

    def __init__(self, device_id, hardware_id, api_key, status, created_at,
                 last_seen_at=None, deleted=False):
        self.device_id = device_id
        self.hardware_id = hardware_id
        self.api_key = api_key
        self.status = status
        self.created_at = created_at
        self.last_seen_at = last_seen_at
        self.deleted = deleted
