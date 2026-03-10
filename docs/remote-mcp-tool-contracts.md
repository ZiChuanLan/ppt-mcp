# Remote MCP Tool Contracts

This document defines the first concrete tool and data contracts for a hosted
`ppt` MCP service.

The design goal is to hide raw `ppt` form fields behind:

- `profile_id`
- `pipeline_id`
- `source`

## Core IDs

- `profile_id`
  Identifies a server-side credential/config bundle
- `pipeline_id`
  Identifies a named conversion route
- `source_id`
  Identifies an uploaded or fetched PDF source
- `job_id`
  Identifies the underlying conversion job

## Pipeline Catalog

### `mineru.default`

Maps to:

```json
{
  "parse_provider": "mineru"
}
```

Required profile kind:

- `mineru`

### `baidu_doc.paddle_vl`

Maps to:

```json
{
  "parse_provider": "baidu_doc",
  "baidu_doc_parse_type": "paddle_vl"
}
```

Required profile kind:

- `baidu_doc`

### `local.aiocr.layout_block`

Maps to:

```json
{
  "parse_provider": "local",
  "ocr_provider": "aiocr",
  "ocr_ai_chain_mode": "layout_block"
}
```

Required profile kind:

- `aiocr`

### `local.aiocr.direct`

Maps to:

```json
{
  "parse_provider": "local",
  "ocr_provider": "aiocr",
  "ocr_ai_chain_mode": "direct"
}
```

Required profile kind:

- `aiocr`

### `local.aiocr.doc_parser`

Maps to:

```json
{
  "parse_provider": "local",
  "ocr_provider": "aiocr",
  "ocr_ai_chain_mode": "doc_parser"
}
```

Required profile kind:

- `aiocr`

Constraint:

- the bound profile must resolve to a `PaddleOCR-VL` model

## Profile Shape

Profiles are server-side objects and are never returned with raw secrets.

Example list item:

```json
{
  "profile_id": "siliconflow.qwen-vl-prod",
  "kind": "aiocr",
  "title": "SiliconFlow Qwen VL",
  "summary": "Qwen VL profile for layout_block OCR",
  "default_pipeline_ids": [
    "local.aiocr.layout_block"
  ],
  "capabilities": [
    "vision",
    "ocr"
  ]
}
```

Profile internals may store:

- provider vendor
- base URL
- API key or token reference
- default model
- prompt preset
- rate limits

## Source Modes

### 1. URL Source

The server fetches a reachable PDF.

```json
{
  "type": "url",
  "url": "https://example.com/file.pdf"
}
```

### 2. Uploaded Source

The client first uploads a PDF to staging and then uses the resulting
`source_id`.

```json
{
  "type": "upload",
  "source_id": "src_01JREMOTE..."
}
```

### 3. Embedded Resource Source

Future path for MCP clients that can pass binary resource contents reliably.

```json
{
  "type": "embedded_resource",
  "resource": {
    "type": "resource",
    "resource": {
      "uri": "file:///Users/me/input.pdf",
      "mimeType": "application/pdf",
      "blob": "base64-encoded-pdf"
    }
  }
}
```

Notes:

- MCP schema supports binary `blob` resource contents and embedded resources
- client support is uneven today, so this should not be the only upload path

## Tool Definitions

### `ppt_list_profiles`

Input:

```json
{}
```

Output:

```json
{
  "profiles": [
    {
      "profile_id": "siliconflow.qwen-vl-prod",
      "kind": "aiocr",
      "title": "SiliconFlow Qwen VL",
      "summary": "Qwen VL profile for layout_block OCR",
      "default_pipeline_ids": [
        "local.aiocr.layout_block"
      ],
      "capabilities": [
        "vision",
        "ocr"
      ]
    }
  ]
}
```

### `ppt_list_pipelines`

Input:

```json
{}
```

Output:

```json
{
  "pipelines": [
    {
      "pipeline_id": "local.aiocr.layout_block",
      "title": "Local Layout Block OCR",
      "summary": "Local layout detection plus AI OCR per block",
      "required_profile_kind": "aiocr",
      "supports_page_range": true
    }
  ]
}
```

### `ppt_create_upload`

Purpose:

- create a temporary upload slot for a PDF source

Input:

```json
{
  "filename": "lesson.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 4829102,
  "sha256": "optional-hex"
}
```

Output:

```json
{
  "source_id": "src_01JREMOTE...",
  "upload": {
    "upload_mode": "single_put",
    "upload_url": "https://ppt.zichuanlan.top/uploads/src_01JREMOTE...",
    "required_headers": {
      "Content-Type": "application/pdf"
    },
    "expires_at": "2026-03-10T12:45:00Z"
  }
}
```

Alternative future output:

