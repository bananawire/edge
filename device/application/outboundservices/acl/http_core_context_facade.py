"""HTTP adapter for edge -> core delivery (contract v1, core-edge fixtures)."""

from __future__ import annotations

import json
import logging
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from device.application.outboundservices.acl.core_context_facade import CoreContextFacade
from device.application.outboundservices.acl.delivery_result import DeliveryResult
from shared.infrastructure.environment import get_core_base_url, get_core_http_timeout, get_edge_to_core_token

logger = logging.getLogger(__name__)


class HttpCoreContextFacadeImpl(CoreContextFacade):
    """Posts edge integration payloads to clair-core and classifies the reply.

    ``opener`` is injectable so unit tests stay deterministic; the default uses
    the standard library and adds no transport dependency.
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
        opener: Callable[..., object] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else get_core_base_url()
        self.token = token if token is not None else get_edge_to_core_token()
        self.timeout = timeout if timeout is not None else get_core_http_timeout()
        self._opener = opener or urlopen

    def publish_telemetry_recorded(self, payload: dict) -> DeliveryResult:
        """One record per batch; the per-record result decides, not the HTTP status alone."""
        status, body = self._post("/api/v1/evaluations/telemetry/batch", {"records": [payload]})
        if status is None:
            return DeliveryResult.retry(body)
        if not 200 <= status < 300:
            return DeliveryResult.from_http_status(status, body)
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            return DeliveryResult.retry("core batch response had no per-record result")
        result = results[0]
        if result.get("status") == "CREATED":
            return DeliveryResult.delivered_ok()
        reason = str(result.get("reason") or "UNKNOWN")
        # DEVICE_NOT_FOUND can be a roster lag on a brand-new unit: worth retrying for a while;
        # the processor escalates it to quarantine after the retry budget. VALIDATION_ERROR is final.
        if reason == "DEVICE_NOT_FOUND":
            return DeliveryResult.retry("core: DEVICE_NOT_FOUND")
        return DeliveryResult.rejected(f"core: {reason}")

    def publish_command_acknowledged(self, payload: dict) -> DeliveryResult:
        command_id = payload.get("command_id")
        if not command_id:
            return DeliveryResult.rejected("command_id missing from ACK payload")
        result = "OK" if payload.get("status") in {"EXECUTED", "OK"} else "FAILED"
        body = {
            "hardware_id": payload.get("hardware_id"),
            "result": result,
            "detail": payload.get("failure_reason"),
        }
        status, detail = self._post(f"/api/v1/edge/commands/{quote(str(command_id), safe='')}/ack", body)
        if status is None:
            return DeliveryResult.retry(detail)
        if 200 <= status < 300 or status == 409:
            # 409: core already holds a terminal result for this command; nothing more to deliver.
            return DeliveryResult.delivered_ok()
        if status == 404:
            return DeliveryResult.rejected("core: command unknown or not owned by this unit")
        return DeliveryResult.from_http_status(status, detail if isinstance(detail, str) else "")

    def _post(self, path: str, body: dict):
        """Return (status, parsed body or error text). Status None means no HTTP reply at all."""
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
                raw = response.read()
                try:
                    parsed = json.loads(raw) if raw else {}
                except ValueError:
                    parsed = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                return response.status, parsed
        except HTTPError as exc:
            return exc.code, exc.reason if isinstance(exc.reason, str) else ""
        except (URLError, TimeoutError, OSError) as exc:
            logger.warning("Core unreachable (%s): %s", path, exc)
            return None, f"core unreachable: {exc}"
