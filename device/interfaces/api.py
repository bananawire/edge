"""Device API — Flask blueprint and telemetry ingestion endpoint.

Provides the device_api blueprint with the POST endpoint
for creating telemetry data records from authenticated devices.
"""

from flask import Blueprint, jsonify, request

from device.application.queries import GetDeviceConnectionStatusQueryHandler
from device.application.services import DeviceCommandApplicationService, DeviceTelemetryAppService
from device.domain.commands import (
    AcknowledgeEmbeddedDeviceCommandCommand,
    CreateFullTelemetryRecordCommand,
)
from device.domain.queries import GetDeviceConnectionStatusQuery
from device.interfaces.resources import (
    AcknowledgeDeviceCommandRequest,
    TelemetryRequest,
    device_command_to_dict,
)
from iam.interfaces.services import authenticate_request

device_api = Blueprint("device_api", __name__)

telemetry_service = DeviceTelemetryAppService()
command_service = DeviceCommandApplicationService()
connection_status_query_handler = GetDeviceConnectionStatusQueryHandler()


@device_api.route("/api/v1/device/telemetry", methods=["POST"])
def create_telemetry_record():
    """Create a new telemetry data record for an authenticated device.

    Headers:
        Content-Type: application/json
        X-Hardware-Id: <physical hardware identifier>
        X-API-Key: <device secret key>

    Body (JSON) — Optimized Payload:
        {
            "deviceId": "CLAIR-0001",
            "timestamp": "16:57:17",
            "uptime": "00:00:15",
            "airQuality": {
                "co2": 420,
                "temperature": 24.99893,
                "humidity": 50
            },
            "particulateMatter": {
                "pm1_0": 12,
                "pm2_5": 20,
                "pm10": 32
            },
            "connectivity": {
                "status": "connected",
                "network": "Wokwi-GUEST",
                "signalStrength": -65
            },
            "location": {
                "country": "PERU"
            },
            "healthStatus": 100,
            "status": "Optimal"
        }

    Returns:
        201: Record created successfully.
        400: Missing fields, invalid values, or malformed request.
        401: Missing credentials or authentication failure.
    """
    auth_error = authenticate_request(update_last_seen=True)
    if auth_error is not None:
        return auth_error

    try:
        data = request.get_json()
        telemetry_request = TelemetryRequest.from_dict(data)

        hardware_id = request.headers.get("X-Hardware-Id") or telemetry_request.device_id

        command = CreateFullTelemetryRecordCommand(
            hardware_id=hardware_id,
            device_time=telemetry_request.timestamp,
            uptime=telemetry_request.uptime,
            air_quality={
                "co2": telemetry_request.air_quality.co2,
                "temperature": telemetry_request.air_quality.temperature,
                "humidity": telemetry_request.air_quality.humidity,
            },
            particulate_matter={
                "pm1_0": telemetry_request.particulate_matter.pm1_0,
                "pm2_5": telemetry_request.particulate_matter.pm2_5,
                "pm10": telemetry_request.particulate_matter.pm10,
            },
            connectivity={
                "status": telemetry_request.connectivity.status,
                "network": telemetry_request.connectivity.network,
                "signalStrength": telemetry_request.connectivity.signal_strength,
            },
            location={
                "country": telemetry_request.location.country,
            },
            health_status=telemetry_request.health_status,
            status=telemetry_request.status,
            created_at=telemetry_request.created_at,
        )

        record = telemetry_service.create_full_telemetry_record(command, raw_payload=data)

        return jsonify({
            "id": record.id,
            "device_id": record.device_id,
            "device_time": record.device_time,
            "uptime_seconds": record.uptime_seconds,
            "air_quality": {
                "co2": record.air_quality.co2,
                "temperature": record.air_quality.temperature,
                "humidity": record.air_quality.humidity,
            },
            "particulate_matter": {
                "pm1_0": record.particulate_matter.pm1_0,
                "pm2_5": record.particulate_matter.pm2_5,
                "pm10": record.particulate_matter.pm10,
            },
            "connectivity": {
                "status": record.connectivity.status,
                "network": record.connectivity.network,
                "signal_strength": record.connectivity.signal_strength,
            },
            "location": {
                "country": record.location.country,
            },
            "health_status": record.health_status,
            "status": record.status,
            "recorded_at": record.recorded_at.isoformat(),
        }), 201

    except KeyError as e:
        return jsonify({"error": f"Missing required field: {str(e)}"}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Invalid request format: {str(e)}"}), 400


@device_api.route("/api/v1/device/commands/pending", methods=["GET"])
def get_pending_device_commands_for_embedded():
    """Return commands pending for the authenticated embedded device.

    Headers:
        X-Hardware-Id: physical hardware identifier.
        X-API-Key: embedded device secret.

    Returns:
        200: Pending commands, marked as delivered to the embedded device.
        401: Missing or invalid device credentials.
    """
    auth_error = authenticate_request()
    if auth_error is not None:
        return auth_error

    hardware_id = request.headers.get("X-Hardware-Id")
    commands = command_service.get_pending_commands_for_embedded(hardware_id)
    return jsonify({
        "count": len(commands),
        "commands": [device_command_to_dict(command) for command in commands],
    }), 200


@device_api.route("/api/v1/device/commands/<command_id>/ack", methods=["POST"])
def acknowledge_embedded_device_command(command_id):
    """Acknowledge command execution from the authenticated embedded device.

    Headers:
        X-Hardware-Id: physical hardware identifier.
        X-API-Key: embedded device secret.

    Body:
        {"status": "EXECUTED"}
        {"status": "FAILED", "failureReason": "Embedded timeout"}

    Returns:
        200: ACK persisted locally and queued in the asynchronous outbox for delivery to clair-core.
        400: Invalid body or unknown command.
        401: Missing or invalid device credentials.
    """
    auth_error = authenticate_request()
    if auth_error is not None:
        return auth_error

    try:
        ack_request = AcknowledgeDeviceCommandRequest.from_dict(request.get_json())
        hardware_id = request.headers.get("X-Hardware-Id")
        command = command_service.acknowledge_embedded_command(
            AcknowledgeEmbeddedDeviceCommandCommand(
                hardware_id=hardware_id,
                command_id=command_id,
                status=ack_request.status,
                failure_reason=ack_request.failure_reason,
            )
        )
        return jsonify(device_command_to_dict(command)), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unable to acknowledge command: {str(e)}"}), 400


@device_api.route("/api/v1/device/<hardware_id>/connection-status", methods=["GET"])
def get_device_connection_status(hardware_id):
    """Get the connection status of a device (online/offline).

    Determines if a device is ONLINE or OFFLINE based on the time elapsed
    since its last telemetry was received. A device is considered OFFLINE
    if it hasn't sent telemetry in the last 30 seconds.

    Args:
        hardware_id: Physical hardware identifier of the device.

    Returns:
        200: Connection status with last seen timestamp.
            {
                "hardware_id": "CLAIR-0001",
                "status": "ONLINE",
                "last_seen_at": "2024-01-15T10:30:00Z",
                "seconds_since_last_seen": 15
            }
        404: Device not found.
    """
    try:
        query = GetDeviceConnectionStatusQuery(hardware_id=hardware_id)
        status = connection_status_query_handler.handle(query)

        return jsonify({
            "hardware_id": status.hardware_id,
            "status": status.status,
            "last_seen_at": status.last_seen_at.isoformat() if status.last_seen_at else None,
            "seconds_since_last_seen": status.seconds_since_last_seen,
        }), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Unable to get connection status: {str(e)}"}), 400
