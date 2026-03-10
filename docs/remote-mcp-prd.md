# Product Requirements Document: Remote PPT MCP

**Version**: 1.0  
**Date**: 2026-03-10  
**Author**: Sarah / Codex  
**Quality Score**: 92/100

---

## Executive Summary

`ppt-mcp` already proves that the existing `ppt` API can be wrapped as MCP, but
the current shape is still a local `stdio` shim. Users who already run
`ppt.zichuanlan.top` should not need a local HTTP bridge just to expose the same
conversion capability to Claude Desktop, Cursor, or other MCP clients.

The next step is a real remote MCP server deployed alongside the existing `ppt`
service. This remote server should not expose raw form fields like
`parse_provider`, `ocr_provider`, or individual vendor secrets. Instead, it
should productize three server-side concepts:

- `profile_id`: a stored credential/config profile such as MinerU, Baidu Doc,
  or AIOCR credentials
- `pipeline_id`: a named conversion route such as
  `local.aiocr.layout_block` or `mineru.default`
- `source`: the input document reference, created either from an upload or a
  fetchable URL

This keeps the remote MCP experience close to other hosted MCP products while
reusing the existing job system, OCR routes, and artifact handling already
implemented in the `ppt` backend.

---

## Problem Statement

**Current Situation**

- The current `ppt-mcp` wrapper is local-first and assumes the MCP process can
  read a local `pdf_path`.
- That works for `stdio`, but it breaks down for a true remote MCP server
  because a server cannot read file paths on a user's laptop.
- The existing web app stores OCR and parser credentials in browser
  `localStorage`, not as reusable server-side profiles.
- The existing `ppt` backend already has a strong job model, but its public API
  is still optimized for the web form flow, not for a remote MCP product.

**Proposed Solution**

Build a remote MCP service that sits in front of the existing `ppt` API and
translates MCP-native concepts into the current job API:

- server-side `profiles`
- server-side `pipelines`
- explicit document `sources`
- long-running job polling and result retrieval

**Business Impact**

- Makes `ppt.zichuanlan.top` usable as a proper hosted MCP product
- Removes the need for local Docker or local MCP shims for most users
- Keeps the parsing/OCR core unchanged, reducing implementation risk

---

## Success Metrics

**Primary KPIs**

- Remote MCP job creation success rate: `>= 95%` for valid inputs
- Time to first successful conversion for a new user: `<= 10 minutes`
- Percentage of successful runs using named `pipeline_id` without raw form
  fields: `>= 90%`

**Validation**

- Measure by remote MCP server logs and job completion telemetry
- Track upload success, create-job success, and final download success
- Review top failure classes weekly during beta

---

## User Personas

### Primary: Hosted MCP User

- **Role**: knowledge worker or developer using Claude Desktop / Cursor
- **Goals**: convert a local or reachable PDF into a PPT without learning the
  underlying parser matrix
- **Pain Points**: local-only tools, secret sprawl, pipeline confusion
- **Technical Level**: intermediate

### Secondary: Operator / Admin

- **Role**: maintains `ppt.zichuanlan.top`
- **Goals**: define safe profiles and pipelines once, then let users reuse them
- **Pain Points**: per-user browser settings, credential leakage, support cost
- **Technical Level**: advanced

---

## User Stories & Acceptance Criteria

### Story 1: Use a Hosted Pipeline

**As a** hosted MCP user  
**I want to** select a named pipeline and profile  
**So that** I do not have to know raw `ppt` form fields

**Acceptance Criteria**

- [ ] The user can list available `pipeline_id` values from MCP
- [ ] The user can list available `profile_id` values from MCP
- [ ] A job can be created using only `source`, `pipeline_id`, and `profile_id`

### Story 2: Convert a Local PDF Through a Remote Server

**As a** hosted MCP user  
**I want to** upload a local PDF to the remote service  
**So that** the remote MCP server can run conversion without local filesystem
access

**Acceptance Criteria**

- [ ] The remote flow supports an upload-backed `source`
- [ ] Upload state is explicit and can be completed or expired safely
- [ ] The server never requires a remote process to read a client-side path

### Story 3: Track and Retrieve Results

**As a** hosted MCP user  
**I want to** poll job state and download results  
**So that** long-running conversions remain usable in MCP clients

**Acceptance Criteria**

- [ ] MCP exposes job status, progress, and error details
- [ ] Completed jobs expose result download and artifact metadata
- [ ] Failed jobs return structured errors rather than opaque transport failures

### Story 4: Operate Safely on the Public Internet

**As an** operator  
**I want to** protect the remote MCP service  
**So that** secrets and compute capacity are not exposed

**Acceptance Criteria**

- [ ] Remote transport uses HTTPS and Streamable HTTP
- [ ] The service validates `Origin` and supports authenticated access
- [ ] Tokens or API keys are not forwarded to the existing `ppt` API unchanged

---

## Functional Requirements

### 1. Remote MCP Transport

- Use MCP Streamable HTTP as the remote transport
- Expose a single MCP endpoint such as `https://ppt.zichuanlan.top/mcp`
- Support MCP session IDs for stateful sessions when the client provides them

### 2. Named Pipelines

The remote MCP layer must expose a stable catalog of named pipelines instead of
raw parser fields.

