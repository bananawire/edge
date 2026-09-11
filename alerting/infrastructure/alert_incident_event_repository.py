"""AlertIncidentEventRepository — one row per core alert transition."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from peewee import DoesNotExist

from alerting.infrastructure.models import AlertIncidentEventModel


class AlertIncidentEventRepository:
    """Stores alert transitions received from clair-core for embedded delivery."""

    @staticmethod
    def create_transition(payload: dict, received_at: datetime) -> AlertIncidentEventModel:
        return AlertIncidentEventModel.create(
            hardware_id=payload["hardware_id"],
            alert_id=str(payload["alert_id"]),
            sequence=payload.get("sequence"),
            device_id=str(payload["device_id"]),
            space_id=str(payload["space_id"]) if payload.get("space_id") else None,
            metric=str(payload.get("metric")),
            status=str(payload.get("status")),
            message=payload.get("message"),
            threshold_value=str(payload["threshold_value"]) if payload.get("threshold_value") is not None else None,
            actual_value=str(payload["actual_value"]) if payload.get("actual_value") is not None else None,
            occurred_at=payload["occurred_at"],
            resolved_at=payload.get("resolved_at"),
            received_at=received_at,
            delivered_at=None,
            acknowledged_at=None,
        )

    @staticmethod
    def find_transition(alert_id: str, sequence) -> Optional[AlertIncidentEventModel]:
        if sequence is None:
            return None
        return AlertIncidentEventModel.get_or_none(
            (AlertIncidentEventModel.alert_id == str(alert_id))
            & (AlertIncidentEventModel.sequence == int(sequence))
        )

    @staticmethod
    def find_latest_for_alert(alert_id: str, hardware_id: str) -> Optional[AlertIncidentEventModel]:
        return (AlertIncidentEventModel.select()
                .where((AlertIncidentEventModel.alert_id == str(alert_id))
                       & (AlertIncidentEventModel.hardware_id == hardware_id))
                .order_by(AlertIncidentEventModel.sequence.desc(nulls="LAST"), AlertIncidentEventModel.id.desc())
                .first())

    @staticmethod
    def find_pending_for_hardware_id(hardware_id: str, lease_seconds: float, limit: int = 50) -> list[AlertIncidentEventModel]:
        """Transitions not yet acked by the device, including delivered ones whose lease expired."""
        lease_expiry = datetime.now(timezone.utc) - timedelta(seconds=lease_seconds)
        return list(
            AlertIncidentEventModel.select()
            .where(
                (AlertIncidentEventModel.hardware_id == hardware_id)
                & AlertIncidentEventModel.acknowledged_at.is_null(True)
                & (AlertIncidentEventModel.delivered_at.is_null(True)
                   | (AlertIncidentEventModel.delivered_at <= lease_expiry))
            )
            .order_by(AlertIncidentEventModel.sequence.asc(nulls="FIRST"), AlertIncidentEventModel.id.asc())
            .limit(limit)
        )

    @staticmethod
    def mark_delivered(event: AlertIncidentEventModel, delivered_at: Optional[datetime] = None) -> None:
        event.delivered_at = delivered_at or datetime.now(timezone.utc)
        event.save()

    @staticmethod
    def acknowledge(event_id: int, hardware_id: str, acknowledged_at: Optional[datetime] = None) -> AlertIncidentEventModel:
        try:
            event = AlertIncidentEventModel.get(
                (AlertIncidentEventModel.id == event_id) & (AlertIncidentEventModel.hardware_id == hardware_id)
            )
        except DoesNotExist as exc:
            raise ValueError("Unknown alert incident event") from exc
        if event.acknowledged_at is None:
            event.acknowledged_at = acknowledged_at or datetime.now(timezone.utc)
            event.save()
        return event
