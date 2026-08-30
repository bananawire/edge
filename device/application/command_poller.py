"""Periodic HTTP poller for commands pending in clair-core."""
from __future__ import annotations
import logging
import os
import threading
from device.application.services import DeviceCommandApplicationService
from shared.infrastructure.core_http_client import CoreHttpClient
from shared.infrastructure.environment import get_positive_interval

logger = logging.getLogger(__name__)

class DeviceCommandPoller:
    POLL_INTERVAL_SECONDS = 5
    def __init__(self, client=None, service=None):
        self.client = client or CoreHttpClient()
        self.service = service or DeviceCommandApplicationService()
        self._running = False
        self._thread = None
        self._trigger = threading.Event()
    def start(self):
        if self._running: return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="device-command-poller")
        self._thread.start()
    def stop(self):
        self._running = False
        self._trigger.set()
    def trigger(self): self._trigger.set()
    def poll_once(self):
        messages = self.client.get("/api/v1/edge/commands/pending")
        if not isinstance(messages, list):
            return 0
        ingested = self.service.ingest_command_messages(messages)
        # Ingestion is deliberately not an acknowledgement.  Only the embedded
        # device, after executing the command, may acknowledge it via the device
        # ACK endpoint; otherwise Core would mark commands executed prematurely.
        return len(ingested)
    def _run(self):
        while self._running:
            try: self.poll_once()
            except Exception: logger.exception("Command poll failed")
            interval = get_positive_interval(
                "EDGE_COMMAND_POLL_INTERVAL_SECONDS", self.POLL_INTERVAL_SECONDS
            )
            self._trigger.wait(interval)
            self._trigger.clear()
