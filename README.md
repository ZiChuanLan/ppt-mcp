# ppt-mcp

`ppt-mcp` 是现有 `ppt` PDF 转 PPT 服务的 MCP 封装层。

它本身不重新实现 PDF 解析、OCR 或 PPT 生成，而是调用你已经在跑的
`ppt` API，把它包装成 MCP tools，方便给 Claude Desktop、Cursor、Codex
CLI 等客户端直接使用。

## 它到底解决什么问题

如果你已经有：

- `ppt` 主仓库：`/home/lan/workspace/ppt`
- 正在运行的 `api / worker / redis`

那 `ppt-mcp` 解决的是：

- 把“上传 PDF -> 创建任务 -> 轮询状态 -> 下载 PPT”这套流程封成 MCP tools
- 让 AI 可以直接调用你的本地或远程 `ppt` 服务
- 不必手动打开 Web 页面点来点去

一句话理解：

```text
MCP Client -> ppt-mcp -> ppt API -> worker
```

## 推荐使用方式

### 1. 本地 stdio MCP，最简单也最稳

这是日常最推荐的方式。

- `ppt` 服务跑在本机
- `ppt-mcp` 也跑在本机
- MCP transport 用 `stdio`
- `PPT_API_BASE_URL` 指向 `http://127.0.0.1:8000`

这时：

- 浏览器用户走 Web 页面
- MCP 用户走本地 API
- 两条链路互不干扰

### 2. 本地 stdio MCP，连接远程 `ppt`

适合：

- 你的 AI 客户端在本机
- 但 PDF 转换服务在远程服务器

这时：

- `PPT_API_BASE_URL` 指向远程 `ppt` 服务根地址
- `ppt-mcp` 仍然在本机运行
- `ppt_create_job` 会读取本地 PDF，然后上传到远程 API

### 3. 远程 `ppt-mcp-remote`

这个模式更像“把 MCP 服务也部署到服务器上”。

适合：

- 需要团队共用
- 需要统一入口
- 需要 Streamable HTTP MCP

但这条路复杂度更高：

- 需要额外的入口认证
- 需要处理上传源文件
- 需要考虑下载和权限

如果只是你自己本机用，优先用第 1 种。

## 最容易搞混的地址问题

`PPT_API_BASE_URL` 应该指向 `ppt` 服务根地址，而不是 `/api/v1`。

正确示例：

```env
PPT_API_BASE_URL=http://127.0.0.1:8000
```

或者：

```env
PPT_API_BASE_URL=https://ppt.example.com
```

不要写成：

```env
PPT_API_BASE_URL=http://127.0.0.1:8000/api/v1
```

也不建议默认写成 Web 入口：

```env
PPT_API_BASE_URL=http://127.0.0.1:3000
```

因为 `3000` 这条链路是 Web 入口，通常会受 `WEB_ACCESS_PASSWORD` 影响。

如果你只是给 `ppt-mcp` 自己使用，最稳的就是直连：

```env
PPT_API_BASE_URL=http://127.0.0.1:8000
```

## 与 `ppt` 主仓库的鉴权关系

如果 `ppt` 主仓库没有配置 `API_BEARER_TOKEN`：

- `ppt-mcp` 直连 `127.0.0.1:8000` 就能用

如果 `ppt` 主仓库配置了：

```env
API_BEARER_TOKEN=your-shared-secret
```

那 `ppt-mcp` 也要配置同一个值：

```env
PPT_API_BEARER_TOKEN=your-shared-secret
```

可以把它理解成：

- `API_BEARER_TOKEN`
  是后端 API 要求的密码
- `PPT_API_BEARER_TOKEN`
  是 `ppt-mcp` 发请求时带上的那个密码

通常这两个值应该一致。

## 环境变量

仓库里提供了一个示例文件：

```bash
cp .env.example .env
```

### 本地 stdio 模式最少需要这些

```env
PPT_API_BASE_URL=http://127.0.0.1:8000
PPT_API_TIMEOUT_SECONDS=120
```

如果后端 API 开了 Bearer，再加：

```env
PPT_API_BEARER_TOKEN=your-shared-secret
```

### 常用变量说明

| 变量 | 说明 |
| --- | --- |
| `PPT_API_BASE_URL` | `ppt` 服务根地址，不带 `/api/v1` |
| `PPT_API_TIMEOUT_SECONDS` | MCP 请求 API 的超时时间 |
| `PPT_API_BEARER_TOKEN` | 直连 `ppt` API 时使用的 Bearer |
| `MINERU_API_TOKEN` | MinerU 云解析 token |
| `BAIDU_API_KEY` | 百度文档解析 key |
| `BAIDU_SECRET_KEY` | 百度文档解析 secret |
| `SILICONFLOW_API_KEY` | 通用远程视觉/OCR 模型 key |

### 远程 `ppt-mcp-remote` 额外变量

| 变量 | 说明 |
| --- | --- |
| `PPT_MCP_BIND_HOST` | 远程 MCP 监听地址，默认 `0.0.0.0` |
| `PPT_MCP_BIND_PORT` | 远程 MCP 端口，默认 `8080` |
| `PPT_MCP_PUBLIC_BASE_URL` | 远程 MCP 对外访问地址 |
| `PPT_MCP_SERVER_TOKEN` | 远程 MCP 自己的入口密码 |
| `PPT_MCP_PROFILE_STORE` | 远程 profile 配置文件路径 |
| `PPT_MCP_DATA_DIR` | 远程上传缓存与元数据目录 |

## 先启动主服务

在使用 `ppt-mcp` 前，先把原始 `ppt` 服务跑起来。

```bash
cd /home/lan/workspace/ppt
docker compose up -d --build api worker redis
```

默认情况下，`ppt-mcp` 会连接 `http://127.0.0.1:8000`。

## 安装

```bash
cd /home/lan/workspace/ppt-mcp
uv sync
```

## 运行

### 本地 stdio

```bash
cd /home/lan/workspace/ppt-mcp
uv run ppt-mcp
```

### 远程 MCP 服务

```bash
cd /home/lan/workspace/ppt-mcp
export PPT_API_BASE_URL=http://127.0.0.1:8000
export PPT_MCP_PUBLIC_BASE_URL=https://ppt.zichuanlan.top
export PPT_MCP_SERVER_TOKEN=change-me
uv run ppt-mcp-remote
```

这会暴露：

- MCP endpoint: `POST/GET https://.../mcp`
- health endpoint: `GET https://.../healthz`
- upload endpoint: `PUT https://.../uploads/{source_id}?token=...`
- result proxy: `GET https://.../jobs/{job_id}/download`

## 工具列表

- `ppt_list_routes`
- `ppt_check_route`
- `ppt_set_conversion_target`
- `ppt_set_route_options`
- `ppt_convert_pdf`
- `ppt_health_check`
- `ppt_create_job`
- `ppt_list_jobs`
- `ppt_get_job_status`
- `ppt_cancel_job`
- `ppt_get_job_artifacts`
- `ppt_download_result`
- `ppt_download_artifact`
- `ppt_list_route_models`
- `ppt_list_ai_models`
- `ppt_check_ai_ocr`

## 常用工作流

日常使用推荐直接走高层工具：

- `ppt_list_routes`
- `ppt_check_route`
- `ppt_set_conversion_target`
- `ppt_list_route_models`（仅 AI OCR 路线需要）
- `ppt_set_route_options`
- `ppt_convert_pdf`

而不是一开始就手填所有底层 job fields。

高层工作流有一条强规则：

- AI 只能列出路线让用户选，不能自己根据 PDF 内容、版式、是否像扫描件之类的信息替用户选路线
- AI 不应该说“我将为您选择最适合的路线”
- 用户没有明确确认 route 之前，不应该调用 `ppt_check_route`、`ppt_set_conversion_target`、`ppt_set_route_options`、`ppt_list_route_models`、`ppt_convert_pdf`
- 如果用户一开始已经给了 `pdf_path` 或页码范围，应该尽早写进同一个 `route_workflow_id`；页数必须明确写成 `page_range_decision=all_pages` 或 `page_range_decision=page_range`，不要只靠对话记忆记住“第几页到第几页”
- 用户问可用模型时，AI 必须先调用模型列表工具，并且只能复述工具真实返回的模型 id；不要脑补供应商分类、模型家族或推荐语
- 如果是从 `model_choices` 里选模型，高层流程优先使用 `ocr_ai_model_choice_index`，不要让低端模型自己重打一长串 model id
- 高层 route 流程里不要再向用户索要 API key；路线凭据默认从 `ppt-mcp` 的环境变量复用
- 高层 route 工具不支持切换 provider / base URL；如需切网关，改用低层 `ppt_list_ai_models` / `ppt_check_ai_ocr` / `ppt_create_job`
- `ppt_create_job` 也是低层 escape hatch；除非用户明确要求绕过引导流程，否则不应该直接调用

### 典型流程

