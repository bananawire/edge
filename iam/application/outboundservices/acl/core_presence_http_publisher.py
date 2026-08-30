"""Best-effort HTTP publisher for device presence transitions."""

from __future__ import annotations

import json
import logging
import os
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class CorePresenceHttpPublisher:
    """Send presence to core without coupling IAM to HTTP implementation."""

    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: float | None = None, opener: Callable[..., object] | None = None) -> None:
        self.base_url = (base_url or os.getenv("CLAIR_CORE_BASE_URL", "https://127.0.0.1:8080")).rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.scheme.lower() != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("CLAIR_CORE_BASE_URL must use HTTPS outside localhost")
        self.token = token if token is not None else os.getenv("EDGE_TO_CORE_TOKEN", "")
        self.timeout = timeout if timeout is not None else float(os.getenv("CLAIR_CORE_HTTP_TIMEOUT", "10"))
        self._opener = opener or urlopen

    def publish_device_presence_changed(self, payload: dict) -> bool:
        request = Request(
            f"{self.base_url}/api/v1/edge/presence",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Core-Token": self.token},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                return 200 <= response.status < 300
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            logger.warning("Failed to publish presence to core: %s", exc)
            return False
