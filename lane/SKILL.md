---
name: lane
version: 2.0.0
description: Lane（泳道模式）——三 Agent（agy / qoder / CodeBuddy）异构协同总线：跨 Agent 会话注入（Session Injection）、统一日志查看器、共享基建与踩坑规范。触发词：lane、泳道、协作、会话记录、查看记录、会话注入、另一个agent、互通、handoff、黑板
keywords: lane,泳道,协作,会话注入,agent-inject,qoder,agy,antigravity,codebuddy,会话记录,log-viewer,agent-log-viewer,互通,分工,handoff,黑板,xssh,GPU,模型下载
---

# 🏊‍♂️ Lane —— 三 Agent 泳道模式协同手册

## 一、 核心概念：什么是「泳道模式（Swimlane Architecture）」？

与传统的“树状主仆（Master-Worker）”不同，**Lane** 采用**长程专业泳道 + 异步会话注入（Session Injection）**：

- **agy 泳道**：系统架构 / 核心研发 / GPU 压测（沉淀后端与系统设计长程上下文）
- **qoder 泳道**：前端 UI / Web 渲染 / 交互测试（沉淀界面与组件渲染长程上下文）
- **CodeBuddy 泳道**：安全审计 / 代码 Review / 交付文档（沉淀代码质量与文档长程上下文）
- **人类 (You)**：可随时通过 `lane resume <agent>` 穿梭到任意泳道进行人机交互接管！

---

## 二、 Lane CLI 极简操作

已全局配置 `lane` 命令（位于 `/root/.local/bin/lane`）：

### 1. 跨泳道一键派活（自动注入到目标 Agent 最新会话）
```bash
lane qoder "我是 agy，已写好后端，请进行前端 UI 测试"
lane codebuddy "请对 /root/demo.py 进行代码审查"
lane agy "继续排查内存泄漏问题"
```

### 2. 精准会话注入（带意图与指定会话 ID）
```bash
# 注入到指定历史会话
lane inject --to qoder --session <ID> --intent "UI测试" --msg "任务说明"

# 开辟全新专属协作会话
lane inject --to codebuddy --new --intent "新建审查" --msg "任务说明"
```

### 3. 多泳道时间线查看与全局检索
```bash
lane log                     # 查看三方 Agent 最近全部会话（默认前 20 条）
lane log -g "关键词"          # 全局关键词搜索所有泳道
lane view <会话ID> [-s]      # 查看指定会话的完整对话流或摘要
```

### 4. 人工随时接管指定泳道
```bash
lane resume qoder [ID]       # 提示或执行恢复该会话
# 或直接运行：
qoder -r <ID>
codebuddy -r <ID>
agy --conversation <ID>
```

---

## 三、 跨 Agent 注入消息标准格式 (Preamble Protocol)

每次通过 `lane` 注入的消息均自动携带身份协议包头，便于目标 Agent 区分人类指令与兄弟 Agent 工单：

```markdown
【跨 Agent 协作消息】
- 发送者: agy
- 发送时间: 2026-08-26 15:00:00
- 来源会话: bec95485-70df-4435-a9b6-4dd0811b1110 (reply-to)
- 协作主题/意图: UI 测试与功能验证

【任务详情与上下文】
任务具体说明、修改的文件路径、测试要求...
```

---

## 四、 共享基建

- **远程运维**: 统一走 `xssh`（勿用裸 ssh），长任务加 `--timeout`，大输出加 `--max-lines`
- **模型下载**: 一律走 76 (`192.2.56.76:/data/AI_model/`) 用 modelscope；test03 只读
- **GPU 测试**: 沐曦 `mx-smi`、海光 `hy-smi`（可能不在 PATH，先 `which`）；跑测试前先确认 GPU 空闲；结果记录 模型名/精度/TP/batch/seq/throughput/TTFT/TPOT

## 五、 环境踩坑（跨 agent 通用）

- **WSL 网络红线**: 不 `ip link set down/up`、不 flush 路由、不改 `.wslconfig` 自己重启——网卡带 `noprefixroute`，link 后路由不重建直接断网。改文件让用户 `wsl --shutdown`
- **H3C TLS 拦截**: 本机 HTTPS 被中间人拦，curl 直连报证书错 → 加 `-k` + sha256 校验；node/curl 内网直连会被系统代理劫持，用 `--noproxy '*'`
- **venv 缺 ensurepip**: Debian 系统 `python3 -m venv` 可能失败（提示装 python3.x-venv），改用 `uv venv` + `uv pip install` 最省事（marimo 场景实测）
- **corepack**: 0.24 与 Node 22 不兼容报 `ERR_VM_DYNAMIC_IMPORT_CALLBACK_MISSING` → `npm install -g --force corepack@0.31.0`
- **pnpm**: `ERR_PNPM_NO_IMPORTER_MANIFEST_FOUND` = 在找本地脚本，实际要全局 CLI；全局存储路径不贯通会报 `ERR_MODULE_NOT_FOUND`，需重建软链接
- **npm 全局更新**: `ENOTEMPTY` = 残留临时目录（如 `.codebuddy-code-XXX`），清理后重装
