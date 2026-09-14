---
name: lane
version: 2.4.0
display_name: "Lane（多 Agent 泳道协同总线与极速会话管理）"
description: Lane（泳道模式）——五 Agent（agy / Reasonix / qoder / CodeBuddy / Cline）异构协同总线：Zig 0.14 高性能极速检索引擎（lane-fast，1.5ms 毫秒级响应）、跨 Agent 会话注入、多工作区会话查看、会话管理（改名/打标/清理/回收站）、轻量 Web 控制面板、共享基建与避坑规范。触发词：lane、lane-fast、泳道、协作、会话记录、查看记录、会话注入、另一个agent、互通、handoff、黑板、面板、会话管理、清理会话、cline、reasonix
keywords: lane,lane-fast,zig,泳道,协作,会话注入,agent-inject,qoder,agy,antigravity,codebuddy,cline,reasonix,会话记录,log-viewer,agent-log-viewer,工作区,互通,分工,handoff,黑板,xssh,GPU,会话管理,panel,改名,打标签,清理
---

# 🏊‍♂️ Lane —— 多 Agent 泳道协同与极速会话管理手册

## 一、 泳道分工

| 泳道 | 主攻领域 | 沉淀上下文 |
|---|---|---|
| **agy / Reasonix** | 系统架构 / 核心后端 / GPU 压测 / 复杂推理 | 系统设计、服务端实现、算力基建、高并发压测 |
| **qoder** | 前端 UI / 组件开发 / 界面交互测试 | Web 前端、组件库、用户交互与还原 |
| **CodeBuddy** | 代码审查 (Review) / 安全审计 / 办公文档交付 | 代码质量、安全防御、Fuzz 测试、工程文档 |
| **Cline** | 全能执行 / 深度工程 / 终端自动化任务 | 终端排障、命令行编排、自动化巡检 |
| **人类 (You)** | 调度中枢 / 随时交互接管 | 业务决策、随时切入任意泳道 |

---

## 二、 CLI 极简速查 (`/root/.local/bin/lane`)

底层集成 **Zig 0.14 原生编译引擎 (`lane-fast`)**，对会话列表与全局关键字搜索提供 **百倍加速（1.5ms ~ 8ms）**。

### 1. 跨泳道派活（自动注入目标 Agent 最新会话）
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

### 3. 多泳道看板（跨 Agent 查看与极速检索）
```bash
lane                         # ⚡ 极速查看最近会话（显示 STAT 状态图标 ✅🔄❌ 与 SUMMARY 摘要卡片）
lane -n 50                   # 查看最近 50 条会话
lane -w metax-workbench      # 按项目工作区筛选
lane -g "关键词"              # ⚡ 全量跨 Agent 全文无缝检索 (跨 256KB 大缓冲滑动无漏检)
lane -g "词A" -g "词B"       # ⚡ 多关键词 AND 组合检索 (同时包含词A和词B才命中)
lane view <会话ID/关键词> [-s] # 查看指定会话卡片序列与对话流或摘要 (-s)
lane -t GPU                  # 按标签筛选 (调用管理引擎)
lane -A                      # 查看全量会话（包含空会话/控制指令/归档/回收站）
lane --trash                 # 查看回收站会话
lane --archived              # 查看已归档会话
```

### 3.1 LANE_CARD 结构化日记卡片约定 (P3a)
Agent 在交互中每达成一个明确里程碑时，可在末尾附带：
`<!-- LANE_CARD: {action: "...", conclusion: "...", status: "done", entities: ["146", "zig"]} -->`
*(注意：文档中请保持非合法 JSON 占位写法，切勿出现合法 JSON 示例，避免被收割器误收)*
`lane` 执行时自动增量收割至 `/root/.agent/lane-summary.db`，并在 `lane view <ID>` 中展示历史时间线节点。

### 4. 会话管理（改名 / 归类 / 垃圾清理 / 回收站）
```bash
lane title <ID>              # ✨ 本地 AI 智能提取 Git Commit 规范标题
lane title --all [--dry-run] # ✨ 批量对未命名会话生成规范标题 (Git Commit 风格)
lane rename <ID> "新标题"     # 手工重命名会话（覆盖原生标题）
lane tag <ID> add "GPU"      # 给会话打标签/分类
lane tag <ID> rm "废弃"      # 移除标签
lane pin <ID> [--off]        # 🌟 置顶 / 取消置顶
lane archive <ID> [--off]    # 📦 归档 / 取消归档（移出默认列表）
lane rm <ID> [-f]            # 🗑️ 移入回收站（加 -f 物理粉碎底层文件）
lane restore <ID>            # ↩️ 从回收站还原会话
lane clean --empty [--dry-run] # 🧹 扫描并清理空会话、控制指令及 <20KB 微型会话
```

### 5. 轻量级 Web 控制面板（零外部依赖）
```bash
lane panel [--port 3457]     # 启动可视化控制台 (http://localhost:3457)
```

### 6. 人工现场接管
```bash
lane resume qoder [ID]       # 智能提示带工作区参数的接管命令
lane resume codebuddy [ID]   # 智能提示 codebuddy -r <id>
lane resume agy [ID]         # 智能提示 agy --conversation <id>
lane resume cline [ID]       # 智能提示 cline --id <id> -i
```

---

## 三、 Zig 0.14 极速核心说明 (`lane-fast`)

- **位置**: `/root/.local/bin/lane-fast` (源码位于 `/root/metax-workbench/lane-fast/`)
- **体积**: 1.3 MB 独立静态二进制，零外部依赖。
- **安全与健壮性**:
  - **Arena Allocator**: 瞬时分配、随进程退出整块释放，零内存碎片与泄漏；
  - **SQLite 优雅解包**: 无论 open 成功与否安全释放句柄，支持只读模式读取 agy 摘要；
  - **无缝滑动窗口 (Sliding Overlap)**: 消除 32KiB 跨块边界接缝处关键字漏检假阴性；
  - **Fuzz 级防御**: 40KB ~ 100KB 超长关键词 Payload 强类型安全防护；
  - **UTF-8 安全截断**: 逐 codepoint 截断，中文与特殊字符绝无乱码。

---

## 四、 共享基建与避坑红线

- **工作区隔离**: Qoder 会话强绑定工作区目录，跨目录 resume 须带 `--cwd <dir>`。
- **WSL 网络红线**: 绝对不要 `ip link set down/up`、不 flush 路由、不自改 `.wslconfig` 重启。改文件后由人类手动 `wsl --shutdown`。
- **模型启动脚本铁律**: 启动脚本千万不能复用，每个模型服务必须拥有专属独立脚本（一对一绑定），删除时归档入 `scripts_archived/`。
