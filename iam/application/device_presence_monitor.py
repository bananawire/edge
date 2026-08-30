"""Background monitor for device online/offline status transitions."""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from iam.application.services import DevicePresenceApplicationService
from shared.infrastructure.database import db
from shared.infrastructure.environment import get_positive_interval

logger = logging.getLogger(__name__)


class DevicePresenceMonitor:
    """Marks devices OFFLINE when telemetry stops arriving."""

    OFFLINE_THRESHOLD_SECONDS = 30
    POLL_INTERVAL_SECONDS = 5

    def __init__(self) -> None:
        self.device_presence_service = DevicePresenceApplicationService()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("DevicePresenceMonitor started")

    def stop(self) -> None:
        self._running = False

    def _run(self) -> None:
        interval = get_positive_interval(
            "EDGE_PRESENCE_POLL_INTERVAL_SECONDS", self.POLL_INTERVAL_SECONDS
        )
        while self._running:
            try:
                if db.is_closed():
                    db.connect()
                offline_before = datetime.now(timezone.utc) - timedelta(
                    seconds=self.OFFLINE_THRESHOLD_SECONDS
                )
                updated = self.device_presence_service.mark_stale_devices_offline(offline_before)
                if updated:
                    logger.info("Marked %s stale devices OFFLINE", updated)
            except Exception:
                logger.exception("DevicePresenceMonitor loop error")
            finally:
                if not db.is_closed():
                    db.close()
            time.sleep(interval)
