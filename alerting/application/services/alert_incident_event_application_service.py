"""Alert incident event ingestion and embedded delivery orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from dateutil import parser as dateutil_parser

from alerting.infrastructure.alert_incident_event_repository import (
    AlertIncidentEventRepository,
)
from shared.infrastructure.database import db


@dataclass(frozen=True)
class IngestAlertIncidentEventResult:
    stored: bool
    event_id: int | None


class AlertIncidentEventApplicationService:
    """Application service for Core -> Edge alert incident events."""

    def __init__(self) -> None:
        self._repository = AlertIncidentEventRepository()

    def ingest_alert_incident_changed_event(self, payload: dict) -> IngestAlertIncidentEventResult:
        """Persist a single core integration event locally.

        Expected payload keys are snake_case (preferred) but camelCase is tolerated.
        """

        normalized = self._normalize_payload(payload)

        with db.atomic():
            existing = self._repository.find_by_alert_id(
                normalized["alert_id"], normalized["hardware_id"]
            )
            if existing is not None:
                if existing.status == normalized["status"]:
                    return IngestAlertIncidentEventResult(stored=False, event_id=existing.id)
                model = self._repository.update_from_integration_payload(
                    existing,
                    normalized,
                    received_at=datetime.now(timezone.utc),
                )
                return IngestAlertIncidentEventResult(stored=True, event_id=model.id)
            model = self._repository.create_from_integration_payload(
                normalized,
                received_at=datetime.now(timezone.utc),
            )
        return IngestAlertIncidentEventResult(stored=True, event_id=model.id)

    def get_pending_for_embedded(self, hardware_id: str, limit: int = 50) -> list[dict]:
        """Return pending alert incident events and mark them as delivered."""

        events = self._repository.find_pending_for_hardware_id(hardware_id, limit=limit)
        now = datetime.now(timezone.utc)
        for e in events:
            self._repository.mark_delivered(e, delivered_at=now)

        return [self._to_dict(e) for e in events]

    def acknowledge_for_embedded(self, event_id: int, hardware_id: str) -> dict:
        """Mark an alert incident event as acknowledged by embedded."""

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

        return {
            "alert_id": payload.get("alert_id") or payload.get("alertId"),
            "device_id": payload.get("device_id") or payload.get("deviceId"),
            "hardware_id": hardware_id,
            "space_id": payload.get("space_id") or payload.get("spaceId"),
            "metric": payload.get("metric"),
            "threshold_value": payload.get("threshold_value") or payload.get("thresholdValue"),
            "actual_value": payload.get("actual_value") or payload.get("actualValue"),
            "message": payload.get("message"),
            "status": payload.get("status"),
            "occurred_at": AlertIncidentEventApplicationService._parse_timestamp(occurred_at),
            "resolved_at": AlertIncidentEventApplicationService._parse_timestamp(resolved_at)
            if resolved_at
            else None,
        }

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        parsed = dateutil_parser.parse(value)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _to_dict(model) -> dict:
        return {
            "id": model.id,
            "metric": model.metric,
            "status": model.status,
            "occurred_at": model.occurred_at.isoformat(),
            "resolved_at": model.resolved_at.isoformat() if model.resolved_at else None,
        }
