"""HTTP ACL adapter for communication from edge to clair-core."""

from __future__ import annotations

import json
import logging
import os
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from device.application.outboundservices.acl.core_context_facade import CoreContextFacade

logger = logging.getLogger(__name__)


class HttpCoreContextFacadeImpl(CoreContextFacade):
    """Posts edge integration payloads to the clair-core HTTP contract.

    ``opener`` is injectable to keep the adapter deterministic in unit tests;
    the default uses the Python standard library and therefore adds no runtime
    transport dependency.
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
        opener: Callable[..., object] | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("CLAIR_CORE_BASE_URL", "https://127.0.0.1:8080")).rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.scheme.lower() != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("CLAIR_CORE_BASE_URL must use HTTPS outside localhost")
        self.token = token if token is not None else os.getenv("EDGE_TO_CORE_TOKEN", "")
        self.timeout = timeout if timeout is not None else float(os.getenv("CLAIR_CORE_HTTP_TIMEOUT", "10"))
        self._opener = opener or urlopen

    def publish_telemetry_recorded(self, payload: dict) -> bool:
        """Deliver one outbox record using the core batch endpoint."""
        response = self._post(
            "/api/v1/evaluations/telemetry/batch", {"records": [payload]}
        )
        if response is None:
            return False
        results = response.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            return False
        return results[0].get("status") == "CREATED"

    def publish_command_acknowledged(self, payload: dict) -> bool:
        """Deliver a command acknowledgement; 409 is an idempotent success."""
        command_id = payload.get("command_id")
        if not command_id:
            return False
        result = "OK" if payload.get("status") in {"EXECUTED", "OK"} else "FAILED"
        body = {
            "hardware_id": payload.get("hardware_id"),
            "acknowledged_at": payload.get("acknowledged_at"),
            "result": result,
            "detail": payload.get("failure_reason"),
        }
        return self._post(
            f"/api/v1/edge/commands/{quote(str(command_id), safe='')}/ack", body,
            accept_conflict=True,
        ) is not None

    def _post(self, path: str, body: dict, *, accept_conflict: bool = False) -> dict | None:
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Core-Token": self.token,
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                if response.status == 409 and accept_conflict:
                    return {}
                if not 200 <= response.status < 300:
                    return None
                raw = response.read()
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            if accept_conflict and exc.code == 409:
                return {}
            logger.warning("Core HTTP request failed (%s): %s", exc.code, path)
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            logger.warning("Core HTTP request failed (%s): %s", path, exc)
        return None
