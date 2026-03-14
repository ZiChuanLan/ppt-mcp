"""Hosted remote MCP server with upload-backed sources."""

from __future__ import annotations

import contextlib
from io import BytesIO
from typing import Any

import uvicorn
from starlette.background import BackgroundTask
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route

from mcp.server.fastmcp import FastMCP

from ppt_mcp.api_client import PptApiError
from ppt_mcp.remote_profiles import ProfileStore
from ppt_mcp.remote_service import RemoteService, RemoteServiceError
from ppt_mcp.remote_settings import RemoteSettings, load_remote_settings
from ppt_mcp.settings import load_settings as load_upstream_settings
from ppt_mcp.source_store import SourceStore


def _tool_error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, RemoteServiceError):
        return {
            "ok": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        }
    if isinstance(exc, PptApiError):
        return {
            "ok": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "status_code": exc.status_code,
            },
        }
    return {
        "ok": False,
        "error": {
            "code": exc.__class__.__name__,
            "message": str(exc),
        },
    }


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Protect remote endpoints with a static bearer token when configured."""

    def __init__(self, app: Starlette, token: str | None) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if not self._token:
            return await call_next(request)
        path = request.url.path
        if path == "/healthz" or path.startswith("/uploads/"):
            return await call_next(request)
        auth_header = request.headers.get("authorization", "")
        expected = f"Bearer {self._token}"
        if auth_header != expected:
            return JSONResponse(
                {"code": "auth_required", "message": "Missing or invalid bearer token"},
                status_code=401,
            )
        return await call_next(request)


def create_remote_mcp(service: RemoteService) -> FastMCP:
    """Create the FastMCP instance for remote use."""
    mcp = FastMCP(
        "ppt-remote",
        instructions=(
            "Hosted PDF-to-PPT MCP service. Ask for missing inputs one by one before creating a job: "
            "1) choose pipeline_id, 2) choose scanned-page image handling (`fullpage` recommended), "
            "3) decide whether NotebookLM footer removal is needed, 4) choose profile_id and any model/profile overrides. "
            "Then create or reference a source and poll the resulting job."
        ),
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
    )

    @mcp.tool()
    def ppt_list_profiles() -> dict[str, Any]:
        """List available server-side credential/config profiles."""
        try:
            return {"ok": True, **service.list_profiles()}
        except Exception as exc:
            return _tool_error_payload(exc)

    @mcp.tool()
    def ppt_list_pipelines() -> dict[str, Any]:
        """List named hosted conversion pipelines."""
        try:
            return {"ok": True, **service.list_pipelines()}
        except Exception as exc:
            return _tool_error_payload(exc)

    @mcp.tool()
    def ppt_create_upload(
        filename: str,
        mime_type: str = "application/pdf",
        size_bytes: int | None = None,
        sha256: str | None = None,
    ) -> dict[str, Any]:
        """Create an upload slot for a PDF source."""
        try:
            payload = service.create_upload(
                filename=filename,
                mime_type=mime_type,
                size_bytes=size_bytes,
                sha256=sha256,
            )
            return {"ok": True, **payload}
        except Exception as exc:
            return _tool_error_payload(exc)

    @mcp.tool()
    def ppt_finalize_upload(source_id: str) -> dict[str, Any]:
        """Finalize an uploaded source and mark it ready for conversion."""
        try:
            payload = service.finalize_upload(source_id=source_id)
            return {"ok": True, **payload}
        except Exception as exc:
            return _tool_error_payload(exc)

    @mcp.tool()
    def ppt_create_job(
        source: dict[str, Any],
        pipeline_id: str,
        profile_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a remote conversion job from a source, pipeline_id, and profile_id."""
        try:
            payload = service.create_job(
                source=source,
                pipeline_id=pipeline_id,
                profile_id=profile_id,
                options=options,
            )
            return {"ok": True, **payload}
        except Exception as exc:
            return _tool_error_payload(exc)

    @mcp.tool()
    def ppt_get_job_status(job_id: str) -> dict[str, Any]:
        """Get remote conversion job status."""
        try:
            return {"ok": True, **service.get_job_status(job_id=job_id)}
        except Exception as exc:
            return _tool_error_payload(exc)

    @mcp.tool()
    def ppt_cancel_job(job_id: str) -> dict[str, Any]:
        """Cancel a running job."""
        try:
            payload = service.cancel_job(job_id=job_id)
            return {"ok": True, **payload}
        except Exception as exc:
            return _tool_error_payload(exc)

    @mcp.tool()
    def ppt_get_job_artifacts(job_id: str) -> dict[str, Any]:
        """Get rewritten artifact metadata for a job."""
        try:
            payload = service.get_job_artifacts(job_id=job_id)
            return {"ok": True, **payload}
        except Exception as exc:
            return _tool_error_payload(exc)

    @mcp.tool()
    def ppt_download_result(job_id: str) -> dict[str, Any]:
        """Return the remote download URL for a completed result."""
        try:
            payload = service.get_result_download(job_id=job_id)
            return {"ok": True, **payload}
        except Exception as exc:
            return _tool_error_payload(exc)

    return mcp


