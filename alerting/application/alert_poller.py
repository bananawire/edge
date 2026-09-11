"""Poller for alert transitions pending in clair-core (contract v1, core-edge/alerts.*)."""
from __future__ import annotations

import logging
import threading
from urllib.parse import quote

from alerting.application.services.alert_incident_event_application_service import (
    AlertIncidentEventApplicationService,
)
from shared.infrastructure.core_http_client import CoreHttpClient
from shared.infrastructure.environment import get_positive_interval
from shared.infrastructure.models import SyncWatermarkModel

logger = logging.getLogger(__name__)


class AlertIncidentPoller:
    """Pages `/api/v1/edge/alerts/pending` by transition sequence.

    For every transition: store it locally, send core a delivery receipt, and only then advance
    the persisted cursor. The poller never calls the business ACK; that is the device's decision,
    relayed elsewhere when a human acknowledges on the unit.
    """

    POLL_INTERVAL_SECONDS = 5
    PAGE_SIZE = 200
    RESOURCE = "alerts"

    def __init__(self, client=None, service=None):
        self.client = client or CoreHttpClient()
        self.service = service or AlertIncidentEventApplicationService()
        self.cursor = self._load_cursor()
        self._running = False
        self._thread = None
        self._trigger = threading.Event()

    def _load_cursor(self):
        try:
            row = SyncWatermarkModel.get_or_none(SyncWatermarkModel.resource == self.RESOURCE)
            return int(row.value) if row and str(row.value).isdigit() else None
        except Exception:
            return None

    def _save_cursor(self, value: int) -> None:
        try:
            SyncWatermarkModel.insert(resource=self.RESOURCE, value=str(value)).on_conflict(
                conflict_target=[SyncWatermarkModel.resource], update={SyncWatermarkModel.value: str(value)}
            ).execute()
        except Exception as exc:
            if SyncWatermarkModel._meta.database.is_closed() or "no such table" in str(exc):
                self.cursor = value
                return
            raise
        self.cursor = value

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="alert-incident-poller")
        self._thread.start()

    def stop(self):
        self._running = False
        self._trigger.set()

    def trigger(self):
        self._trigger.set()

    def poll_once(self):
        stored_total = 0
        while True:
            params = {"after_sequence": self.cursor, "limit": self.PAGE_SIZE}
            messages = self.client.get("/api/v1/edge/alerts/pending", params)
            if not isinstance(messages, list):
                return stored_total
            highest = self.cursor
            for message in messages:
                if not isinstance(message, dict):
                    continue
                try:
                    result = self.service.ingest_alert_incident_changed_event(message)
                except (KeyError, TypeError, ValueError):
                    logger.warning("Skipping malformed alert transition from core: %s", message)
                    continue
                stored_total += int(result.stored)
                alert_id = message.get("alert_id") or message.get("alertId")
                hardware_id = message.get("hardware_id") or message.get("hardwareId")
                sequence = message.get("sequence")
                if alert_id and hardware_id and sequence is not None:
                    receipt = self.client.post(
                        f"/api/v1/edge/alerts/{quote(str(alert_id), safe='')}/receipt",
                        {"hardware_id": hardware_id, "sequence": int(sequence)},
                    )
                    if receipt is None:
                        # Stored locally anyway; core will offer it again and we will dedupe it.
                        logger.warning("Alert receipt failed for %s#%s", alert_id, sequence)
                        continue
                    if highest is None or int(sequence) > highest:
                        highest = int(sequence)
            if highest is not None and highest != self.cursor:
                self._save_cursor(highest)
            if len(messages) < self.PAGE_SIZE:
                return stored_total

    def _run(self):
        interval = get_positive_interval("EDGE_ALERT_POLL_INTERVAL_SECONDS", self.POLL_INTERVAL_SECONDS)
        while self._running:
            try:
                self.poll_once()
            except Exception:
                logger.exception("Alert incident poll failed")
            self._trigger.wait(interval)
            self._trigger.clear()
