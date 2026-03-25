# 使用模式与部署建议

## 1. 本地 stdio MCP，默认推荐

这是最简单、最稳的方式。

- `PDF2PPT` 服务跑在本机
- `ppt-mcp` 也跑在本机
- transport 使用 `stdio`
- `PPT_API_BASE_URL` 指向 `http://127.0.0.1:8000`

适合：

- 自己本机使用
- Claude Desktop / Cursor / Codex CLI 直连
- 不想处理额外公网暴露和鉴权

## 2. 本地 stdio MCP，连接远程 PDF2PPT

适合：

- AI 客户端在本机
- 但转换服务在远程服务器

这时：

- `PPT_API_BASE_URL` 指向远程服务根地址
- `ppt-mcp` 仍然在本机运行
- 本地 PDF 由 `ppt-mcp` 读取后上传到远程 API

## 3. 远程 `ppt-mcp-remote`

这个模式更像“把 MCP 服务也部署到服务器上”。

适合：

- 团队共用
- 需要统一 MCP 入口
- 需要 Streamable HTTP MCP

但复杂度更高：

- 需要入口认证
- 需要上传源文件处理
- 需要下载、权限和公网暴露策略

如果只是本机自用，优先使用本地 stdio。

## 推荐理解方式

可以把三种方式理解成：

1. `本机 Agent -> 本机 ppt-mcp -> 本机 PDF2PPT`
2. `本机 Agent -> 本机 ppt-mcp -> 远程 PDF2PPT`
3. `远程 Agent / 客户端 -> 远程 ppt-mcp-remote -> 远程 PDF2PPT`
