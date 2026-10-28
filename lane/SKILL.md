---
name: lane
version: 2.7.0
display_name: "Lane（多 Agent 泳道协同总线与极速会话管理）"
description: Lane（泳道模式）——多 Agent（agy / qoder / Trae / CodeBuddy / Cline / Reasonix）异构协同总线，覆盖 GUI 桌面端与 CLI：Zig 0.14 高性能极速检索引擎与倒排索引（lane-search P0）、跨 Agent 会话注入、多工作区会话查看、会话管理（改名/打标/清理/回收站）、轻量 Web 控制面板、共享基建与避坑规范。触发词：lane、lane-fast、lane-search、泳道、协作、会话记录、查看记录、会话注入、倒排索引、全文搜索、另一个agent、互通、handoff、黑板、面板、会话管理、清理会话、qoder、trae、agy、antigravity、cline、reasonix
keywords: lane,lane-fast,lane-search,zig,倒排索引,泳道,协作,会话注入,agent-inject,qoder,trae,agy,antigravity,codebuddy,cline,reasonix,会话记录,log-viewer,agent-log-viewer,工作区,互通,分工,handoff,黑板,xssh,GPU,会话管理,panel,改名,打标签,清理,GUI
---

# 🏊‍♂️ Lane —— 多 Agent 泳道协同与极速会话管理手册

## 一、 泳道分工

| 泳道 | 主攻领域 | 沉淀上下文 |
|---|---|---|
| **agy / Reasonix** | 系统架构 / 核心后端 / GPU 压测 / 复杂推理 | 系统设计、服务端实现、算力基建、高并发压测 |
| **qoder** | 前端 UI / 组件开发 / 界面交互测试 | Web 前端、组件库、用户交互与还原 |
| **Trae** | IDE 内快速问答 / 项目内小步修改 | 完整对话（解密只读镜像）＋ memory 摘要回退 |
| **CodeBuddy** | 代码审查 (Review) / 安全审计 / 办公文档交付 | 代码质量、安全防御、Fuzz 测试、工程文档 |
| **Cline** | 全能执行 / 深度工程 / 终端自动化任务 | 终端排障、命令行编排、自动化巡检 |
| **人类 (You)** | 调度中枢 / 随时交互接管 | 业务决策、随时切入任意泳道 |

---

## 附：GUI 会话数据源（本机 macOS）

macOS 运行时脚本与 Zig 源码存放在 `macos/`（Linux 继续用 `scripts/` 与 `lane-fast/`）。

| Agent | 数据位置 | 可读内容 | 注入 |
|---|---|---|---|
| Qoder GUI / CLI | `~/.qoder-cn/projects/*/*.jsonl` | 完整对话 | `lane qoder`（走自带 CLI，经 `~/.local/bin/qoder` 包装） |
| Antigravity GUI | `~/.gemini/antigravity/conversations/*.db` + `brain/` | 完整对话 | 不支持（GUI 会话无法被 agy CLI 接管） |
| Antigravity CLI | `~/.gemini/antigravity-cli/conversations/*.db` | 完整对话 | `lane agy`（仅 CLI 会话） |
| Trae CN | `~/.agent/trae/trae.db`（由加密库解密导出） | 完整对话（用户/助手/工具步骤） | 不支持 |
| CodeBuddy / Cline | `~/.codebuddy/projects`、`~/.cline/data` | 完整对话 | `lane codebuddy` / `lane cline` |

Trae 的完整对话在 `~/Library/Application Support/Trae CN/ModularData/ai-agent/database.db`，是 SQLCipher 加密库。`trae_db.py` 用固定派生密钥解密到 `~/.agent/trae/trae.db`（0600），并导出 `~/.agent/trae/projects/<project>/<sid>.jsonl`（0600）供 lane-fast 列表与检索；密钥在 `~/.agent/trae/key.hex`（0600）。解密失败时自动回退到 memory 摘要。Antigravity GUI 与 CLI 共用同一套 SQLite schema，`lane` 会把两边会话合并展示。

---

## 二、 CLI 极简速查 (`~/.local/bin/lane`)

底层集成 **Zig 0.14 原生编译引擎 (`lane-fast`)**，内置持久化倒排索引（lane-search P0）与流式扫描，对会话列表与全文检索提供 **毫秒级极速响应**。

### 1. 跨泳道派活（自动注入目标 Agent 最新会话）
```bash
lane qoder "我是 agy，已写好后端，请进行前端 UI 测试"
lane codebuddy "请对 /root/demo.py 进行安全审查"
lane cline "排查服务器配置并执行自动化验证"
lane agy "继续排查内存泄漏问题"       # 只注入 agy CLI 会话；Antigravity GUI 会话请人工接管
lane trae "..."                      # Trae GUI 暂不支持命令行注入
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
lane -a agy                  # 只看 Antigravity（GUI + CLI 合并）
lane -a qoder                # 只看 Qoder
lane -a trae                 # 只看 Trae（完整对话）
lane -w metax-workbench      # 按项目工作区筛选
lane -g "关键词"              # ⚡ 全量跨 Agent 极速检索（自动走倒排索引 1.3ms，损坏/缺失时平滑降级 Grep）
lane -g "词A" -g "词B"       # ⚡ 多关键词 AND 组合检索 (倒排索引求交，同时包含两词才命中)
lane-fast search <kw>...     # ⚡ 直接调用 Zig 倒排索引引擎查询 (支持多词 AND、-n、-w、--bench)
lane-fast index [--force]    # 🔨 手动触发倒排索引构建/全量重建 (~/.agent/lane-search.idx)
lane sync                    # 🔐 强制刷新 Trae 解密镜像 + 导出 JSONL（平时 lane 会自动增量同步）
lane view <会话ID/关键词> [-s] # 查看指定会话卡片序列与对话流或摘要 (-s)
lane view <会话ID> --full     # 对话流全文输出（关闭 300 字截断；截断时必带「已截断：全文 N 字」标记）
lane msg <会话ID> <序号> [--line] # ⭐ 取单条消息全文（永不截断）；序号默认按 step_index，--line 按物理行号，两口径自动兜底
lane -t GPU                  # 按标签筛选 (调用管理引擎)
lane -A                      # 查看全量会话（包含空会话/控制指令/归档/回收站）
lane --trash                 # 查看回收站会话
lane --archived              # 查看已归档会话
```

