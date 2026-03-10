"""Named remote MCP pipeline definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PipelineDefinition:
    """Stable remote pipeline definition."""

    pipeline_id: str
    title: str
    summary: str
    required_profile_kind: str
    job_fields: dict[str, Any]
    notes: tuple[str, ...] = ()


REMOTE_PIPELINES: tuple[PipelineDefinition, ...] = (
    PipelineDefinition(
        pipeline_id="local.basic",
        title="Local Basic Parse",
        summary="Local PDF parsing without a dedicated remote OCR profile.",
        required_profile_kind="local",
        job_fields={
            "parse_provider": "local",
        },
        notes=(
            "Use enable_ocr=false for text-based PDFs to avoid OCR dependencies.",
        ),
    ),
    PipelineDefinition(
        pipeline_id="mineru.default",
        title="MinerU Default",
        summary="Cloud MinerU parsing for structured PDFs.",
        required_profile_kind="mineru",
        job_fields={
            "parse_provider": "mineru",
        },
    ),
    PipelineDefinition(
        pipeline_id="baidu_doc.paddle_vl",
        title="Baidu Doc PaddleOCR-VL",
        summary="Baidu document parsing with the PaddleOCR-VL variant.",
        required_profile_kind="baidu_doc",
        job_fields={
            "parse_provider": "baidu_doc",
            "baidu_doc_parse_type": "paddle_vl",
        },
    ),
    PipelineDefinition(
        pipeline_id="local.aiocr.layout_block",
        title="Local AI OCR Layout Block",
        summary="Local layout detection followed by per-block AI OCR.",
        required_profile_kind="aiocr",
        job_fields={
            "parse_provider": "local",
            "ocr_provider": "aiocr",
            "ocr_ai_chain_mode": "layout_block",
        },
    ),
    PipelineDefinition(
        pipeline_id="local.aiocr.direct",
        title="Local AI OCR Direct",
        summary="Whole-page prompt-driven AI OCR with box and text output.",
        required_profile_kind="aiocr",
        job_fields={
            "parse_provider": "local",
            "ocr_provider": "aiocr",
            "ocr_ai_chain_mode": "direct",
        },
        notes=(
            "Do not bind PaddleOCR-VL profiles to the direct chain.",
        ),
    ),
    PipelineDefinition(
        pipeline_id="local.aiocr.doc_parser",
        title="Local AI OCR Doc Parser",
        summary="PaddleOCR-VL document parsing through the dedicated doc_parser chain.",
        required_profile_kind="aiocr",
        job_fields={
            "parse_provider": "local",
            "ocr_provider": "aiocr",
            "ocr_ai_chain_mode": "doc_parser",
        },
        notes=(
            "Requires a PaddleOCR-VL model in the selected profile.",
        ),
    ),
)

REMOTE_PIPELINES_BY_ID = {item.pipeline_id: item for item in REMOTE_PIPELINES}


def get_remote_pipeline(pipeline_id: str) -> PipelineDefinition | None:
    """Look up a named remote pipeline."""
    return REMOTE_PIPELINES_BY_ID.get(pipeline_id)
