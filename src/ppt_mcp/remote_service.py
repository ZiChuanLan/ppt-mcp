"""Remote MCP service layer."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from ppt_mcp.api_client import PptApiClient, PptApiError
from ppt_mcp.remote_catalog import REMOTE_PIPELINES, get_remote_pipeline
from ppt_mcp.remote_profiles import ProfileRecord, ProfileStore
from ppt_mcp.remote_settings import RemoteSettings
from ppt_mcp.source_store import SourceRecord, SourceStore
from ppt_mcp.settings import Settings as UpstreamSettings


class RemoteServiceError(RuntimeError):
    """Structured remote-server error."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


class RemoteService:
    """Hosted MCP wrapper around the existing ppt API."""

    def __init__(
        self,
        *,
        remote_settings: RemoteSettings,
        upstream_settings: UpstreamSettings,
        profile_store: ProfileStore,
        source_store: SourceStore,
    ) -> None:
        self.remote_settings = remote_settings
        self.upstream_settings = upstream_settings
        self.profile_store = profile_store
        self.source_store = source_store
        self.api_client = PptApiClient(upstream_settings)
        self._fetch_client = httpx.Client(timeout=upstream_settings.api_timeout_seconds)

    def close(self) -> None:
        self.api_client.close()
        self._fetch_client.close()

    def list_profiles(self) -> dict[str, Any]:
        return {"profiles": [item.to_public_dict() for item in self.profile_store.list_profiles()]}

    def list_pipelines(self) -> dict[str, Any]:
        return {
            "pipelines": [
                {
                    "pipeline_id": item.pipeline_id,
                    "title": item.title,
                    "summary": item.summary,
                    "required_profile_kind": item.required_profile_kind,
                    "supports_page_range": True,
                    "notes": list(item.notes),
                }
                for item in REMOTE_PIPELINES
            ]
        }

    def create_upload(
        self,
        *,
        filename: str,
        mime_type: str,
        size_bytes: int | None,
        sha256: str | None,
    ) -> dict[str, Any]:
        record = self.source_store.create_upload(
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
        )
        return {
            "source_id": record.source_id,
            "upload": {
                "upload_mode": "single_put",
                "upload_url": (
                    f"{self.remote_settings.public_base_url}/uploads/{record.source_id}"
                    f"?token={quote(record.upload_token, safe='')}"
                ),
                "required_headers": {
                    "Content-Type": mime_type,
                },
                "expires_at": record.expires_at,
            },
        }

    def finalize_upload(self, *, source_id: str) -> dict[str, Any]:
        try:
            record = self.source_store.finalize_upload(source_id)
        except FileNotFoundError as exc:
            raise RemoteServiceError(
                code="invalid_source",
                message=str(exc),
            ) from exc
        except ValueError as exc:
            raise RemoteServiceError(
                code="upload_incomplete",
                message=str(exc),
                details={"source_id": source_id},
            ) from exc
        return {
            "source_id": record.source_id,
            "status": record.status,
            "mime_type": record.mime_type,
            "size_bytes": record.actual_size_bytes,
        }

    def create_job(
        self,
        *,
        source: dict[str, Any],
        pipeline_id: str,
        profile_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pipeline = get_remote_pipeline(pipeline_id)
        if pipeline is None:
            raise RemoteServiceError(
                code="invalid_pipeline",
                message=f"Unknown pipeline_id: {pipeline_id}",
            )
        profile = self.profile_store.get_profile(profile_id)
        if profile is None:
            raise RemoteServiceError(
                code="invalid_profile",
                message=f"Unknown profile_id: {profile_id}",
            )
        if profile.kind != pipeline.required_profile_kind:
            raise RemoteServiceError(
                code="profile_pipeline_mismatch",
                message="Profile kind does not match pipeline requirements",
                details={
                    "profile_id": profile_id,
                    "profile_kind": profile.kind,
                    "pipeline_id": pipeline_id,
                    "required_profile_kind": pipeline.required_profile_kind,
                },
            )
        source_record = self._resolve_source(source)
        merged_options = self._build_job_options(
            pipeline_id=pipeline_id,
            pipeline_fields=pipeline.job_fields,
            profile=profile,
            options=options or {},
        )
        try:
            payload = self.api_client.create_job(
                pdf_path=source_record.file_path,
                options=merged_options,
            )
        except PptApiError as exc:
            raise RemoteServiceError(
                code="provider_error",
                message=exc.message,
                details={"upstream_code": exc.code, "upstream_details": exc.details},
            ) from exc
        return {
            "job_id": payload["job_id"],
            "status": payload["status"],
            "pipeline_id": pipeline_id,
            "profile_id": profile_id,
            "source_id": source_record.source_id,
            "created_at": payload.get("created_at"),
            "expires_at": payload.get("expires_at"),
        }

    def get_job_status(self, *, job_id: str) -> dict[str, Any]:
        return self.api_client.get_job_status(job_id)

    def cancel_job(self, *, job_id: str) -> dict[str, Any]:
        return self.api_client.cancel_job(job_id)

    def get_job_artifacts(self, *, job_id: str) -> dict[str, Any]:
        payload = self.api_client.get_job_artifacts(job_id)
        source_pdf_url = None
        if payload.get("source_pdf_url"):
            source_pdf_url = (
                f"{self.remote_settings.public_base_url}/jobs/{job_id}/artifacts/file"
                "?path=input.pdf"
            )
        payload["source_pdf_url"] = source_pdf_url
        for key in (
            "original_images",
            "cleaned_images",
            "final_preview_images",
            "ocr_overlay_images",
            "layout_before_images",
            "layout_after_images",
        ):
            for item in payload.get(key, []) or []:
                path = item.get("path")
                if not isinstance(path, str):
                    continue
                item["url"] = (
                    f"{self.remote_settings.public_base_url}/jobs/{job_id}/artifacts/file"
                    f"?path={quote(path, safe='')}"
                )
        return payload

    def get_result_download(self, *, job_id: str) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "filename": f"converted-{job_id}.pptx",
            "download_url": f"{self.remote_settings.public_base_url}/jobs/{job_id}/download",
            "requires_bearer_token": bool(self.remote_settings.server_token),
        }

    def _resolve_source(self, source: dict[str, Any]) -> SourceRecord:
        source_type = str(source.get("type") or "").strip().lower()
        if source_type == "upload":
            source_id = str(source.get("source_id") or "").strip()
            if not source_id:
                raise RemoteServiceError(
                    code="invalid_source",
                    message="source_id is required for upload sources",
                )
            try:
                return self.source_store.require_ready(source_id)
            except FileNotFoundError as exc:
                raise RemoteServiceError(code="invalid_source", message=str(exc)) from exc
            except ValueError as exc:
                raise RemoteServiceError(
                    code="source_not_ready",
                    message=str(exc),
                    details={"source_id": source_id},
                ) from exc
        if source_type == "url":
            url = str(source.get("url") or "").strip()
            if not url:
                raise RemoteServiceError(
                    code="invalid_source",
                    message="url is required for url sources",
                )
            return self._fetch_url_source(url)
        raise RemoteServiceError(
            code="invalid_source",
            message=f"Unsupported source type: {source_type or '<empty>'}",
        )

    def _fetch_url_source(self, url: str) -> SourceRecord:
        parsed = urlparse(url)
        filename = Path(parsed.path).name or "remote.pdf"
        suffix = Path(filename).suffix.lower()
        if suffix != ".pdf":
            filename = f"{filename}.pdf" if suffix else "remote.pdf"
        try:
            response = self._fetch_client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RemoteServiceError(
                code="invalid_source",
                message="Failed to fetch URL source",
                details={"url": url, "error": str(exc)},
            ) from exc
        content_type = response.headers.get("content-type", "application/pdf")
        if "pdf" not in content_type.lower():
            raise RemoteServiceError(
                code="invalid_source",
                message="URL source did not return a PDF content type",
                details={"content_type": content_type},
            )
        content = response.content
        if len(content) > self.remote_settings.max_upload_bytes:
            raise RemoteServiceError(
                code="invalid_source",
                message="URL source exceeds max_upload_bytes",
                details={"max_upload_bytes": self.remote_settings.max_upload_bytes},
            )
        digest = hashlib.sha256(content).hexdigest()
        record = self.source_store.create_url_source(
            filename=filename,
            mime_type="application/pdf",
            size_bytes=len(content),
            sha256=digest,
            origin_value=url,
        )
        path = Path(record.file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return record

    def _build_job_options(
        self,
        *,
        pipeline_id: str,
        pipeline_fields: dict[str, Any],
        profile: ProfileRecord,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        resolved_profile_defaults = profile.resolve_job_defaults()
        job_options = dict(pipeline_fields)
        job_options.update(
            {
                key: value
                for key, value in resolved_profile_defaults.items()
                if value not in (None, "")
            }
        )
        job_options.update(
            {
                key: value
                for key, value in options.items()
                if value not in (None, "")
            }
        )
        if profile.kind == "aiocr":
            if not str(job_options.get("ocr_ai_api_key") or "").strip():
                raise RemoteServiceError(
                    code="invalid_profile",
                    message="AIOCR profile did not resolve an ocr_ai_api_key",
                    details={"profile_id": profile.profile_id},
                )
            model = str(job_options.get("ocr_ai_model") or "").strip().lower()
            if pipeline_id == "local.aiocr.doc_parser" and "paddleocr-vl" not in model:
                raise RemoteServiceError(
                    code="profile_pipeline_mismatch",
                    message="doc_parser requires a PaddleOCR-VL model",
                    details={"profile_id": profile.profile_id, "pipeline_id": pipeline_id},
                )
            if pipeline_id == "local.aiocr.direct" and "paddleocr-vl" in model:
                raise RemoteServiceError(
                    code="profile_pipeline_mismatch",
                    message="direct does not support PaddleOCR-VL models",
                    details={"profile_id": profile.profile_id, "pipeline_id": pipeline_id},
                )
        if profile.kind == "mineru" and not str(job_options.get("mineru_api_token") or "").strip():
            raise RemoteServiceError(
                code="invalid_profile",
                message="MinerU profile did not resolve a mineru_api_token",
                details={"profile_id": profile.profile_id},
            )
        if profile.kind == "baidu_doc":
            if not str(job_options.get("ocr_baidu_api_key") or "").strip():
                raise RemoteServiceError(
                    code="invalid_profile",
                    message="Baidu profile did not resolve an ocr_baidu_api_key",
                    details={"profile_id": profile.profile_id},
                )
            if not str(job_options.get("ocr_baidu_secret_key") or "").strip():
                raise RemoteServiceError(
                    code="invalid_profile",
                    message="Baidu profile did not resolve an ocr_baidu_secret_key",
                    details={"profile_id": profile.profile_id},
                )
        return job_options
