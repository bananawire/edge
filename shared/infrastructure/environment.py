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
    return os.getenv("EDGE_DATABASE_PATH", "clair_edge.db").strip() or "clair_edge.db"


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
