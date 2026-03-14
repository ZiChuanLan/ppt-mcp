"""MCP server entrypoint for the local ppt wrapper."""

from __future__ import annotations

from contextlib import suppress
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from ppt_mcp.api_client import PptApiClient, PptApiError
from ppt_mcp.route_config import (
    RouteConfigError,
    get_route_definition,
    list_routes,
    resolve_route,
)
from ppt_mcp.settings import load_settings


settings = load_settings()
client = PptApiClient(settings)
mcp = FastMCP(
    "ppt-mcp",
    instructions=(
        "Wrap the local ppt PDF-to-PPT HTTP API. "
        "Before starting a conversion, ask for missing inputs one by one in this order: "
        "1) route/chain, and do not infer, recommend, or choose the route on the user's behalf, "
        "2) explicitly confirm that route with `route_confirmed=true`, "
        "3) scanned-page image handling (`fullpage` recommended, `segmented` only when the user explicitly wants editable image blocks), "
        "4) whether to remove NotebookLM footer, "
        "5) for AI OCR routes only, call `ppt_list_route_models`, then ask whether to keep the route default model or use a fetched model explicitly. "
        "Never say you will choose the best route for the user. If the user only asks to convert a page range, first show the available routes and ask the user to pick one. "
        "Do not silently choose `segmented`, and prefer `ppt_convert_pdf` over `ppt_create_job` for normal use. "
        "Use local file paths for PDFs, then poll job status until completion."
    ),
)


def _route_confirmation_value(route_confirmed: bool | None) -> bool:
    return route_confirmed is True


def _low_level_override_value(low_level_override_confirmed: bool | None) -> bool:
    return low_level_override_confirmed is True


def _normalize_ai_model_decision(
    decision: str | None,
) -> Literal["route_default", "explicit"] | None:
    normalized = str(decision or "").strip().lower()
    if not normalized:
        return None
    aliases = {
        "route_default": "route_default",
        "default": "route_default",
        "keep_default": "route_default",
        "use_default": "route_default",
        "沿用默认": "route_default",
        "默认": "route_default",
        "默认模型": "route_default",
        "explicit": "explicit",
        "custom": "explicit",
        "override": "explicit",
        "specified": "explicit",
        "select": "explicit",
        "显式指定": "explicit",
        "指定": "explicit",
        "指定模型": "explicit",
    }
    resolved = aliases.get(normalized)
    if resolved is None:
        raise RouteConfigError(
            code="invalid_model_decision",
            message="ocr_ai_model_decision must be route_default or explicit",
            details={"ocr_ai_model_decision": decision},
        )
    return resolved


def _normalize_model_list_capability(
    capability: str | None,
) -> Literal["all", "vision", "ocr"] | None:
    normalized = str(capability or "").strip().lower()
    if not normalized:
        return None
    if normalized not in {"all", "vision", "ocr"}:
        raise RouteConfigError(
            code="invalid_model_capability",
            message="capability must be all, vision, or ocr",
            details={"capability": capability},
        )
    return normalized


def _default_route_model_capability(
    *,
    options: dict[str, Any],
) -> Literal["vision", "ocr"]:
    chain_mode = str(options.get("ocr_ai_chain_mode") or "").strip().lower()
    if chain_mode == "layout_block":
        return "vision"
    return "ocr"


def _route_uses_ai_ocr(route_id: str) -> bool:
    return route_id in {"layout_block", "direct", "doc_parser"}


def _build_route_selection_policy(*, route_title: str | None = None) -> dict[str, Any]:
    next_question = (
        "请先让用户从可用路线里明确选一条；不要自己根据 PDF 内容、版式或主观判断替用户决定。"
    )
    if route_title:
        next_question = (
            f"请先确认用户是否明确要走 `{route_title}`；在用户确认前，不要继续做该路线的检查、模型拉取或提交。"
        )
    return {
        "user_must_choose_route": True,
        "route_confirmed_required": True,
        "do_not_choose_for_user": True,
        "do_not_infer_from_pdf": True,
        "do_not_claim_best_route": True,
        "bad_agent_reply_example": (
            "我将先检查这份 PDF，再为您选择最适合的转换路线。"
        ),
        "good_agent_reply_example": (
            "可用路线如下，请您先选一条；在您确认前我不会替您决定。"
        ),
        "next_tool_after_user_choice": "ppt_check_route",
        "next_field": "route" if route_title is None else "route_confirmed",
        "next_question": next_question,
    }


