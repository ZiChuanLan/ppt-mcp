"""Runtime settings for the local MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    api_base_url: str = "http://127.0.0.1:8000/"
    api_timeout_seconds: float = 120.0
    api_bearer_token: str | None = None
    route_workflow_store_dir: Path = _REPO_ROOT / "var" / "route-workflows"
    route_workflow_ttl_seconds: int = 60 * 60


def load_settings() -> Settings:
    """Load runtime settings from environment variables."""
    base_url = os.getenv("PPT_API_BASE_URL", "http://127.0.0.1:8000").strip()
    timeout_raw = os.getenv("PPT_API_TIMEOUT_SECONDS", "120").strip()
    bearer_token = os.getenv("PPT_API_BEARER_TOKEN", "").strip() or None
    workflow_store_dir_raw = os.getenv(
        "PPT_MCP_ROUTE_WORKFLOW_STORE_DIR",
        str(_REPO_ROOT / "var" / "route-workflows"),
    ).strip()
    workflow_ttl_raw = os.getenv("PPT_MCP_ROUTE_WORKFLOW_TTL_SECONDS", "3600").strip()
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError as exc:
        raise ValueError(
            "PPT_API_TIMEOUT_SECONDS must be a number"
        ) from exc
    try:
        workflow_ttl_seconds = max(60, int(workflow_ttl_raw))
    except ValueError as exc:
        raise ValueError(
            "PPT_MCP_ROUTE_WORKFLOW_TTL_SECONDS must be an integer"
        ) from exc
    normalized_base_url = base_url.rstrip("/") + "/"
    workflow_store_dir = Path(workflow_store_dir_raw).expanduser()
    if not workflow_store_dir.is_absolute():
        workflow_store_dir = (_REPO_ROOT / workflow_store_dir).resolve()
    else:
        workflow_store_dir = workflow_store_dir.resolve()
    return Settings(
        api_base_url=normalized_base_url,
        api_timeout_seconds=timeout_seconds,
        api_bearer_token=bearer_token,
        route_workflow_store_dir=workflow_store_dir,
        route_workflow_ttl_seconds=workflow_ttl_seconds,
    )
