"""Daemon poller for the authoritative clair-core device roster."""
from __future__ import annotations

import logging
import os
import threading

from provisioning.application.services.device_provisioning_application_service import (
    DeviceProvisioningApplicationService,
)
from shared.infrastructure.core_http_client import CoreHttpClient
from shared.infrastructure.models import SyncWatermarkModel
from shared.infrastructure.environment import get_positive_interval

logger = logging.getLogger(__name__)


class DeviceRosterPoller:
    POLL_INTERVAL_SECONDS = 30
    PAGE_SIZE = 200
    RESOURCE = "devices"

    def __init__(self, client=None, service=None):
        self.client = client or CoreHttpClient()
        self.service = service or DeviceProvisioningApplicationService()
        self.watermark = self._load_watermark()
        self._running = False
        self._thread = None
        self._trigger = threading.Event()

    def _load_watermark(self):
        try:
            row = SyncWatermarkModel.get_or_none(SyncWatermarkModel.resource == self.RESOURCE)
            return row.value if row else "0"
        except Exception:
            # init_db runs before the daemon; retain safe first-sync behavior if
            # a caller constructs the poller independently.
            return "0"

    def _save_watermark(self, value):
        try:
            SyncWatermarkModel.insert(resource=self.RESOURCE, value=value).on_conflict(
                conflict_target=[SyncWatermarkModel.resource], update={SyncWatermarkModel.value: value}
            ).execute()
        except Exception as exc:
            # A standalone poller may be used before database initialization;
            # the application initializes it before starting the worker.
            if SyncWatermarkModel._meta.database.is_closed() or "no such table" in str(exc):
                self.watermark = value
                return
            raise
        self.watermark = value

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="device-roster-poller")
        self._thread.start()

    def stop(self):
        self._running = False
        self._trigger.set()

    def trigger(self):
        self._trigger.set()

    def sync_once(self):
        initial_since = self.watermark
        since, after_id = initial_since, None
        try:
            while True:
                response = self.client.get(
                    "/api/v1/edge/devices",
                    {"since": since, "afterId": after_id, "limit": self.PAGE_SIZE},
                )
                if not isinstance(response, dict):
                    return False
                devices = response.get("devices", [])
                if not isinstance(devices, list):
                    return False
                self.service.sync_from_roster(devices)
                if not response.get("has_more", response.get("hasMore", False)):
                    watermark = response.get("watermark")
                    if watermark is not None:
                        self._save_watermark(str(watermark))
                    return True
                next_since = response.get("next_since", response.get("nextSince"))
                next_after_id = response.get("next_after_id", response.get("nextAfterId"))
                if next_since is None or next_after_id is None:
                    logger.error("Roster page is missing its continuation cursor")
                    return False
                since, after_id = next_since, next_after_id
        except Exception:
            logger.exception("Roster synchronization failed")
            return False

    def _run(self):
        base_interval = get_positive_interval(
            "DEVICE_ROSTER_POLL_INTERVAL_SECONDS", self.POLL_INTERVAL_SECONDS
        )
        interval = base_interval
        while self._running:
            try:
                succeeded = self.sync_once()
            except Exception:
                logger.exception("Roster synchronization failed")
                succeeded = False
            interval = base_interval if succeeded else min(interval * 2, base_interval * 16)
            self._trigger.wait(interval)
            if self._trigger.is_set():
                interval = base_interval
            self._trigger.clear()
