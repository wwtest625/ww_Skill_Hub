# 🛠️ ww_Skill_Hub

来福的 Agent Skills 仓库。收集了日常 WorkBuddy / Claude Code 等 AI agent 用到的各种技能，按项目需要软链接安装。

<p>
  <img src="https://img.shields.io/badge/skills-16-blue?style=flat-square" alt="skills count">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="license">
  <img src="https://img.shields.io/badge/maintained-yes-brightgreen?style=flat-square" alt="maintained">
</p>

---

## 📦 Skills 一览

### 🤖 SSH & 远程操作

| Skill | 说明 |
|-------|------|
| **ssh-skill** v3.3.0 | SSH 操作统一入口。远程连接、文件传输、端口转发、Docker/K8s 执行、跳板机穿透 |
| **agents-remote-mount** | 把本地文件夹通过 rclone WebDAV + SSH 反向隧道挂载到远程服务器 |

### 🎨 设计 & 内容

| Skill | 说明 |
|-------|------|
| **baoyu-design** | 生成即用 HTML 设计稿：UI 原型、落地页、仪表盘、APP 界面等 |
| **markitdown** | 微软开源的文件转 Markdown — PDF/DOCX/PPTX/图片 → Markdown |

### 🌐 搜索 & 爬取

| Skill | 说明 |
|-------|------|
| **anysearch** | 实时搜索引擎，支持网页搜索、垂直搜索、批量并行搜索和 URL 内容提取 |
| **firecrawl** | AI agent 网页抓取工具：搜索、爬取、与动态页面交互 |
| **search-router** | 统一搜索入口，根据查询类型自动路由到 anysearch / firecrawl / a-stock-data 等 |
| **search-skills-sh** | 从 skills.sh 社区搜索开源 agent skill |
| **opencli-site-adapter** | 创建或修复 opencli 站点爬虫适配器 |

### ⚡ 沐曦 GPU (MetaX)

| Skill | 说明 |
|-------|------|
| **metax-inference-loop** | GPU 推理部署标准化工作流 — 5 阶段循环：环境评估→模型确认→容器编排→压测调优→固化交付 |
| **metax-sgpu-k8s-deploy** | C500 sGPU 软切分部署到 K8s，GPU Operator + HAMi 全流程 |
| **metax-stream-download** | 浏览器获取沐曦驱动/SDK 下载链接，流式传到远程服务器 |
| **docx-model-guide-replace** | 沐曦 C500 模型测试指导文档模板替换 |

### 🔧 工具 & 框架

| Skill | 说明 |
|-------|------|
| **lavish-axi** | Human-AI 协作 HTML 编辑器，用于评审/标注/修改 Agent 生成的 HTML |
| **ponytail-lazy-dev** | 懒人开发原则：最少代码、最少成本、最快搞定 |

### 📈 金融数据

| Skill | 说明 |
|-------|------|
| **a-stock-data** | A 股全栈数据工具包：行情、研报、资金流、公告、财报查询 |

---

## 🔗 使用方式

```bash
# 在项目里创建软链接指向仓库中的原件
ln -s ~/GitHub/ww_Skill_Hub/<skill-name> .agents/skills/<skill-name>

# 或者让 agent 帮你干
# "帮我把 ww_Skill_Hub 里的 ssh-skill 链接到当前项目"
```

所有 Skill 只存一份原件在各项目目录中创建**软链接**引用。更新仓库拉取最新代码，所有用到的项目自动同步。

---

## 📋 维护

```bash
# 克隆
gh repo clone wwtest625/ww_Skill_Hub

# 拉取更新
cd ~/GitHub/ww_Skill_Hub && git pull

# 发布新技能
cp -r ~/.workbuddy/skills/<new-skill> .
git add <new-skill>
git commit -m "add: <new-skill>"
git push
```

---

*Made with ⚡ by 来福*