### 3.1 LANE_CARD 结构化日记卡片约定 (P3a)
Agent 在交互中每达成一个明确里程碑时，可在末尾附带：
`<!-- LANE_CARD: {action: "...", conclusion: "...", status: "done", entities: ["146", "zig"]} -->`
*(注意：文档中请保持非合法 JSON 占位写法，切勿出现合法 JSON 示例，避免被收割器误收)*
`lane` 执行时可增量收割至 `~/.agent/lane-summary.db`，并在 `lane view <ID>` 中展示历史时间线节点。当前 macOS 运行时尚未把 harvester 接入 `lane` 主命令，需手动执行 `python3 ~/.agent/scripts/lane_harvester.py --backfill`。

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
lane resume qoder [ID]       # 智能提示带工作区参数的接管命令（~/.local/bin/qoder 已包装）
lane resume codebuddy [ID]   # 智能提示 codebuddy -r <id>
lane resume agy [ID]         # 智能提示 agy --conversation <id>
lane resume cline [ID]       # 智能提示 cline --id <id> -i
lane resume trae [ID]        # 提示打开 Trae CN / 查看完整对话
```

---

## 三、 Zig 0.14 极速核心说明 (`lane-fast`)

- **位置**: `~/.local/bin/lane-fast` (源码位于 `~/.agent/lane-fast-src/src/`)
- **体积**: 约 1.7 MB 独立二进制，静态链接 Homebrew SQLite（`/opt/homebrew/opt/sqlite/lib/libsqlite3.a`），无运行时依赖。
- **macOS 构建**:
  ```bash
  cd ~/.agent/lane-fast-src/src
  zig build-exe main.zig -target aarch64-macos.15.0 -lc \
    /opt/homebrew/opt/sqlite/lib/libsqlite3.a -O ReleaseFast -femit-bin=/tmp/lane-fast
  install -m 755 /tmp/lane-fast ~/.local/bin/lane-fast
  ```
  （macOS 27 的 SDK 只给 arm64e 提供 libsqlite3.tbd，直接 `-lsqlite3` 会链接失败，故静态链接 Homebrew 版本。）
- **倒排索引引擎 (lane-search P0)**:
  - **索引文件**: `~/.agent/lane-search.idx`（LEB128 Varint 压缩 + Delta 编码 + 4 字节严格对齐）；
  - **零拷贝查询**: `mmap` 配合预建固定宽度偏移表二分查找，纯索引查询耗时 < 0.1ms；
  - **增量段合并**: 记录 mtime 与 size，变动会话单段提取更新，未变动会话零重复计算；
  - **自愈与降级**: Header/Data 结构校验与损坏自动容错，故障秒级静默 Fallback 到 Grep 流式扫描。
- **安全与健壮性**:
  - **Arena Allocator**: 瞬时分配、随进程退出整块释放，零内存碎片与泄漏；
  - **SQLite 优雅解包**: 无论 open 成功与否安全释放句柄，支持只读模式读取 agy 摘要；
  - **无缝滑动窗口 (Sliding Overlap)**: 消除 32KiB 跨块边界接缝处关键字漏检假阴性；
  - **Fuzz 级防御**: 40KB ~ 100KB 超长关键词 Payload 强类型安全防护；
  - **UTF-8 安全截断**: 逐 codepoint 截断，中文与特殊字符绝无乱码。

---

## 四、 共享基建与避坑红线

- **工作区隔离**: Qoder 会话强绑定工作区目录，跨目录 resume 须带 `--cwd <dir>`。
- **Qoder 数据目录**: 中文版在 `~/.qoder-cn/projects`（不是 `~/.qoder`）。CLI 用 `~/.local/bin/qoder` 包装，自动选最新 `qoderclicn-*` 并清除桌面端注入的 `QODER_AGENT_SDK_*`，否则 CLI 会误入 SDK 模式报 `sdk_invalid_args`。
- **Trae 加密库**: `database.db` 是 SQLCipher；`trae_db.py` 用固定密钥解密到 `~/.agent/trae/trae.db`，并导出 JSONL 索引。密钥/镜像/导出均为 0600。Trae 升级若更换密钥，`lane sync` 会报错并沿用旧镜像。明文镜像含完整对话，勿外传。
- **Antigravity GUI 注入**: GUI 会话无法被 `agy` CLI 接管；`lane agy` 的注入只选 CLI 目录下最新会话，`lane view` 两边都能看。
- **WSL 网络红线**: 绝对不要 `ip link set down/up`、不 flush 路由、不自改 `.wslconfig` 重启。改文件后由人类手动 `wsl --shutdown`。
- **模型启动脚本铁律**: 启动脚本千万不能复用，每个模型服务必须拥有专属独立脚本（一对一绑定），删除时归档入 `scripts_archived/`。
