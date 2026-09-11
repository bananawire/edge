"""IAM interface services — Flask blueprint and authentication function.

Provides the iam_api blueprint and the authenticate_request() function
that other bounded contexts use to validate device credentials.
"""

from flask import Blueprint, jsonify, request

from iam.application.services import AuthApplicationService, DevicePresenceApplicationService

iam_api = Blueprint("iam_api", __name__)

auth_service = AuthApplicationService()
device_presence_service = DevicePresenceApplicationService()


def authenticate_request(update_last_seen: bool = False, touch: bool = False):
    """Authenticate the current HTTP request using device credentials.

    Extracts hardware_id from the X-Hardware-Id header and api_key
    from the X-API-Key header. Validates credentials and updates
    last_seen_at on successful authentication.

    Returns:
        None if authentication succeeds.
        A (response, status_code) tuple if authentication fails (401).
    """
    hardware_id = request.headers.get("X-Hardware-Id", "").strip()
    api_key = request.headers.get("X-API-Key", "").strip()

    if not hardware_id or not api_key:
        return jsonify({"error": "Missing X-Hardware-Id or X-API-Key"}), 401

    if not auth_service.authenticate(hardware_id, api_key):
        return jsonify({"error": "Invalid hardware ID or API key"}), 401

    if update_last_seen:
        device_presence_service.mark_seen(hardware_id)
    elif touch:
        # Polls are a heartbeat: the unit is reachable even while it sends no telemetry (standby).
        device_presence_service.touch(hardware_id)
    return None
