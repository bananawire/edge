"""OutboxRepository — infrastructure layer for reliable outbound delivery.

Provides data access operations for OutboxEntry persistence and retrieval,
mapping between Peewee models and domain entities.
"""

from datetime import datetime, timezone
from typing import List

from peewee import JOIN

from device.domain.outbox_entry import OutboxEntry
from device.infrastructure.outbox.outbox_record_model import OutboxRecordModel
from device.infrastructure.outbox.outbox_payload_snapshot_model import OutboxPayloadSnapshotModel
from shared.infrastructure.database import db


class OutboxRepository:
    """Repository for OutboxEntry aggregate root persistence."""

    def save(self, entry: OutboxEntry) -> OutboxEntry:
        """Persist an outbox entry to the database.

        Args:
            entry: OutboxEntry domain entity to persist.

        Returns:
            A new OutboxEntry domain entity with the database-assigned ID.
        """
        values = {
            "aggregate_type": entry.aggregate_type,
            "aggregate_id": str(entry.aggregate_id),
            "event_type": entry.event_type,
            "status": entry.status,
            "retry_count": entry.retry_count,
            "next_retry_at": entry.next_retry_at,
            "created_at": entry.created_at,
        }
        # Keep the record and its immutable snapshot in one transaction. A
        # stale snapshot can exist when SQLite reuses an outbox id after a
        # crash/interrupted cleanup; remove it before inserting the new one.
        with db.atomic():
            model = OutboxRecordModel.create(**values)
            OutboxPayloadSnapshotModel.delete().where(
                OutboxPayloadSnapshotModel.outbox_id == model.id
            ).execute()
            if entry.payload is not None:
                OutboxPayloadSnapshotModel.create(
                    outbox_id=model.id,
                    payload=entry.payload,
                )
        return self._model_to_entity(model)

    def find_pending(self, limit: int = 10) -> List[OutboxEntry]:
        """Find pending outbox entries eligible for retry.

        Args:
            limit: Maximum number of entries to fetch.

        Returns:
            List of OutboxEntry entities ordered by creation time.
        """
        now = datetime.now(timezone.utc)
        query = (
            self._select_for_schema()
            .where(
                (OutboxRecordModel.status == "pending")
                & (OutboxRecordModel.next_retry_at <= now)
            )
            .order_by(OutboxRecordModel.created_at)
            .limit(limit)
        )
        return [self._model_to_entity(m) for m in query]

    def mark_sent(self, entry_id: int) -> None:
        """Mark an outbox entry as successfully sent."""
        OutboxRecordModel.update(
            status="sent",
            sent_at=datetime.now(timezone.utc),
            error_message=None,
        ).where(OutboxRecordModel.id == entry_id).execute()

    def mark_retry(
        self, entry_id: int, next_retry_at: datetime, error: str
    ) -> None:
        """Mark an outbox entry for retry with updated schedule."""
        OutboxRecordModel.update(
            status="pending",
            retry_count=OutboxRecordModel.retry_count + 1,
            next_retry_at=next_retry_at,
            error_message=error,
        ).where(OutboxRecordModel.id == entry_id).execute()

    def mark_dead_letter(self, entry_id: int, error: str) -> None:
        """Mark an outbox entry as dead letter after exhausting retries."""
        OutboxRecordModel.update(
            status="dead_letter",
            error_message=error,
        ).where(OutboxRecordModel.id == entry_id).execute()

    def delete_sent_older_than(self, before: datetime) -> int:
        """Delete sent outbox records older than the cutoff.

        Args:
            before: UTC cutoff datetime.

        Returns:
            Number of deleted rows.
        """
        sent_ids = [
            row.id
            for row in OutboxRecordModel.select(OutboxRecordModel.id).where(
                (OutboxRecordModel.status == "sent")
                & (OutboxRecordModel.sent_at <= before)
            )
        ]
        with db.atomic():
            # Delete children first so cleanup is safe even when deployments
            # later add a foreign key to the snapshot table.
            if sent_ids:
                OutboxPayloadSnapshotModel.delete().where(
                    OutboxPayloadSnapshotModel.outbox_id << sent_ids
                ).execute()
            # Also remove leftovers from an interrupted/older cleanup. Use a
            # left join rather than NOT IN: nullable values in a NOT IN
            # subquery can make the predicate match no rows at all.
            orphan_ids = (
                OutboxPayloadSnapshotModel
                .select(OutboxPayloadSnapshotModel.id)
                .join(
                    OutboxRecordModel,
                    JOIN.LEFT_OUTER,
                    on=(OutboxPayloadSnapshotModel.outbox_id == OutboxRecordModel.id),
                )
                .where(OutboxRecordModel.id.is_null())
            )
            OutboxPayloadSnapshotModel.delete().where(
                OutboxPayloadSnapshotModel.id << orphan_ids
            ).execute()
            if not sent_ids:
                return 0
            return (
                OutboxRecordModel.delete()
                .where(OutboxRecordModel.id << sent_ids)
                .execute()
            )

    def _model_to_entity(self, model: OutboxRecordModel) -> OutboxEntry:
        """Convert a Peewee model instance to an OutboxEntry domain entity."""
        return OutboxEntry(
            id=model.id,
            aggregate_type=model.aggregate_type,
            aggregate_id=model.aggregate_id,
            event_type=model.event_type,
            status=model.status,
            retry_count=model.retry_count,
            next_retry_at=model.next_retry_at,
            created_at=model.created_at,
            sent_at=model.sent_at,
            error_message=model.error_message,
            payload=self._snapshot_for(model),
        )

    @staticmethod
    def _snapshot_for(model: OutboxRecordModel) -> str | None:
        """Read the immutable snapshot linked to this exact outbox id."""
        snapshot = OutboxPayloadSnapshotModel.get_or_none(
            OutboxPayloadSnapshotModel.outbox_id == model.id
        )
        if snapshot is not None:
            return snapshot.payload
        # Rows written by an earlier rollout may already have the optional
        # column.  Reading it is only a backwards-compatibility fallback;
        # new writes always use the auxiliary model above.
        return getattr(model, "payload", None)

    @staticmethod
    def _has_payload_column() -> bool:
        """Report schema capability through Peewee, without hand-written SQL."""
        return any(column.name == "payload" for column in db.get_columns("device_outbox"))

    def _select_for_schema(self):
        fields = [
            field for name, field in OutboxRecordModel._meta.fields.items()
            if name != "payload" or self._has_payload_column()
        ]
        return OutboxRecordModel.select(*fields)