Minimum MVP pipelines:

- `mineru.default`
- `baidu_doc.paddle_vl`
- `local.aiocr.layout_block`
- `local.aiocr.direct`
- `local.aiocr.doc_parser`

Each pipeline maps to existing `ppt` form fields.

Examples:

- `mineru.default`
  - `parse_provider=mineru`
- `baidu_doc.paddle_vl`
  - `parse_provider=baidu_doc`
  - `baidu_doc_parse_type=paddle_vl`
- `local.aiocr.layout_block`
  - `parse_provider=local`
  - `ocr_provider=aiocr`
  - `ocr_ai_chain_mode=layout_block`

### 3. Server-Side Profiles

The remote MCP layer must store reusable credential bundles and route settings
as `profile_id`.

Examples:

- `mineru.prod`
- `baidu.prod`
- `siliconflow.qwen-vl`
- `openai.gpt-4.1-mini`

Profiles should carry the secrets and defaults required to call the underlying
provider, not the client.

### 4. Source Ingestion

The remote MCP layer must support at least two source modes:

- `url_source`
  - the server fetches a reachable PDF URL
- `upload_source`
  - the client uploads the file to server-managed staging, then references the
    resulting `source_id`

Future mode:

- `embedded_resource_source`
  - when MCP clients reliably pass binary resource content

### 5. Long-Running Job Operations

Required tools:

- `ppt_list_profiles`
- `ppt_list_pipelines`
- `ppt_create_upload`
- `ppt_finalize_upload`
- `ppt_create_job`
- `ppt_get_job_status`
- `ppt_cancel_job`
- `ppt_get_job_artifacts`
- `ppt_download_result`

### 6. Output Handling

- Result downloads should be delivered by server-generated URLs or a download
  tool
- Artifact manifests should expose retained files without leaking internal
  storage paths

### 7. Error Handling

The remote MCP layer must normalize the underlying `ppt` API errors into stable
MCP-facing error categories:

- `invalid_source`
- `invalid_profile`
- `invalid_pipeline`
- `upload_incomplete`
- `job_failed`
- `provider_error`
- `auth_required`
- `rate_limited`

---

## Out of Scope

- Rewriting the existing `ppt` parser or OCR engine
- Making every raw web setting editable from remote MCP on day one
- Building full per-user billing in MVP
- Replacing the existing web app

---

## Technical Constraints

### Existing Backend Reuse

The solution should continue to reuse the existing `ppt` job API:

- job creation
- job polling
- cancel
- artifacts
- result download

This keeps OCR and parser logic in the original backend.

### Upload Reality

Remote MCP cannot depend on a client-side filesystem path string. For local
documents, the bytes must be uploaded or otherwise transferred to the server.

### Security

- The current `ppt` app does not provide built-in end-user API auth
- The remote MCP layer therefore needs its own protection and should not expose
  the raw `ppt` API directly
- MVP can use a reverse proxy or gateway token, but target-state should follow
  MCP HTTP authorization requirements

### Performance

- Upload staging should support files at least as large as the current `ppt`
  file-size limit
- Job polling should remain cheap and idempotent
- Result delivery should avoid storing duplicate binaries when possible

---

## Recommended Architecture

### Components

- `remote-mcp`
  - Streamable HTTP MCP server
  - profile and pipeline catalog
  - upload/session handling
- `ppt-api`
  - existing FastAPI job interface
- `ppt-worker`
  - existing RQ worker
- `source-staging`
  - temporary storage for uploaded PDFs
- `profile-store`
  - credential and pipeline defaults store

### Request Flow

1. Client connects to remote MCP over Streamable HTTP
2. Client lists pipelines and profiles
3. Client creates a source by URL or upload
4. Client calls `ppt_create_job(source_id, pipeline_id, profile_id, options)`
5. Remote MCP translates that into the current `ppt` `POST /api/v1/jobs`
6. Existing `ppt` API and worker process the conversion
7. Remote MCP returns job status and result access

---

## Delivery Phases

### Phase 1: Beta Remote MCP

- Streamable HTTP endpoint
- static pipeline catalog
- static profile catalog
- upload-backed source creation
- remote create/status/cancel/download
- gateway token or private-network access

### Phase 2: Hosted Productization

- operator UI or config files for profiles/pipelines
- full MCP-compliant OAuth-based authorization
- artifact retention policies
- per-user quotas and audit logs

### Phase 3: Smarter Client Integration

- attachment-aware source ingestion
- resumable uploads
- richer resources and prompts for diagnostics

---

## Risks

- Some MCP clients still have uneven support for remote auth flows
- Upload UX varies between clients and may require a fallback path
- Exposing too many raw parser flags would recreate current UI complexity
- Secret management becomes a server responsibility

---

## References

- Current `ppt` job creation and long-running job model:
  `/home/lan/workspace/ppt/api/app/routers/jobs.py`
- Current parser/OCR validation rules:
  `/home/lan/workspace/ppt/api/app/job_options.py`
- MCP Streamable HTTP transport:
  https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- MCP resources and binary blobs:
  https://modelcontextprotocol.io/specification/2025-06-18/server/resources
- MCP authorization guidance:
  https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization
