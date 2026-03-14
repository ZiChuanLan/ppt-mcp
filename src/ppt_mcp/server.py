"""MCP server entrypoint for the local ppt wrapper."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
import time
from typing import Any, Literal
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from ppt_mcp.api_client import PptApiClient, PptApiError, _normalize_local_pdf_path
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
        "2) explicitly confirm that route with `route_confirmed=true` by calling `ppt_check_route`, which returns a locked `route_workflow_id`, "
        "3) call `ppt_set_conversion_target` on that same `route_workflow_id` to write `pdf_path` and `page_range_decision`, and if needed `page_start`/`page_end`, "
        "4) call `ppt_set_route_options` on that same `route_workflow_id` to write scanned-page handling, footer removal, and AI model choice, "
        "5) continue the same high-level flow only with that `route_workflow_id`; do not start mixing other routes into the same flow, "
        "6) for AI OCR routes only, call `ppt_list_route_models` with the same `route_workflow_id`, then ask whether to keep the route default model or use a fetched model explicitly. "
        "If the user picks a model from `ppt_list_route_models`, reuse the locked route workflow's provider/base URL/API key by default. "
        "Prefer `ocr_ai_model_choice_index` over retyping a long `ocr_ai_model` string when the user selects from `model_choices`. "
        "Never invent a local `pdf_path`, sample file, or placeholder path. Only use a file path the user explicitly provided. "
        "The high-level route tools do not support gateway switching. Do not ask the user for another API key, provider, or base URL in the guided flow. "
        "If the user explicitly wants to switch gateways, stop the guided flow and use low-level expert tools instead. "
        "When the user asks for available models, never answer from memory: call `ppt_list_route_models` (preferred) or `ppt_list_ai_models`, "
        "then repeat only the exact returned model IDs. Do not invent provider buckets, categories, or recommendations that the tool did not return. "
        "If an earlier step is still missing, do not skip ahead to later tools. "
        "Never say you will choose the best route for the user. If the user only asks to convert a page range, first show the available routes and ask the user to pick one. "
        "Do not silently choose `segmented`, and prefer `ppt_convert_pdf` over `ppt_create_job` for normal use. "
        "Use local file paths for PDFs, then poll job status until completion."
    ),
)


_ROUTE_WORKFLOW_TTL_SECONDS = 60 * 60


@dataclass
class RouteWorkflowState:
    workflow_id: str
    route: str
    title: str
    summary: str
    options: dict[str, Any]
    effective_config: dict[str, Any]
    ai_route: bool
    created_at: float
    updated_at: float
    pdf_path: str | None = None
    page_range_decision: Literal["all_pages", "page_range"] | None = None
    page_start: int | None = None
    page_end: int | None = None
    scanned_page_mode: Literal["fullpage", "segmented"] | None = None
    remove_footer_notebooklm: bool | None = None
    ocr_ai_model_decision: Literal["route_default", "explicit"] | None = None
    ocr_ai_model_choice_index: int | None = None
    ocr_ai_model: str | None = None
    last_model_choices: list[dict[str, Any]] = field(default_factory=list)
    last_listed_models: list[str] = field(default_factory=list)
    last_model_listing_capability: str | None = None


_ROUTE_WORKFLOWS: dict[str, RouteWorkflowState] = {}


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


def _normalize_page_range_decision(
    decision: str | None,
) -> Literal["all_pages", "page_range"] | None:
    normalized = str(decision or "").strip().lower()
    if not normalized:
        return None
    aliases = {
        "all_pages": "all_pages",
        "all": "all_pages",
        "full_document": "all_pages",
        "full_doc": "all_pages",
        "entire_document": "all_pages",
        "entire_pdf": "all_pages",
        "整份": "all_pages",
        "全部": "all_pages",
        "所有页": "all_pages",
        "整份pdf": "all_pages",
        "page_range": "page_range",
        "range": "page_range",
        "pages": "page_range",
        "subset": "page_range",
        "指定页码": "page_range",
        "页码范围": "page_range",
        "部分页面": "page_range",
    }
    resolved = aliases.get(normalized)
    if resolved is None:
        raise RouteConfigError(
            code="invalid_page_range_decision",
            message="page_range_decision must be all_pages or page_range",
            details={"page_range_decision": decision},
        )
    return resolved


def _default_route_model_capability(
    *,
    options: dict[str, Any],
) -> Literal["vision", "ocr"]:
    chain_mode = str(options.get("ocr_ai_chain_mode") or "").strip().lower()
    if chain_mode == "layout_block":
        return "ocr"
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
            "ppt_set_conversion_target",
            "ppt_list_route_models",
            "ppt_set_route_options",
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


def _build_model_listing_policy(*, preferred_tool: str) -> dict[str, Any]:
    return {
        "preferred_tool": preferred_tool,
        "must_call_tool": True,
        "only_repeat_returned_model_ids": True,
        "do_not_invent_models": True,
        "do_not_group_into_unverified_provider_buckets": True,
        "do_not_add_recommendations_without_user_request": True,
    }


def _build_route_credential_reuse_policy(
    *,
    effective_config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "reuse_route_credentials_by_default": True,
        "route_credentials_resolved_from_env": bool(
            effective_config.get("api_key_source")
        ),
        "api_key_source": effective_config.get("api_key_source"),
        "default_provider": effective_config.get("ocr_ai_provider"),
        "default_base_url": effective_config.get("ocr_ai_base_url"),
        "do_not_ask_user_for_api_key_again": True,
        "high_level_route_tools_do_not_accept_raw_api_keys": True,
        "do_not_ask_user_for_provider_or_base_url_again_when_using_same_gateway": True,
        "gateway_switch_supported_in_high_level_flow": False,
        "gateway_switch_requires_expert_tools": [
            "ppt_list_ai_models",
            "ppt_check_ai_ocr",
            "ppt_create_job",
        ],
        "same_gateway_explicit_submit_fields": [
            "ocr_ai_model_choice_index",
            "ocr_ai_model",
        ],
    }


def _build_route_model_choices(
    *,
    route_default_model: str | None,
    fetched_models: list[str],
) -> list[dict[str, Any]]:
    choices: list[dict[str, Any]] = []
    seen: set[str] = set()
    default_in_fetched = bool(
        route_default_model and route_default_model in set(fetched_models)
    )
    if route_default_model:
        choices.append(
            {
                "index": 0,
                "model": route_default_model,
                "source": "route_default",
                "is_default": True,
                "in_provider_list": default_in_fetched,
            }
        )
        seen.add(route_default_model)
    for model in fetched_models:
        if model in seen:
            continue
        choices.append(
            {
                "index": len(choices),
                "model": model,
                "source": "provider_list",
                "is_default": False,
                "in_provider_list": True,
            }
        )
        seen.add(model)
    return choices


def _build_choice_display_lines(model_choices: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in model_choices:
        suffix = " [route_default]" if item.get("is_default") else ""
        lines.append(f"{item['index']}. {item['model']}{suffix}")
    return lines


def _normalize_pdf_path(pdf_path: str | None) -> str | None:
    if pdf_path is None:
        return None
    cleaned = str(pdf_path).strip()
    if not cleaned:
        raise RouteConfigError(
            code="invalid_pdf_path",
            message="pdf_path cannot be empty",
            details={"pdf_path": pdf_path},
        )
    return cleaned


def _resolve_existing_local_pdf_path(
    pdf_path: str | None,
    *,
    next_tool: str = "ppt_set_conversion_target",
) -> str | None:
    cleaned = _normalize_pdf_path(pdf_path)
    if cleaned is None:
        return None
    normalized_path = _normalize_local_pdf_path(cleaned).expanduser().resolve()
    if not normalized_path.exists():
        raise RouteConfigError(
            code="pdf_path_not_found",
            message=(
                "pdf_path does not exist on the MCP host; only use a real local "
                "PDF path explicitly provided by the user"
            ),
            details={
                "submitted_pdf_path": cleaned,
                "normalized_pdf_path": str(normalized_path),
                "next_field": "pdf_path",
                "next_tool": next_tool,
            },
        )
    if not normalized_path.is_file():
        raise RouteConfigError(
            code="invalid_pdf_path",
            message="pdf_path must point to a file, not a directory",
            details={
                "submitted_pdf_path": cleaned,
                "normalized_pdf_path": str(normalized_path),
                "next_field": "pdf_path",
                "next_tool": next_tool,
            },
        )
    if normalized_path.suffix.lower() != ".pdf":
        raise RouteConfigError(
            code="invalid_pdf_path",
            message="Only .pdf files are supported for pdf_path",
            details={
                "submitted_pdf_path": cleaned,
                "normalized_pdf_path": str(normalized_path),
                "next_field": "pdf_path",
                "next_tool": next_tool,
            },
        )
    return str(normalized_path)


def _normalize_page_value(field_name: str, value: int | None) -> int | None:
    if value is None:
        return None
    if value < 1:
        raise RouteConfigError(
            code="invalid_page_range",
            message=f"{field_name} must be >= 1",
            details={field_name: value},
        )
    return value


def _validate_page_range(
    *,
    page_start: int | None,
    page_end: int | None,
) -> None:
    if page_start is not None and page_end is not None and page_start > page_end:
        raise RouteConfigError(
            code="invalid_page_range",
            message="page_start cannot be greater than page_end",
            details={"page_start": page_start, "page_end": page_end},
        )


def _page_range_label(
    *,
    page_range_decision: Literal["all_pages", "page_range"] | None = None,
    page_start: int | None,
    page_end: int | None,
) -> str:
    if page_range_decision == "all_pages":
        return "all_pages"
    if page_range_decision == "page_range":
        if page_start is not None and page_end is not None:
            return f"{page_start}-{page_end}"
        return "page_range_pending"
    if page_start is None and page_end is None:
        return "all_pages"
    if page_start is not None and page_end is not None:
        return f"{page_start}-{page_end}"
    if page_start is not None:
        return f"{page_start}-end"
    return f"1-{page_end}"


def _prune_route_workflows() -> None:
    cutoff = time.time() - _ROUTE_WORKFLOW_TTL_SECONDS
    expired = [
        workflow_id
        for workflow_id, state in _ROUTE_WORKFLOWS.items()
        if state.updated_at < cutoff
    ]
    for workflow_id in expired:
        _ROUTE_WORKFLOWS.pop(workflow_id, None)


def _route_workflow_required_details() -> dict[str, Any]:
    return {
        "missing_fields": ["route_workflow_id"],
        "next_field": "route_workflow_id",
        "next_question": (
            "请先调用 `ppt_check_route` 锁定一条路线，拿到 `route_workflow_id` 后，再继续同一条高层链路。"
        ),
        "workflow_lock_required": True,
        "preferred_tool_sequence": [
            "ppt_list_routes",
            "ppt_check_route",
            "ppt_set_conversion_target",
            "ppt_list_route_models",
            "ppt_set_route_options",
            "ppt_convert_pdf",
        ],
        "route_selection": _build_route_selection_policy(),
    }


def _create_route_workflow(*, resolved_route: Any) -> RouteWorkflowState:
    _prune_route_workflows()
    now = time.time()
    state = RouteWorkflowState(
        workflow_id=uuid4().hex,
        route=resolved_route.route,
        title=resolved_route.title,
        summary=resolved_route.summary,
        options=dict(resolved_route.options),
        effective_config=dict(resolved_route.effective_config),
        ai_route=bool(resolved_route.options.get("ocr_provider") == "aiocr"),
        created_at=now,
        updated_at=now,
    )
    _ROUTE_WORKFLOWS[state.workflow_id] = state
    return state


def _get_route_workflow(route_workflow_id: str | None) -> RouteWorkflowState:
    cleaned = str(route_workflow_id or "").strip()
    if not cleaned:
        raise RouteConfigError(
            code="missing_route_workflow",
            message=(
                "High-level route tools require route_workflow_id from ppt_check_route"
            ),
            details=_route_workflow_required_details(),
        )
    _prune_route_workflows()
    state = _ROUTE_WORKFLOWS.get(cleaned)
    if state is None:
        raise RouteConfigError(
            code="unknown_route_workflow",
            message=(
                "route_workflow_id was not found or has expired; restart from "
                "ppt_check_route"
            ),
            details={
                **_route_workflow_required_details(),
                "route_workflow_id": cleaned,
            },
        )
    return state


def _update_route_workflow(
    *,
    state: RouteWorkflowState,
    pdf_path: str | None = None,
    page_range_decision: Literal["all_pages", "page_range"] | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    scanned_page_mode: Literal["fullpage", "segmented"] | None = None,
    remove_footer_notebooklm: bool | None = None,
    ocr_ai_model_decision: Literal["route_default", "explicit"] | None = None,
    ocr_ai_model_choice_index: int | None = None,
    ocr_ai_model: str | None = None,
) -> None:
    if pdf_path is not None:
        state.pdf_path = _resolve_existing_local_pdf_path(pdf_path)
    if page_range_decision is not None:
        state.page_range_decision = page_range_decision
        if page_range_decision == "all_pages":
            state.page_start = None
            state.page_end = None
    if page_start is not None:
        state.page_start = _normalize_page_value("page_start", page_start)
    if page_end is not None:
        state.page_end = _normalize_page_value("page_end", page_end)
    if state.page_range_decision == "all_pages" and (
        state.page_start is not None or state.page_end is not None
    ):
        raise RouteConfigError(
            code="invalid_page_range_decision",
            message=(
                "page_range_decision=all_pages cannot be combined with explicit "
                "page_start/page_end"
            ),
            details={
                "page_range_decision": state.page_range_decision,
                "page_start": state.page_start,
                "page_end": state.page_end,
            },
        )
    _validate_page_range(page_start=state.page_start, page_end=state.page_end)
    if scanned_page_mode is not None:
        state.scanned_page_mode = scanned_page_mode
    if remove_footer_notebooklm is not None:
        state.remove_footer_notebooklm = bool(remove_footer_notebooklm)
    if ocr_ai_model_decision is not None:
        state.ocr_ai_model_decision = ocr_ai_model_decision
        if ocr_ai_model_decision == "route_default":
            state.ocr_ai_model = None
            state.ocr_ai_model_choice_index = None
    if ocr_ai_model is not None:
        state.ocr_ai_model = str(ocr_ai_model).strip() or None
    if ocr_ai_model_choice_index is not None:
        choice = _resolve_model_choice_from_index(
            state=state,
            choice_index=ocr_ai_model_choice_index,
        )
        selected_model = str(choice["model"]).strip()
        if state.ocr_ai_model and state.ocr_ai_model != selected_model:
            raise RouteConfigError(
                code="model_choice_conflict",
                message=(
                    "ocr_ai_model_choice_index and ocr_ai_model refer to different "
                    "models"
                ),
                details={
                    "ocr_ai_model_choice_index": ocr_ai_model_choice_index,
                    "ocr_ai_model": state.ocr_ai_model,
                    "choice_model": selected_model,
                },
            )
        state.ocr_ai_model_choice_index = ocr_ai_model_choice_index
        state.ocr_ai_model = selected_model
    _validate_explicit_model_selection(state)
    if state.ai_route and state.ocr_ai_model_decision == "explicit" and state.ocr_ai_model:
        for item in state.last_model_choices:
            if str(item["model"]) == state.ocr_ai_model:
                state.ocr_ai_model_choice_index = int(item["index"])
                break
    state.updated_at = time.time()


def _preview_route_workflow(
    *,
    state: RouteWorkflowState,
) -> tuple[dict[str, Any], dict[str, Any]]:
    options = dict(state.options)
    effective_config = dict(state.effective_config)
    if state.page_range_decision == "page_range" and state.page_start is not None:
        options["page_start"] = state.page_start
        effective_config["page_start"] = state.page_start
    if state.page_range_decision == "page_range" and state.page_end is not None:
        options["page_end"] = state.page_end
        effective_config["page_end"] = state.page_end
    options, effective_config = _apply_preview_conversion_preferences(
        options=options,
        effective_config=effective_config,
        scanned_page_mode=state.scanned_page_mode,
        remove_footer_notebooklm=state.remove_footer_notebooklm,
    )
    _validate_ai_model_decision(
        ai_route=state.ai_route,
        ocr_ai_model_decision=state.ocr_ai_model_decision,
        ocr_ai_model_choice_index=state.ocr_ai_model_choice_index,
        ocr_ai_model=state.ocr_ai_model,
    )
    options, effective_config = _apply_ai_route_overrides(
        route=state.title,
        options=options,
        effective_config=effective_config,
        overrides=_build_ai_route_overrides(
            ocr_ai_model=state.ocr_ai_model,
        ),
    )
    if state.ai_route and state.ocr_ai_model_decision is not None:
        effective_config["ocr_ai_model_decision"] = state.ocr_ai_model_decision
    return options, effective_config


def _build_route_workflow_payload(state: RouteWorkflowState) -> dict[str, Any]:
    decision_status = _build_decision_status(
        route_selected=True,
        route_title=state.title,
        route_confirmed=True,
        pdf_path=state.pdf_path,
        page_range_decision=state.page_range_decision,
        page_start=state.page_start,
        page_end=state.page_end,
        ai_route=state.ai_route,
        scanned_page_mode=state.scanned_page_mode,
        remove_footer_notebooklm=state.remove_footer_notebooklm,
        ocr_ai_model_decision=state.ocr_ai_model_decision,
        ocr_ai_model=state.ocr_ai_model,
    )
    return {
        "workflow_id": state.workflow_id,
        "locked": True,
        "carry_forward_field": "route_workflow_id",
        "locked_route": state.route,
        "display_name": state.title,
        "same_route_only": True,
        "same_gateway_only": True,
        "allowed_next_tools": [
            "ppt_set_conversion_target",
            "ppt_list_route_models",
            "ppt_set_route_options",
            "ppt_convert_pdf",
        ],
        "gateway": {
            "ocr_ai_provider": state.effective_config.get("ocr_ai_provider"),
            "ocr_ai_base_url": state.effective_config.get("ocr_ai_base_url"),
            "api_key_source": state.effective_config.get("api_key_source"),
        },
        "conversion_target": {
            "pdf_path": state.pdf_path,
            "page_range_decision": state.page_range_decision,
            "page_start": state.page_start,
            "page_end": state.page_end,
            "page_range_label": _page_range_label(
                page_range_decision=state.page_range_decision,
                page_start=state.page_start,
                page_end=state.page_end,
            ),
        },
        "last_model_listing": {
            "capability": state.last_model_listing_capability,
            "model_count": len(state.last_listed_models),
        },
        "next_required_step": {
            "ready_for_submit": decision_status["ready_for_submit"],
            "missing_fields": decision_status["missing_fields"],
            "next_field": decision_status.get("next_field"),
            "next_tool": decision_status.get("next_tool"),
            "step_lock_active": bool(decision_status["missing_fields"]),
        },
        "created_at_unix": int(state.created_at),
        "updated_at_unix": int(state.updated_at),
    }


def _build_current_decisions(state: RouteWorkflowState) -> dict[str, Any]:
    return {
        "route_confirmed": True,
        "pdf_path": state.pdf_path,
        "page_range_decision": state.page_range_decision,
        "page_start": state.page_start,
        "page_end": state.page_end,
        "page_range_label": _page_range_label(
            page_range_decision=state.page_range_decision,
            page_start=state.page_start,
            page_end=state.page_end,
        ),
        "scanned_page_mode": state.scanned_page_mode,
        "remove_footer_notebooklm": state.remove_footer_notebooklm,
        "ocr_ai_model_decision": state.ocr_ai_model_decision,
        "ocr_ai_model_choice_index": state.ocr_ai_model_choice_index,
        "ocr_ai_model": state.ocr_ai_model,
    }


def _missing_conversion_target_decisions(
    *,
    pdf_path: str | None,
    page_range_decision: Literal["all_pages", "page_range"] | None,
    page_start: int | None,
    page_end: int | None,
) -> list[str]:
    missing: list[str] = []
    if not str(pdf_path or "").strip():
        missing.append("pdf_path")
    if page_range_decision is None:
        missing.append("page_range_decision")
    elif page_range_decision == "page_range":
        if page_start is None:
            missing.append("page_start")
        if page_end is None:
            missing.append("page_end")
    return missing


def _require_conversion_target_step(
    *,
    state: RouteWorkflowState,
    requested_tool: str,
) -> None:
    blocking_fields = _missing_conversion_target_decisions(
        pdf_path=state.pdf_path,
        page_range_decision=state.page_range_decision,
        page_start=state.page_start,
        page_end=state.page_end,
    )
    if not blocking_fields:
        return
    status = _build_decision_status(
        route_selected=True,
        route_title=state.title,
        route_confirmed=True,
        pdf_path=state.pdf_path,
        page_range_decision=state.page_range_decision,
        page_start=state.page_start,
        page_end=state.page_end,
        ai_route=state.ai_route,
        scanned_page_mode=state.scanned_page_mode,
        remove_footer_notebooklm=state.remove_footer_notebooklm,
        ocr_ai_model_decision=state.ocr_ai_model_decision,
        ocr_ai_model=state.ocr_ai_model,
    )
    raise RouteConfigError(
        code="workflow_step_out_of_order",
        message=(
            f"{requested_tool} requires pdf_path and page range to be confirmed "
            "first on the current route_workflow_id"
        ),
        details={
            "route_workflow_id": state.workflow_id,
            "requested_tool": requested_tool,
            "step_lock": True,
            "step_lock_reason": "confirm_conversion_target_first",
            "blocking_fields": blocking_fields,
            "next_field": status.get("next_field", "pdf_path"),
            "next_tool": "ppt_set_conversion_target",
            "next_question": status.get("next_question"),
            "workflow_guidance": status.get("workflow_guidance"),
        },
    )


def _resolve_model_choice_from_index(
    *,
    state: RouteWorkflowState,
    choice_index: int,
) -> dict[str, Any]:
    if choice_index < 0:
        raise RouteConfigError(
            code="invalid_model_choice_index",
            message="ocr_ai_model_choice_index must be >= 0",
            details={"ocr_ai_model_choice_index": choice_index},
        )
    if not state.last_model_choices:
        raise RouteConfigError(
            code="missing_model_listing",
            message=(
                "Explicit model selection requires calling ppt_list_route_models "
                "first on the current route_workflow_id"
            ),
            details={
                "route_workflow_id": state.workflow_id,
                "next_tool": "ppt_list_route_models",
                "selection_field": "ocr_ai_model_choice_index",
            },
        )
    for item in state.last_model_choices:
        if int(item["index"]) == choice_index:
            return item
    raise RouteConfigError(
        code="invalid_model_choice_index",
        message="ocr_ai_model_choice_index is not in the last fetched model_choices",
        details={
            "ocr_ai_model_choice_index": choice_index,
            "valid_indexes": [int(item["index"]) for item in state.last_model_choices],
            "model_choices": state.last_model_choices,
        },
    )


def _validate_explicit_model_selection(state: RouteWorkflowState) -> None:
    if not state.ai_route or state.ocr_ai_model_decision != "explicit":
        return
    if not state.last_model_choices:
        raise RouteConfigError(
            code="missing_model_listing",
            message=(
                "Explicit model selection requires calling ppt_list_route_models "
                "first on the current route_workflow_id"
            ),
            details={
                "route_workflow_id": state.workflow_id,
                "next_tool": "ppt_list_route_models",
                "selection_field": "ocr_ai_model_choice_index",
            },
        )
    selected_model = str(state.ocr_ai_model or "").strip()
    if not selected_model:
        return
    valid_models = {str(item["model"]) for item in state.last_model_choices}
    if selected_model not in valid_models:
        raise RouteConfigError(
            code="invalid_explicit_model",
            message=(
                "ocr_ai_model must be chosen from the last fetched model_choices "
                "for this route_workflow_id"
            ),
            details={
                "ocr_ai_model": selected_model,
                "route_workflow_id": state.workflow_id,
                "model_choices": state.last_model_choices,
            },
        )


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
                "page_range_decision": "all_pages",
                "scanned_page_mode": "fullpage",
                "remove_footer_notebooklm": False,
            },
            "defaults_require_explicit_user_acceptance": True,
            "hard_rules": [
                "不要自己替用户选择路线。",
                "不要根据 PDF 内容、版式或主观判断推断路线。",
                "不要说“我来为你选择最适合的路线”。",
                "不要编造 `pdf_path`；只能使用用户明确提供的真实本地 PDF 路径。",
                "一旦 `ppt_check_route` 返回 `route_workflow_id`，后续高层 route 工具必须沿用同一个 workflow，不要把不同路线串起来。",
                "高层 route 流程里不要再向用户索要 API key；路线凭据默认由 MCP 环境变量复用。",
                "高层 route 工具不支持切换 provider / base_url；如需切网关，停止引导流程并改用低层工具。",
                "列模型时只复述工具返回的原始 model id，不要脑补供应商分类、模型家族或推荐语。",
                "一次只问一个问题，等用户回答后再继续。",
                "如果 `next_tool` 仍然指向上一步，就不要跳到后面的工具。",
            ],
            "steps": steps,
        }
    route_label = f"`{route_title}`" if route_title else "当前路线"
    steps.append(
        {
            "field": "route_confirmed",
            "question": f"先确认用户是否明确要走 {route_label}；在用户确认前，不要继续做该路线的检查、模型拉取或提交。",
            "recommended": True,
            "tool": "ppt_check_route",
        }
    )
    steps.extend(
        [
            {
                "field": "pdf_path",
                "question": "再确认本地 PDF 路径；如果用户一开始已经给了路径，就尽早写进当前 `route_workflow_id`，不要把文件路径只留在对话记忆里。`pdf_path` 必须来自用户明确提供的真实本地路径，不要自己编示例路径、测试路径或占位路径。",
                "tool": "ppt_set_conversion_target",
            },
            {
                "field": "page_range_decision",
                "question": "再明确确认页码范围决策：`all_pages`（整份 PDF）还是 `page_range`（指定页码范围）。这一步必须明确，不要跳过。",
                "recommended": "all_pages",
                "tool": "ppt_set_conversion_target",
            },
            {
                "field": "page_start",
                "question": "如果刚才选择了 `page_range`，再填写 `page_start`。",
                "when": "page_range_decision=page_range",
                "tool": "ppt_set_conversion_target",
            },
            {
                "field": "page_end",
                "question": "如果刚才选择了 `page_range`，再填写 `page_end`。",
                "when": "page_range_decision=page_range",
                "tool": "ppt_set_conversion_target",
            },
            {
                "field": "scanned_page_mode",
                "question": "再确认扫描页图片处理方式：`fullpage`（默认，最像原图）还是 `segmented`（拆成可编辑图片块，但更容易切错）。",
                "recommended": "fullpage",
                "tool": "ppt_set_route_options",
            },
            {
                "field": "remove_footer_notebooklm",
                "question": "再确认是否删除 NotebookLM 页脚；只有明确存在该页脚时才建议打开。",
                "recommended": False,
                "tool": "ppt_set_route_options",
            },
        ]
    )
    if ai_route is None or ai_route:
        steps.extend(
            [
                {
                    "field": "ocr_ai_model_decision",
                    "question": "如果选择的是 AI OCR 链路，先用同一个 `route_workflow_id` 调用 `ppt_list_route_models` 拉取候选模型；列模型时只可复述工具返回的原始 model id，不要补充未验证的模型分类或推荐。再确认是沿用该路线默认模型（`route_default`）还是显式指定（`explicit`）。如果只是改模型，默认沿用当前路线 workflow 已锁定的 provider / base URL / API key；高层 route 工具不支持切网关。",
                    "when": "route uses aiocr",
                    "tool": "ppt_list_route_models",
                    "decision_options": ["route_default", "explicit"],
                },
                {
                    "field": "ocr_ai_model_choice_index",
                    "question": "如果刚才选择了 `explicit`，优先填写你从 `model_choices` 里选中的 `ocr_ai_model_choice_index`，不要让低端模型自己复述长 model id。",
                    "when": "ocr_ai_model_decision=explicit",
                    "tool": "ppt_set_route_options",
                },
                {
                    "field": "ocr_ai_model",
                    "question": "只有在确实需要时，再填写你从模型列表里选中的 `ocr_ai_model`；它必须来自同一个 `route_workflow_id` 最近一次返回的 `model_choices`。同网关下不要再问 API key，也不要重复追问 `ocr_ai_provider` / `ocr_ai_base_url`。如果用户明确要求切换网关，就不要继续用高层 route 工具，改走低层工具。",
                    "when": "ocr_ai_model_decision=explicit",
                    "tool": "ppt_set_route_options",
                },
            ]
        )
    return {
        "ask_one_by_one": True,
        "defaults": {
            "page_range_decision": "all_pages",
            "scanned_page_mode": "fullpage",
            "remove_footer_notebooklm": False,
        },
        "defaults_require_explicit_user_acceptance": True,
        "hard_rules": [
            "不要自己替用户选择路线。",
            "不要根据 PDF 内容、版式或主观判断推断路线。",
            "不要说“我来为你选择最适合的路线”。",
            "不要编造 `pdf_path`；只能使用用户明确提供的真实本地 PDF 路径。",
            "一旦 `ppt_check_route` 返回 `route_workflow_id`，后续高层 route 工具必须沿用同一个 workflow，不要把不同路线串起来。",
            "高层 route 流程里不要再向用户索要 API key；路线凭据默认由 MCP 环境变量复用。",
            "高层 route 工具不支持切换 provider / base_url；如需切网关，停止引导流程并改用低层工具。",
            "列模型时只复述工具返回的原始 model id，不要脑补供应商分类、模型家族或推荐语。",
            "一次只问一个问题，等用户回答后再继续。",
            "如果 `next_tool` 仍然指向上一步，就不要跳到后面的工具。",
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
        "preferred_choice_field": "ocr_ai_model_choice_index",
        "explicit_model_field": "ocr_ai_model",
        "listing_policy": _build_model_listing_policy(
            preferred_tool="ppt_list_route_models"
        ),
        "user_must_choose_or_accept_default_explicitly": True,
        "do_not_keep_default_silently": True,
        "explicit_model_requires_prior_listing": True,
        "credential_reuse_policy": _build_route_credential_reuse_policy(
            effective_config=effective_config
        ),
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
    pdf_path: str | None,
    page_range_decision: Literal["all_pages", "page_range"] | None,
    page_start: int | None,
    page_end: int | None,
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
    missing.extend(
        _missing_conversion_target_decisions(
            pdf_path=pdf_path,
            page_range_decision=page_range_decision,
            page_start=page_start,
            page_end=page_end,
        )
    )
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
    pdf_path: str | None = None,
    page_range_decision: Literal["all_pages", "page_range"] | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
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
        pdf_path=pdf_path,
        page_range_decision=page_range_decision,
        page_start=page_start,
        page_end=page_end,
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
            if "tool" in next_step:
                payload["next_tool"] = next_step["tool"]
    return payload


def _explicit_ai_model_override_fields(
    *,
    ocr_ai_model_choice_index: int | None = None,
    ocr_ai_model: str | None = None,
) -> list[str]:
    fields: list[str] = []
    if ocr_ai_model_choice_index is not None:
        fields.append("ocr_ai_model_choice_index")
    if ocr_ai_model is not None:
        fields.append("ocr_ai_model")
    return fields


def _validate_ai_model_decision(
    *,
    ai_route: bool,
    ocr_ai_model_decision: Literal["route_default", "explicit"] | None,
    ocr_ai_model_choice_index: int | None = None,
    ocr_ai_model: str | None = None,
) -> None:
    if not ai_route or ocr_ai_model_decision != "route_default":
        return
    override_fields = _explicit_ai_model_override_fields(
        ocr_ai_model_choice_index=ocr_ai_model_choice_index,
        ocr_ai_model=ocr_ai_model,
    )
    if not override_fields:
        return
    raise RouteConfigError(
        code="invalid_model_decision",
        message=(
            "ocr_ai_model_decision=route_default cannot be combined with explicit "
            "ocr_ai_model overrides"
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
    pdf_path: str | None = None,
    page_range_decision: Literal["all_pages", "page_range"] | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
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
        pdf_path=pdf_path,
        page_range_decision=page_range_decision,
        page_start=page_start,
        page_end=page_end,
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
    ocr_ai_model: str | None = None,
) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if ocr_ai_model is not None:
        overrides["ocr_ai_model"] = ocr_ai_model
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
                "ppt_set_conversion_target",
                "ppt_list_route_models",
                "ppt_set_route_options",
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
) -> dict[str, Any]:
    """Lock a high-level route workflow after the user explicitly confirms it.

    This is step 1 of the high-level guided flow. It only locks the route and
    returns a `route_workflow_id`. Then use `ppt_set_conversion_target`,
    `ppt_list_route_models` if needed, `ppt_set_route_options`, and finally
    `ppt_convert_pdf`.
    """
    try:
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
                    pdf_path=None,
                    page_range_decision=None,
                    page_start=None,
                    page_end=None,
                    ai_route=_route_uses_ai_ocr(route_definition.route),
                    scanned_page_mode=None,
                    remove_footer_notebooklm=None,
                    ocr_ai_model_decision=None,
                    ocr_ai_model=None,
                ),
            )
        state = _create_route_workflow(resolved_route=resolve_route(route))
        route_default_effective_config = dict(state.effective_config)
        _, effective_config = _preview_route_workflow(state=state)
        decision_status = _build_decision_status(
            route_selected=True,
            route_title=state.title,
            route_confirmed=True,
            pdf_path=state.pdf_path,
            page_range_decision=state.page_range_decision,
            page_start=state.page_start,
            page_end=state.page_end,
            ai_route=state.ai_route,
            scanned_page_mode=state.scanned_page_mode,
            remove_footer_notebooklm=state.remove_footer_notebooklm,
            ocr_ai_model_decision=state.ocr_ai_model_decision,
            ocr_ai_model=state.ocr_ai_model,
        )
        return {
            "ok": True,
            "route": state.route,
            "display_name": state.title,
            "recommended_input": state.title,
            "title": state.title,
            "summary": state.summary,
            "ready": True,
            "route_workflow_id": state.workflow_id,
            "route_workflow": _build_route_workflow_payload(state),
            "current_decisions": _build_current_decisions(state),
            "effective_config": effective_config,
            "route_selection": _build_route_selection_policy(route_title=state.title),
            "ai_model_selection": _build_ai_model_selection(
                options=state.options,
                effective_config=route_default_effective_config,
                ocr_ai_model_decision=state.ocr_ai_model_decision,
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
def ppt_set_conversion_target(
    route_workflow_id: str,
    pdf_path: str | None = None,
    page_range_decision: Literal["all_pages", "page_range"] | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
) -> dict[str, Any]:
    """Write pdf_path and page range decisions into a locked route workflow.

    This is step 2 of the high-level guided flow. Use it to persist conversion
    target state so weaker models do not have to remember page ranges from chat
    history. `pdf_path` must be a real local PDF path explicitly provided by
    the user.
    """
    try:
        normalized_page_range_decision = _normalize_page_range_decision(
            page_range_decision
        )
        state = _get_route_workflow(route_workflow_id)
        _update_route_workflow(
            state=state,
            pdf_path=pdf_path,
            page_range_decision=normalized_page_range_decision,
            page_start=page_start,
            page_end=page_end,
        )
        _, effective_config = _preview_route_workflow(state=state)
        decision_status = _build_decision_status(
            route_selected=True,
            route_title=state.title,
            route_confirmed=True,
            pdf_path=state.pdf_path,
            page_range_decision=state.page_range_decision,
            page_start=state.page_start,
            page_end=state.page_end,
            ai_route=state.ai_route,
            scanned_page_mode=state.scanned_page_mode,
            remove_footer_notebooklm=state.remove_footer_notebooklm,
            ocr_ai_model_decision=state.ocr_ai_model_decision,
            ocr_ai_model=state.ocr_ai_model,
        )
        return {
            "ok": True,
            "route": state.route,
            "display_name": state.title,
            "recommended_input": state.title,
            "route_workflow_id": state.workflow_id,
            "route_workflow": _build_route_workflow_payload(state),
            "current_decisions": _build_current_decisions(state),
            "effective_config": effective_config,
            "route_selection": _build_route_selection_policy(route_title=state.title),
            **decision_status,
        }
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_set_route_options(
    route_workflow_id: str,
    scanned_page_mode: Literal["fullpage", "segmented"] | None = None,
    remove_footer_notebooklm: bool | None = None,
    ocr_ai_model_decision: Literal["route_default", "explicit"] | None = None,
    ocr_ai_model_choice_index: int | None = None,
    ocr_ai_model: str | None = None,
) -> dict[str, Any]:
    """Write scanned-page, footer, and AI model decisions into the workflow.

    This is step 3 of the high-level guided flow. Use `ppt_list_route_models`
    first when the user wants to select a non-default AI OCR model. Do not use
    this tool before `pdf_path` and page range are confirmed.
    """
    try:
        normalized_model_decision = _normalize_ai_model_decision(ocr_ai_model_decision)
        state = _get_route_workflow(route_workflow_id)
        _require_conversion_target_step(
            state=state,
            requested_tool="ppt_set_route_options",
        )
        _update_route_workflow(
            state=state,
            scanned_page_mode=scanned_page_mode,
            remove_footer_notebooklm=remove_footer_notebooklm,
            ocr_ai_model_decision=normalized_model_decision,
            ocr_ai_model_choice_index=ocr_ai_model_choice_index,
            ocr_ai_model=ocr_ai_model,
        )
        options, effective_config = _preview_route_workflow(state=state)
        decision_status = _build_decision_status(
            route_selected=True,
            route_title=state.title,
            route_confirmed=True,
            pdf_path=state.pdf_path,
            page_range_decision=state.page_range_decision,
            page_start=state.page_start,
            page_end=state.page_end,
            ai_route=state.ai_route,
            scanned_page_mode=state.scanned_page_mode,
            remove_footer_notebooklm=state.remove_footer_notebooklm,
            ocr_ai_model_decision=state.ocr_ai_model_decision,
            ocr_ai_model=state.ocr_ai_model,
        )
        return {
            "ok": True,
            "route": state.route,
            "display_name": state.title,
            "recommended_input": state.title,
            "route_workflow_id": state.workflow_id,
            "route_workflow": _build_route_workflow_payload(state),
            "current_decisions": _build_current_decisions(state),
            "route_selection": _build_route_selection_policy(route_title=state.title),
            "effective_config": effective_config,
            **decision_status,
        }
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_convert_pdf(
    route_workflow_id: str,
    retain_process_artifacts: bool = False,
) -> dict[str, Any]:
    """Submit the locked high-level workflow after target and options are ready."""
    try:
        state = _get_route_workflow(route_workflow_id)
        _require_submit_decisions(
            route_selected=True,
            route_title=state.title,
            route_confirmed=True,
            pdf_path=state.pdf_path,
            page_range_decision=state.page_range_decision,
            page_start=state.page_start,
            page_end=state.page_end,
            ai_route=state.ai_route,
            scanned_page_mode=state.scanned_page_mode,
            remove_footer_notebooklm=state.remove_footer_notebooklm,
            ocr_ai_model_decision=state.ocr_ai_model_decision,
            ocr_ai_model=state.ocr_ai_model,
        )
        normalized_pdf_path = _resolve_existing_local_pdf_path(state.pdf_path)
        options, effective_config = _preview_route_workflow(state=state)
        options["retain_process_artifacts"] = retain_process_artifacts
        effective_config["retain_process_artifacts"] = retain_process_artifacts
        payload = client.create_job(pdf_path=str(normalized_pdf_path), options=options)
        return {
            "ok": True,
            "route": state.route,
            "display_name": state.title,
            "recommended_input": state.title,
            "route_workflow_id": state.workflow_id,
            "route_workflow": _build_route_workflow_payload(state),
            "current_decisions": _build_current_decisions(state),
            "route_selection": _build_route_selection_policy(route_title=state.title),
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
        "高层 route 流程先用 `ppt_check_route` 锁定路线，再用 `ppt_set_conversion_target` 写 `pdf_path` 和页码范围，再用 `ppt_set_route_options` 写扫描页处理、页脚和模型选择，最后才用 `ppt_convert_pdf` 提交。",
        "拿到 `route_workflow_id` 后，后续继续沿用这个 workflow；不要把不同路线串到同一条流程里。",
        "如果用户一开始就给了 `pdf_path` 或页码范围，尽早在 `ppt_set_conversion_target` 里写进同一个 `route_workflow_id`，其中页码范围要明确写成 `page_range_decision`，不要只靠聊天记忆记住“第几页到第几页”。",
        "`pdf_path` 只能使用用户明确提供的真实本地 PDF 路径；不要编造示例路径、测试路径或占位路径。",
        "高层 route 流程里，路线凭据默认从 MCP 环境变量复用；不要在用户选了路线或模型后再反问 API key。",
        "高层 route 工具不支持切换 provider / base_url；如需切网关，改用低层专家工具。",
        "用户问可用模型时，必须先调用模型列表工具，并且只复述工具返回的原始 model id；不要脑补供应商分类、模型家族或推荐语。",
        "低端模型优先使用 `ocr_ai_model_choice_index`，不要自己重打一长串 `ocr_ai_model`。",
        "如果 `next_tool` 仍然指向上一步，就不要跳到后面的工具。",
        "建议回复示例：可用路线如下，请您先选一条；在您确认前我不会替您决定。",
    ]
    if route_title:
        lines.append(f"当前路线：{route_title}")
        lines.append(
            "先问用户是否明确确认这条路线；确认后调用 `ppt_check_route`，随后用 `ppt_set_conversion_target` 和 `ppt_set_route_options` 逐步补齐决策。"
        )
    if route_default_model and route_model_capability:
        lines.append(
            "AI 路线默认模型："
            f"{route_default_model}。建议先调用 "
            "`ppt_list_route_models(route_workflow_id=...)` "
            "拉取候选模型，再让用户明确选择；优先记录 `ocr_ai_model_choice_index`。"
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
    `ppt_list_routes` -> `ppt_check_route` -> `ppt_set_conversion_target` ->
    `ppt_list_route_models` (if needed) -> `ppt_set_route_options` ->
    `ppt_convert_pdf`.

    Never use this tool unless the user explicitly asks to bypass the guided
    route workflow and confirms `low_level_override_confirmed=true`. The
    `pdf_path` must be a real existing local PDF path explicitly provided by
    the user.
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
                        "ppt_set_conversion_target",
                        "ppt_list_route_models",
                        "ppt_set_route_options",
                        "ppt_convert_pdf",
                    ],
                    "route_selection": _build_route_selection_policy(),
                    "low_level_escape_hatch": _build_low_level_escape_hatch_policy(),
                },
            )
        normalized_pdf_path = _resolve_existing_local_pdf_path(
            pdf_path,
            next_tool="ppt_create_job",
        )
        payload = client.create_job(pdf_path=str(normalized_pdf_path), options=options)
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
    route_workflow_id: str,
) -> dict[str, Any]:
    """List candidate models for the currently locked high-level route workflow.

    Only use this after `pdf_path` and page range are already confirmed on the
    same route_workflow_id.
    """
    try:
        state = _get_route_workflow(route_workflow_id)
        _require_conversion_target_step(
            state=state,
            requested_tool="ppt_list_route_models",
        )
        if not state.ai_route:
            raise RouteConfigError(
                code="invalid_route_model_listing",
                message="Route model listing is only supported on AI OCR routes",
                details={
                    "route": state.title,
                    "supported_routes": [
                        "本地切块识别",
                        "模型直出框和文字",
                        "内置文档解析",
                    ],
                },
            )
        provider = str(state.options.get("ocr_ai_provider") or "")
        base_url = state.options.get("ocr_ai_base_url")
        api_key = str(state.options.get("ocr_ai_api_key") or "").strip()
        if not api_key:
            raise RouteConfigError(
                code="missing_env",
                message="Resolved AI OCR route does not have an API key configured",
                details={"route": state.title},
            )
        resolved_capability = _default_route_model_capability(options=state.options)
        payload = client.list_ai_models(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            capability=resolved_capability,
        )
        models = payload.get("models") if isinstance(payload.get("models"), list) else []
        route_default_model = str(state.effective_config.get("ocr_ai_model") or "").strip() or None
        model_choices = _build_route_model_choices(
            route_default_model=route_default_model,
            fetched_models=models,
        )
        state.last_listed_models = list(models)
        state.last_model_choices = list(model_choices)
        state.last_model_listing_capability = resolved_capability
        state.updated_at = time.time()
        choice_display_lines = _build_choice_display_lines(model_choices)
        return {
            "ok": True,
            "route": state.route,
            "display_name": state.title,
            "recommended_input": state.title,
            "route_workflow_id": state.workflow_id,
            "route_workflow": _build_route_workflow_payload(state),
            "current_decisions": _build_current_decisions(state),
            "route_selection": _build_route_selection_policy(route_title=state.title),
            "capability": resolved_capability,
            "route_default": {
                "ocr_ai_provider": state.effective_config.get("ocr_ai_provider"),
                "ocr_ai_base_url": state.effective_config.get("ocr_ai_base_url"),
                "ocr_ai_model": route_default_model,
            },
            "route_default_in_provider_list": bool(
                route_default_model and route_default_model in set(models)
            ),
            "model_source_explanation": {
                "route_default": "Resolved from the current route configuration and environment variables.",
                "models": "Fetched live from the provider model-list API using the route's provider/base URL/API key and the route capability filter.",
            },
            "models": models,
            "model_count": len(models),
            "model_choices": model_choices,
            "choice_display_lines": choice_display_lines,
            "choice_count": len(model_choices),
            "listing_policy": _build_model_listing_policy(
                preferred_tool="ppt_list_route_models"
            ),
            "credential_reuse_policy": _build_route_credential_reuse_policy(
                effective_config=state.effective_config
            ),
            "selection_instructions": {
                "route_workflow_field": "route_workflow_id",
                "route_confirmation_field": "route_confirmed",
                "decision_field": "ocr_ai_model_decision",
                "decision_options": ["route_default", "explicit"],
                "user_must_choose_or_accept_default_explicitly": True,
                "do_not_keep_default_silently": True,
                "preferred_choice_field": "ocr_ai_model_choice_index",
                "explicit_model_field": "ocr_ai_model",
                "choice_source_field": "model_choices",
                "choice_display_field": "choice_display_lines",
                "explicit_model_requires_prior_listing": True,
                "if_user_selects_route_default_model": {
                    "submit_decision": "route_default",
                    "do_not_send_override_fields": [
                        "ocr_ai_model",
                        "ocr_ai_model_choice_index",
                        "ocr_ai_provider",
                        "ocr_ai_base_url",
                    ],
                },
                "if_user_selects_different_model_on_same_gateway": {
                    "submit_decision": "explicit",
                    "required_fields": ["ocr_ai_model_choice_index"],
                    "optional_fields": ["ocr_ai_model"],
                    "reuse_route_credentials": True,
                    "do_not_ask_for_api_key_again": True,
                },
                "gateway_switch_supported_in_high_level_flow": False,
                "gateway_switch_requires_expert_tools": [
                    "ppt_list_ai_models",
                    "ppt_check_ai_ocr",
                    "ppt_create_job",
                ],
                "submit_tool": "ppt_set_route_options",
            },
        }
    except Exception as exc:
        return _tool_error_payload(exc)


@mcp.tool()
def ppt_list_ai_models(
    provider: str,
    api_key: str,
    base_url: str | None = None,
    capability: str = "ocr",
) -> dict[str, Any]:
    """Low-level raw model discovery. Prefer `ppt_list_route_models` in normal OCR flows.

    Only repeat the exact returned model IDs to the user. Do not invent
    provider categories, unofficial aliases, or recommendations.
    """
    try:
        payload = client.list_ai_models(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            capability=capability,
        )
        model_ids = payload.get("models") if isinstance(payload.get("models"), list) else []
        return {
            "ok": True,
            "provider": provider,
            "base_url": base_url,
            "capability": capability,
            "model_ids": model_ids,
            "model_count": len(model_ids),
            "models": payload,
            "listing_policy": _build_model_listing_policy(
                preferred_tool="ppt_list_ai_models"
            ),
        }
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
