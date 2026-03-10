"""MCP server entrypoint for the local ppt wrapper."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from mcp.server.fastmcp import FastMCP

from ppt_mcp.api_client import PptApiClient, PptApiError
from ppt_mcp.route_config import RouteConfigError, list_routes, resolve_route
from ppt_mcp.settings import load_settings


settings = load_settings()
client = PptApiClient(settings)
mcp = FastMCP(
    "ppt-mcp",
    instructions=(
        "Wrap the local ppt PDF-to-PPT HTTP API. "
        "Use local file paths for PDFs, then poll job status until completion."
    ),
)


def _build_ai_route_overrides(
    *,
    ocr_ai_provider: str | None = None,
    ocr_ai_base_url: str | None = None,
    ocr_ai_model: str | None = None,
    ocr_ai_prompt_preset: str | None = None,
) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if ocr_ai_provider is not None:
        overrides["ocr_ai_provider"] = ocr_ai_provider
    if ocr_ai_base_url is not None:
        overrides["ocr_ai_base_url"] = ocr_ai_base_url
    if ocr_ai_model is not None:
        overrides["ocr_ai_model"] = ocr_ai_model
    if ocr_ai_prompt_preset is not None:
        overrides["ocr_ai_prompt_preset"] = ocr_ai_prompt_preset
    return overrides


def _apply_ai_route_overrides(
    *,
    route: str,
    options: dict[str, Any],
    effective_config: dict[str, Any],
    overrides: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not overrides:
        return options, effective_config
    if options.get("ocr_provider") != "aiocr":
        raise RouteConfigError(
            code="invalid_override",
            message="AI OCR overrides are only supported on AI OCR routes",
            details={
                "route": route,
                "supported_routes": [
                    "本地切块识别",
                    "模型直出框和文字",
                    "内置文档解析",
                ],
            },
        )
    patched_options = dict(options)
    patched_effective_config = dict(effective_config)
    for key, value in overrides.items():
        patched_options[key] = value
        patched_effective_config[key] = value
    return patched_options, patched_effective_config


def _tool_error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, RouteConfigError):
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
                "status_code": exc.status_code,
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        }
    return {
        "ok": False,
        "error": {
            "code": exc.__class__.__name__,
            "message": str(exc),
        },
    }


@mcp.tool()
def ppt_health_check() -> dict[str, Any]:
    """Check whether the wrapped local ppt API is reachable."""
    try:
        payload = client.health_check()
        return {"ok": True, "api_base_url": settings.api_base_url, "health": payload}
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_list_routes() -> dict[str, Any]:
    """List human-friendly routes such as 本地切块识别 or 内置文档解析."""
    try:
        return {"ok": True, "routes": list_routes()}
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_check_route(
    route: str,
    ocr_ai_provider: str | None = None,
    ocr_ai_base_url: str | None = None,
    ocr_ai_model: str | None = None,
    ocr_ai_prompt_preset: str | None = None,
) -> dict[str, Any]:
    """Check whether a route is ready and preview the effective model settings."""
    try:
        resolved = resolve_route(route)
        effective_config = dict(resolved.effective_config)
        _, effective_config = _apply_ai_route_overrides(
            route=resolved.title,
            options=resolved.options,
            effective_config=effective_config,
            overrides=_build_ai_route_overrides(
                ocr_ai_provider=ocr_ai_provider,
                ocr_ai_base_url=ocr_ai_base_url,
                ocr_ai_model=ocr_ai_model,
                ocr_ai_prompt_preset=ocr_ai_prompt_preset,
            ),
        )
        return {
            "ok": True,
            "route": resolved.route,
            "display_name": resolved.title,
            "recommended_input": resolved.title,
            "title": resolved.title,
            "summary": resolved.summary,
            "ready": True,
            "effective_config": effective_config,
            "missing_envs": [],
        }
    except RouteConfigError as exc:
        return {
            "ok": False,
            "route": route,
            "ready": False,
            "missing_envs": list(exc.details.get("missing_envs", [])),
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        }
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_convert_pdf(
    pdf_path: str,
    route: str = "本地切块识别",
    page_start: int | None = None,
    page_end: int | None = None,
    retain_process_artifacts: bool = False,
    ocr_ai_provider: str | None = None,
    ocr_ai_base_url: str | None = None,
    ocr_ai_model: str | None = None,
    ocr_ai_prompt_preset: str | None = None,
    extra_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a conversion job using a human-friendly route such as 本地切块识别.

    This is the main high-level tool for everyday use. It resolves provider
    secrets from environment variables and translates the chosen route into the
    lower-level ppt API form fields automatically.
    """
    try:
        resolved = resolve_route(route)
        options = dict(resolved.options)
        effective_config = dict(resolved.effective_config)
        if page_start is not None:
            options["page_start"] = page_start
            effective_config["page_start"] = page_start
        if page_end is not None:
            options["page_end"] = page_end
            effective_config["page_end"] = page_end
        options["retain_process_artifacts"] = retain_process_artifacts
        effective_config["retain_process_artifacts"] = retain_process_artifacts
        for key, value in (extra_options or {}).items():
            if key in options:
                continue
            options[key] = value
        options, effective_config = _apply_ai_route_overrides(
            route=resolved.title,
            options=options,
            effective_config=effective_config,
            overrides=_build_ai_route_overrides(
                ocr_ai_provider=ocr_ai_provider,
                ocr_ai_base_url=ocr_ai_base_url,
                ocr_ai_model=ocr_ai_model,
                ocr_ai_prompt_preset=ocr_ai_prompt_preset,
            ),
        )
        payload = client.create_job(pdf_path=pdf_path, options=options)
        return {
            "ok": True,
            "route": resolved.route,
            "display_name": resolved.title,
            "recommended_input": resolved.title,
            "effective_config": effective_config,
            "job": payload,
        }
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_create_job(
    pdf_path: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a PDF-to-PPT conversion job from a local pdf_path.

    The PDF is read from local disk by this MCP server, then uploaded to the
    running ppt API. This is the low-level escape hatch. For normal use prefer
    `ppt_convert_pdf`, which lets the AI pick a high-level route like `mineru`
    or `layout_block`.
    """
    try:
        payload = client.create_job(pdf_path=pdf_path, options=options)
        return {"ok": True, "job": payload}
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_list_jobs(limit: int = 20) -> dict[str, Any]:
    """List recent conversion jobs from the wrapped local ppt API."""
    try:
        payload = client.list_jobs(limit=limit)
        return {"ok": True, "jobs": payload}
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_get_job_status(job_id: str) -> dict[str, Any]:
    """Get the current status, stage, progress, and errors for a job."""
    try:
        payload = client.get_job_status(job_id)
        return {"ok": True, "job": payload}
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_cancel_job(job_id: str) -> dict[str, Any]:
    """Cancel a pending or running conversion job."""
    try:
        payload = client.cancel_job(job_id)
        return {"ok": True, "result": payload}
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_get_job_artifacts(job_id: str) -> dict[str, Any]:
    """Return debug/process artifact metadata for a job."""
    try:
        payload = client.get_job_artifacts(job_id)
        return {"ok": True, "artifacts": payload}
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_download_result(job_id: str, output_path: str | None = None) -> dict[str, Any]:
    """Download the completed PPTX result to a local output path."""
    try:
        payload = client.download_result(job_id=job_id, output_path=output_path)
        return {"ok": True, "result": payload}
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_download_artifact(
    job_id: str,
    artifact_path: str,
    output_path: str | None = None,
) -> dict[str, Any]:
    """Download a retained artifact file to a local output path."""
    try:
        payload = client.download_artifact(
            job_id=job_id,
            artifact_path=artifact_path,
            output_path=output_path,
        )
        return {"ok": True, "result": payload}
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_list_ai_models(
    provider: str,
    api_key: str,
    base_url: str | None = None,
    capability: str = "vision",
) -> dict[str, Any]:
    """List candidate AI models from the wrapped model discovery API."""
    try:
        payload = client.list_ai_models(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            capability=capability,
        )
        return {"ok": True, "models": payload}
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_check_ai_ocr(
    api_key: str,
    model: str,
    provider: str = "auto",
    base_url: str | None = None,
    ocr_ai_chain_mode: str = "layout_block",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Probe whether a model can work on the current AI OCR chain."""
    try:
        payload = client.check_ai_ocr(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
            ocr_ai_chain_mode=ocr_ai_chain_mode,
            options=options,
        )
        return {"ok": True, "check": payload}
    except Exception as exc:
        return _tool_error_payload(exc)


def main() -> None:
    """Run the MCP server over stdio."""
    try:
        mcp.run(transport="stdio")
    finally:
        with suppress(Exception):
            client.close()
