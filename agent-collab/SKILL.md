---
name: agent-collab
version: 1.2.0
description: 三 agent（qoder/agy/CodeBuddy）协作手册——互相读取会话记录、跨Agent会话注入(Session Injection)、共享基建（xssh/模型存储/GPU）、协作模式与踩坑经验。触发词：协作、会话记录、查看记录、会话注入、另一个agent、互通、handoff、黑板
keywords: 协作,会话注入,agent-inject,qoder,agy,antigravity,codebuddy,会话记录,log-viewer,agent-log-viewer,互通,分工,handoff,黑板,xssh,GPU,模型下载
---

# 三 Agent 协作手册

## 三个 agent 是谁

| agent | 入口 | 数据目录 | 会话记录位置 |
|---|---|---|---|
| qoder | `qoder` | `~/.qoder/` | `~/.qoder/projects/-root/<id>.jsonl` |
| agy (Antigravity) | `/root/.local/bin/agy` | `~/.gemini/antigravity-cli/` | `conversations/<id>.db` |
| CodeBuddy | `/root/.local/bin/codebuddy` | `~/.codebuddy/` | `projects/<ws>/<id>.jsonl` |

三套数据都在本机 `/root` 下，互为透明，任何一方都能读另一方记录并互相注入对话。

---

## 跨 Agent 会话注入 (Session Injection)

通过 `/root/agent-inject.py` 工具，一个 Agent 可以代表特定身份，将包含上下文的结构化提示词**直接注入到另一个 Agent 的指定/最新会话中**。目标 Agent 结合历史上下文完成思考与执行，会话完全落盘，用户可随时切入接管互动。

### 1. 注入命令用法
```bash
# 注入到目标 Agent 的最新活跃会话
python3 /root/agent-inject.py --to qoder --latest --from agy --intent "UI测试" --msg "我写好了xx，请测试"

# 注入到目标 Agent 的指定历史会话
python3 /root/agent-inject.py --to codebuddy --session 7ba3ad69 --from agy --intent "代码Review" --msg "请Review /root/demo.py"

# 为目标 Agent 开辟新会话注入
python3 /root/agent-inject.py --to qoder --new --from agy --intent "新建独立任务" --msg "请调研xx框架"
```

### 2. 注入消息标准格式 (Preamble Protocol)
```markdown
【跨 Agent 协作消息】
- 发送者: agy
- 发送时间: 2026-08-26 14:00:00
- 来源会话: bec95485-70df-4435-a9b6-4dd0811b1110 (reply-to)
- 协作主题/意图: UI 测试与功能验证

【任务详情与上下文】
任务具体说明、修改的文件路径、测试要求...
```

### 3. 用户如何接管会话
当注入完成后，目标 Agent 已生成解答并落盘，用户在终端直接恢复该会话即可继续互动：
```bash
qoder -r <会话ID>
codebuddy -r <会话ID>
agy --conversation <会话ID>
```

---

## 互相读取会话记录

### 1. 统一聚合查看器（推荐）
```bash
python3 /root/agent-log-viewer.py                     # 聚合按时间列出最近会话
python3 /root/agent-log-viewer.py -g 关键词           # 全局搜索三方所有会话
python3 /root/agent-log-viewer.py <会话ID> [-s]       # 自动识别 Agent 并查看详情/摘要
```

### 2. 各 Agent 独立查看器
三个查看器同款 CLI（无参=列表；`-g 关键词` 全局搜索或过滤；`-s` 摘要；传 ID 前缀或路径查看单会话）：

```bash
python3 /root/agy-log-viewer.py [id] [-s] [-g kw] [-T]
python3 /root/qoder-log-viewer.py [id] [-s] [-g kw]
python3 /root/codebuddy-log-viewer.py [id] [-s] [-g kw]
```

---

## 共享基建

- **远程运维**: 统一走 `xssh`（勿用裸 ssh），长任务加 `--timeout`，大输出加 `--max-lines`
- **模型下载**: 一律走 76 (`192.2.56.76:/data/AI_model/`) 用 modelscope；test03 只读
- **GPU 测试**: 沐曦 `mx-smi`、海光 `hy-smi`（可能不在 PATH，先 `which`）；跑测试前先确认 GPU 空闲；结果记录 模型名/精度/TP/batch/seq/throughput/TTFT/TPOT

## 环境踩坑（跨 agent 通用）

- **WSL 网络红线**: 不 `ip link set down/up`、不 flush 路由、不改 `.wslconfig` 自己重启——网卡带 `noprefixroute`，link 后路由不重建直接断网。改文件让用户 `wsl --shutdown`
- **H3C TLS 拦截**: 本机 HTTPS 被中间人拦，curl 直连报证书错 → 加 `-k` + sha256 校验；node/curl 内网直连会被系统代理劫持，用 `--noproxy '*'`
- **venv 缺 ensurepip**: Debian 系统 `python3 -m venv` 可能失败（提示装 python3.x-venv），改用 `uv venv` + `uv pip install` 最省事（marimo 场景实测）
- **corepack**: 0.24 与 Node 22 不兼容报 `ERR_VM_DYNAMIC_IMPORT_CALLBACK_MISSING` → `npm install -g --force corepack@0.31.0`
- **pnpm**: `ERR_PNPM_NO_IMPORTER_MANIFEST_FOUND` = 在找本地脚本，实际要全局 CLI；全局存储路径不贯通会报 `ERR_MODULE_NOT_FOUND`，需重建软链接
- **npm 全局更新**: `ENOTEMPTY` = 残留临时目录（如 `.codebuddy-code-XXX`），清理后重装
