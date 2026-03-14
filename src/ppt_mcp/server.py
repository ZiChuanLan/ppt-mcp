"""MCP server entrypoint for the local ppt wrapper."""

from __future__ import annotations

from contextlib import suppress
from typing import Any, Literal

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
        "Before starting a conversion, ask for missing inputs one by one in this order: "
        "1) route/chain, 2) scanned-page image handling (`fullpage` recommended, `segmented` only when the user explicitly wants editable image blocks), "
        "3) whether to remove NotebookLM footer, 4) for AI OCR routes only, whether to keep the route default model or specify provider/base URL/model. "
        "Do not silently choose `segmented`, and prefer `ppt_convert_pdf` over `ppt_create_job` for normal use. "
        "Use local file paths for PDFs, then poll job status until completion."
    ),
)

def _build_workflow_guidance(*, route_selected: bool, ai_route: bool | None = None) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    if not route_selected:
        steps.append(
            {
                "field": "route",
                "question": "先确认走什么链路：基础本地解析、MinerU 云解析、百度文档解析、本地切块识别、模型直出框和文字、内置文档解析。",
            }
        )
    steps.extend(
        [
            {
                "field": "scanned_page_mode",
                "question": "再确认扫描页图片处理方式：`fullpage`（默认，最像原图）还是 `segmented`（拆成可编辑图片块，但更容易切错）。",
                "recommended": "fullpage",
            },
            {
                "field": "remove_footer_notebooklm",
                "question": "再确认是否删除 NotebookLM 页脚；只有明确存在该页脚时才建议打开。",
                "recommended": False,
            },
        ]
    )
    if ai_route is None or ai_route:
        steps.append(
            {
                "field": "ocr_ai_model",
                "question": "如果选择的是 AI OCR 链路，再确认是沿用该路线默认模型，还是显式指定 `ocr_ai_provider` / `ocr_ai_base_url` / `ocr_ai_model`。",
                "when": "route uses aiocr",
            }
        )
    return {
        "ask_one_by_one": True,
        "defaults": {
            "scanned_page_mode": "fullpage",
            "remove_footer_notebooklm": False,
        },
        "steps": steps,
    }


def _apply_conversion_preferences(
    *,
    options: dict[str, Any],
    effective_config: dict[str, Any],
    scanned_page_mode: Literal["fullpage", "segmented"] | None = None,
    remove_footer_notebooklm: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    patched_options = dict(options)
    patched_effective_config = dict(effective_config)

    resolved_scanned_page_mode = scanned_page_mode or str(
        patched_options.get("scanned_page_mode") or "fullpage"
    ).strip().lower()
    if resolved_scanned_page_mode not in {"fullpage", "segmented"}:
        raise RouteConfigError(
            code="invalid_scanned_page_mode",
            message="scanned_page_mode must be fullpage or segmented",
            details={"scanned_page_mode": resolved_scanned_page_mode},
        )
    patched_options["scanned_page_mode"] = resolved_scanned_page_mode
    patched_effective_config["scanned_page_mode"] = resolved_scanned_page_mode

    resolved_remove_footer = (
        bool(remove_footer_notebooklm)
        if remove_footer_notebooklm is not None
        else bool(patched_options.get("remove_footer_notebooklm", False))
    )
    patched_options["remove_footer_notebooklm"] = resolved_remove_footer
    patched_effective_config["remove_footer_notebooklm"] = resolved_remove_footer
    return patched_options, patched_effective_config


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
        return {
            "ok": True,
            "routes": list_routes(),
            "workflow_guidance": _build_workflow_guidance(route_selected=False),
        }
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_check_route(
    route: str,
    scanned_page_mode: Literal["fullpage", "segmented"] | None = None,
    remove_footer_notebooklm: bool | None = None,
    ocr_ai_provider: str | None = None,
    ocr_ai_base_url: str | None = None,
    ocr_ai_model: str | None = None,
    ocr_ai_prompt_preset: str | None = None,
) -> dict[str, Any]:
    """Check whether a route is ready and preview the effective settings.

    Use this after the user picks a route. Then continue asking in order:
    scanned-page mode, footer removal, and for AI OCR routes whether to keep
    the default model or override provider/base URL/model.
    """
    try:
        resolved = resolve_route(route)
        effective_config = dict(resolved.effective_config)
        options, effective_config = _apply_conversion_preferences(
            options=resolved.options,
            effective_config=effective_config,
            scanned_page_mode=scanned_page_mode,
            remove_footer_notebooklm=remove_footer_notebooklm,
        )
        _, effective_config = _apply_ai_route_overrides(
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
            "workflow_guidance": _build_workflow_guidance(
                route_selected=True,
                ai_route=bool(options.get("ocr_provider") == "aiocr"),
            ),
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
    route: str,
    scanned_page_mode: Literal["fullpage", "segmented"] | None = None,
    remove_footer_notebooklm: bool | None = None,
    ocr_ai_model: str | None = None,
    ocr_ai_provider: str | None = None,
    ocr_ai_base_url: str | None = None,
    ocr_ai_prompt_preset: str | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    retain_process_artifacts: bool = False,
    extra_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a conversion job using a human-friendly route such as 本地切块识别.

    This is the main high-level tool for everyday use. Before calling it,
    collect missing inputs one by one in this order:
    1. `route`
    2. `scanned_page_mode`
    3. `remove_footer_notebooklm`
    4. For AI OCR routes only, whether to keep the route default model or
       override `ocr_ai_provider` / `ocr_ai_base_url` / `ocr_ai_model`

    The tool resolves provider secrets from environment variables and translates
    the chosen route into lower-level ppt API form fields automatically.
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
            effective_config[key] = value
        options, effective_config = _apply_conversion_preferences(
            options=options,
            effective_config=effective_config,
            scanned_page_mode=scanned_page_mode,
            remove_footer_notebooklm=remove_footer_notebooklm,
        )
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


@mcp.prompt(
    name="ppt_conversion_intake",
    title="PDF 转 PPT 分步询问清单",
    description="Ask for conversion decisions one by one before calling ppt_convert_pdf.",
)
def ppt_conversion_intake(route: str | None = None) -> str:
    """Return the recommended question order for a new conversion."""
    route_selected = False
    ai_route: bool | None = None
    route_title = ""
    if route:
        try:
            resolved = resolve_route(route)
            route_selected = True
            route_title = resolved.title
            ai_route = bool(resolved.options.get("ocr_provider") == "aiocr")
        except RouteConfigError:
            route_title = str(route).strip()
            ai_route = None
    guidance = _build_workflow_guidance(
        route_selected=route_selected,
        ai_route=ai_route,
    )
    lines = ["请按下面顺序逐个确认，不要一次性抛出所有参数。"]
    if route_title:
        lines.append(f"当前路线：{route_title}")
    for index, step in enumerate(guidance["steps"], start=1):
        line = f"{index}. {step['question']}"
        recommended = step.get("recommended")
        if recommended is not None:
            line += f" 默认建议：{recommended}"
        when = step.get("when")
        if when:
            line += f" 条件：{when}"
        lines.append(line)
    return "\n".join(lines)


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