1. 用 `ppt_list_routes` 看有哪些可用路线
2. 先让用户明确选择并确认一个路线，例如 `本地切块识别`、`MinerU 云解析`
3. 用 `ppt_check_route(route=..., route_confirmed=true)` 锁定这条路线，并拿到返回的 `route_workflow_id`
4. 用 `ppt_set_conversion_target(route_workflow_id=..., pdf_path=...)` 写入本地 PDF 路径
5. 在同一个 `ppt_set_conversion_target(...)` 里明确写入页码范围决策：
   `page_range_decision=all_pages`，或者
   `page_range_decision=page_range` 并继续填写 `page_start`、`page_end`
6. 后续高层 route 工具都继续沿用同一个 `route_workflow_id`，不要把不同路线串到同一条流程里
7. 用 `ppt_set_route_options(route_workflow_id=..., scanned_page_mode=..., remove_footer_notebooklm=...)` 写扫描页处理和页脚选项
8. 如果是 AI OCR 路线，先调用 `ppt_list_route_models(route_workflow_id=...)` 拉候选模型
   只复述工具真实返回的模型 id，不要自己补充“本地模型 / Groq / OpenAI / Anthropic”之类的分类
9. 再用 `ppt_set_route_options(...)` 让用户明确选择：
   `ocr_ai_model_decision=route_default`，或者
   `ocr_ai_model_decision=explicit` 并优先填写选中的 `ocr_ai_model_choice_index`
   只有确实需要时再补 `ocr_ai_model`
   如果只是同一路线下换模型，默认沿用该路线已有的 provider / base URL / API key，不应该再反问 API key
   如果用户明确要切换 provider / base URL，就不要继续走高层 route 工具，改走低层 expert 工具
10. 最后再用 `ppt_convert_pdf(route_workflow_id=...)` 提交任务
11. 用 `ppt_get_job_status` 轮询进度
12. 用 `ppt_download_result` 下载结果

默认建议：

- `scanned_page_mode=fullpage`
- `remove_footer_notebooklm=false`

`ppt_check_route` 现在只负责锁 route；拿到 `route_workflow_id` 之后，高层流程应该继续走 `ppt_set_conversion_target`、`ppt_list_route_models`（如有需要）、`ppt_set_route_options`，最后才到 `ppt_convert_pdf`。

`ppt_convert_pdf` 现在只负责提交；如果 `pdf_path`、`page_range_decision`、`scanned_page_mode`、`remove_footer_notebooklm`，或者 AI 路线下的模型选择还没在 workflow 里确认完，它会直接返回缺失字段、下一步字段和下一步工具，而不是静默套默认值直接开跑。

`ppt_create_job` 现在也要求显式传 `low_level_override_confirmed=true`；如果用户只是说“转第 3 页”，不应该直接走这个低层工具。

路径说明：

- 如果 `ppt-mcp` 跑在 WSL，而 MCP 客户端给的是 Windows 路径，例如 `C:\Users\27783\Desktop\file.pdf`，本地 stdio 模式现在会自动转换成 `/mnt/c/Users/27783/Desktop/file.pdf`
- 如果客户端给的是 `\\wsl.localhost\发行版名\...`，也会自动转换成对应的 Linux 路径

## 常用路线

- `基础本地解析` (`local_basic`)
- `MinerU 云解析` (`mineru`)
- `百度文档解析` (`baidu_doc`)
- `本地切块识别` (`layout_block`)
- `模型直出框和文字` (`direct`)
- `内置文档解析` (`doc_parser`)

## MCP 配置示例

### 1. 本地 clone 方式

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

