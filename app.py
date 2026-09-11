"""Edge Service — Flask application entry point.

Registers bounded-context blueprints, initializes SQLite, and starts the
reliable telemetry outbox and local presence monitor. Core synchronization
pollers can be added to this composition as their HTTP contracts land.
"""

import logging
import os
import hmac
import threading
import time

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
from shared.infrastructure.database import db, init_db
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
    """Liveness: the process answers. Says nothing about the database or the core."""
    return {"status": "UP"}, 200


@app.get("/ready")
def ready():
    """Readiness: workers started, local database answers, and the roster synced recently.

    A 503 here means the edge should not receive device traffic yet (a device would get 401
    because its identity is not cached). ``EDGE_READY_ROSTER_MAX_AGE_SECONDS`` bounds how old
    the last successful roster sync may be (default 300; 0 disables the check).
    """
    checks = {"workers": _initialized, "database": False, "roster": False}
    try:
        with db.connection_context():
            db.execute_sql("SELECT 1").fetchone()
        checks["database"] = True
    except Exception as exc:  # pragma: no cover - depends on the local file
        logger.warning("Readiness: database check failed: %s", exc)
    max_age = float(os.getenv("EDGE_READY_ROSTER_MAX_AGE_SECONDS", "300") or 0)
    last = getattr(_device_roster_poller, "last_success_at", None)
    checks["roster"] = max_age <= 0 or (last is not None and time.time() - last <= max_age)
    status = 200 if all(checks.values()) else 503
    return {"status": "READY" if status == 200 else "NOT_READY", "checks": checks}, status


logger = logging.getLogger(__name__)
_initialized = False
_init_lock = threading.Lock()
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


def start_workers() -> bool:
    """Initialize persistence and start the background workers exactly once per process.

    Idempotent and thread-safe: the WSGI entry point calls it at import time, the dev server
    calls it before serving, and a stray early request cannot start a second set of pollers.
    This edge is designed for ONE worker process; run it under a single-process server
    (waitress, or gunicorn with ``-w 1``) so there is one roster/command/alert poller.
    """
    global _initialized
    if _initialized:
        return False
    with _init_lock:
        if _initialized:
            return False
        init_db()
        _outbox_processor.start()
        _device_presence_monitor.start()
        _device_roster_poller.start()
        _command_poller.start()
        _alert_poller.start()
        _initialized = True
        logger.info("Edge workers started (pid %s)", os.getpid())
        return True


@app.before_request
def initialize():
    """Safety net for servers that import the app without calling start_workers()."""
    if not _initialized:
        start_workers()


if __name__ == "__main__":
    start_workers()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
