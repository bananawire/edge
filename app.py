"""Edge Service — Flask application entry point.

Registers bounded-context blueprints, initializes SQLite, and starts the
reliable telemetry outbox and local presence monitor. Core synchronization
pollers can be added to this composition as their HTTP contracts land.
"""

import logging
import os
import hmac

from dotenv import load_dotenv
from flask import Flask, request

load_dotenv()

from alerting.interfaces.api import alerting_api
from alerting.application.alert_poller import AlertIncidentPoller
from device.application.command_poller import DeviceCommandPoller
from device.application.outbox_processor import TelemetryOutboxProcessor
from device.interfaces.api import device_api
from iam.application.device_presence_monitor import DevicePresenceMonitor
from iam.interfaces.services import iam_api
from provisioning.application.device_roster_poller import DeviceRosterPoller
from shared.infrastructure.database import init_db
from shared.infrastructure.environment import (
    get_edge_cors_allowed_headers,
    get_edge_cors_allowed_origins,
)
from shared.interfaces.docs_api import docs_api

app = Flask(__name__)
app.register_blueprint(iam_api)
app.register_blueprint(device_api)
app.register_blueprint(alerting_api)
app.register_blueprint(docs_api)


@app.get("/health")
def health():
    return {"status": "UP"}, 200


logger = logging.getLogger(__name__)
_initialized = False
_outbox_processor = TelemetryOutboxProcessor()
_device_presence_monitor = DevicePresenceMonitor()
_device_roster_poller = DeviceRosterPoller()
_command_poller = DeviceCommandPoller()
_alert_poller = AlertIncidentPoller()


@app.post("/api/v1/edge/notify")
def edge_notify():
    """Accept a lightweight core notification and pull the authoritative roster."""
    # Core authenticates in the core->edge direction with EDGE_TOKEN.
    expected = os.getenv("EDGE_TOKEN", "")
    supplied = request.headers.get("X-Edge-Token", "")
    if not expected or not hmac.compare_digest(supplied, expected):
        return {"error": "Unauthorized"}, 401
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return {"error": "JSON body must be an object"}, 400
    resource = payload.get("resource")
    if resource not in ("device", "command", "alert"):
        return {"error": "Unsupported resource"}, 400
    # Event.set() only wakes the already-running daemon; it never performs
    # network or database work on the request thread.
    if resource == "device":
        _device_roster_poller.trigger()
    elif resource == "command":
        _command_poller.trigger()
    else:
        _alert_poller.trigger()
    return {"status": "accepted"}, 200


@app.after_request
def add_cors_headers(response):
    """Allow browser clients to call the edge API with device auth headers."""
    allowed_origins = get_edge_cors_allowed_origins()
    request_origin = request.headers.get("Origin")
    if "*" in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif request_origin in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = request_origin
        response.headers.add("Vary", "Origin")
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = get_edge_cors_allowed_headers()
    response.headers["Access-Control-Max-Age"] = "86400"
    return response


@app.before_request
def initialize():
    """Initialize persistence and start edge-local background workers once."""
    global _initialized
    if not _initialized:
        init_db()
        _outbox_processor.start()
        _device_presence_monitor.start()
        _device_roster_poller.start()
        _command_poller.start()
        _alert_poller.start()
        _initialized = True


if __name__ == "__main__":
    initialize()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
