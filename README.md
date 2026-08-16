# Huawei Training Camp MCP

让 AI 可以通过 [MCP（Model Context Protocol）](https://modelcontextprotocol.io) 查询并分析
Huawei Training Camp / Huawei Health 的运动训练数据。

> **本项目处于早期开发阶段。** 第一阶段仅使用本地保存的 Huawei API 响应 JSON 作为测试数据，
> **不连接任何 Huawei 服务**，也不实现任何绕过权限、破解认证、窃取 Cookie 或绕过访问控制的功能。
> 所有数据均来自用户本地保存的、自己账户授权的数据文件。

## 技术栈

- Python 3.12+
- [FastMCP](https://github.com/jlowin/fastmcp)
- Pydantic
- httpx
- SQLite（Python 内置，无需额外依赖）

## 项目结构

```
huawei-health-mcp/
├─ src/
│  └─ huawei_health_mcp/
│     ├─ __init__.py        # 包标识 + 版本号
│     ├─ server.py          # 最小 FastMCP 服务 + health_check 工具（当前步骤）
│     ├─ models.py          # 数据模型（后续步骤）
│     ├─ database.py        # SQLite 存储（后续步骤）
│     ├─ parser.py          # 解析 Huawei API 响应（后续步骤）
│     └─ tools.py           # MCP 工具定义（后续步骤）
├─ data/                    # 本地测试数据（JSON）与本地数据库
├─ tests/                   # 测试
├─ README.md
├─ requirements.txt
└─ .gitignore
```

## 当前进度

- [x] 步骤 1：初始化项目 + 最小 FastMCP 服务 + `health_check` 工具
- [ ] 步骤 2+：数据模型、解析、存储、训练分析工具（待实现）

## 许可证

计划以开源协议发布，具体协议待定。

## 免责声明

本项目仅用于分析用户**自己授权、自己导出**的运动数据。不实现任何绕过华为权限、
破解认证、窃取 Cookie、绕过访问控制的功能。
