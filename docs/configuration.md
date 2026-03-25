# 配置说明

## `PPT_API_BASE_URL`

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

## Bearer Token 关系

如果主服务配置了：

```bash
API_BEARER_TOKEN=your-shared-secret
```

那么 `ppt-mcp` 也要配置：

```bash
PPT_API_BEARER_TOKEN=your-shared-secret
```

通常这两个值应保持一致。

## 常用变量

| 变量 | 说明 |
| --- | --- |
| `PPT_API_BASE_URL` | `PDF2PPT` 服务根地址，不带 `/api/v1` |
| `PPT_API_TIMEOUT_SECONDS` | MCP 请求 API 的超时时间 |
| `PPT_API_BEARER_TOKEN` | 直连 API 时使用的 Bearer |
| `MINERU_API_TOKEN` | MinerU 云解析 token |
| `BAIDU_API_KEY` | 百度文档解析 key |
| `BAIDU_SECRET_KEY` | 百度文档解析 secret |
| `SILICONFLOW_API_KEY` | 通用远程视觉/OCR 模型 key |

## 远程 `ppt-mcp-remote` 额外变量

| 变量 | 说明 |
| --- | --- |
| `PPT_MCP_BIND_HOST` | 远程 MCP 监听地址，默认 `0.0.0.0` |
| `PPT_MCP_BIND_PORT` | 远程 MCP 端口，默认 `8080` |
| `PPT_MCP_PUBLIC_BASE_URL` | 远程 MCP 对外访问地址 |
| `PPT_MCP_SERVER_TOKEN` | 远程 MCP 自己的入口密码 |
| `PPT_MCP_PROFILE_STORE` | 远程 profile 配置文件路径 |
| `PPT_MCP_DATA_DIR` | 远程上传缓存与元数据目录 |

## 路径兼容性

本地 stdio 模式下，`ppt-mcp` 现在会转换常见路径格式：

- Windows 路径，例如 `C:\Users\...\file.pdf`
- `\\wsl.localhost\发行版名\...` 路径

这使得 MCP 客户端在 Windows / WSL 混合环境下更容易把本地 PDF 路径传给 `ppt-mcp`。
