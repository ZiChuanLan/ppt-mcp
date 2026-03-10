"""Upload-backed source staging for the hosted remote MCP server."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class SourceRecord:
    """Persisted uploaded source metadata."""

    source_id: str
    status: str
    filename: str
    mime_type: str
    expected_size_bytes: int | None
    expected_sha256: str | None
    actual_size_bytes: int | None
    actual_sha256: str | None
    created_at: str
    expires_at: str
    file_path: str
    upload_token: str
    origin: str
    origin_value: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceRecord":
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SourceStore:
    """Local filesystem-backed upload staging."""

    def __init__(self, root_dir: Path, upload_ttl_seconds: int) -> None:
        self._root_dir = root_dir
        self._upload_ttl_seconds = upload_ttl_seconds
        self._meta_dir = root_dir / "sources"
        self._file_dir = root_dir / "files"
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        self._file_dir.mkdir(parents=True, exist_ok=True)

    def create_upload(
        self,
        *,
        filename: str,
        mime_type: str,
        size_bytes: int | None,
        sha256: str | None,
    ) -> SourceRecord:
        source_id = f"src_{uuid4().hex}"
        now = _utc_now()
        expires_at = now + timedelta(seconds=self._upload_ttl_seconds)
        suffix = Path(filename).suffix or ".pdf"
        file_path = self._file_dir / f"{source_id}{suffix}"
        record = SourceRecord(
            source_id=source_id,
            status="awaiting_upload",
            filename=filename,
            mime_type=mime_type,
            expected_size_bytes=size_bytes,
            expected_sha256=sha256,
            actual_size_bytes=None,
            actual_sha256=None,
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            file_path=str(file_path),
            upload_token=secrets.token_urlsafe(24),
            origin="upload",
        )
        self.save(record)
        return record

    def create_url_source(
        self,
        *,
        filename: str,
        mime_type: str,
        size_bytes: int | None,
        sha256: str | None,
        origin_value: str,
    ) -> SourceRecord:
        source_id = f"src_{uuid4().hex}"
        now = _utc_now()
        expires_at = now + timedelta(seconds=self._upload_ttl_seconds)
        suffix = Path(filename).suffix or ".pdf"
        file_path = self._file_dir / f"{source_id}{suffix}"
        record = SourceRecord(
            source_id=source_id,
            status="ready",
            filename=filename,
            mime_type=mime_type,
            expected_size_bytes=size_bytes,
            expected_sha256=sha256,
            actual_size_bytes=size_bytes,
            actual_sha256=sha256,
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            file_path=str(file_path),
            upload_token="",
            origin="url",
            origin_value=origin_value,
        )
        self.save(record)
        return record

    def get(self, source_id: str) -> SourceRecord | None:
        path = self._meta_path(source_id)
        if not path.exists():
            return None
        return SourceRecord.from_dict(json.loads(path.read_text()))

    def save(self, record: SourceRecord) -> None:
        self._meta_path(record.source_id).write_text(
            json.dumps(record.to_dict(), ensure_ascii=True, indent=2)
        )

    def write_upload_stream(
        self,
        *,
        source_id: str,
        upload_token: str,
        stream: BinaryIO,
        max_upload_bytes: int,
    ) -> SourceRecord:
        record = self.require(source_id)
        if record.upload_token != upload_token:
            raise ValueError("Invalid upload token")
        target = Path(record.file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        with target.open("wb") as file_handle:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_upload_bytes:
                    raise ValueError("Upload exceeds max_upload_bytes")
                file_handle.write(chunk)
        record.status = "uploaded"
        record.actual_size_bytes = total
        self.save(record)
        return record

    def finalize_upload(self, source_id: str) -> SourceRecord:
        record = self.require(source_id)
        path = Path(record.file_path)
        if not path.exists():
            raise FileNotFoundError("Uploaded file not found")
        sha256 = hashlib.sha256()
        with path.open("rb") as file_handle:
            while True:
                chunk = file_handle.read(1024 * 1024)
                if not chunk:
                    break
                sha256.update(chunk)
        digest = sha256.hexdigest()
        size_bytes = path.stat().st_size
        if (
            record.expected_size_bytes is not None
            and size_bytes != record.expected_size_bytes
        ):
            raise ValueError("Uploaded file size does not match expected_size_bytes")
        if record.expected_sha256 and digest != record.expected_sha256:
            raise ValueError("Uploaded file sha256 does not match expected_sha256")
        record.actual_size_bytes = size_bytes
        record.actual_sha256 = digest
        record.status = "ready"
        self.save(record)
        return record

    def require_ready(self, source_id: str) -> SourceRecord:
        record = self.require(source_id)
        if record.status != "ready":
            raise ValueError("Source is not ready")
        return record

    def require(self, source_id: str) -> SourceRecord:
        record = self.get(source_id)
        if record is None:
            raise FileNotFoundError(f"Unknown source_id: {source_id}")
        return record

    def _meta_path(self, source_id: str) -> Path:
        return self._meta_dir / f"{source_id}.json"