def _build_low_level_escape_hatch_policy() -> dict[str, Any]:
    return {
        "escape_hatch": True,
        "not_for_normal_use": True,
        "user_must_request_bypass_explicitly": True,
        "confirmation_field": "low_level_override_confirmed",
        "preferred_tool_sequence": [
            "ppt_list_routes",
            "ppt_check_route",
            "ppt_convert_pdf",
        ],
        "bad_agent_reply_example": (
            "我直接用底层参数帮您创建任务，这样更快。"
        ),
        "good_agent_reply_example": (
            "除非您明确要求绕过引导流程，否则我会先列出路线并请您确认。"
        ),
        "next_question": (
            "只有当用户明确要求绕过引导流程、并愿意自己承担底层参数选择时，才可以继续使用 `ppt_create_job`。"
        ),
    }


def _build_workflow_guidance(
    *,
    route_selected: bool,
    route_title: str | None = None,
    ai_route: bool | None = None,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    if not route_selected:
        steps.append(
            {
                "field": "route",
                "question": "先让用户明确选择要走什么链路：基础本地解析、MinerU 云解析、百度文档解析、本地切块识别、模型直出框和文字、内置文档解析。不要自己代替用户决定。",
            }
        )
        return {
            "ask_one_by_one": True,
            "defaults": {
                "scanned_page_mode": "fullpage",
                "remove_footer_notebooklm": False,
            },
            "hard_rules": [
                "不要自己替用户选择路线。",
                "不要根据 PDF 内容、版式或主观判断推断路线。",
                "不要说“我来为你选择最适合的路线”。",
                "一次只问一个问题，等用户回答后再继续。",
            ],
            "steps": steps,
        }
    route_label = f"`{route_title}`" if route_title else "当前路线"
    steps.append(
        {
            "field": "route_confirmed",
            "question": f"先确认用户是否明确要走 {route_label}；在用户确认前，不要继续做该路线的检查、模型拉取或提交。",
            "recommended": True,
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
        steps.extend(
            [
                {
                    "field": "ocr_ai_model_decision",
                    "question": "如果选择的是 AI OCR 链路，先调用 `ppt_list_route_models` 拉取候选模型，再确认是沿用该路线默认模型（`route_default`）还是显式指定（`explicit`）。",
                    "when": "route uses aiocr",
                    "tool": "ppt_list_route_models",
                    "decision_options": ["route_default", "explicit"],
                },
                {
                    "field": "ocr_ai_model",
                    "question": "如果刚才选择了 `explicit`，再填写你从模型列表里选中的 `ocr_ai_model`；只有跨网关时才额外改 `ocr_ai_provider` / `ocr_ai_base_url`。",
                    "when": "ocr_ai_model_decision=explicit",
                },
            ]
        )
    return {
        "ask_one_by_one": True,
        "defaults": {
            "scanned_page_mode": "fullpage",
            "remove_footer_notebooklm": False,
        },
        "hard_rules": [
            "不要自己替用户选择路线。",
            "不要根据 PDF 内容、版式或主观判断推断路线。",
            "不要说“我来为你选择最适合的路线”。",
            "一次只问一个问题，等用户回答后再继续。",
        ],
        "steps": steps,
    }


def _apply_preview_conversion_preferences(
    *,
    options: dict[str, Any],
    effective_config: dict[str, Any],
    scanned_page_mode: Literal["fullpage", "segmented"] | None = None,
    remove_footer_notebooklm: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    patched_options = dict(options)
    patched_effective_config = dict(effective_config)

    if scanned_page_mode is not None:
        resolved_scanned_page_mode = str(scanned_page_mode).strip().lower()
        if resolved_scanned_page_mode not in {"fullpage", "segmented"}:
            raise RouteConfigError(
                code="invalid_scanned_page_mode",
                message="scanned_page_mode must be fullpage or segmented",
                details={"scanned_page_mode": resolved_scanned_page_mode},
            )
        patched_options["scanned_page_mode"] = resolved_scanned_page_mode
        patched_effective_config["scanned_page_mode"] = resolved_scanned_page_mode

    if remove_footer_notebooklm is not None:
        patched_options["remove_footer_notebooklm"] = bool(remove_footer_notebooklm)
        patched_effective_config["remove_footer_notebooklm"] = bool(
            remove_footer_notebooklm
        )

    return patched_options, patched_effective_config


def _apply_conversion_preferences(
    *,
    options: dict[str, Any],
    effective_config: dict[str, Any],
    scanned_page_mode: Literal["fullpage", "segmented"] | None = None,
    remove_footer_notebooklm: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    patched_options, patched_effective_config = _apply_preview_conversion_preferences(
        options=options,
        effective_config=effective_config,
        scanned_page_mode=scanned_page_mode,
        remove_footer_notebooklm=remove_footer_notebooklm,
    )

    resolved_scanned_page_mode = str(
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


def _build_ai_model_selection(
    *,
    options: dict[str, Any],
    effective_config: dict[str, Any],
    ocr_ai_model_decision: Literal["route_default", "explicit"] | None,
) -> dict[str, Any] | None:
    if options.get("ocr_provider") != "aiocr":
        return None
    return {
        "required": True,
        "decision": ocr_ai_model_decision,
        "list_tool": "ppt_list_route_models",
        "recommended_capability": _default_route_model_capability(options=options),
        "user_must_choose_or_accept_default_explicitly": True,
        "do_not_keep_default_silently": True,
        "route_default": {
            "ocr_ai_provider": effective_config.get("ocr_ai_provider"),
            "ocr_ai_base_url": effective_config.get("ocr_ai_base_url"),
            "ocr_ai_model": effective_config.get("ocr_ai_model"),
            "ocr_ai_prompt_preset": effective_config.get("ocr_ai_prompt_preset"),
        },
    }


def _missing_submit_decisions(
    *,
    route_confirmed: bool | None,
    ai_route: bool,
    scanned_page_mode: Literal["fullpage", "segmented"] | None,
    remove_footer_notebooklm: bool | None,
    ocr_ai_model_decision: Literal["route_default", "explicit"] | None = None,
    ocr_ai_model: str | None = None,
) -> list[str]:
    missing: list[str] = []
    if not _route_confirmation_value(route_confirmed):
        missing.append("route_confirmed")
        return missing
    if scanned_page_mode is None:
        missing.append("scanned_page_mode")
    if remove_footer_notebooklm is None:
        missing.append("remove_footer_notebooklm")
    if not ai_route:
        return missing
    if ocr_ai_model_decision is None:
        missing.append("ocr_ai_model_decision")
        return missing
    if ocr_ai_model_decision == "explicit" and not str(ocr_ai_model or "").strip():
        missing.append("ocr_ai_model")
    return missing


def _build_decision_status(
    *,
    route_selected: bool,
    route_title: str | None = None,
    route_confirmed: bool | None = None,
    ai_route: bool,
    scanned_page_mode: Literal["fullpage", "segmented"] | None,
    remove_footer_notebooklm: bool | None,
    ocr_ai_model_decision: Literal["route_default", "explicit"] | None = None,
    ocr_ai_model: str | None = None,
) -> dict[str, Any]:
    workflow_guidance = _build_workflow_guidance(
        route_selected=route_selected,
        route_title=route_title,
        ai_route=ai_route,
    )
    missing_fields = _missing_submit_decisions(
        route_confirmed=route_confirmed,
        ai_route=ai_route,
        scanned_page_mode=scanned_page_mode,
        remove_footer_notebooklm=remove_footer_notebooklm,
        ocr_ai_model_decision=ocr_ai_model_decision,
        ocr_ai_model=ocr_ai_model,
    )
    payload = {
        "ready_for_submit": not missing_fields,
        "missing_fields": missing_fields,
        "workflow_guidance": workflow_guidance,
    }
    if missing_fields:
        step_by_field = {
            step["field"]: step for step in workflow_guidance["steps"] if "field" in step
        }
        next_step = step_by_field.get(missing_fields[0])
        if next_step:
            payload["next_field"] = next_step["field"]
            payload["next_question"] = next_step["question"]
    return payload


def _explicit_ai_model_override_fields(
    *,
    ocr_ai_provider: str | None = None,
    ocr_ai_base_url: str | None = None,
    ocr_ai_model: str | None = None,
) -> list[str]:
    fields: list[str] = []
    if ocr_ai_provider is not None:
        fields.append("ocr_ai_provider")
    if ocr_ai_base_url is not None:
        fields.append("ocr_ai_base_url")
    if ocr_ai_model is not None:
        fields.append("ocr_ai_model")
    return fields


def _validate_ai_model_decision(
    *,
    ai_route: bool,
    ocr_ai_model_decision: Literal["route_default", "explicit"] | None,
    ocr_ai_provider: str | None = None,
    ocr_ai_base_url: str | None = None,
    ocr_ai_model: str | None = None,
) -> None:
    if not ai_route or ocr_ai_model_decision != "route_default":
        return
    override_fields = _explicit_ai_model_override_fields(
        ocr_ai_provider=ocr_ai_provider,
        ocr_ai_base_url=ocr_ai_base_url,
        ocr_ai_model=ocr_ai_model,
    )
    if not override_fields:
        return
    raise RouteConfigError(
        code="invalid_model_decision",
        message=(
            "ocr_ai_model_decision=route_default cannot be combined with explicit "
            "ocr_ai_provider / ocr_ai_base_url / ocr_ai_model overrides"
        ),
        details={
            "ocr_ai_model_decision": ocr_ai_model_decision,
            "override_fields": override_fields,
        },
    )


def _require_submit_decisions(
    *,
    route_selected: bool,
    route_title: str | None = None,
    route_confirmed: bool | None = None,
    ai_route: bool,
    scanned_page_mode: Literal["fullpage", "segmented"] | None,
    remove_footer_notebooklm: bool | None,
    ocr_ai_model_decision: Literal["route_default", "explicit"] | None = None,
    ocr_ai_model: str | None = None,
) -> None:
    status = _build_decision_status(
        route_selected=route_selected,
        route_title=route_title,
        route_confirmed=route_confirmed,
        ai_route=ai_route,
        scanned_page_mode=scanned_page_mode,
        remove_footer_notebooklm=remove_footer_notebooklm,
        ocr_ai_model_decision=ocr_ai_model_decision,
        ocr_ai_model=ocr_ai_model,
    )
    if status["ready_for_submit"]:
        return
    raise RouteConfigError(
        code="missing_required_decision",
        message=(
            "ppt_convert_pdf requires explicit user-confirmed decisions before submission"
        ),
        details=status,
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
    """List human-friendly routes for the user to choose from.

    Do not choose, infer, or recommend a route on the user's behalf. The next
    step is to ask the user to pick one route, then call `ppt_check_route`
    with that route and `route_confirmed=true`.
    """
    try:
        workflow_guidance = _build_workflow_guidance(route_selected=False)
        return {
            "ok": True,
            "routes": list_routes(),
            "workflow_guidance": workflow_guidance,
            "route_selection": _build_route_selection_policy(),
            "preferred_tool_sequence": [
                "ppt_list_routes",
                "ppt_check_route",
                "ppt_convert_pdf",
            ],
            "next_field": "route",
            "next_question": workflow_guidance["steps"][0]["question"],
        }
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_check_route(
    route: str,
    route_confirmed: bool | None = None,
    scanned_page_mode: Literal["fullpage", "segmented"] | None = None,
    remove_footer_notebooklm: bool | None = None,
    ocr_ai_model_decision: Literal["route_default", "explicit"] | None = None,
    ocr_ai_provider: str | None = None,
    ocr_ai_base_url: str | None = None,
    ocr_ai_model: str | None = None,
    ocr_ai_prompt_preset: str | None = None,
) -> dict[str, Any]:
    """Check whether a route is ready and preview the effective settings.

    Use this only after the user explicitly confirms the route. Then continue
    asking in order: scanned-page mode, footer removal, and for AI OCR routes
    whether to keep the default model or pick a fetched model explicitly.
    Never use this tool to auto-decide which route to use.
    """
    try:
        normalized_model_decision = _normalize_ai_model_decision(ocr_ai_model_decision)
        route_definition = get_route_definition(route)
        if not _route_confirmation_value(route_confirmed):
            raise RouteConfigError(
                code="missing_required_decision",
                message=(
                    "ppt_check_route requires explicit user confirmation of the route "
                    "before route-specific inspection"
                ),
                details=_build_decision_status(
                    route_selected=True,
                    route_title=route_definition.title,
                    route_confirmed=route_confirmed,
                    ai_route=_route_uses_ai_ocr(route_definition.route),
                    scanned_page_mode=scanned_page_mode,
                    remove_footer_notebooklm=remove_footer_notebooklm,
                    ocr_ai_model_decision=normalized_model_decision,
                    ocr_ai_model=ocr_ai_model,
                ),
            )
        resolved = resolve_route(route)
        ai_route = bool(resolved.options.get("ocr_provider") == "aiocr")
        route_default_effective_config = dict(resolved.effective_config)
        effective_config = dict(resolved.effective_config)
        options = dict(resolved.options)
        options, effective_config = _apply_preview_conversion_preferences(
            options=options,
            effective_config=effective_config,
            scanned_page_mode=scanned_page_mode,
            remove_footer_notebooklm=remove_footer_notebooklm,
        )
        _validate_ai_model_decision(
            ai_route=ai_route,
            ocr_ai_model_decision=normalized_model_decision,
            ocr_ai_provider=ocr_ai_provider,
            ocr_ai_base_url=ocr_ai_base_url,
            ocr_ai_model=ocr_ai_model,
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
        if ai_route and normalized_model_decision is not None:
            effective_config["ocr_ai_model_decision"] = normalized_model_decision
        decision_status = _build_decision_status(
            route_selected=True,
            route_title=resolved.title,
            route_confirmed=route_confirmed,
            ai_route=ai_route,
            scanned_page_mode=scanned_page_mode,
            remove_footer_notebooklm=remove_footer_notebooklm,
            ocr_ai_model_decision=normalized_model_decision,
            ocr_ai_model=ocr_ai_model,
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
            "route_selection": _build_route_selection_policy(route_title=resolved.title),
            "ai_model_selection": _build_ai_model_selection(
                options=resolved.options,
                effective_config=route_default_effective_config,
                ocr_ai_model_decision=normalized_model_decision,
            ),
            "missing_envs": [],
            **decision_status,
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
    route_confirmed: bool | None = None,
    scanned_page_mode: Literal["fullpage", "segmented"] | None = None,
    remove_footer_notebooklm: bool | None = None,
    ocr_ai_model_decision: Literal["route_default", "explicit"] | None = None,
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
    2. `route_confirmed=true` after the user explicitly confirms the route
    3. `scanned_page_mode`
    4. `remove_footer_notebooklm`
    5. For AI OCR routes only, call `ppt_list_route_models`, then set
       `ocr_ai_model_decision` to `route_default` or `explicit`
    6. If `ocr_ai_model_decision=explicit`, provide the chosen `ocr_ai_model`
       and optional `ocr_ai_provider` / `ocr_ai_base_url`

    The tool resolves provider secrets from environment variables and translates
    the chosen route into lower-level ppt API form fields automatically. Never
    call this tool until the user has explicitly chosen and confirmed the route.
    """
    try:
        normalized_model_decision = _normalize_ai_model_decision(ocr_ai_model_decision)
        route_definition = get_route_definition(route)
        if not _route_confirmation_value(route_confirmed):
            raise RouteConfigError(
                code="missing_required_decision",
                message=(
                    "ppt_convert_pdf requires explicit user confirmation of the route "
                    "before submission"
                ),
                details=_build_decision_status(
                    route_selected=True,
                    route_title=route_definition.title,
                    route_confirmed=route_confirmed,
                    ai_route=_route_uses_ai_ocr(route_definition.route),
                    scanned_page_mode=scanned_page_mode,
                    remove_footer_notebooklm=remove_footer_notebooklm,
                    ocr_ai_model_decision=normalized_model_decision,
                    ocr_ai_model=ocr_ai_model,
                ),
            )
        resolved = resolve_route(route)
        ai_route = bool(resolved.options.get("ocr_provider") == "aiocr")
        _require_submit_decisions(
            route_selected=True,
            route_title=resolved.title,
            route_confirmed=route_confirmed,
            ai_route=ai_route,
            scanned_page_mode=scanned_page_mode,
            remove_footer_notebooklm=remove_footer_notebooklm,
            ocr_ai_model_decision=normalized_model_decision,
            ocr_ai_model=ocr_ai_model,
        )
        _validate_ai_model_decision(
            ai_route=ai_route,
            ocr_ai_model_decision=normalized_model_decision,
            ocr_ai_provider=ocr_ai_provider,
            ocr_ai_base_url=ocr_ai_base_url,
            ocr_ai_model=ocr_ai_model,
        )
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
        if ai_route:
            effective_config["ocr_ai_model_decision"] = normalized_model_decision
        payload = client.create_job(pdf_path=pdf_path, options=options)
        return {
            "ok": True,
            "route": resolved.route,
            "display_name": resolved.title,
            "recommended_input": resolved.title,
            "route_selection": _build_route_selection_policy(route_title=resolved.title),
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
    route_default_model: str | None = None
    route_model_capability: Literal["vision", "ocr"] | None = None
    if route:
        try:
            route_definition = get_route_definition(route)
            route_selected = True
            route_title = route_definition.title
            ai_route = _route_uses_ai_ocr(route_definition.route)
            if ai_route:
                try:
                    resolved = resolve_route(route)
                except RouteConfigError:
                    resolved = None
                if resolved is not None:
                    route_default_model = str(
                        resolved.effective_config.get("ocr_ai_model") or ""
                    ).strip() or None
                    route_model_capability = _default_route_model_capability(
                        options=resolved.options
                    )
        except RouteConfigError:
            route_title = str(route).strip()
            ai_route = None
    guidance = _build_workflow_guidance(
        route_selected=route_selected,
        route_title=route_title or None,
        ai_route=ai_route,
    )
    lines = [
        "请按下面顺序逐个确认，不要一次性抛出所有参数。",
        "不要自己替用户选路线，也不要说“我来为你选择最适合的路线”。",
        "建议回复示例：可用路线如下，请您先选一条；在您确认前我不会替您决定。",
    ]
    if route_title:
        lines.append(f"当前路线：{route_title}")
        lines.append("先问用户是否明确确认这条路线；确认前不要继续调用 route 级工具。")
    if route_default_model and route_model_capability:
        lines.append(
            "AI 路线默认模型："
            f"{route_default_model}。建议先调用 "
            f"`ppt_list_route_models(route={route_title!r}, route_confirmed=True, capability={route_model_capability!r})` "
            "拉取候选模型，再让用户明确选择。"
        )
    for index, step in enumerate(guidance["steps"], start=1):
        line = f"{index}. {step['question']}"
        recommended = step.get("recommended")
        if recommended is not None:
            line += f" 默认建议：{recommended}"
        when = step.get("when")
        if when:
            line += f" 条件：{when}"
        tool = step.get("tool")
        if tool:
            line += f" 工具：{tool}"
        lines.append(line)
    return "\n".join(lines)


@mcp.tool()
def ppt_create_job(
    pdf_path: str,
    options: dict[str, Any] | None = None,
    low_level_override_confirmed: bool | None = None,
) -> dict[str, Any]:
    """Create a PDF-to-PPT conversion job from a local pdf_path.

    The PDF is read from local disk by this MCP server, then uploaded to the
    running ppt API. This is the low-level escape hatch. For normal use prefer
    `ppt_list_routes` -> `ppt_check_route` -> `ppt_convert_pdf`.

    Never use this tool unless the user explicitly asks to bypass the guided
    route workflow and confirms `low_level_override_confirmed=true`.
    """
    try:
        if not _low_level_override_value(low_level_override_confirmed):
            raise RouteConfigError(
                code="missing_required_decision",
                message=(
                    "ppt_create_job is a low-level escape hatch and requires "
                    "explicit user confirmation before bypassing the guided workflow"
                ),
                details={
                    "missing_fields": ["low_level_override_confirmed"],
                    "next_field": "low_level_override_confirmed",
                    "next_question": _build_low_level_escape_hatch_policy()[
                        "next_question"
                    ],
                    "preferred_tool_sequence": [
                        "ppt_list_routes",
                        "ppt_check_route",
                        "ppt_convert_pdf",
                    ],
                    "route_selection": _build_route_selection_policy(),
                    "low_level_escape_hatch": _build_low_level_escape_hatch_policy(),
                },
            )
        payload = client.create_job(pdf_path=pdf_path, options=options)
        return {
            "ok": True,
            "job": payload,
            "low_level_escape_hatch": _build_low_level_escape_hatch_policy(),
        }
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
def ppt_list_route_models(
    route: str,
    route_confirmed: bool | None = None,
    capability: Literal["all", "vision", "ocr"] | None = None,
    ocr_ai_provider: str | None = None,
    ocr_ai_base_url: str | None = None,
) -> dict[str, Any]:
    """List candidate models for an AI OCR route after the user confirms the route."""
    try:
        route_definition = get_route_definition(route)
        if not _route_confirmation_value(route_confirmed):
            raise RouteConfigError(
                code="missing_required_decision",
                message=(
                    "ppt_list_route_models requires explicit user confirmation of the "
                    "route before route-specific model listing"
                ),
                details=_build_decision_status(
                    route_selected=True,
                    route_title=route_definition.title,
                    route_confirmed=route_confirmed,
                    ai_route=_route_uses_ai_ocr(route_definition.route),
                    scanned_page_mode=None,
                    remove_footer_notebooklm=None,
                ),
            )
        resolved = resolve_route(route)
        if resolved.options.get("ocr_provider") != "aiocr":
            raise RouteConfigError(
                code="invalid_route_model_listing",
                message="Route model listing is only supported on AI OCR routes",
                details={
                    "route": resolved.title,
                    "supported_routes": [
                        "本地切块识别",
                        "模型直出框和文字",
                        "内置文档解析",
                    ],
                },
            )
        provider = ocr_ai_provider or str(resolved.options.get("ocr_ai_provider") or "")
        base_url = (
            ocr_ai_base_url
            if ocr_ai_base_url is not None
            else resolved.options.get("ocr_ai_base_url")
        )
        api_key = str(resolved.options.get("ocr_ai_api_key") or "").strip()
        if not api_key:
            raise RouteConfigError(
                code="missing_env",
                message="Resolved AI OCR route does not have an API key configured",
                details={"route": resolved.title},
            )
        resolved_capability = _normalize_model_list_capability(capability)
        if resolved_capability is None:
            resolved_capability = _default_route_model_capability(options=resolved.options)
        payload = client.list_ai_models(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            capability=resolved_capability,
        )
        models = payload.get("models") if isinstance(payload.get("models"), list) else []
        return {
            "ok": True,
            "route": resolved.route,
            "display_name": resolved.title,
            "recommended_input": resolved.title,
            "route_selection": _build_route_selection_policy(route_title=resolved.title),
            "provider": provider,
            "base_url": base_url,
            "capability": resolved_capability,
            "route_default": {
                "ocr_ai_provider": resolved.effective_config.get("ocr_ai_provider"),
                "ocr_ai_base_url": resolved.effective_config.get("ocr_ai_base_url"),
                "ocr_ai_model": resolved.effective_config.get("ocr_ai_model"),
            },
            "models": models,
            "model_count": len(models),
            "selection_instructions": {
                "route_confirmation_field": "route_confirmed",
                "decision_field": "ocr_ai_model_decision",
                "decision_options": ["route_default", "explicit"],
                "user_must_choose_or_accept_default_explicitly": True,
                "do_not_keep_default_silently": True,
                "submit_tool": "ppt_convert_pdf",
            },
        }
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
