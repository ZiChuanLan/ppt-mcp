# 快速开始

## 1. 先启动主服务

```bash
cd /home/lan/workspace/ppt
docker compose up -d --build api worker redis
```

默认情况下，`ppt-mcp` 会连接：

- `http://127.0.0.1:8000`

## 2. 安装

```bash
cd /home/lan/workspace/ppt-mcp
uv sync
```

## 3. 配置环境变量

```bash
cp .env.example .env
```

本地 stdio 模式最少需要：

```bash
PPT_API_BASE_URL=http://127.0.0.1:8000
PPT_API_TIMEOUT_SECONDS=120
```

如果主服务 API 开了 Bearer：

```bash
PPT_API_BEARER_TOKEN=your-shared-secret
```

## 4. 运行

### 本地 stdio

```bash
cd /home/lan/workspace/ppt-mcp
uv run ppt-mcp
```

### 远程 MCP 服务

```bash
cd /home/lan/workspace/ppt-mcp
export PPT_API_BASE_URL=http://127.0.0.1:8000
export PPT_MCP_PUBLIC_BASE_URL=https://your-mcp.example.com
export PPT_MCP_SERVER_TOKEN=change-me
uv run ppt-mcp-remote
```
