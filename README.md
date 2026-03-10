# ppt-mcp

Local-first MCP server for the existing `ppt` PDF-to-PPT pipeline.

It does not reimplement parsing, OCR, or PPT generation. It wraps the running
`ppt` API on the same machine and exposes it as MCP tools.

## How local MCP uses the parsing pipeline

The parsing stack stays in the original repo:

1. Start the existing `ppt` service locally.
2. Run this MCP server locally over `stdio`.
3. Call `ppt_create_job` with a local `pdf_path`.
4. This server reads that file and uploads it to the local `ppt` API.
5. The existing `ppt` API + worker do all parsing/OCR/conversion work.
6. Poll with `ppt_get_job_status`, then fetch output with
   `ppt_download_result`.

In other words, local MCP is only an orchestration layer. The actual parsing
chain still lives in the original project.

## Prerequisites

- Original repo available locally, for example at `/home/lan/workspace/ppt`
- The `ppt` backend stack running locally
- Python 3.11+

Start the original service first:

```bash
cd /home/lan/workspace/ppt
docker compose up -d --build api worker redis
```

By default this MCP server talks to `http://127.0.0.1:8000`.

You can also point it at a remote server:

- `PPT_API_BASE_URL=https://ppt.example.com`
- or behind a sub-path proxy:
  `PPT_API_BASE_URL=https://gateway.example.com/ppt`
- optional gateway auth:
  `PPT_API_BEARER_TOKEN=...`

`PPT_API_BASE_URL` should point to the service root or mounted prefix, not to
`/api/v1`.

## Install

```bash
cd /home/lan/workspace/ppt-mcp
uv sync
```

## Run

```bash
cd /home/lan/workspace/ppt-mcp
uv run ppt-mcp
```

Environment variables:

- `PPT_API_BASE_URL`
  Default: `http://127.0.0.1:8000`
- `PPT_API_TIMEOUT_SECONDS`
  Default: `120`
- `PPT_API_BEARER_TOKEN`
  Optional bearer token for a reverse proxy or API gateway

Hosted remote server variables:

- `PPT_MCP_BIND_HOST`
  Default: `0.0.0.0`
- `PPT_MCP_BIND_PORT`
  Default: `8080`
- `PPT_MCP_PUBLIC_BASE_URL`
  Example: `https://ppt.zichuanlan.top/mcp-gateway`
- `PPT_MCP_SERVER_TOKEN`
  Optional bearer token required for MCP and download routes
- `PPT_MCP_PROFILE_STORE`
  Path to the server-side profile catalog JSON
- `PPT_MCP_DATA_DIR`
  Path for staged uploads and source metadata

## Main stdio workflow

The default path for personal use is now:

- run `ppt-mcp` locally over `stdio`
- point it at your existing `ppt` server with `PPT_API_BASE_URL`
- keep parser and OCR secrets in your local MCP `env`
- tell the AI which `route` to use, for example `mineru` or `layout_block`

High-level routes:

- `local_basic`
- `mineru`
- `baidu_doc`
- `layout_block`
- `direct`
- `doc_parser`

This is the intended day-to-day interface. The old low-level form-field tool is
still available as an escape hatch, but it is no longer the primary UX.

## Tools

- `ppt_list_routes`
- `ppt_check_route`
- `ppt_convert_pdf`
- `ppt_health_check`
- `ppt_create_job`
- `ppt_list_jobs`
- `ppt_get_job_status`
- `ppt_cancel_job`
- `ppt_get_job_artifacts`
- `ppt_download_result`
- `ppt_download_artifact`
- `ppt_list_ai_models`
- `ppt_check_ai_ocr`

## Common usage

Create a conversion job:

- `pdf_path` is a local filesystem path on the same machine as the MCP server
- `options` is forwarded to the existing `/api/v1/jobs` form fields

Normal use should prefer `ppt_convert_pdf(pdf_path, route, ...)` instead of the
low-level `ppt_create_job`.

## Example MCP config

```json
{
  "mcpServers": {
    "ppt": {
      "command": "uv",
      "args": [
        "--directory",
        "/home/lan/workspace/ppt-mcp",
        "run",
        "ppt-mcp"
      ],
      "env": {
        "PPT_API_BASE_URL": "https://ppt.zichuanlan.top",
        "PPT_API_BEARER_TOKEN": "optional-gateway-token",
        "MINERU_API_TOKEN": "your-mineru-token",
        "BAIDU_API_KEY": "your-baidu-api-key",
        "BAIDU_SECRET_KEY": "your-baidu-secret-key",
        "SILICONFLOW_API_KEY": "your-siliconflow-key"
      }
    }
  }
}
```

