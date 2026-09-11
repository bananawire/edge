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
from device.domain.errors import TelemetryConflictError
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
    """Store one reading from an authenticated device (contract v1, device-edge/telemetry.*).

    Headers:
        Content-Type: application/json
        X-Hardware-Id: <physical hardware identifier>
        X-API-Key: <device secret key>

    Body: see docs/contracts/edge-v1/device-edge/telemetry.request.json. ``reading_id`` and
    ``measured_at`` identify the sample; when both are absent the client is treated as legacy.

    Returns:
        201: Reading stored (``duplicate`` is true for an exact retry).
        400: Missing fields, invalid values, or malformed request.
        401: Missing credentials or authentication failure.
        409: Same reading_id already stored with different measurement data.
    """
    auth_error = authenticate_request(update_last_seen=True)
    if auth_error is not None:
        return auth_error

    try:
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({"error": "JSON body must be an object"}), 400
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
            location={"country": telemetry_request.location.country},
            health_status=telemetry_request.health_status,
            status=telemetry_request.status,
            reading_id=telemetry_request.reading_id,
            measured_at=telemetry_request.measured_at,
        )

        result = telemetry_service.ingest(command)
        record = result.record
        return jsonify({
            "id": record.id,
            "reading_id": record.reading_id,
            "device_id": record.device_id,
            "measured_at": record.recorded_at.isoformat(),
            "received_at": record.received_at.isoformat() if record.received_at else None,
            "time_source": record.time_source,
            "duplicate": result.duplicate,
        }), 201

    except TelemetryConflictError as e:
        return jsonify({"error": str(e)}), 409
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
