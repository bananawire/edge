"""Immutable payload snapshots for outbox rows.

This separate table keeps new events durable on installations whose legacy
``device_outbox`` table predates the payload column.  It is deliberately
linked by the outbox id rather than by a mutable aggregate.
"""

from peewee import IntegerField, Model, TextField

from shared.infrastructure.database import db


class OutboxPayloadSnapshotModel(Model):
    """One immutable serialized integration payload per outbox entry."""

    outbox_id = IntegerField(unique=True, index=True, null=False)
    payload = TextField(null=False)

    class Meta:
        database = db
        table_name = "device_outbox_payload_snapshot"
