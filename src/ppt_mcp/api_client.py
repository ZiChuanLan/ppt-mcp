"""Thin HTTP client for the existing ppt API."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from ppt_mcp.settings import Settings


class PptApiError(RuntimeError):
    """Raised when the wrapped ppt API returns an error."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"{code}: {message}")


_WINDOWS_DRIVE_PATH_RE = re.compile(r"^(?P<drive>[A-Za-z]):[\\/](?P<rest>.*)$")
_WSL_UNC_PATH_RE = re.compile(
    r"^[\\/]{2}wsl\.localhost[\\/][^\\/]+[\\/](?P<rest>.*)$",
    re.IGNORECASE,
)


def _serialize_form_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True)
    return str(value)


def _normalize_local_pdf_path(pdf_path: str) -> Path:
    raw = str(pdf_path or "").strip()
    if not raw:
        return Path(raw)

    windows_match = _WINDOWS_DRIVE_PATH_RE.match(raw)
    if windows_match:
        drive = windows_match.group("drive").lower()
        rest = windows_match.group("rest").replace("\\", "/")
        return Path(f"/mnt/{drive}/{rest}")

    wsl_unc_match = _WSL_UNC_PATH_RE.match(raw)
    if wsl_unc_match:
        rest = wsl_unc_match.group("rest").replace("\\", "/")
        return Path(f"/{rest.lstrip('/')}")

    return Path(raw)


class PptApiClient:
    """HTTP wrapper over the existing local ppt backend."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        headers: dict[str, str] = {}
        if settings.api_bearer_token:
            headers["Authorization"] = f"Bearer {settings.api_bearer_token}"
        self._client = httpx.Client(
            base_url=settings.api_base_url,
            timeout=settings.api_timeout_seconds,
            headers=headers,
        )

    def close(self) -> None:
        self._client.close()

    def _raise_for_error(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            payload = response.json()
        except Exception:
            payload = {}
        code = str(payload.get("code") or f"http_{response.status_code}")
        message = str(payload.get("message") or response.text or "HTTP request failed")
        details = payload.get("details")
        raise PptApiError(
            status_code=response.status_code,
            code=code,
            message=message,
            details=details if isinstance(details, dict) else None,
        )

    def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._client.request(method, path, **kwargs)
        self._raise_for_error(response)
        return dict(response.json())

    def open_stream(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Open a streamed upstream response.

        The caller is responsible for closing the returned response.
        """
        request = self._client.build_request(method, path, **kwargs)
        response = self._client.send(request, stream=True)
        self._raise_for_error(response)
        return response

    def _absolute_api_url(self, path: str) -> str:
        return f"{self._settings.api_base_url.rstrip('/')}{path}"

    def health_check(self) -> dict[str, Any]:
        return self._json("GET", "health")

    def list_jobs(self, *, limit: int = 20) -> dict[str, Any]:
        return self._json("GET", "api/v1/jobs", params={"limit": limit})

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        return self._json("GET", f"api/v1/jobs/{job_id}")

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        return self._json("POST", f"api/v1/jobs/{job_id}/cancel")

    def get_job_artifacts(self, job_id: str) -> dict[str, Any]:
        payload = self._json("GET", f"api/v1/jobs/{job_id}/artifacts")
        for key in ("source_pdf_url",):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("/"):
                payload[key] = self._absolute_api_url(value)
        for key in (
            "original_images",
            "cleaned_images",
            "final_preview_images",
            "ocr_overlay_images",
            "layout_before_images",
            "layout_after_images",
        ):
            for item in payload.get(key, []) or []:
                url = item.get("url")
                if isinstance(url, str) and url.startswith("/"):
                    item["url"] = self._absolute_api_url(url)
        return payload

    def list_ai_models(
        self,
        *,
        provider: str,
        api_key: str,
        base_url: str | None = None,
        capability: str = "vision",
    ) -> dict[str, Any]:
        payload = {
            "provider": provider,
            "api_key": api_key,
            "base_url": base_url,
            "capability": capability,
        }
        return self._json("POST", "api/v1/models", json=payload)

    def check_ai_ocr(
        self,
        *,
        provider: str = "auto",
        api_key: str,
        base_url: str | None,
        model: str,
        ocr_ai_chain_mode: str = "layout_block",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider": provider,
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "ocr_ai_chain_mode": ocr_ai_chain_mode,
        }
        if options:
            payload.update(options)
        return self._json("POST", "api/v1/jobs/ocr/ai/check", json=payload)

    def create_job(
        self,
        *,
        pdf_path: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = _normalize_local_pdf_path(pdf_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {path}")
        if not path.is_file():
            raise ValueError(f"PDF path is not a file: {path}")
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"Only PDF files are supported: {path}")

        form_data: dict[str, str] = {}
        for key, value in (options or {}).items():
            if value is None:
                continue
            form_data[key] = _serialize_form_value(value)

        with path.open("rb") as file_handle:
            files = {"file": (path.name, file_handle, "application/pdf")}
            response = self._client.post("api/v1/jobs", data=form_data, files=files)
        self._raise_for_error(response)
        payload = dict(response.json())
        payload["input_pdf_path"] = str(path)
        return payload

    def download_result(
        self,
        *,
        job_id: str,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        target = Path(output_path or f"converted-{job_id}.pptx").expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._client.stream("GET", f"api/v1/jobs/{job_id}/download") as response:
            self._raise_for_error(response)
            with target.open("wb") as file_handle:
                for chunk in response.iter_bytes():
                    file_handle.write(chunk)
        return {"job_id": job_id, "saved_to": str(target)}

    def download_artifact(
        self,
        *,
        job_id: str,
        artifact_path: str,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        safe_name = Path(artifact_path).name
        target = Path(output_path or safe_name).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._client.stream(
            "GET",
            f"api/v1/jobs/{job_id}/artifacts/file",
            params={"path": artifact_path},
        ) as response:
            self._raise_for_error(response)
            with target.open("wb") as file_handle:
                for chunk in response.iter_bytes():
                    file_handle.write(chunk)
        return {
            "job_id": job_id,
            "artifact_path": artifact_path,
            "saved_to": str(target),
        }
