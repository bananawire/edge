"""Alert transition ingestion (core -> edge) and embedded delivery (edge -> device)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from dateutil import parser as dateutil_parser

from alerting.infrastructure.alert_incident_event_repository import AlertIncidentEventRepository
from shared.infrastructure.database import db
from shared.infrastructure.environment import get_alert_delivery_lease_seconds


@dataclass(frozen=True)
class IngestAlertIncidentEventResult:
    stored: bool
    event_id: int | None
    sequence: int | None


class AlertIncidentEventApplicationService:
    """Every core transition becomes one local row; the device acks each row it processed.

    Storing is a delivery receipt towards core (the poller sends it after this returns) and
    never a business acknowledgement. Rows are redelivered to the device until acked; an ack
    for an older transition of an alert is recorded but never hides a newer one.
    """

    def __init__(self, repository: AlertIncidentEventRepository | None = None) -> None:
        self._repository = repository or AlertIncidentEventRepository()

    def ingest_alert_incident_changed_event(self, payload: dict) -> IngestAlertIncidentEventResult:
        normalized = self._normalize_payload(payload)
        with db.atomic():
            existing = self._repository.find_transition(normalized["alert_id"], normalized["sequence"])
            if existing is not None:
                return IngestAlertIncidentEventResult(stored=False, event_id=existing.id, sequence=existing.sequence)
            if normalized["sequence"] is None:
                # Pre-v1 core without sequences: fall back to (alert, status) identity.
                latest = self._repository.find_latest_for_alert(normalized["alert_id"], normalized["hardware_id"])
                if latest is not None and latest.status == normalized["status"]:
                    return IngestAlertIncidentEventResult(stored=False, event_id=latest.id, sequence=None)
            model = self._repository.create_transition(normalized, received_at=datetime.now(timezone.utc))
        return IngestAlertIncidentEventResult(stored=True, event_id=model.id, sequence=model.sequence)

    def get_pending_for_embedded(self, hardware_id: str, limit: int = 50) -> list[dict]:
        """Transitions the device has not acked, oldest first; each is leased for redelivery."""
        events = self._repository.find_pending_for_hardware_id(
            hardware_id, lease_seconds=get_alert_delivery_lease_seconds(), limit=limit)
        now = datetime.now(timezone.utc)
        with db.atomic():
            for event in events:
                self._repository.mark_delivered(event, delivered_at=now)
        return [self._to_dict(event) for event in events]

    def acknowledge_for_embedded(self, event_id: int, hardware_id: str) -> dict:
        model = self._repository.acknowledge(event_id=event_id, hardware_id=hardware_id)
        return self._to_dict(model)

    @staticmethod
    def _normalize_payload(payload: dict) -> dict:
        hardware_id = payload.get("hardware_id") or payload.get("hardwareId")
        if not hardware_id:
            raise ValueError("Missing hardware_id")
        occurred_at = payload.get("occurred_at") or payload.get("occurredAt")
        resolved_at = payload.get("resolved_at") or payload.get("resolvedAt")
        if not occurred_at:
            raise ValueError("Missing occurred_at")
        alert_id = payload.get("alert_id") or payload.get("alertId")
        device_id = payload.get("device_id") or payload.get("deviceId")
        if not alert_id or not device_id:
            raise ValueError("Missing alert_id or device_id")
        sequence = payload.get("sequence")
        return {
            "alert_id": str(alert_id),
            "sequence": int(sequence) if sequence is not None else None,
            "device_id": str(device_id),
            "hardware_id": hardware_id,
            "space_id": payload.get("space_id") or payload.get("spaceId"),
            "metric": payload.get("metric"),
            "threshold_value": payload.get("threshold_value") if payload.get("threshold_value") is not None else payload.get("thresholdValue"),
            "actual_value": payload.get("actual_value") if payload.get("actual_value") is not None else payload.get("actualValue"),
            "message": payload.get("message"),
            "status": payload.get("status"),
            "occurred_at": AlertIncidentEventApplicationService._parse_timestamp(occurred_at),
            "resolved_at": AlertIncidentEventApplicationService._parse_timestamp(resolved_at) if resolved_at else None,
        }

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        return dateutil_parser.parse(value).astimezone(timezone.utc)

    @staticmethod
    def _to_dict(model) -> dict:
        occurred = model.occurred_at if isinstance(model.occurred_at, datetime) else dateutil_parser.parse(str(model.occurred_at))
        resolved = model.resolved_at
        if resolved is not None and not isinstance(resolved, datetime):
            resolved = dateutil_parser.parse(str(resolved))
        return {
            "id": model.id,
            "alert_id": model.alert_id,
            "sequence": model.sequence,
            "metric": model.metric,
            "status": model.status,
            "occurred_at": occurred.isoformat(),
            "resolved_at": resolved.isoformat() if resolved else None,
        }
