# ppt-mcp

> `PDF2PPT` 主服务的 MCP 接入层。

`ppt-mcp` 不重新实现 PDF 解析、OCR 或 PPT 生成。  
它做的事情是把现有 `PDF2PPT` API 包装成 MCP tools，让 Claude Desktop、Cursor、Codex CLI 等客户端可以直接调用转换能力。

## 它和主服务是什么关系

一句话理解：

```text
MCP Client -> ppt-mcp -> PDF2PPT API -> worker
```

职责边界：

- `PDF2PPT` 主服务负责 PDF 解析、OCR、任务调度和 PPT 生成
- `ppt-mcp` 负责 MCP 协议适配和工具封装
- 两者不是两套重复系统，而是主服务与接入层关系

## 适合什么场景

- 想让 AI 客户端直接调用 PDF 转 PPT，而不是手动打开 Web 页面
- 想把“上传 PDF -> 创建任务 -> 轮询状态 -> 下载结果”封装成 MCP tools
- 想把现有 `PDF2PPT` 服务接入本地 Agent 或自动化工作流

## 推荐使用方式

### 1. 本地 stdio MCP，最简单也最稳

这是默认推荐模式。

- `PDF2PPT` 服务跑在本机
- `ppt-mcp` 也跑在本机
- transport 使用 `stdio`
- `PPT_API_BASE_URL` 指向 `http://127.0.0.1:8000`

这时：

- 浏览器用户走 Web 页面
- MCP 用户走本地 API
- 两条链路互不干扰

### 2. 本地 stdio MCP，连接远程 PDF2PPT

适合：

- AI 客户端在本机
- 但转换服务部署在远程服务器

这时：

- `PPT_API_BASE_URL` 指向远程服务根地址
- `ppt-mcp` 仍然在本机运行
- 本地 PDF 由 `ppt-mcp` 读取后上传到远程 API

### 3. 远程 `ppt-mcp-remote`

适合：

- 团队共用
- 需要统一 MCP 入口
- 需要 Streamable HTTP MCP

但复杂度更高：

- 需要入口认证
- 需要处理上传源文件
- 需要考虑下载、权限和公网暴露

如果只是本机自用，优先使用第 1 种。

## 快速开始

### 1. 先启动主服务

```bash
cd /home/lan/workspace/ppt
docker compose up -d --build api worker redis
```

默认情况下，`ppt-mcp` 会连接：

- `http://127.0.0.1:8000`

### 2. 安装

```bash
cd /home/lan/workspace/ppt-mcp
uv sync
```

### 3. 运行本地 stdio MCP

```bash
cd /home/lan/workspace/ppt-mcp
uv run ppt-mcp
```

### 4. 最少环境变量

```bash
cp .env.example .env
```

最少需要：

```bash
PPT_API_BASE_URL=http://127.0.0.1:8000
PPT_API_TIMEOUT_SECONDS=120
```

如果主服务开启了：

```bash
API_BEARER_TOKEN=your-shared-secret
```

那么这里也要配置：

```bash
PPT_API_BEARER_TOKEN=your-shared-secret
```

## 关键配置

### `PPT_API_BASE_URL` 应该怎么写

它应该指向 `PDF2PPT` 服务根地址，而不是 `/api/v1`。

正确示例：

```bash
PPT_API_BASE_URL=http://127.0.0.1:8000
```

或者：

```bash
PPT_API_BASE_URL=https://ppt.example.com
```

不建议写成：

```bash
PPT_API_BASE_URL=http://127.0.0.1:8000/api/v1
```

也不建议默认写成 Web 入口：

```bash
PPT_API_BASE_URL=http://127.0.0.1:3000
```

因为 `3000` 这条链路通常会受到 `WEB_ACCESS_PASSWORD` 影响。

### Bearer Token 的对应关系

- `API_BEARER_TOKEN` 是主服务 API 要求的密码
- `PPT_API_BEARER_TOKEN` 是 `ppt-mcp` 请求 API 时带上的密码

通常这两个值应保持一致。

## 当前工具能力

`ppt-mcp` 已覆盖主服务的常见任务流，包括：

- 路线查询与确认
- 创建任务
- 查询任务状态
- 列出任务
- 取消任务
- 下载结果
- 读取产物
- 列出模型
- 检查 AI OCR 路线

从使用方式上，更推荐优先走高层 route workflow，而不是一开始就手填所有底层字段。

## 路径兼容性

本地 stdio 模式下，`ppt-mcp` 现在会转换常见路径格式：

- Windows 路径，例如 `C:\Users\...\file.pdf`
- `\\wsl.localhost\发行版名\...` 路径

这使得 MCP 客户端在 Windows / WSL 混合环境下更容易把本地 PDF 路径传给 `ppt-mcp`。

## 文档

更详细的说明已拆到 `docs/`：

- [文档首页](docs/index.md)
- [快速开始](docs/getting-started.md)
- [使用模式与部署建议](docs/usage-modes.md)
- [配置说明](docs/configuration.md)
- [Remote MCP PRD](docs/remote-mcp-prd.md)
- [Remote MCP Tool Contracts](docs/remote-mcp-tool-contracts.md)

## MCP 配置示例

本地 clone 方式：

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

## License

MIT.
