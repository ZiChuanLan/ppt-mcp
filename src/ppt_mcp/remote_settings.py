"""Settings for the hosted remote MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RemoteSettings:
    bind_host: str
    bind_port: int
    public_base_url: str
    server_token: str | None
    upload_ttl_seconds: int
    data_dir: Path
    profile_store_path: Path
    max_upload_bytes: int


def load_remote_settings() -> RemoteSettings:
    """Load remote-server settings from environment variables."""
    bind_host = os.getenv("PPT_MCP_BIND_HOST", "0.0.0.0").strip()
    bind_port_raw = os.getenv("PPT_MCP_BIND_PORT", "8080").strip()
    public_base_url = os.getenv(
        "PPT_MCP_PUBLIC_BASE_URL", f"http://127.0.0.1:{bind_port_raw}"
    ).strip()
    server_token = os.getenv("PPT_MCP_SERVER_TOKEN", "").strip() or None
    upload_ttl_raw = os.getenv("PPT_MCP_UPLOAD_TTL_SECONDS", "3600").strip()
    max_upload_raw = os.getenv("PPT_MCP_MAX_UPLOAD_BYTES", "104857600").strip()
    data_dir_raw = os.getenv("PPT_MCP_DATA_DIR", str(_REPO_ROOT / "var" / "remote"))
    profile_store_raw = os.getenv(
        "PPT_MCP_PROFILE_STORE", str(_REPO_ROOT / "config" / "profiles.example.json")
    )

    try:
        bind_port = int(bind_port_raw)
    except ValueError as exc:
        raise ValueError("PPT_MCP_BIND_PORT must be an integer") from exc
    try:
        upload_ttl_seconds = int(upload_ttl_raw)
    except ValueError as exc:
        raise ValueError("PPT_MCP_UPLOAD_TTL_SECONDS must be an integer") from exc
    try:
        max_upload_bytes = int(max_upload_raw)
    except ValueError as exc:
        raise ValueError("PPT_MCP_MAX_UPLOAD_BYTES must be an integer") from exc

    return RemoteSettings(
        bind_host=bind_host,
        bind_port=bind_port,
        public_base_url=public_base_url.rstrip("/"),
        server_token=server_token,
        upload_ttl_seconds=upload_ttl_seconds,
        data_dir=Path(data_dir_raw).expanduser().resolve(),
        profile_store_path=Path(profile_store_raw).expanduser().resolve(),
        max_upload_bytes=max_upload_bytes,
    )
