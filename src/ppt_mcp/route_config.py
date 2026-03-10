"""Simplified route-based job configuration for stdio MCP usage."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_LAYOUT_BLOCK_MODEL = "Qwen/Qwen2.5-VL-72B-Instruct"
DEFAULT_DIRECT_MODEL = "deepseek-ai/DeepSeek-OCR"
DEFAULT_DOC_PARSER_MODEL = "PaddlePaddle/PaddleOCR-VL-1.5"


class RouteConfigError(RuntimeError):
    """Raised when a simplified route cannot be resolved."""

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


@dataclass(frozen=True)
class RouteDefinition:
    """Public route metadata shown to the MCP client."""

    route: str
    title: str
    summary: str
    env_hints: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedRoute:
    """Resolved route configuration after env expansion."""

    route: str
    title: str
    summary: str
    options: dict[str, Any]
    effective_config: dict[str, Any]
    missing_envs: tuple[str, ...] = ()


ROUTES: tuple[RouteDefinition, ...] = (
    RouteDefinition(
        route="local_basic",
        title="基础本地解析",
        summary="本地解析 PDF，不调用远程 OCR 模型。",
        env_hints=(),
        aliases=("基础本地解析", "basic", "local", "local_basic"),
        notes=("适合文本型 PDF 或快速冒烟测试。",),
    ),
    RouteDefinition(
        route="mineru",
        title="MinerU 云解析",
        summary="调用 MinerU 云端解析，适合需要完整文档理解的场景。",
        env_hints=("MINERU_API_TOKEN",),
        aliases=("MinerU 云解析", "云端文档解析", "mineru_cloud"),
    ),
    RouteDefinition(
        route="baidu_doc",
        title="百度文档解析",
        summary="调用百度文档解析能力，适合百度 OCR 生态。",
        env_hints=("BAIDU_API_KEY", "BAIDU_SECRET_KEY"),
        aliases=("百度文档解析", "baidu", "baidu_doc"),
    ),
    RouteDefinition(
        route="layout_block",
        title="本地切块识别",
        summary="先做本地版面切块，再逐块调用 AI OCR。适合 Qwen-VL 类模型。",
        env_hints=("SILICONFLOW_API_KEY or PPT_LAYOUT_BLOCK_API_KEY",),
        aliases=("本地切块识别", "local.aiocr.layout_block"),
    ),
    RouteDefinition(
        route="direct",
        title="模型直出框和文字",
        summary="整页直接交给模型输出文字与框。适合 DeepSeek-OCR 类模型。",
        env_hints=("SILICONFLOW_API_KEY or PPT_DIRECT_API_KEY",),
        aliases=("模型直出框和文字", "local.aiocr.direct"),
    ),
    RouteDefinition(
        route="doc_parser",
        title="内置文档解析",
        summary="走内置文档解析链路，适合 PaddleOCR-VL 类模型。",
        env_hints=("SILICONFLOW_API_KEY or PPT_DOC_PARSER_API_KEY",),
        aliases=("内置文档解析", "local.aiocr.doc_parser"),
        notes=("需要支持 PaddleOCR-VL 的模型。",),
    ),
)

_ROUTE_BY_KEY = {}
for item in ROUTES:
    _ROUTE_BY_KEY[item.route] = item
    for alias in item.aliases:
        _ROUTE_BY_KEY[alias] = item


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _first_env(*names: str) -> tuple[str | None, str | None]:
    for name in names:
        value = _env(name)
        if value:
            return value, name
    return None, None


def _parse_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RouteConfigError(
        code="invalid_env",
        message=f"{name} must be a boolean string",
        details={"env": name, "value": raw},
    )


def list_routes() -> list[dict[str, Any]]:
    """Return public metadata for all simplified routes."""
    items: list[dict[str, Any]] = []
    for item in ROUTES:
        try:
            resolved = resolve_route(item.route)
            ready = True
            missing_envs = []
            effective_config = resolved.effective_config
        except RouteConfigError as exc:
            ready = False
            missing_envs = list(exc.details.get("missing_envs", []))
            effective_config = {}
        items.append(
            {
                "route": item.route,
                "display_name": item.title,
                "recommended_input": item.title,
                "title": item.title,
                "summary": item.summary,
                "aliases": list(item.aliases),
                "env_hints": list(item.env_hints),
                "notes": list(item.notes),
                "ready": ready,
                "missing_envs": missing_envs,
                "effective_config": effective_config,
            }
        )
    return items


def resolve_route(route: str) -> ResolvedRoute:
    """Resolve a simplified route into concrete job options."""
    key = str(route or "").strip()
    definition = _ROUTE_BY_KEY.get(key)
    if definition is None:
        raise RouteConfigError(
            code="invalid_route",
            message=f"Unknown route: {route}",
            details={
                "route": route,
                "available_routes": [item.route for item in ROUTES],
            },
        )

    if definition.route == "local_basic":
        return ResolvedRoute(
            route=definition.route,
            title=definition.title,
            summary=definition.summary,
            options={
                "parse_provider": "local",
                "enable_ocr": False,
            },
            effective_config={
                "parse_provider": "local",
                "enable_ocr": False,
            },
        )

    if definition.route == "mineru":
        token = _env("MINERU_API_TOKEN")
        missing = [name for name in ("MINERU_API_TOKEN",) if not _env(name)]
        if missing:
            raise RouteConfigError(
                code="missing_env",
                message="MinerU route requires MINERU_API_TOKEN",
                details={"missing_envs": missing},
            )
        base_url = _env("MINERU_BASE_URL")
        model_version = _env("MINERU_MODEL_VERSION") or "vlm"
        enable_formula = _parse_bool("MINERU_ENABLE_FORMULA", True)
        enable_table = _parse_bool("MINERU_ENABLE_TABLE", True)
        options = {
            "parse_provider": "mineru",
            "mineru_api_token": token,
            "mineru_model_version": model_version,
            "mineru_enable_formula": enable_formula,
            "mineru_enable_table": enable_table,
        }
        if base_url:
            options["mineru_base_url"] = base_url
        return ResolvedRoute(
            route=definition.route,
            title=definition.title,
            summary=definition.summary,
            options=options,
            effective_config={
                "parse_provider": "mineru",
                "mineru_model_version": model_version,
                "mineru_enable_formula": enable_formula,
                "mineru_enable_table": enable_table,
                "mineru_base_url": base_url or None,
                "token_source": "MINERU_API_TOKEN",
            },
        )

    if definition.route == "baidu_doc":
        api_key = _env("BAIDU_API_KEY")
        secret_key = _env("BAIDU_SECRET_KEY")
        app_id = _env("BAIDU_APP_ID")
        missing = [
            name
            for name, value in (
                ("BAIDU_API_KEY", api_key),
                ("BAIDU_SECRET_KEY", secret_key),
            )
            if not value
        ]
        if missing:
            raise RouteConfigError(
                code="missing_env",
                message="Baidu Doc route requires BAIDU_API_KEY and BAIDU_SECRET_KEY",
                details={"missing_envs": missing},
            )
        parse_type = _env("BAIDU_DOC_PARSE_TYPE") or "paddle_vl"
        options = {
            "parse_provider": "baidu_doc",
            "baidu_doc_parse_type": parse_type,
            "ocr_baidu_api_key": api_key,
            "ocr_baidu_secret_key": secret_key,
        }
        if app_id:
            options["ocr_baidu_app_id"] = app_id
        return ResolvedRoute(
            route=definition.route,
            title=definition.title,
            summary=definition.summary,
            options=options,
            effective_config={
                "parse_provider": "baidu_doc",
                "baidu_doc_parse_type": parse_type,
                "app_id_set": bool(app_id),
                "credential_sources": ["BAIDU_API_KEY", "BAIDU_SECRET_KEY"],
            },
        )

    if definition.route == "layout_block":
        api_key, api_key_env = _first_env(
            "PPT_LAYOUT_BLOCK_API_KEY", "SILICONFLOW_API_KEY"
        )
        missing = [] if api_key else ["PPT_LAYOUT_BLOCK_API_KEY or SILICONFLOW_API_KEY"]
        if missing:
            raise RouteConfigError(
                code="missing_env",
                message="layout_block route requires an AI OCR API key",
                details={"missing_envs": missing},
            )
        provider = _env("PPT_LAYOUT_BLOCK_PROVIDER") or "siliconflow"
        base_url = _env("PPT_LAYOUT_BLOCK_BASE_URL") or SILICONFLOW_BASE_URL
        model = _env("PPT_LAYOUT_BLOCK_MODEL") or DEFAULT_LAYOUT_BLOCK_MODEL
        prompt_preset = _env("PPT_LAYOUT_BLOCK_PROMPT_PRESET") or "qwen_vl"
        options = {
            "parse_provider": "local",
            "enable_ocr": True,
            "ocr_provider": "aiocr",
            "ocr_ai_provider": provider,
            "ocr_ai_base_url": base_url,
            "ocr_ai_api_key": api_key,
            "ocr_ai_model": model,
            "ocr_ai_chain_mode": "layout_block",
            "ocr_ai_prompt_preset": prompt_preset,
        }
        return ResolvedRoute(
            route=definition.route,
            title=definition.title,
            summary=definition.summary,
            options=options,
            effective_config={
                "parse_provider": "local",
                "enable_ocr": True,
                "ocr_provider": "aiocr",
                "ocr_ai_chain_mode": "layout_block",
                "ocr_ai_provider": provider,
                "ocr_ai_base_url": base_url,
                "ocr_ai_model": model,
                "ocr_ai_prompt_preset": prompt_preset,
                "api_key_source": api_key_env,
            },
        )

    if definition.route == "direct":
        api_key, api_key_env = _first_env("PPT_DIRECT_API_KEY", "SILICONFLOW_API_KEY")
        missing = [] if api_key else ["PPT_DIRECT_API_KEY or SILICONFLOW_API_KEY"]
        if missing:
            raise RouteConfigError(
                code="missing_env",
                message="direct route requires an AI OCR API key",
                details={"missing_envs": missing},
            )
        provider = _env("PPT_DIRECT_PROVIDER") or "deepseek"
        base_url = _env("PPT_DIRECT_BASE_URL") or SILICONFLOW_BASE_URL
        model = _env("PPT_DIRECT_MODEL") or DEFAULT_DIRECT_MODEL
        prompt_preset = _env("PPT_DIRECT_PROMPT_PRESET") or "deepseek_ocr"
        options = {
            "parse_provider": "local",
            "enable_ocr": True,
            "ocr_provider": "aiocr",
            "ocr_ai_provider": provider,
            "ocr_ai_base_url": base_url,
            "ocr_ai_api_key": api_key,
            "ocr_ai_model": model,
            "ocr_ai_chain_mode": "direct",
            "ocr_ai_prompt_preset": prompt_preset,
        }
        return ResolvedRoute(
            route=definition.route,
            title=definition.title,
            summary=definition.summary,
            options=options,
            effective_config={
                "parse_provider": "local",
                "enable_ocr": True,
                "ocr_provider": "aiocr",
                "ocr_ai_chain_mode": "direct",
                "ocr_ai_provider": provider,
                "ocr_ai_base_url": base_url,
                "ocr_ai_model": model,
                "ocr_ai_prompt_preset": prompt_preset,
                "api_key_source": api_key_env,
            },
        )

    if definition.route == "doc_parser":
        api_key, api_key_env = _first_env(
            "PPT_DOC_PARSER_API_KEY", "SILICONFLOW_API_KEY"
        )
        missing = [] if api_key else ["PPT_DOC_PARSER_API_KEY or SILICONFLOW_API_KEY"]
        if missing:
            raise RouteConfigError(
                code="missing_env",
                message="doc_parser route requires an AI OCR API key",
                details={"missing_envs": missing},
            )
        provider = _env("PPT_DOC_PARSER_PROVIDER") or "openai"
        base_url = _env("PPT_DOC_PARSER_BASE_URL") or SILICONFLOW_BASE_URL
        model = _env("PPT_DOC_PARSER_MODEL") or DEFAULT_DOC_PARSER_MODEL
        max_side_px = _env("PPT_DOC_PARSER_MAX_SIDE_PX") or "2200"
        options = {
            "parse_provider": "local",
            "enable_ocr": True,
            "ocr_provider": "aiocr",
            "ocr_ai_provider": provider,
            "ocr_ai_base_url": base_url,
            "ocr_ai_api_key": api_key,
            "ocr_ai_model": model,
            "ocr_ai_chain_mode": "doc_parser",
            "ocr_paddle_vl_docparser_max_side_px": max_side_px,
        }
        return ResolvedRoute(
            route=definition.route,
            title=definition.title,
            summary=definition.summary,
            options=options,
            effective_config={
                "parse_provider": "local",
                "enable_ocr": True,
                "ocr_provider": "aiocr",
                "ocr_ai_chain_mode": "doc_parser",
                "ocr_ai_provider": provider,
                "ocr_ai_base_url": base_url,
                "ocr_ai_model": model,
                "ocr_paddle_vl_docparser_max_side_px": max_side_px,
                "api_key_source": api_key_env,
            },
        )

    raise RouteConfigError(
        code="invalid_route",
        message=f"Unsupported route: {definition.route}",
        details={"route": definition.route},
    )
