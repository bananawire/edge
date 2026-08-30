"""Peewee ORM model for the device_outbox table.

Stores pending outbound telemetry payloads for guaranteed delivery
to clair-core via the outbox pattern.
"""

from datetime import datetime, timezone
from peewee import (
    AutoField,
    CharField,
    DateTimeField,
    IntegerField,
    Model,
    TextField,
)

from shared.infrastructure.database import db


class OutboxRecordModel(Model):
    """Peewee model representing the 'device_outbox' table."""

    id = AutoField()
    aggregate_type = CharField(index=True)
    # Telemetry uses integer IDs; command ACKs use UUID/string IDs.
    # SQLite's affinity keeps existing telemetry rows readable during rollout.
    aggregate_id = TextField(index=True)
    event_type = CharField(index=True)
    status = CharField(default="pending")  # pending | sent | dead_letter
    retry_count = IntegerField(default=0)
    next_retry_at = DateTimeField()
    created_at = DateTimeField(default=datetime.now(timezone.utc))
    sent_at = DateTimeField(null=True)
    error_message = TextField(null=True)
    # Serialized immutable integration-event payload (captured by the application service).
    payload = TextField(null=True)

    class Meta:
        database = db
        table_name = "device_outbox"
        indexes = (
            (("status", "next_retry_at"), False),
            (("aggregate_type", "aggregate_id"), False),
        )
