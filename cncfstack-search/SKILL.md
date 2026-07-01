---
name: cncfstack-search
description: 搜索藏云阁(cncfstack.com)上的云原生资源。当老大说"在藏云阁搜/找/查 X"或"cncfstack 上搜 X"时触发。覆盖资源类型：Agent Skills、Docker镜像、HelmChart、代码仓库、网站镜像、文件、Logo、软件包。
agent_created: true
---

# 藏云阁(cncfstack.com) 资源搜索

搜索 https://www.cncfstack.com/ 上的云原生资源。该站是中文云原生资源镜像站，提供多种类型资源的国内高速镜像。

## 重要限制

- **SPA 客户端渲染**：`?search=`, `?page=`, `?letter=` 等查询参数均无效，搜索完全在浏览器 JS 内完成
- **列表页只展示约 50 条热门条目**：Docker Images（2.2万）、Skills（9万）的热门项在前 50，能搜到。**HelmChart（29.4万）和 Code（1.4万）从 a~z 排序，冷门条目不在首屏**，几乎不可能通过 WebFetch 搜到
- **Google site 搜索**：实测 Google 未索引该站，不可用

## 资源类型与 URL 映射

| 资源类型 | 中文名 | 大小 | URL | 可搜索性 |
|---|---|---|---|---|
| `skills` | Agent SKILLS 库 | 9万 | `/skills` | ✅ 热门可搜 |
| `images` | Docker 镜像库 | 2.2万 | `/images` | ✅ 热门可搜 |
| `charts` | HelmChart 库 | 29.4万 | `/charts` | ❌ 排序靠后的搜不到 |
| `code` | 代码仓库 | 1.4万 | `/code` | ⚠️ 部分可搜 |
| `website` | 网站镜像 | 69 | `/website` | ✅ 少量直接搜 |
| `file` | 文件仓库 | 6千 | `/app/file` | ⚠️ 部分可搜 |
| `logo` | Logo 库 | 5.6千 | `/logo` | ✅ 全部可搜 |
| `packages` | 软件包库 | 629 | `/packages` | ✅ 全部可搜 |

## 搜索策略

### 策略：WebFetch 抓取分类页 + AI 内容匹配（唯一可行方式）

**操作步骤：**

1. 确定目标资源类型，用 WebFetch 抓取对应分类页
2. prompt 设为："列出所有与 '{关键词}' 相关的条目，包含名称和描述"

```
示例：搜索 nginx 相关 Docker 镜像
→ WebFetch https://www.cncfstack.com/images
→ prompt: "在这个 Docker 镜像列表中，找出与 'nginx' 相关的镜像，列出名称和描述"

示例：搜索 redis 相关 Logos
→ WebFetch https://www.cncfstack.com/logo
→ prompt: "在这个 Logo 列表中，找出与 'redis' 相关的 Logo，列出名称和格式"
```

**已知限制：**
- Docker images 页：热门镜像（nginx/redis/mysql/ubuntu 等）在前 50 条，可搜到。冷门镜像可能不在首屏
- HelmChart 页：29.4 万 Chart 按名称字母序排列，首屏从 0-9 和 a 开头。除非 Chart 名称以 a 之前开头（数字/符号），否则无法通过 WebFetch 搜到
- Skills 页：9 万 Skill 按热度排序，热门 Skill（anthropics 系列等）在前 50
- Code 页：1.4 万仓库按 star 数降序，热门仓库（>15 万 star）在前 50

**如果页面内容中没有匹配项：** 直接告诉老大"藏云阁该分类的首页列表中没有找到匹配 {关键词} 的条目（可能是因为该资源不在前 50 条热门列表中）"

## 搜索结果呈现格式

```
🔍 藏云阁搜索结果：<关键词>

📦 <资源类型> | <找到 N 条>
- <名称1> — <描述/版本>
- <名称2> — <描述/版本>
  ...

📎 来源：<页面 URL>
```

没搜到直接说"藏云阁上没找到匹配 {关键词} 的资源"。

## Docker 镜像拉取说明

藏云阁 Docker 镜像是透明代理（DNS 劫持 `registry-1.docker.io`），**不需要改配置**，直接 `docker pull` 就能走国内加速。另外也提供独立 registry：

```
docker pull registry.cncfstack.com/library/nginx:latest
```
