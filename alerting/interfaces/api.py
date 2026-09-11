"""Alerting API — Flask blueprint for embedded alert incident delivery."""

from flask import Blueprint, jsonify, request

from alerting.application.services.alert_incident_event_application_service import (
    AlertIncidentEventApplicationService,
)
from iam.interfaces.services import authenticate_request

alerting_api = Blueprint("alerting_api", __name__)
alert_incident_event_service = AlertIncidentEventApplicationService()


@alerting_api.route("/api/v1/alerting/incidents/pending", methods=["GET"])
def get_pending_alert_incidents_for_embedded():
    """Return alert incident events pending for the authenticated embedded device.

    The embedded device pulls these events to start/stop local UX
    (LED/buzzer/screen) when an incident opens or closes.

    Headers:
        X-Hardware-Id: physical hardware identifier.
        X-API-Key: embedded device secret.

    Returns:
        200: Pending transitions, oldest first, each leased for redelivery until acked
             (``id`` is the edge-local transition id to ack; ``alert_id``/``sequence``
             identify the core alert and its state). An empty page means "nothing new",
             never "no active incidents".
        401: Missing or invalid device credentials.
    """
    auth_error = authenticate_request(touch=True)
    if auth_error is not None:
        return auth_error

    hardware_id = request.headers.get("X-Hardware-Id")
    try:
        events = alert_incident_event_service.get_pending_for_embedded(hardware_id)
        return jsonify({"count": len(events), "events": events}), 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@alerting_api.route("/api/v1/alerting/incidents/<int:event_id>/ack", methods=["POST"])
def acknowledge_alert_incident_event(event_id: int):
    """Acknowledge that the embedded processed an alert incident event.

    Headers:
        X-Hardware-Id: physical hardware identifier.
        X-API-Key: embedded device secret.

    Returns:
        200: ACK stored. Payload is minimal and echoes the event.
        400: Unknown event.
        401: Missing or invalid device credentials.
    """
    auth_error = authenticate_request(update_last_seen=False)
    if auth_error is not None:
        return auth_error

    hardware_id = request.headers.get("X-Hardware-Id")
    try:
        event = alert_incident_event_service.acknowledge_for_embedded(event_id, hardware_id)
        return jsonify(event), 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