```json
{
  "source_id": "src_01JREMOTE...",
  "upload": {
    "upload_mode": "multipart",
    "part_size_bytes": 5242880,
    "parts": [
      {
        "part_number": 1,
        "upload_url": "https://..."
      }
    ]
  }
}
```

### `ppt_finalize_upload`

Purpose:

- mark the staged file as ready for conversion

Input:

```json
{
  "source_id": "src_01JREMOTE..."
}
```

Output:

```json
{
  "source_id": "src_01JREMOTE...",
  "status": "ready",
  "mime_type": "application/pdf",
  "size_bytes": 4829102
}
```

### `ppt_create_job`

Input:

```json
{
  "source": {
    "type": "upload",
    "source_id": "src_01JREMOTE..."
  },
  "pipeline_id": "local.aiocr.layout_block",
  "profile_id": "siliconflow.qwen-vl-prod",
  "options": {
    "page_start": 1,
    "page_end": 20,
    "retain_process_artifacts": false
  }
}
```

Alternative URL source:

```json
{
  "source": {
    "type": "url",
    "url": "https://example.com/file.pdf"
  },
  "pipeline_id": "mineru.default",
  "profile_id": "mineru.prod",
  "options": {}
}
```

Output:

```json
{
  "job_id": "5bb4c6f3-....",
  "status": "pending",
  "pipeline_id": "local.aiocr.layout_block",
  "profile_id": "siliconflow.qwen-vl-prod",
  "source_id": "src_01JREMOTE..."
}
```

### `ppt_get_job_status`

Input:

```json
{
  "job_id": "5bb4c6f3-...."
}
```

Output:

```json
{
  "job_id": "5bb4c6f3-....",
  "status": "processing",
  "stage": "ocr_running",
  "progress": 42,
  "message": "Running AI OCR",
  "error": null
}
```

### `ppt_cancel_job`

Input:

```json
{
  "job_id": "5bb4c6f3-...."
}
```

Output:

```json
{
  "job_id": "5bb4c6f3-....",
  "status": "cancelled"
}
```

### `ppt_get_job_artifacts`

Input:

```json
{
  "job_id": "5bb4c6f3-...."
}
```

Output:

```json
{
  "job_id": "5bb4c6f3-....",
  "status": "completed",
  "artifacts": {
    "source_pdf_url": "https://ppt.zichuanlan.top/jobs/5bb4c6f3/artifacts/input.pdf",
    "final_preview_images": [
      {
        "page_index": 0,
        "url": "https://ppt.zichuanlan.top/jobs/5bb4c6f3/artifacts/final/page-0000.png"
      }
    ]
  }
}
```

### `ppt_download_result`

Input:

```json
{
  "job_id": "5bb4c6f3-...."
}
```

Output:

```json
{
  "job_id": "5bb4c6f3-....",
  "filename": "converted.pptx",
  "download_url": "https://ppt.zichuanlan.top/jobs/5bb4c6f3/download"
}
```

## Mapping Rules to Existing `ppt` API

The remote layer should translate named pipelines and profiles to the current
`POST /api/v1/jobs` form fields.

Examples:

- `pipeline_id=mineru.default`
  - `parse_provider=mineru`
  - inject `mineru_api_token` and other MinerU defaults from the profile

- `pipeline_id=local.aiocr.layout_block`
  - `parse_provider=local`
  - `ocr_provider=aiocr`
  - `ocr_ai_chain_mode=layout_block`
  - inject `ocr_ai_provider`, `ocr_ai_base_url`, `ocr_ai_api_key`,
    `ocr_ai_model`, and prompt defaults from the profile

- `pipeline_id=baidu_doc.paddle_vl`
  - `parse_provider=baidu_doc`
  - `baidu_doc_parse_type=paddle_vl`
  - inject Baidu credentials from the profile

## Error Model

Every tool should return a stable error shape:

```json
{
  "ok": false,
  "error": {
    "code": "invalid_pipeline",
    "message": "Pipeline local.aiocr.doc_parser requires a PaddleOCR-VL profile",
    "details": {
      "pipeline_id": "local.aiocr.doc_parser",
      "profile_id": "siliconflow.qwen-vl-prod"
    }
  }
}
```

Common remote error codes:

- `auth_required`
- `forbidden`
- `invalid_source`
- `source_not_ready`
- `invalid_profile`
- `invalid_pipeline`
- `profile_pipeline_mismatch`
- `rate_limited`
- `provider_error`
- `job_not_found`

## Notes on MCP Compatibility

- Remote MCP should use Streamable HTTP
- Remote HTTP auth should target MCP's authorization model when possible
- Binary resource blobs are supported by the MCP schema, but hosted clients may
  not expose file attachments uniformly yet

References:

- https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- https://modelcontextprotocol.io/specification/2025-06-18/server/resources
- https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization
