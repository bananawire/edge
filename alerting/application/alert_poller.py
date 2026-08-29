"""Periodic HTTP poller for alert incidents pending in clair-core."""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from urllib.parse import quote

from alerting.application.services.alert_incident_event_application_service import (
    AlertIncidentEventApplicationService,
)
from shared.infrastructure.core_http_client import CoreHttpClient
from shared.infrastructure.environment import get_positive_interval

logger = logging.getLogger(__name__)


class AlertIncidentPoller:
    POLL_INTERVAL_SECONDS = 5

    def __init__(self, client=None, service=None):
        self.client = client or CoreHttpClient()
        self.service = service or AlertIncidentEventApplicationService()
        self._running = False
        self._thread = None
        self._trigger = threading.Event()

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="alert-incident-poller"
        )
        self._thread.start()

    def stop(self):
        self._running = False
        self._trigger.set()

    def trigger(self):
        self._trigger.set()

    def poll_once(self):
        messages = self.client.get("/api/v1/edge/alerts/pending")
        if not isinstance(messages, list):
            return 0
        stored = 0
        for message in messages:
            if not isinstance(message, dict):
                continue
            try:
                result = self.service.ingest_alert_incident_changed_event(message)
                stored += int(result.stored)
                alert_id = message.get("alert_id") or message.get("alertId")
                hardware_id = message.get("hardware_id") or message.get("hardwareId")
                if alert_id and hardware_id:
                    acknowledged = self.client.post(
                        f"/api/v1/edge/alerts/{quote(str(alert_id), safe='')}/ack",
                        {
                            "hardware_id": hardware_id,
                            "acknowledged_at": datetime.now(timezone.utc).isoformat(),
                        },
                        accept_conflict=True,
                    )
                    if acknowledged is None:
                        logger.warning("Alert ACK failed for %s", alert_id)
            except (KeyError, TypeError, ValueError):
                logger.warning("Skipping malformed alert from HTTP: %s", message)
        return stored

    def _run(self):
        interval = get_positive_interval(
            "EDGE_ALERT_POLL_INTERVAL_SECONDS", self.POLL_INTERVAL_SECONDS
        )
        while self._running:
            try:
                self.poll_once()
            except Exception:
                logger.exception("Alert incident poll failed")
            self._trigger.wait(interval)
            self._trigger.clear()
