---
name: lane
version: 2.3.0
display_name: "Lane（四 Agent 泳道协同与会话管理）"
description: Lane（泳道模式）——四 Agent（agy / qoder / CodeBuddy / Cline）异构协同总线：跨 Agent 会话注入、多工作区会话查看、会话管理（改名/打标/清理/回收站）、轻量 Web 控制面板、共享基建与避坑规范。触发词：lane、泳道、协作、会话记录、查看记录、会话注入、另一个agent、互通、handoff、黑板、面板、会话管理、清理会话、cline
keywords: lane,泳道,协作,会话注入,agent-inject,qoder,agy,antigravity,codebuddy,cline,会话记录,log-viewer,agent-log-viewer,工作区,互通,分工,handoff,黑板,xssh,GPU,会话管理,panel,改名,打标签,清理
---

# 🏊‍♂️ Lane —— 四 Agent 泳道协同与会话管理手册

## 一、 泳道分工

| 泳道 | 主攻领域 | 沉淀上下文 |
|---|---|---|
| **agy** | 系统架构 / 核心后端 / GPU 压测 | 系统设计、服务端实现、算力基建 |
| **qoder** | 前端 UI / 组件开发 / 界面交互测试 | Web 前端、组件库、用户交互 |
| **CodeBuddy** | 代码审查 (Review) / 安全审计 / 文档交付 | 代码质量、安全合规、工程文档 |
| **Cline** | 全能执行 / 深度工程 / 终端自动化任务 | 终端排障、命令行编排、项目巡检 |
| **人类 (You)** | 调度中枢 / 随时交互接管 | 业务决策、随时切入任意泳道 |

---

## 二、 CLI 极简速查 (`/root/.local/bin/lane`)

### 1. 跨泳道派活（自动注入最新会话）
```bash
lane qoder "我是 agy，已写好后端，请进行前端 UI 测试"
lane codebuddy "请对 /root/demo.py 进行安全审查"
lane cline "排查服务器配置并执行自动化验证"
lane agy "继续排查内存泄漏问题"
```

### 2. 精准会话注入（指定会话 / 新开会话）
```bash
lane inject --to qoder --session <ID> --intent "UI测试" --msg "任务说明"
lane inject --to cline --session <ID> --intent "自动化执行" --msg "任务说明"
lane inject --to codebuddy --new --intent "新建审查" --msg "任务说明"
```

### 3. 多泳道看板（跨 Agent 查看与检索）
```bash
lane                         # 查看最近有效会话（默认自动过滤空会话/指令/归档/回收站）
lane -w metax-workbench      # 按项目工作区筛选
lane -t GPU                  # 按标签筛选
lane -a qoder                # 仅看指定 Agent
lane -g "关键词"              # 全局跨 Agent 搜索
lane -A                      # 查看全量会话（包含空会话/控制指令/归档/回收站）
lane --trash                 # 查看回收站会话
lane --archived              # 查看已归档会话
lane view <会话ID> [-s]      # 查看指定会话对话流或摘要 (-s)
```

### 4. 会话管理（改名 / 归类 / 垃圾清理 / 回收站）
```bash
lane rename <ID> "新标题"     # 重命名会话（覆盖原生标题）
lane tag <ID> add "GPU"      # 给会话打标签/分类
lane tag <ID> rm "废弃"      # 移除标签
lane pin <ID> [--off]        # 🌟 置顶 / 取消置顶
lane archive <ID> [--off]    # 📦 归档 / 取消归档（移出默认列表）
lane rm <ID> [-f]            # 🗑️ 移入回收站（加 -f 物理粉碎底层文件）
lane restore <ID>            # ↩️ 从回收站还原会话
lane clean --empty [--dry-run] # 🧹 批量扫描并清理空会话与控制指令残留
```

### 5. 轻量级 Web 控制面板（零外部依赖）
```bash
lane panel [--port 3457]     # 启动可视化控制台 (http://localhost:3457)
```
- 支持批量选择删除、行内修改标题、一键添加标签、右侧抽屉式查看对话历史、一键复制接管命令。

### 6. 人工现场接管
```bash
lane resume qoder [ID]       # 智能提示带工作区参数的接管命令
lane resume cline [ID]       # 提示 cline --id <id> -i
```

---

## 三、 注入协议包头 (Preamble Protocol)

跨 Agent 注入的消息自动附带结构化包头，便于目标 Agent 解析：
```markdown
【跨 Agent 协作消息】
- 发送者: agy
- 发送时间: 2026-08-28 14:00:00
- 来源会话: <ID> (reply-to)
- 协作主题/意图: UI 测试与功能验证

【任务详情与上下文】
任务说明、修改文件、测试要求...
```

---

## 四、 共享基建与避坑红线

- **工作区隔离**: Qoder 会话强绑定工作区目录，跨目录 resume 须带 `--cwd <dir>`。
- **WSL 网络红线**: 绝对不要 `ip link set down/up`、不 flush 路由、不自改 `.wslconfig` 重启。改文件后由人类手动 `wsl --shutdown`。
- **远程运维**: 统一用 `xssh`，长任务加 `--timeout`，大输出加 `--max-lines`。
- **模型下载**: 一律存 76 (`192.2.56.76:/data/AI_model/`)，走 modelscope。
- **GPU 测试**: 沐曦 `mx-smi`、海光 `hy-smi`；测试前先确认显卡空闲，记录 `模型/精度/TP/batch/seq/throughput/TTFT/TPOT`。

