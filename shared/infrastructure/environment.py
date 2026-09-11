"""Shared environment configuration.

All integration with clair-core is now via HTTP.  Environment helpers
are stateless and context-agnostic.
"""

from __future__ import annotations

import math
import os


def _optional(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value if value else default


def get_edge_database_path() -> str:
    """Return the local libSQL file path used as the production fallback.

    Production deployments should set EDGE_TURSO_URL/EDGE_TURSO_TOKEN instead
    and leave this empty. The path is honored only when no remote Turso
    database is configured, keeping unit tests and offline local dev working
    through the same libSQL client.
    """
    return os.getenv("EDGE_DATABASE_PATH", "clair_edge.db").strip() or "clair_edge.db"


def get_edge_turso_url() -> str:
    """Return the remote libsql:// URL of the Turso database, or empty.

    When non-empty the edge talks to Turso over HTTP. Empty means: fall back
    to the local libSQL file (EDGEDATABASE_PATH).
    """
    return os.getenv("EDGE_TURSO_URL", "").strip()


def get_edge_turso_token() -> str:
    """Return the JWT used to authenticate against the remote Turso database.

    Required when EDGE_TURSO_URL is set. Returned as an empty string otherwise
    so unit tests can construct a TursoDatabase without supplying a token.
    """
    return os.getenv("EDGE_TURSO_TOKEN", "").strip()


def _flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")


def get_core_base_url() -> str:
    """Return the clair-core base URL every edge->core client uses.

    Defaults to the core's own default port on this laptop. Plain HTTP is
    accepted for loopback hosts and, when ``CLAIR_CORE_ALLOW_INSECURE_HTTP``
    is set, for private/container hostnames on a trusted local network.
    Anything else must be HTTPS.
    """
    from urllib.parse import urlparse

    raw = _optional("CLAIR_CORE_BASE_URL", "http://localhost:49220").rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme.lower() == "https":
        return raw
    if parsed.scheme.lower() != "http":
        raise ValueError("CLAIR_CORE_BASE_URL must be an http(s) URL")
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"} or _flag("CLAIR_CORE_ALLOW_INSECURE_HTTP"):
        return raw
    raise ValueError(
        "CLAIR_CORE_BASE_URL must use HTTPS outside localhost "
        "(set CLAIR_CORE_ALLOW_INSECURE_HTTP=true only on a trusted local network)"
    )


def get_core_http_timeout() -> float:
    try:
        return max(float(os.getenv("CLAIR_CORE_HTTP_TIMEOUT", "10")), 1.0)
    except ValueError:
        return 10.0


def get_edge_to_core_token() -> str:
    return os.getenv("EDGE_TO_CORE_TOKEN", "").strip()


def get_edge_require_measured_at() -> bool:
    """When true, a reading without ``measured_at`` is rejected instead of stamped with receipt time."""
    return _flag("EDGE_REQUIRE_MEASURED_AT", default=False)


def get_outbox_dead_letter_retention_hours() -> float:
    try:
        return max(float(os.getenv("EDGE_OUTBOX_DEAD_LETTER_RETENTION_HOURS", "168")), 1.0)
    except ValueError:
        return 168.0


def get_positive_interval(name: str, default: float, minimum: float = 0.1) -> float:
    """Read a worker interval safely, preventing a busy loop from bad config."""
    raw = os.getenv(name)
    try:
        value = float(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(value, minimum)


def get_edge_public_base_url() -> str:
    # Only used for docs. Do not require.
    return os.getenv("EDGE_PUBLIC_BASE_URL", "http://127.0.0.1:5000").strip() or "http://127.0.0.1:5000"


def get_edge_cors_allowed_origins() -> list[str]:
    """Return allowed CORS origins.

    Use "*" for development or embedded clients with many origins. In production,
    prefer a comma-separated allowlist such as "https://admin.example.com".
    """
    value = os.getenv("EDGE_CORS_ALLOWED_ORIGINS", "*").strip()
    if not value:
        return ["*"]
    return [origin.strip() for origin in value.split(",") if origin.strip()]


def get_edge_cors_allowed_headers() -> str:
    return os.getenv(
        "EDGE_CORS_ALLOWED_HEADERS",
        "Content-Type,X-Hardware-Id,X-API-Key",
    ).strip()
