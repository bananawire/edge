"""Swagger/OpenAPI documentation endpoints for the edge API."""

from flask import Blueprint, jsonify, render_template_string

from shared.infrastructure.environment import get_edge_public_base_url

docs_api = Blueprint("docs_api", __name__)


OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "Clair Edge API",
        "version": "1.0.0",
        "description": "Edge API for CO2 and PM2.5 telemetry ingestion and clair-core device provisioning.",
    },
    "servers": [{"url": get_edge_public_base_url(), "description": "Edge service"}],
    "tags": [
        {"name": "Telemetry", "description": "Environmental telemetry ingestion from IoT sensors."},
        {"name": "Commands", "description": "Embedded device command delivery. Commands arrive via HTTP from clair-core."},
        {"name": "Alerting", "description": "Embedded-to-core alert condition state transitions (NORMAL/CRITICAL)."},
    ],
    "components": {
        "securitySchemes": {
            "DeviceCredentials": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Hardware-Id",
                "description": "Physical hardware identifier of the device.",
            },
            "DeviceApiKey": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
                "description": "Device API key for physical device -> edge authentication.",
            },
        },
        "schemas": {
            "CreateTelemetryRequest": {
                "type": "object",
                "required": ["deviceId", "timestamp", "uptime", "airQuality", "particulateMatter", "connectivity", "location", "healthStatus", "status"],
                "properties": {
                    "deviceId": {"type": "string", "description": "Device identifier (also sent in X-Hardware-Id header)."},
                    "timestamp": {"type": "string", "description": "Device local time (e.g., 14:30:25)."},
                    "uptime": {"type": "string", "description": "System uptime as HH:MM:SS or seconds."},
                    "airQuality": {
                        "type": "object",
                        "required": ["co2", "temperature", "humidity"],
                        "properties": {
                            "co2": {"type": "number", "format": "float", "minimum": 0, "maximum": 10000, "description": "CO2 concentration in ppm."},
                            "temperature": {"type": "number", "format": "float", "description": "Temperature in Celsius."},
                            "humidity": {"type": "number", "description": "Relative humidity percentage."},
                        },
                    },
                    "particulateMatter": {
                        "type": "object",
                        "required": ["pm1_0", "pm2_5", "pm10"],
                        "properties": {
                            "pm1_0": {"type": "integer", "description": "PM1.0 concentration in µg/m³."},
                            "pm2_5": {"type": "integer", "description": "PM2.5 concentration in µg/m³."},
                            "pm10": {"type": "integer", "description": "PM10 concentration in µg/m³."},
                        },
                    },
                    "connectivity": {
                        "type": "object",
                        "required": ["status"],
                        "properties": {
                            "status": {"type": "string", "description": "WiFi connection status."},
                            "network": {"type": "string", "description": "WiFi network name/SSID."},
                            "signalStrength": {"type": "integer", "description": "WiFi signal strength in dBm (e.g., -65)."},
                        },
                    },
                    "location": {
                        "type": "object",
                        "properties": {
                            "country": {"type": "string", "description": "Device country location (e.g., PERU)."},
                        },
                    },
                    "healthStatus": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Device health status percentage (0-100)."},
                    "status": {"type": "string", "description": "Overall device status."},
                    "created_at": {"type": "string", "description": "Optional timestamp override."},
                },
            },
            "TelemetryResponse": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Database record ID."},
                    "device_id": {"type": "string", "description": "Logical device identifier."},
                    "device_time": {"type": "string", "description": "Device local time."},
                    "uptime_seconds": {"type": "integer", "description": "System uptime in seconds."},
                    "air_quality": {
                        "type": "object",
                        "properties": {
                            "co2": {"type": "number", "format": "float", "description": "CO2 in ppm."},
                            "temperature": {"type": "number", "format": "float", "description": "Temperature in Celsius."},
                            "humidity": {"type": "number", "description": "Relative humidity %."},
                        },
                    },
                    "particulate_matter": {
                        "type": "object",
                        "properties": {
                            "pm1_0": {"type": "integer", "description": "PM1.0 in µg/m³."},
                            "pm2_5": {"type": "integer", "description": "PM2.5 in µg/m³."},
                            "pm10": {"type": "integer", "description": "PM10 in µg/m³."},
                        },
                    },
                    "connectivity": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string", "description": "WiFi connection status."},
                            "network": {"type": "string", "description": "WiFi network name/SSID."},
                            "signal_strength": {"type": "integer", "description": "WiFi signal strength in dBm."},
                        },
                    },
                    "location": {
                        "type": "object",
                        "properties": {
                            "country": {"type": "string", "description": "Device country location."},
                        },
                    },
                    "health_status": {"type": "integer", "description": "Device health status percentage (0-100)."},
                    "status": {"type": "string", "description": "Overall device status."},
                    "recorded_at": {"type": "string", "format": "date-time", "description": "UTC timestamp when recorded."},
                },
            },
            "DeviceCacheRecord": {
                "type": "object",
                "required": ["device_id", "hardware_id", "api_key", "status"],
                "properties": {
                    "device_id": {"type": "string"},
                    "hardware_id": {"type": "string"},
                    "api_key": {"type": "string"},
                    "status": {"type": "string", "enum": ["OFFLINE", "ONLINE", "STANDBY", "ERROR", "MAINTENANCE", "DECOMMISSIONED"]},
                },
            },
            "DeviceCommand": {
                "type": "object",
                "properties": {
                    "commandId": {"type": "string"},
                    "deviceId": {"type": "string"},
                    "hardwareId": {"type": "string"},
                    "type": {"type": "string", "enum": ["STANDBY", "WAKE", "RESTART"]},
                    "status": {"type": "string", "enum": ["RECEIVED", "DELIVERED_TO_EMBEDDED", "EXECUTED", "FAILED"]},
                    "payload": {"type": "string", "nullable": True},
                    "receivedAt": {"type": "string", "format": "date-time"},
                    "deliveredAt": {"type": "string", "format": "date-time", "nullable": True},
                    "acknowledgedAt": {"type": "string", "format": "date-time", "nullable": True},
                    "failureReason": {"type": "string", "nullable": True},
                },
            },
            "AcknowledgeDeviceCommandRequest": {
                "type": "object",
                "required": ["status"],
                "properties": {
                    "status": {"type": "string", "enum": ["EXECUTED", "FAILED"]},
                    "failureReason": {"type": "string"},
                },
            },
            "DeviceChangedEvent": {
                "type": "object",
                "required": ["event_type", "device"],
                "properties": {
                    "event_type": {"type": "string", "example": "DeviceChanged"},
                    "device": {"$ref": "#/components/schemas/DeviceCacheRecord"},
                },
            },
            "DeviceConnectionStatusResponse": {
                "type": "object",
                "properties": {
                    "hardware_id": {"type": "string", "description": "Physical hardware identifier."},
                    "status": {"type": "string", "enum": ["ONLINE", "OFFLINE"], "description": "Connection status (ONLINE if telemetry received within 30s, OFFLINE otherwise)."},
                    "last_seen_at": {"type": "string", "format": "date-time", "nullable": True, "description": "UTC timestamp when device was last seen."},
                    "seconds_since_last_seen": {"type": "integer", "description": "Seconds elapsed since last telemetry (-1 if never seen)."},
                },
            },
            "AlertIncidentEvent": {
                "type": "object",
                "required": ["id", "metric", "status", "occurred_at", "resolved_at"],
                "properties": {
                    "id": {"type": "integer", "description": "Edge-local event ID used for ACK."},
                    "metric": {"type": "string", "description": "Metric identifier (e.g., CO2, PM25, TEMPERATURE, HUMIDITY)."},
                    "status": {"type": "string", "description": "Incident status (ACTIVE to start UX, RESOLVED to stop UX)."},
                    "occurred_at": {"type": "string", "format": "date-time", "description": "When the incident opened."},
                    "resolved_at": {"type": "string", "format": "date-time", "nullable": True, "description": "When the incident closed (nullable)."},
                },
            },
            "PendingAlertIncidentEventsResponse": {
                "type": "object",
                "required": ["count", "events"],
                "properties": {
                    "count": {"type": "integer"},
                    "events": {"type": "array", "items": {"$ref": "#/components/schemas/AlertIncidentEvent"}},
                },
            },
            "ErrorResponse": {
                "type": "object",
                "properties": {"error": {"type": "string"}},
            },
        },
    },
    "paths": {
        "/api/v1/device/telemetry": {
            "post": {
                "tags": ["Telemetry"],
                "summary": "Create environmental telemetry record",
                "description": "Authenticates the device locally using the SQLite cache, validates sensor readings (CO2, PM, temperature, humidity), and stores the optimized telemetry record with connectivity status.",
                "security": [{"DeviceCredentials": [], "DeviceApiKey": []}],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CreateTelemetryRequest"}}},
                },
                "responses": {
                    "201": {"description": "Telemetry record created.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/TelemetryResponse"}}}},
                    "400": {"description": "Missing fields or invalid telemetry values.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                    "401": {"description": "Missing or invalid device credentials.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                },
            }
        },
        "/api/v1/device/{hardware_id}/connection-status": {
            "get": {
                "tags": ["Telemetry"],
                "summary": "Get device connection status",
                "description": "Determines if a device is ONLINE or OFFLINE based on the time elapsed since its last telemetry was received. A device is considered OFFLINE if it hasn't sent telemetry in the last 30 seconds.",
                "parameters": [
                    {"name": "hardware_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "Physical hardware identifier of the device."}
                ],
                "responses": {
                    "200": {"description": "Connection status retrieved successfully.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DeviceConnectionStatusResponse"}}}},
                    "401": {"description": "Missing or invalid edge token.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                    "404": {"description": "Device not found.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                    "400": {"description": "Invalid request.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                },
            }
        },
        "/api/v1/device/commands/pending": {
            "get": {
                "tags": ["Commands"],
                "summary": "Get pending commands for embedded device",
                "description": "Authenticates the embedded device and returns locally cached commands, marking them as delivered.",
                "security": [{"DeviceCredentials": [], "DeviceApiKey": []}],
                "responses": {
                    "200": {"description": "Pending commands returned."},
                    "401": {"description": "Missing or invalid device credentials.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                },
            }
        },
        "/api/v1/device/commands/{commandId}/ack": {
            "post": {
                "tags": ["Commands"],
                "summary": "Acknowledge embedded command execution",
                "description": "Persists the embedded ACK locally and publishes it to HTTP for clair-core.",
                "security": [{"DeviceCredentials": [], "DeviceApiKey": []}],
                "parameters": [{"name": "commandId", "in": "path", "required": True, "schema": {"type": "string"}}],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AcknowledgeDeviceCommandRequest"}}},
                },
                "responses": {
                    "200": {"description": "Command acknowledged.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DeviceCommand"}}}},
                    "400": {"description": "Invalid ACK or unknown command.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                    "401": {"description": "Missing or invalid device credentials.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                },
            }
        },


        "/api/v1/alerting/incidents/pending": {
            "get": {
                "tags": ["Alerting"],
                "summary": "Get pending alert incident events",
                "description": "Authenticates the embedded device and returns locally cached alert incident events received from clair-core, marking them as delivered.",
                "security": [{"DeviceCredentials": [], "DeviceApiKey": []}],
                "responses": {
                    "200": {
                        "description": "Pending events returned.",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PendingAlertIncidentEventsResponse"}}},
                    },
                    "401": {"description": "Missing or invalid device credentials.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                },
            }
        },

        "/api/v1/alerting/incidents/{eventId}/ack": {
            "post": {
                "tags": ["Alerting"],
                "summary": "Acknowledge alert incident event",
                "description": "Marks an alert incident event as acknowledged by embedded.",
                "security": [{"DeviceCredentials": [], "DeviceApiKey": []}],
                "parameters": [{"name": "eventId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "responses": {
                    "200": {
                        "description": "Event acknowledged.",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AlertIncidentEvent"}}},
                    },
                    "400": {"description": "Unknown event.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                    "401": {"description": "Missing or invalid device credentials.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                },
            }
        },
        
    },
}


@docs_api.route("/openapi.json", methods=["GET"])
def openapi_json():
    """Return the OpenAPI contract consumed by Swagger UI."""
    return jsonify(OPENAPI_SPEC)


@docs_api.route("/docs", methods=["GET"])
def swagger_docs():
    """Render Swagger UI for the edge API at /docs."""
    return render_template_string(
        """
        <!doctype html>
        <html lang="en">
        <head>
          <meta charset="utf-8">
          <title>Clair Edge API Docs</title>
          <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
        </head>
        <body>
          <div id="swagger-ui"></div>
          <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
          <script>
            window.onload = function() {
              SwaggerUIBundle({ url: "/openapi.json", dom_id: "#swagger-ui" });
            };
          </script>
        </body>
        </html>
        """
    )
