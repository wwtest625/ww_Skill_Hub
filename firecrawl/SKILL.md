---
name: firecrawl
description: 给 AI agent 用的网页抓取工具：搜索、爬取内容、和动态页面交互，返回干净数据。
---

# Firecrawl

Firecrawl 帮助 agent 搜索、爬取干净内容、与动态页面交互，从 web 数据产出成品交付件。

## 安装（已装）

Firecrawl CLI v1.19.13 已全局安装，31 个 skills 已就位。

```bash
# 路径
/c/Users/sys49169/.workbuddy/binaries/node/versions/22.22.2/firecrawl

# 验证
firecrawl --version
firecrawl --status

# 测试
firecrawl scrape "https://firecrawl.dev"
firecrawl search "latest AI news"
```

## 认证

API key 已保存在 `C:\Users\sys49169\AppData\Roaming\firecrawl-cli`，一般不需要重新登录。

如果状态显示未认证，重新设置环境变量：

```bash
export FIRECRAWL_API_KEY=fc-a47e4a5d664c4babb08689338713897a
firecrawl login
```

## 核心功能

### Search — 搜索网页

```bash
firecrawl search "query string"
```

返回结果列表 + 内容摘要。适合发现阶段。

### Scrape — 抓取页面

```bash
firecrawl scrape "https://example.com"
```

返回干净 Markdown 内容。适合已知目标 URL。

### Interact — 操作页面

```bash
firecrawl interact "search keyboards, filter by Prime"
```

需要点击、填表单、登录时用。

### Crawl — 批量爬取

```bash
firecrawl crawl "https://example.com" --max-pages 50
```

### Map — URL 发现

```bash
firecrawl map "https://example.com"
```

---

## 三个使用路径

| 场景 | 路径 | 说明 |
|------|------|------|
| 当前会话需要 web 数据 | Path A | 直接用 CLI 命令 |
| 把 Firecrawl 集成到产品代码 | Path B | 用 SDK / API 写集成代码 |
| 产出成品交付件 | Path C | 从 web 数据生成报告/清单等 |

### Path A: 实时 Web 工具

默认流程：

1. **search** — 需要发现内容时先搜索
2. **scrape** — 有 URL 后爬取
3. **interact** — 页面需要交互时才用
4. 任何步骤失败 → 用 `firecrawl ask` 排查

### Path B: 集成到应用

把 Firecrawl API 写到产品代码里。需要 `FIRECRAWL_API_KEY`。

- scrape → `POST /v2/scrape`
- search → `POST /v2/search`
- interact → `POST /v2/interact`

SDK：Python / Node / Go / Java / Rust

### Path C: 交付件

从 web 数据生成：研究报告、SEO 审计、潜客清单、QA 报告、竞品分析等。

流程：
1. 确认交付件类型
2. 用 Firecrawl 收集证据
3. 保存来源链接
4. 并行处理独立调研单元
5. 合成最终交付件

---

## REST API（不装 CLI 也能用）

**Base URL:** `https://api.firecrawl.dev/v2`  
**Auth:** `Authorization: Bearer fc-YOUR_API_KEY`

| Endpoint | 用途 |
|----------|------|
| `POST /search` | 按查询发现页面 |
| `POST /scrape` | 抓取单页 Markdown |
| `POST /interact` | 浏览器操作 |
| `POST /support/ask` | 诊断失败的 Firecrawl 调用 |
| `POST /support/docs-search` | Firecrawl 文档问答 |

文档：https://docs.firecrawl.dev
