"""Outcome of one delivery attempt to clair-core, classified for the outbox."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DeliveryOutcome(str, Enum):
    DELIVERED = "delivered"   # core stored it (or already had it): mark sent
    RETRY = "retry"           # transient: connection, timeout, 5xx, 429: back off and retry forever
    BLOCKED = "blocked"       # auth/config: 401/403 or bad base URL: keep, back off long, alert operator
    REJECTED = "rejected"     # permanent: core validated and refused it: quarantine with the reason


@dataclass(frozen=True)
class DeliveryResult:
    outcome: DeliveryOutcome
    reason: str = ""

    @property
    def delivered(self) -> bool:
        return self.outcome is DeliveryOutcome.DELIVERED

    @staticmethod
    def delivered_ok() -> "DeliveryResult":
        return DeliveryResult(DeliveryOutcome.DELIVERED)

    @staticmethod
    def retry(reason: str) -> "DeliveryResult":
        return DeliveryResult(DeliveryOutcome.RETRY, reason)

    @staticmethod
    def blocked(reason: str) -> "DeliveryResult":
        return DeliveryResult(DeliveryOutcome.BLOCKED, reason)

    @staticmethod
    def rejected(reason: str) -> "DeliveryResult":
        return DeliveryResult(DeliveryOutcome.REJECTED, reason)

    @staticmethod
    def from_http_status(status: int, detail: str = "") -> "DeliveryResult":
        """Classify a non-success HTTP status the way the outbox must treat it."""
        if status in (401, 403):
            return DeliveryResult.blocked(f"HTTP {status} from core: check EDGE_TO_CORE_TOKEN {detail}".strip())
        if status == 429 or status >= 500:
            return DeliveryResult.retry(f"HTTP {status} {detail}".strip())
        if 400 <= status < 500:
            return DeliveryResult.rejected(f"HTTP {status} {detail}".strip())
        return DeliveryResult.retry(f"HTTP {status} {detail}".strip())
