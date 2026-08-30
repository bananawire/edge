"""OutboxEntry entity — represents a pending outbound message to clair-core.

Part of the Device bounded context; ensures reliable delivery of telemetry
payloads from edge to core via the outbox pattern.
"""

from datetime import datetime, timezone
from typing import Optional


class OutboxEntry:
    """Domain entity for an outbox record ensuring reliable delivery to clair-core.

    Attributes:
        id: Database ID (None before persistence).
        aggregate_type: Source aggregate type, e.g. TELEMETRY.
        aggregate_id: Source aggregate identifier.
        event_type: Integration event type to publish.
        status: pending, sent, or dead_letter.
        retry_count: Number of delivery attempts made.
        next_retry_at: UTC timestamp when the entry is eligible for retry.
        created_at: UTC timestamp when the entry was created.
        sent_at: UTC timestamp when successfully sent (None if not sent).
        error_message: Last error message if delivery failed.
    """

    def __init__(
        self,
        aggregate_type: str,
        aggregate_id: int | str,
        event_type: str,
        status: str = "pending",
        retry_count: int = 0,
        next_retry_at: Optional[datetime] = None,
        created_at: Optional[datetime] = None,
        id: Optional[int] = None,
        sent_at: Optional[datetime] = None,
        error_message: Optional[str] = None,
        payload: Optional[str] = None,
    ):
        if not aggregate_type:
            raise ValueError("aggregate_type is required")
        if aggregate_id is None:
            raise ValueError("aggregate_id is required")
        if not event_type:
            raise ValueError("event_type is required")
        if status not in ("pending", "sent", "dead_letter"):
            raise ValueError("status must be pending, sent, or dead_letter")

        self.id = id
        self.aggregate_type = aggregate_type
        self.aggregate_id = aggregate_id
        self.event_type = event_type
        self.status = status
        self.retry_count = retry_count
        self.next_retry_at = next_retry_at or datetime.now(timezone.utc)
        self.created_at = created_at or datetime.now(timezone.utc)
        self.sent_at = sent_at
        self.error_message = error_message
        # Immutable serialized integration-event snapshot captured at enqueue time.
        self.payload = payload
