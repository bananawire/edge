"""Best-effort HTTP publisher for device presence transitions."""

from __future__ import annotations

import json
import logging
import os
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from shared.infrastructure.environment import get_core_base_url, get_core_http_timeout, get_edge_to_core_token

logger = logging.getLogger(__name__)


class CorePresenceHttpPublisher:
    """Send presence to core without coupling IAM to HTTP implementation."""

    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: float | None = None, opener: Callable[..., object] | None = None) -> None:
        self.base_url = base_url.rstrip("/") if base_url else get_core_base_url()
        self.token = token if token is not None else get_edge_to_core_token()
        self.timeout = timeout if timeout is not None else get_core_http_timeout()
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