Then you can ask the AI things like:

- `Use mineru to parse this PDF`
- `Run this PDF with layout_block`
- `Try doc_parser on this file`

The MCP tool will translate that route into the right lower-level job fields.

## Route env defaults

`layout_block`

- key: `PPT_LAYOUT_BLOCK_API_KEY` or `SILICONFLOW_API_KEY`
- provider default: `siliconflow`
- base URL default: `https://api.siliconflow.cn/v1`
- model default: `Qwen/Qwen2.5-VL-72B-Instruct`

`direct`

- key: `PPT_DIRECT_API_KEY` or `SILICONFLOW_API_KEY`
- provider default: `deepseek`
- base URL default: `https://api.siliconflow.cn/v1`
- model default: `deepseek-ai/DeepSeek-OCR`

`doc_parser`

- key: `PPT_DOC_PARSER_API_KEY` or `SILICONFLOW_API_KEY`
- provider default: `openai`
- base URL default: `https://api.siliconflow.cn/v1`
- model default: `PaddlePaddle/PaddleOCR-VL-1.5`

`mineru`

- key: `MINERU_API_TOKEN`

`baidu_doc`

- key: `BAIDU_API_KEY`
- secret: `BAIDU_SECRET_KEY`

Common `options` examples:

```json
{
  "parse_provider": "local",
  "ocr_provider": "aiocr",
  "ocr_ai_provider": "openai",
  "ocr_ai_base_url": "https://api.openai.com/v1",
  "ocr_ai_api_key": "sk-...",
  "ocr_ai_model": "gpt-4.1-mini",
  "ocr_ai_chain_mode": "layout_block",
  "retain_process_artifacts": true
}
```

List AI OCR models:

```json
{
  "provider": "openai",
  "api_key": "sk-...",
  "base_url": "https://api.openai.com/v1",
  "capability": "vision"
}
```

Check an AI OCR model:

```json
{
  "provider": "openai",
  "api_key": "sk-...",
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-4.1-mini",
  "ocr_ai_chain_mode": "layout_block"
}
```

## Claude Desktop example

```json
{
  "mcpServers": {
    "ppt": {
      "command": "uv",
      "args": [
        "--directory",
        "/home/lan/workspace/ppt-mcp",
        "run",
        "ppt-mcp"
      ],
      "env": {
        "PPT_API_BASE_URL": "http://127.0.0.1:8000"
      }
    }
  }
}
```

## Why local first

This project is a better fit for local `stdio` MCP before remote MCP because:

- input files are local PDFs
- conversions are long-running background jobs
- output is a generated PPTX plus optional artifacts
- the existing project already has a local API/worker architecture

Remote MCP can be added later, but it adds auth, upload, artifact access, and
multi-tenant concerns that are unnecessary for the first version.

## Remote server mode

You can keep the MCP server on your local machine and point it at a remote
`ppt` deployment. In that setup:

- `ppt_create_job` still accepts your local `pdf_path`
- this MCP server reads the local file and uploads it over HTTP to the remote
  `ppt` API
- parsing and conversion happen on the remote server
- `ppt_download_result` downloads the generated PPTX back to your local machine

Security note:

- the current `ppt` app does not expose built-in end-user API auth
- if you put it on a server, prefer HTTPS plus a reverse proxy, VPN, or SSH
  tunnel

## Next design docs

- `docs/remote-mcp-prd.md`
- `docs/remote-mcp-tool-contracts.md`

These remote hosted docs and the `ppt-mcp-remote` code are intentionally kept
for a future productized remote MCP service. For now they are secondary to the
simpler stdio route-driven flow above.

## Hosted remote server

Run the hosted remote MCP server:

```bash
cd /home/lan/workspace/ppt-mcp
export PPT_API_BASE_URL=http://127.0.0.1:8000
export PPT_MCP_PUBLIC_BASE_URL=https://ppt.zichuanlan.top
export PPT_MCP_SERVER_TOKEN=change-me
uv run ppt-mcp-remote
```

This starts:

- MCP endpoint: `POST/GET https://.../mcp`
- health endpoint: `GET https://.../healthz`
- upload endpoint: `PUT https://.../uploads/{source_id}?token=...`
- result proxy: `GET https://.../jobs/{job_id}/download`
