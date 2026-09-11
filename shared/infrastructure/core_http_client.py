"""Small authenticated HTTP client for edge-to-core polling."""
from __future__ import annotations

import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from shared.infrastructure.environment import get_core_base_url, get_core_http_timeout, get_edge_to_core_token

logger = logging.getLogger(__name__)


class CoreHttpClient:
    def __init__(self, base_url=None, token=None, timeout=None, opener=None):
        self.base_url = base_url.rstrip("/") if base_url else get_core_base_url()
        self.token = token if token is not None else get_edge_to_core_token()
        self.timeout = timeout if timeout is not None else get_core_http_timeout()
        self.opener = opener or urlopen

    def get(self, path, params=None):
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
        request = Request(url, headers={"Accept": "application/json", "X-Core-Token": self.token})
        try:
            with self.opener(request, timeout=self.timeout) as response:
                if not 200 <= response.status < 300:
                    return None
                return json.loads(response.read() or b"{}")
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            logger.warning("Core HTTP GET failed (%s): %s", path, exc)
            return None

    def post(self, path, body=None, accept_conflict=False):
        request = Request(
            f"{self.base_url}{path}", data=json.dumps(body or {}).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json", "X-Core-Token": self.token},
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                if response.status == 409 and accept_conflict:
                    return {}
                if not 200 <= response.status < 300:
                    return None
                raw = response.read()
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            if accept_conflict and exc.code == 409:
                return {}
            logger.warning("Core HTTP POST failed (%s): %s", path, exc)
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            logger.warning("Core HTTP POST failed (%s): %s", path, exc)
        return None
