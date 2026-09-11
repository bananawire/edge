"""Peewee ORM models for the Alerting bounded context.

Stores alert incident events received from clair-core so the embedded device
can pull them from the edge API.
"""

from peewee import (
    AutoField,
    CharField,
    DateTimeField,
    IntegerField,
    Model,
)

from shared.infrastructure.database import db


class AlertIncidentEventModel(Model):
    """Edge-local record of alert incident events emitted by clair-core."""

    id = AutoField()

    # Key used by embedded authentication and command pulling.
    hardware_id = CharField(index=True)

    # Values from core integration event.
    alert_id = CharField(index=True)
    # Core transition sequence; one row per transition. Null only on rows older than contract v1.
    sequence = IntegerField(null=True)
    device_id = CharField(index=True)
    space_id = CharField(null=True)
    metric = CharField()
    status = CharField(index=True)
    message = CharField(null=True)
    threshold_value = CharField(null=True)
    actual_value = CharField(null=True)
    occurred_at = DateTimeField()
    resolved_at = DateTimeField(null=True)

    received_at = DateTimeField()
    delivered_at = DateTimeField(null=True)
    acknowledged_at = DateTimeField(null=True)

    class Meta:
        database = db
        table_name = "alert_incident_events"
        indexes = (
            (("hardware_id", "delivered_at"), False),
            (("hardware_id", "received_at"), False),
            (("alert_id", "sequence"), True),
        )