async def healthz(_request: Request) -> Response:
    """Plain HTTP health endpoint for operators."""
    return JSONResponse({"status": "ok"})


async def upload_put(request: Request) -> Response:
    """Receive raw PDF bytes for a staged upload source."""
    service: RemoteService = request.app.state.remote_service
    remote_settings: RemoteSettings = request.app.state.remote_settings
    source_id = request.path_params["source_id"]
    token = request.query_params.get("token", "")
    try:
        body = await request.body()
        record = service.source_store.write_upload_stream(
            source_id=source_id,
            upload_token=token,
            stream=BytesIO(body),
            max_upload_bytes=remote_settings.max_upload_bytes,
        )
    except Exception as exc:
        payload = _tool_error_payload(exc)
        return JSONResponse(payload["error"], status_code=400)
    return JSONResponse(
        {
            "source_id": record.source_id,
            "status": record.status,
            "size_bytes": record.actual_size_bytes,
        }
    )


async def proxy_download_result(request: Request) -> Response:
    """Proxy the generated PPTX download through the remote MCP service."""
    service: RemoteService = request.app.state.remote_service
    job_id = request.path_params["job_id"]
    try:
        upstream = service.api_client.open_stream(
            "GET",
            f"api/v1/jobs/{job_id}/download",
        )
    except Exception as exc:
        payload = _tool_error_payload(exc)
        return JSONResponse(payload["error"], status_code=400)
    headers = {}
    content_type = upstream.headers.get("content-type")
    if content_type:
        headers["content-type"] = content_type
    content_disposition = upstream.headers.get("content-disposition")
    if content_disposition:
        headers["content-disposition"] = content_disposition
    return StreamingResponse(
        upstream.iter_bytes(),
        headers=headers,
        background=BackgroundTask(upstream.close),
    )


async def proxy_artifact_file(request: Request) -> Response:
    """Proxy an artifact file through the remote MCP service."""
    service: RemoteService = request.app.state.remote_service
    job_id = request.path_params["job_id"]
    artifact_path = request.query_params.get("path", "")
    if not artifact_path:
        return JSONResponse(
            {"code": "invalid_source", "message": "path query parameter is required"},
            status_code=400,
        )
    try:
        streamed = service.api_client.open_stream(
            "GET",
            f"api/v1/jobs/{job_id}/artifacts/file",
            params={"path": artifact_path},
        )
    except Exception as exc:
        payload = _tool_error_payload(exc)
        return JSONResponse(payload["error"], status_code=400)
    headers = {}
    content_type = streamed.headers.get("content-type")
    if content_type:
        headers["content-type"] = content_type
    return StreamingResponse(
        streamed.iter_bytes(),
        headers=headers,
        background=BackgroundTask(streamed.close),
    )


def create_app() -> Starlette:
    """Create the combined remote MCP and HTTP upload/proxy app."""
    remote_settings = load_remote_settings()
    upstream_settings = load_upstream_settings()
    profile_store = ProfileStore(remote_settings.profile_store_path)
    source_store = SourceStore(
        root_dir=remote_settings.data_dir,
        upload_ttl_seconds=remote_settings.upload_ttl_seconds,
    )
    service = RemoteService(
        remote_settings=remote_settings,
        upstream_settings=upstream_settings,
        profile_store=profile_store,
        source_store=source_store,
    )
    mcp = create_remote_mcp(service)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with contextlib.AsyncExitStack() as stack:
            await stack.enter_async_context(mcp.session_manager.run())
            app.state.remote_service = service
            app.state.remote_settings = remote_settings
            yield
        service.close()

    app = Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/uploads/{source_id}", upload_put, methods=["PUT"]),
            Route("/jobs/{job_id}/download", proxy_download_result, methods=["GET"]),
            Route(
                "/jobs/{job_id}/artifacts/file",
                proxy_artifact_file,
                methods=["GET"],
            ),
            Mount("/mcp", app=mcp.streamable_http_app()),
        ],
        lifespan=lifespan,
    )
    app.add_middleware(BearerTokenMiddleware, token=remote_settings.server_token)
    return app


def main() -> None:
    """Run the hosted remote MCP server."""
    remote_settings = load_remote_settings()
    uvicorn.run(
        create_app(),
        host=remote_settings.bind_host,
        port=remote_settings.bind_port,
    )
