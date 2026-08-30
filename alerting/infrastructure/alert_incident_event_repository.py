"""AlertIncidentEventRepository.

Maps ORM models to plain dicts suitable for API responses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from peewee import DoesNotExist

from alerting.infrastructure.models import AlertIncidentEventModel


class AlertIncidentEventRepository:
    """Repository for alert incident events stored on edge."""

    @staticmethod
    def create_from_integration_payload(payload: dict, received_at: datetime) -> AlertIncidentEventModel:
        return AlertIncidentEventModel.create(
            hardware_id=payload["hardware_id"],
            alert_id=str(payload.get("alert_id") or payload.get("alertId")),
            device_id=str(payload.get("device_id") or payload.get("deviceId")),
            space_id=str(payload.get("space_id") or payload.get("spaceId")) if (payload.get("space_id") or payload.get("spaceId")) else None,
            metric=str(payload.get("metric")),
            status=str(payload.get("status")),
            message=payload.get("message"),
            threshold_value=str(payload.get("threshold_value")) if payload.get("threshold_value") is not None else None,
            actual_value=str(payload.get("actual_value")) if payload.get("actual_value") is not None else None,
            occurred_at=payload["occurred_at"],
            resolved_at=payload.get("resolved_at"),
            received_at=received_at,
            delivered_at=None,
            acknowledged_at=None,
        )

    @staticmethod
    def update_from_integration_payload(
        event: AlertIncidentEventModel, payload: dict, received_at: datetime
    ) -> AlertIncidentEventModel:
        """Apply a lifecycle transition and make it pending for embedded again."""

        event.device_id = str(payload["device_id"])
        event.space_id = str(payload["space_id"]) if payload.get("space_id") else None
        event.metric = str(payload.get("metric"))
        event.status = str(payload.get("status"))
        event.message = payload.get("message")
        event.threshold_value = (
            str(payload["threshold_value"])
            if payload.get("threshold_value") is not None
            else None
        )
        event.actual_value = (
            str(payload["actual_value"])
            if payload.get("actual_value") is not None
            else None
        )
        event.occurred_at = payload["occurred_at"]
        event.resolved_at = payload.get("resolved_at")
        event.received_at = received_at
        event.delivered_at = None
        event.acknowledged_at = None
        event.save()
        return event

    @staticmethod
    def find_by_alert_id(alert_id: str, hardware_id: str):
        return AlertIncidentEventModel.get_or_none(
            (AlertIncidentEventModel.alert_id == str(alert_id))
            & (AlertIncidentEventModel.hardware_id == hardware_id)
        )

    @staticmethod
    def find_pending_for_hardware_id(hardware_id: str, limit: int = 50) -> list[AlertIncidentEventModel]:
        return list(
            AlertIncidentEventModel.select()
            .where(
                (AlertIncidentEventModel.hardware_id == hardware_id)
                & (AlertIncidentEventModel.delivered_at.is_null(True))
            )
            .order_by(AlertIncidentEventModel.received_at.asc())
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

        event.acknowledged_at = acknowledged_at or datetime.now(timezone.utc)
        event.save()
        return event
