"""Runtime settings for the local MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    api_base_url: str = "http://127.0.0.1:8000/"
    api_timeout_seconds: float = 120.0
    api_bearer_token: str | None = None


def load_settings() -> Settings:
    """Load runtime settings from environment variables."""
    base_url = os.getenv("PPT_API_BASE_URL", "http://127.0.0.1:8000").strip()
    timeout_raw = os.getenv("PPT_API_TIMEOUT_SECONDS", "120").strip()
    bearer_token = os.getenv("PPT_API_BEARER_TOKEN", "").strip() or None
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError as exc:
        raise ValueError(
            "PPT_API_TIMEOUT_SECONDS must be a number"
        ) from exc
    normalized_base_url = base_url.rstrip("/") + "/"
    return Settings(
        api_base_url=normalized_base_url,
        api_timeout_seconds=timeout_seconds,
        api_bearer_token=bearer_token,
    )