如果后端 API 开了 Bearer：

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
        "PPT_API_BASE_URL": "http://127.0.0.1:8000",
        "PPT_API_BEARER_TOKEN": "your-shared-secret"
      }
    }
  }
}
```

### 2. `uvx` 方式

```json
{
  "mcpServers": {
    "ppt": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/ZiChuanLan/ppt-mcp",
        "ppt-mcp"
      ],
      "env": {
        "PPT_API_BASE_URL": "https://ppt.zichuanlan.top",
        "PPT_API_BEARER_TOKEN": "your-shared-secret",
        "MINERU_API_TOKEN": "your-mineru-token",
        "BAIDU_API_KEY": "your-baidu-api-key",
        "BAIDU_SECRET_KEY": "your-baidu-secret-key",
        "SILICONFLOW_API_KEY": "your-siliconflow-key"
      }
    }
  }
}
```

## 常见参数示例

### `ppt_create_job`

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

高层 route 工具现在拆成四步：

- `ppt_check_route(route, route_confirmed=true)`
- `ppt_set_conversion_target(route_workflow_id, pdf_path, page_range_decision, page_start?, page_end?)`
- `ppt_set_route_options(route_workflow_id, scanned_page_mode, remove_footer_notebooklm, ocr_ai_model_decision, ocr_ai_model_choice_index?, ocr_ai_model?)`
- `ppt_convert_pdf(route_workflow_id, retain_process_artifacts?)`

高层 route 工具不会暴露 `ocr_ai_provider`、`ocr_ai_base_url`、`ocr_ai_prompt_preset`、`extra_options`。如果需要切换网关或传更底层的高级参数，请改用 `ppt_create_job`。

如果你确实要走低层 `ppt_create_job`，还必须显式传：

- `low_level_override_confirmed=true`
  表示用户明确要求绕过高层引导，愿意自己承担底层参数选择

AI OCR 路线的模型决策现在必须显式确认：

- `ocr_ai_model_decision=route_default`
  表示用户明确接受 route 默认模型
- `ocr_ai_model_decision=explicit`
  表示用户已经从 `ppt_list_route_models` 返回的列表里选了一个模型
  高层流程优先填写 `ocr_ai_model_choice_index`
  只有确实需要时再补 `ocr_ai_model`

### 列模型

高层 AI OCR 工作流优先用 `ppt_list_route_models`，它会自动使用 route 对应的 provider / base URL / API key，并返回 route 默认模型和候选列表。

先通过 `ppt_check_route(...)` 锁定路线，拿到 `route_workflow_id`，再列模型：

```json
{
  "route_workflow_id": "your-route-workflow-id"
}
```

如果你就是想手动探测任意 provider，也可以继续用底层 `ppt_list_ai_models`：

```json
{
  "provider": "openai",
  "api_key": "sk-...",
  "base_url": "https://api.openai.com/v1",
  "capability": "ocr"
}
```

低层 `ppt_list_ai_models` 也只应该复述工具返回的原始模型 id；不要自己扩写成供应商分类、推荐列表或“以及更多选择”。

如果用户明确要切换 provider / base URL，不要继续走高层 route 工具，改用低层 `ppt_list_ai_models` / `ppt_check_ai_ocr` / `ppt_create_job`。

### 检查 AI OCR 模型

```json
{
  "provider": "openai",
  "api_key": "sk-...",
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-4.1-mini",
  "ocr_ai_chain_mode": "layout_block"
}
```

## 路线默认依赖的环境变量

### `本地切块识别` / `layout_block`

- key: `PPT_LAYOUT_BLOCK_API_KEY` 或 `SILICONFLOW_API_KEY`
- provider 默认：`siliconflow`
- base URL 默认：`https://api.siliconflow.cn/v1`
- model 默认：`deepseek-ai/DeepSeek-OCR`

### `模型直出框和文字` / `direct`

- key: `PPT_DIRECT_API_KEY` 或 `SILICONFLOW_API_KEY`
- provider 默认：`deepseek`
- base URL 默认：`https://api.siliconflow.cn/v1`
- model 默认：`deepseek-ai/DeepSeek-OCR`

### `内置文档解析` / `doc_parser`

- key: `PPT_DOC_PARSER_API_KEY` 或 `SILICONFLOW_API_KEY`
- provider 默认：`openai`
- base URL 默认：`https://api.siliconflow.cn/v1`
- model 默认：`PaddlePaddle/PaddleOCR-VL-1.5`

### `MinerU 云解析` / `mineru`

- key: `MINERU_API_TOKEN`

### `百度文档解析` / `baidu_doc`

- key: `BAIDU_API_KEY`
- secret: `BAIDU_SECRET_KEY`

## 为什么还是建议 local-first

这个项目更适合先做本地 `stdio` MCP，再考虑远程化。

原因很简单：

- 输入通常是本地 PDF 文件
- 转换任务是长任务
- 输出是 PPTX 和可选过程产物
- 现有 `ppt` 项目本来就是本地 API + worker 架构

远程 MCP 当然能做，但会马上引入这些额外问题：

- 认证
- 上传
- 下载
- 权限
- 多用户隔离

如果你只是自己用，先不要给自己加这些复杂度。

## 远程模式的安全建议

如果你把 `ppt` 部署到远程服务器：

- 优先 HTTPS
- 优先让 `ppt-mcp` 直连真实 API 根地址
- 如果 `ppt` 开了 `API_BEARER_TOKEN`，记得同步到 `PPT_API_BEARER_TOKEN`
- 不建议默认把 `PPT_API_BASE_URL` 指向 Web 域名入口，除非你明确要复用 Web 的访问控制

## 后续设计文档

- `docs/remote-mcp-prd.md`
- `docs/remote-mcp-tool-contracts.md`

这些文档主要对应将来更产品化的远程 MCP 方案。
当前日常使用仍然优先推荐本地 `stdio` 模式。
