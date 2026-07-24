# CHANGELOG

## v3.5.0 (2026-07-24)

### 新增

- **`ssh_pty.py` PTY 交互式终端**：基于 pyte 终端模拟器 + Paramiko invoke_shell，支持 mysql CLI、python REPL、tail -f、交互式问答等多轮对话场景。pyte 解析 ANSI 转义序列，输出干净文本而非乱码。支持特殊按键注入（ctrl+c、方向键、Tab 等 30+ 按键）。`xssh p` 子命令入口。
- **四种执行模式**：一次性执行 → 持久 Shell → PTY 交互式 → 交互式终端，按场景选择。

### 修复

- **`ssh_shell.py` argparse 子命令与兼容模式冲突**：子命令（session/exec/stop/list）和兼容模式（`<alias> ["<cmd>"]`）共用 positional `alias` 参数导致 argparse 解析错误。采用 Method C：先检测 `sys.argv[1]` 是否属于已知子命令，不是则走兼容模式解析器，避免冲突。`ssh_pty.py` 同步修复。
- **`ssh_shell.py` marker 提取误匹配命令回显行**：`echo "marker" $?` 的回显行（含 prompt+`echo`+`$?`）被识别为 marker 输出行，导致退出码提取为 `$?`（ValueError → fallback 0）而非真实退出码。修复：用 `startswith(marker)` 代替 `in`，只匹配以 marker 开头的纯输出行。
- **`ssh_shell.py` prompt 行残留**：shell prompt 行（`root@host:~#`、`bash-5.1#` 等）未被 `cleaned_lines` 和 `_clean_shell_output` 过滤，导致 stdout 残留 prompt。修复：新增 `_PROMPT_PATTERN` 正则，在 `cleaned_lines` 和 `_clean_shell_output` 两层都过滤 prompt 行。

---

## v3.4.0 (2026-07-21)

### 新增

- **`xssh` 全局快捷入口**：Bash wrapper 脚本（`~/bin/xssh`），智能分发到 ssh-skill 所有子脚本。支持 13 个子命令及简写（d=docker, k=k8s, s=shell, p=pty, c=connect, up=upload, dl=download, tx=transfer, m=multi, t=tunnel）。默认无子命令走 ssh_execute.py。
- **`ssh_multi.py` 多连接管理器**：Workspace + Connection + Note 三概念模型。支持 create/add/remove/exec/status/note/check/list/delete 9 个命令。解决多机操作时"窗口泛滥、状态丢失、上厕所回来忘了"的问题。
- **`ssh_multi.py` `--watch` 自动刷新**：`xssh m status <workspace> --watch` 每 N 秒自动刷新状态面板（默认 3 秒），Ctrl+C 退出。支持 `--interval` 自定义间隔，支持 `--watch --check` 实时 ping 检测。
- **`ssh_daemon.py` 连接管理增强**：新增 `list` 命令（全局概览 + 实时 ping 检测），新增 `stop --all` 命令。

### 改进

- **SKILL.md 重构**：从冗长的 v3.3 版本重构为简洁的 dispatch 风格，新增多连接管理完整章节、xssh 快捷入口章节，新增 Docker/K8s/多连接完成检查条件。
- **`__init__.py` 版本号统一**：从 0.1.0 → 3.3.0 → 3.4.0 保持一致。

### 修复

- **`ssh_k8s_exec.py` 参数冲突**：`--dry-run` 和 `--namespace` 都使用 `-n` 简写，去掉 dry-run 的 `-n`。
- **`ssh_k8s_exec.py` 快捷指令**：`k8s-get-pods` 等快捷命令拼出 `kubectl kubectl get pods` 的重复前缀，已修复。
- **英文 help 中文化**：`ssh_execute.py`、`ssh_upload.py`、`ssh_download.py` 的 argparse description 和 help 文本从英文改为中文。

---

## v3.3.0 (2026-06-23)

### 新增

- **`ssh_tunnel.py` 端口转发/隧道管理**：支持 local/remote/dynamic 三种隧道模式，原生 SSH 命令驱动，进程管理（start/stop/status/list）。
- **`ssh_shell.py` 持久 Shell 会话**：基于 Paramiko `invoke_shell()`，socket daemon 接收命令请求，跨命令保持状态（cwd/env），标记法提取退出码。
- **`ssh_connect.py` 交互式 Shell**：通过 `os.execvp` 调用原生 `ssh -t`，支持 SSH Agent Forwarding (`-A`) 自动检测。
- **`ssh_docker_exec.py` Docker 容器执行**：自动 `bash -l -c` 解决两层 non-interactive shell 环境变量丢失，支持 `-e` 传环境变量。
- **`ssh_k8s_exec.py` Kubernetes Pod 执行**：默认 `sh -c` 包装，支持 namespace/container/env/logs，内置 7 个 kubectl 快捷指令。
- **SKILL.md 大幅扩展**：新增端口转发/隧道、交互式 Shell、持久 Shell 会话、Docker 容器执行、K8s Pod 执行完整章节及环境变量说明。

### 改进

- **`ssh_daemon.py` 守护进程管理**：新增 `list` 和 `stop --all` 命令。
- **`ssh_shell.py` 会话管理**：v1.0 持久 Shell 会话工具。需 TTY 时使用 `ssh_connect.py`。

### 修复

- **[审查] `lib/paramiko_client.py` 吞异常**：全部 `except: pass` 替换为 `logger.warning()`/`logger.debug()`。
- **[审查] 结构化日志**：引入 `logging.getLogger` 替代 `print(... file=sys.stderr)`。
- **[审查] ConnectionPool 锁粒度**：网络 I/O 移出锁，仅 dict 读写在锁内；新增 `size` 属性和周期清理。
- **[审查] 版本不一致**：`lib/__init__.py` 中 `__version__` 从 0.1.0 改为 3.3.0。

---

## v3.2.0 (2026-06)

### 已有能力

- **守护进程长连接**：Paramiko 长连接 + 本地 TCP socket daemon，响应时间 ~0.12s。
- **SFTP 文件传输**：断点续传、进度显示、目录递归上传/下载。
- **服务器间直接传输**：直连/流式/混合/rsync 四种传输模式，无需本地中转。
- **跳板机支持**：ProxyJump 多级跳板，通过 `~/.ssh/config` 自动识别。
- **批量并发操作**：按环境/标签过滤，`ThreadPoolExecutor` 并行执行。
- **配置管理**：标准 OpenSSH config 格式 + 注释元数据（描述、环境、标签、密码）。
- **密钥管理**：ssh_key_manager.py / deploy_pubkey.py / migrate_to_key_auth.py。
- **自动错误恢复**：SSH 连接断开自动重连，最多 3 次。
