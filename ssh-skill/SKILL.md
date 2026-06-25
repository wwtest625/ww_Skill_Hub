---
name: ssh-skill
version: 3.3.0
description: "SSH 操作统一入口。远程连接、文件传输、端口转发、Docker/K8s 执行、跳板机穿透。禁止直接用 bash ssh/scp。触发词：任何 SSH/远程服务器/部署/隧道相关操作。"
allowed-tools: Bash, Read, Write, Glob
keywords: SSH,服务器,远程,连接,命令,上传,下载,文件传输,跳板机,批量,集群,deploy,部署,运维,登录,执行,查看,检查,管理,操作,访问,传输,迁移,服务器间,隧道,端口转发,socks,shell,会话,交互式,docker,容器,docker exec,k8s,kubernetes,kubectl,pod,容器编排
---

# SSH Skill v3.3

高性能 SSH 操作技能，支持守护进程长连接、端口转发/隧道、持久 Shell 会话、自动连接复用、跳板机、批量并发、服务器间直接传输、自动错误恢复。

## 快捷命令

当用户通过 `/ssh-skill <参数>` 调用本 skill 时，根据参数执行对应操作：

### `/ssh-skill list`

列出所有已配置的服务器。执行以下步骤：

1. 运行命令获取数据：
```bash
python ~/.workbuddy/skills/ssh-skill/scripts/ssh_config_manager_v3.py list-servers
```
2. 解析返回的 JSON 数据
3. 以 **Markdown 表格** 格式展示，列：序号、别名、备注(description)、标签(tags)、位置(location)、认证方式(auth)、用户名(user)
4. 在表格末尾显示服务器总数

表格示例格式：
```
| # | 别名 | 备注 | 标签 | 位置 | 认证 | 用户名 |
|---|------|------|------|------|------|--------|
| 1 | mgmt-01 | 管理服务器 | 管理,Warpgate | 丰台机房 | 密钥 | root |
```

### `/ssh-skill find <关键词>`

查找匹配的服务器，格式同 list。

### `/ssh-skill help`

展示 SSH Skill 的帮助文档。以 Markdown 格式输出以下内容：

**SSH Skill v3.3 - 高性能 SSH 操作技能**

**核心特点：**
- 守护进程长连接：首次连接后自动启动守护进程，后续命令响应时间从 ~0.45s 降至 ~0.12s
- 端口转发/隧道：支持本地转发、远程转发、SOCKS 代理，守护进程模式管理
- 持久 Shell 会话：状态保持的交互式 shell，环境变量和工作目录跨命令持久
- Docker 容器执行：自动处理两层 non-interactive shell 环境变量问题，`bash -l -c` 包裹
- Kubernetes Pod 执行：kubectl exec 封装，自动 `sh -c` 包装，支持 namespace 和多容器 Pod
- 自动连接复用：多个 codex Code 实例可共享同一守护进程
- SFTP 高级传输：支持断点续传、进度显示、目录递归上传/下载
- 服务器间直接传输：支持服务器到服务器的文件直接传输，无需本地中转
- 跳板机支持：通过 ProxyJump 自动处理多级跳板机
- 批量并发操作：支持对多台服务器并发执行命令
- 自动错误恢复：SSH 连接断开自动重连（最多 3 次）

**快捷命令：**
- `/ssh-skill list` - 列出所有已配置的服务器
- `/ssh-skill find <关键词>` - 查找匹配的服务器
- `/ssh-skill transfer <源> <源路径> <目标> <目标路径>` - 服务器间文件传输
- `/ssh-skill tunnel start <别名> <类型> <映射>` - 启动端口转发隧道
- `/ssh-skill tunnel list` - 列出所有隧道
- `/ssh-skill tunnel stop <ID>` - 停止隧道
- `/ssh-skill shell <别名>` - 启动持久 Shell 会话
- `/ssh-skill shell <别名> "<命令>"` - 在持久 Shell 中执行命令
- `/ssh-skill connect <别名>` - 交互式 SSH 登录（打开终端）
- `/ssh-skill docker <别名> <容器> "<命令>"` - 在远程容器中执行命令
- `/ssh-skill docker <别名> <容器> --shell` - 交互式进入容器
- `/ssh-skill k8s <别名> <pod名> "<命令>"` - 在 k8s Pod 中执行命令
- `/ssh-skill k8s <别名> <pod名> --shell` - 交互式进入 Pod
- `/ssh-skill k8s <别名> k8s-get-pods` - 查看 Pod 列表
- `/ssh-skill help` - 显示此帮助信息

**常用操作：**

1. 执行远程命令：
   ```
   在 <别名> 上执行 <命令>
   ```

2. 上传文件：
   ```
   上传 <本地路径> 到 <别名> 的 <远程路径>
   ```

3. 下载文件：
   ```
   从 <别名> 下载 <远程路径> 到 <本地路径>
   ```

4. 服务器间传输：
   ```
   从 <源别名> 传输 <路径> 到 <目标别名> 的 <路径>
   将 <别名A> 的文件迁移到 <别名B>
   ```

5. 批量操作：
   ```
   在所有服务器上执行 <命令>
   在生产环境服务器上执行 <命令>
   ```

6. Docker 容器执行：
   ```
   在 <别名> 的 <容器名> 中执行 <命令>
   进入 <别名> 的 <容器名> 容器
   查看 <别名> 的 <容器名> 环境变量
   ```

7. 持久 Shell 会话（AI agent 状态保持）：
   ```
   在 <别名> 上打开 shell 会话并执行部署命令
   ```

8. 交互式 Shell 登录（手动操作）：
   ```
   用 ssh 登录到 <别名>
   连上 <别名>
   ```

9. Kubernetes Pod 执行：
   ```
   在 <别名> 的 Pod <pod名> 中执行 <命令>
   查看 <别名> 的集群 Pod 状态
   进入 <别名> 的 Pod <pod名>
   ```

**配置管理：**
- 配置文件位置：`~/.ssh/config`
- 使用标准 OpenSSH 格式 + 注释元数据
- 支持密钥认证和密码认证
- 支持 ProxyJump 跳板机配置

**性能对比：**
- 直连模式：单次命令 ~0.45s，连续 10 条 ~4.5s
- 守护进程模式：单次命令 ~0.12s，连续 10 条 ~1.2s

更多详细信息请参考 SKILL.md 文档（v3.3.0）。

### 其他参数

将参数作为用户意图理解，按照下方调用规则执行对应的 SSH 操作。

## CRITICAL: 调用规则

### 路径说明

**默认路径**：`~/.workbuddy/skills/ssh-skill/scripts`
- `~` 会自动展开为用户家目录（Windows 和 Linux 通用）
- Windows: `C:\Users\用户名\.workbuddy\skills\ssh-skill\scripts`
- Linux: `/home/用户名/.workbuddy/skills/ssh-skill/scripts`

**项目目录中的 skill**：如果 skill 放在项目的 `.workbuddy/skills/ssh-skill/` 中，使用相对路径：
```
.workbuddy/skills/ssh-skill/scripts
```

**路径自动识别**：Python 的 `os.path.expanduser()` 会自动处理 `~`，无需手动替换。

### 调用格式（唯一正确方式）

**MUST**: 使用 `python ~/.workbuddy/skills/ssh-skill/scripts/脚本名.py` 格式。使用别名（alias）标识服务器。

**NEVER**: 不要使用 `cd` 到脚本目录再执行，不要使用反斜杠 `\`，不要直接写 `ssh` 或 `scp` 命令。

### 执行远程命令

```bash
python ~/.workbuddy/skills/ssh-skill/scripts/ssh_execute.py <别名> "<命令>"
```

可选参数：`--timeout <秒>` `--no-daemon`

ssh_execute.py 会自动检测守护进程：有则走长连接（~0.12s），无则自动启动守护进程。

### 上传文件

```bash
MSYS_NO_PATHCONV=1 python ~/.workbuddy/skills/ssh-skill/scripts/ssh_upload.py <别名> "<本地路径>" "<远程路径>"
```

可选参数：`--resume`（断点续传） `--recursive`（目录递归上传） `--no-progress`（禁用进度输出）

### 下载文件

```bash
MSYS_NO_PATHCONV=1 python ~/.workbuddy/skills/ssh-skill/scripts/ssh_download.py <别名> "<远程路径>" "<本地路径>"
```

可选参数：`--resume`（断点续传） `--recursive`（目录递归下载） `--no-progress`（禁用进度输出）

**CRITICAL**: 上传/下载命令**必须**加 `MSYS_NO_PATHCONV=1` 前缀，防止 Windows MSYS bash 将远程路径（如 `/tmp/file`）转换为 Windows 路径。

### 服务器间传输

```bash
# 自动模式（推荐）- 根据文件大小和网络环境自动选择最优方式
MSYS_NO_PATHCONV=1 python "~/.workbuddy/skills/ssh-skill/scripts/ssh_server_transfer.py" <源别名> "<源路径>" <目标别名> "<目标路径>"

# 强制直连模式（大文件推荐，数据直接在服务器间传输）
MSYS_NO_PATHCONV=1 python "~/.workbuddy/skills/ssh-skill/scripts/ssh_server_transfer.py" <源别名> "<源路径>" <目标别名> "<目标路径>" --mode direct

# 强制流式转发（小文件或服务器间网络不通时）
MSYS_NO_PATHCONV=1 python "~/.workbuddy/skills/ssh-skill/scripts/ssh_server_transfer.py" <源别名> "<源路径>" <目标别名> "<目标路径>" --mode stream

# 混合模式（先尝试直连，失败后自动降级到流式）
MSYS_NO_PATHCONV=1 python "~/.workbuddy/skills/ssh-skill/scripts/ssh_server_transfer.py" <源别名> "<源路径>" <目标别名> "<目标路径>" --mode hybrid

# 使用 rsync（仅直连模式，支持增量同步）
MSYS_NO_PATHCONV=1 python "~/.workbuddy/skills/ssh-skill/scripts/ssh_server_transfer.py" <源别名> "<源路径>" <目标别名> "<目标路径>" --use-rsync
```

可选参数：`--mode <auto|direct|stream|hybrid>`（传输模式） `--use-rsync`（使用 rsync） `--no-progress`（禁用进度） `--size-threshold <MB>`（大小阈值，默认 10） `--timeout <秒>`（超时，默认 300）

**传输模式说明：**

| 模式 | 适用场景 | 数据流向 | 优点 |
|------|----------|----------|------|
| 直连 (direct) | 大文件、服务器间网络通 | 源服务器 → 目标服务器 | 速度快，不占本地带宽 |
| 流式 (stream) | 小文件、网络不通 | 源 → 本地 → 目标（流式） | 无需服务器间配置 |
| 混合 (hybrid) | 不确定环境 | 先尝试直连，失败降级 | 自动适应 |
| 自动 (auto) | 默认 | 智能判断 | 最优选择 |

**CRITICAL**: 服务器间传输命令也**必须**加 `MSYS_NO_PATHCONV=1` 前缀。

### 批量操作

```bash
# 对所有服务器执行
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_cluster.py" "<命令>" --parallel

# 对指定别名列表执行
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_cluster.py" "<命令>" --hosts "DEV-002,DEV-003" --parallel

# 按环境过滤
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_cluster.py" "<命令>" --environment production --parallel

# 按标签过滤
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_cluster.py" "<命令>" --tags "web,nginx" --parallel
```

可选参数：`--timeout <秒>` `--health-check` `--max-workers <数量>`

### 端口转发/隧道管理

使用 `ssh_tunnel.py` 管理 SSH 端口转发隧道。

**启动本地转发：**（将本地端口流量转发到远程目标）
```bash
# 格式：python ssh_tunnel.py start <别名> local <本地端口>:<目标主机>:<目标端口>
# 示例：将本地 8080 转发到目标服务器的 127.0.0.1:3306（MySQL）
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_tunnel.py" start prod-web-01 local 8080:127.0.0.1:3306

# 简写：只指定端口，目标主机默认为 127.0.0.1
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_tunnel.py" start prod-web-01 local 8080:3306

# 命名隧道方便管理
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_tunnel.py" start prod-web-01 local 8080:127.0.0.1:3306 --name mysql-tunnel
```

**启动远程转发：**（将远程端口流量转发到本地目标）
```bash
# 格式：python ssh_tunnel.py start <别名> remote <远程端口>:<目标主机>:<目标端口>
# 示例：将远程 2222 转发到本地 22
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_tunnel.py" start dev-server remote 2222:127.0.0.1:22
```

**启动 SOCKS 代理（动态转发）：**
```bash
# 格式：python ssh_tunnel.py start <别名> dynamic <本地端口>
# 示例：在本地 1080 启动 SOCKS 代理，流量经 bastion 转发
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_tunnel.py" start bastion dynamic 1080
```

**管理隧道：**
```bash
# 列出所有活跃隧道
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_tunnel.py" list

# 查看隧道状态
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_tunnel.py" status <tunnel_id>

# 停止隧道（支持隧道 ID、端口号或别名）
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_tunnel.py" stop <tunnel_id>
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_tunnel.py" stop 8080
```

**隧道类型说明：**

| 类型 | 命令 | 场景 | 数据流向 |
|------|------|------|----------|
| 本地转发 (local) | `-L 本地:目标:端口` | 访问内网服务（DB、Web） | 本地 → SSH → 远程目标 |
| 远程转发 (remote) | `-R 远程:本地:端口` | 暴露本地服务到远程 | 远程 → SSH → 本地目标 |
| 动态转发 (dynamic) | `-D 本地端口` | SOCKS 代理 | 本地 → SSH → 任意目标 |

**CRITICAL**: 隧道使用原生 SSH 命令，需要密钥认证。密码认证暂不支持。

### 交互式 Shell 连接（手动操作）

使用 `ssh_connect.py` 打开真正的交互式 SSH 终端。**与 `ssh_execute.py` 和 `ssh_shell.py` 的核心区别：**

| 脚本 | 场景 | 交互方式 | TTY | 典型用途 |
|------|------|----------|-----|----------|
| `ssh_execute.py` | AI agent 执行命令 | 一次性执行，返回结果 | ❌ | 自动化命令、健康检查 |
| `ssh_shell.py` | AI agent 状态保持 | 持久会话，命令式 | ❌ | 多步部署、交叉命令 |
| `ssh_connect.py` | **人类手动操作** | **实时终端交互** | **✅** | **调试、tail -f、htop、vim** |

**用法：**
```bash
# 交互式登录（进入 shell，可执行任何命令）
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_connect.py" prod-web-01

# 执行命令后退出（仍带 TTY）
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_connect.py" prod-web-01 "tail -f /var/log/app.log"
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_connect.py" dev-server "htop"
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_connect.py" db-master "mysql -e 'show databases;'"

# 干运行：仅显示要执行的 ssh 命令，不实际连接
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_connect.py" prod-web-01 --dry-run
```

**实现原理：** 通过 `os.execvp` 用原生 `ssh -t` 替换当前进程，直接继承终端。配置参数从 SSH config 自动读取。

**SSH Agent Forwarding：** 自动检测本地 `ssh-agent` 状态：
- 若 `ForwardAgent yes` 已在 SSH config 中设置，强制启用 `-A`
- 若本地 `ssh-agent` 在运行且有密钥已加载（`ssh-add -l` 返回 0），自动启用 `-A`
- 远程服务器可通过 agent 借用本地密钥，适合跳板机场景

**CRITICAL**: 
- 需要在**支持 TTY 的终端**中执行（如 Git Bash、CMD、PowerShell）
- AI agent 环境通常无 TTY，此时应使用 `ssh_execute.py` 或 `ssh_shell.py`
- 在 Windows Git Bash 中执行可能需要加 `winpty` 前缀

### 持久 Shell 会话（AI agent 状态保持）

使用 `ssh_shell.py` 管理持久 shell 会话。与 `ssh_execute.py` 的区别：shell 会话**保持状态**（cwd、环境变量、别名等），适合多步操作。与 `ssh_connect.py` 的区别：**无 TTY 交互**，通过 socket daemon 接收命令请求，适合 AI agent 自动化场景。

**常用方式（一行搞定，自动启动会话 + 执行命令）：**
```bash
# 自动开始新会话（或复用已有），执行命令，保持状态到下一个命令
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_shell.py" prod-web-01 "cd /app && git pull"
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_shell.py" prod-web-01 "pwd"  # 输出 /app
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_shell.py" prod-web-01 "systemctl restart app"
```

**显式管理会话：**
```bash
# 启动会话
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_shell.py" session dev-server

# 在指定会话中执行命令（通过 session_id 或别名）
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_shell.py" exec dev-server "cd /data && ls -la"
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_shell.py" exec <session_id> "pwd"  # 输出 /data

# 列出所有活跃会话
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_shell.py" list

# 停止会话
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_shell.py" stop dev-server
```

**使用场景：**
- 部署流水线：`cd /app && git pull && npm install && npm run build && pm2 restart`
- 调试问题：`cd /var/log && tail -n 100 app.log && grep ERROR app.log | head -20`
- 多步操作需要保持工作目录和环境变量状态

**性能对比：**

| 模式 | 适用场景 | 状态保持 | 单次命令延迟 |
|------|----------|----------|-------------|
| `ssh_execute.py` | 一次性独立命令 | ❌ | ~0.12s（守护进程）|
| `ssh_shell.py` | 多步关联操作 | ✅ | ~0.5s |
| `ssh_shell.py` 已启动 | 连续多步操作 | ✅ 复用通道 | ~0.15s |

### Docker 容器执行

使用 `ssh_docker_exec.py` 在远程服务器的 Docker 容器中执行命令。解决两层 non-interactive shell 的环境变量丢失问题：

```
ssh_execute (non-interactive) → docker exec (non-interactive) → 环境变量丢失!
ssh_docker_exec.py → docker exec <容器> bash -l -c "<命令>" → ✅ 环境完整
```

**用法：**
```bash
# 基本用法：进容器执行命令
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_docker_exec.py" gpu-node my_container "nvidia-smi"
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_docker_exec.py" gpu-node my_container "python -c 'import torch; print(torch.cuda.is_available())'"

# 查看容器内环境变量（验证环境是否完整）
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_docker_exec.py" gpu-node my_container "env | sort"

# 传递额外环境变量（-e 可多次使用）
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_docker_exec.py" gpu-node my_container "python train.py" -e CUDA_VISIBLE_DEVICES=0,1 -e OMP_NUM_THREADS=4

# 直接在远程执行 docker 命令（不进容器）
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_docker_exec.py" gpu-node docker-ps
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_docker_exec.py" gpu-node docker-ps "--all"
# 或省略容器名直接写 docker 命令
# 说明：当容器名设为 docker-ps 时，等效于在远程执行 docker ps
```

**自定义超时：**
```bash
# 长时间训练任务
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_docker_exec.py" gpu-node my_container "python train.py --epochs 100" --timeout 3600
```

**交互式进入容器（支持 TTY 的环境）：**
```bash
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_docker_exec.py" gpu-node my_container --shell
```

**干运行（只看命令不执行）：**
```bash
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_docker_exec.py" gpu-node my_container "nvidia-smi" --dry-run
```

**原理说明：**

| 写法 | Shell 类型 | PATH/环境变量 | 适用场景 |
|------|-----------|--------------|----------|
| `docker exec <容器> <命令>` | non-login, non-interactive | **仅容器初始 ENV** | **❌ 环境不全** |
| `docker exec <容器> bash -l -c "<命令>"` | **login shell** | **加载 /etc/profile + .bashrc** | ✅ **推荐** |
| `docker exec -it <容器> bash -l` | login + interactive | **完整** | ✅ **终端调试** |

**CRITICAL**: 
- `ssh_docker_exec.py` 底层使用 `ssh_execute.py` → 自动走守护进程长连接
- `--shell` 模式底层使用 `ssh_connect.py` → 需要 TTY 环境
- 容器名参数设为 `docker-ps` 时，直接执行 `docker ps` 命令（不进容器）
- 如果容器内的命令依赖特定环境变量且未在 Dockerfile 中 ENV 定义，通过 `-e KEY=VALUE` 传入

### Kubernetes Pod 执行

使用 `ssh_k8s_exec.py` 在远程服务器上的 Kubernetes Pod 中执行命令。

与 `docker exec` 的差异：
- Pod 容器可能没有 bash，只有 sh → 默认用 `sh -c`，所有容器都支持
- 支持 namespace 多租户隔离
- 支持多容器 Pod 指定 `-c`
- 内置 kubectl 快捷命令

**用法：**
```bash
# 在 Pod 中执行命令（自动 sh -c 包装）
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_k8s_exec.py" gpu-node my-pod "nvidia-smi"
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_k8s_exec.py" gpu-node my-pod "python -c 'import torch; print(torch.cuda.is_available())'"

# 指定 namespace
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_k8s_exec.py" gpu-node my-pod "kubectl get pods" -n kube-system

# 指定容器（多容器 Pod）
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_k8s_exec.py" gpu-node my-pod "ls /workspace" -c inference

# 传递环境变量
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_k8s_exec.py" gpu-node my-pod "python train.py" -e CUDA_VISIBLE_DEVICES=0

# 交互式进入 Pod
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_k8s_exec.py" gpu-node my-pod --shell

# 查看 Pod 日志
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_k8s_exec.py" gpu-node my-pod --logs
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_k8s_exec.py" gpu-node my-pod --logs -n kube-system --tail 100
```

**快捷命令（不进 Pod，直接在远程执行 kubectl）：**

| 快捷指令 | 等效命令 |
|----------|----------|
| `k8s-get-pods` | `kubectl get pods` |
| `k8s-get-nodes` | `kubectl get nodes` |
| `k8s-get-svc` | `kubectl get services` |
| `k8s-get-deploy` | `kubectl get deployments` |
| `k8s-get-ns` | `kubectl get namespaces` |
| `k8s-get-events` | `kubectl get events --sort-by=.lastTimestamp` |
| `k8s-get-gpu` | 查看集群 GPU 资源 |

```bash
# 使用快捷命令
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_k8s_exec.py" gpu-node k8s-get-pods -n default
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_k8s_exec.py" gpu-node k8s-get-gpu
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_k8s_exec.py" gpu-node k8s-get-nodes
```

**环境变量说明：**

| 场景 | 环境变量来源 | exec 时是否继承 |
|------|-------------|--------------|
| Dockerfile `ENV` | 容器镜像构建时 | ✅ 继承 |
| Deployment `env:` | K8s Pod Spec | ✅ 继承 |
| `kubectl run -e` | 运行时注入 | ✅ 继承 |
| `.bashrc` / `/etc/profile` | 容器启动后加载 | **❌ 不加载** |
| K8s Downward API | Pod metadata 注入 | ✅ 继承 |

**CRITICAL**:
- 优先用 `sh -c` 包装命令（所有容器都有 sh）
- 如果容器确定有 bash，可在命令前加 `bash -c "..."` 手动指定
- `--shell` 模式底层走 `ssh_connect.py`，需要 TTY 环境
- `--logs` 模式走 `kubectl logs`，不需要进入 Pod

### 配置管理

```bash
# 列出所有服务器
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_config_manager_v3.py" list-servers

# 按环境过滤
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_config_manager_v3.py" list-servers --environment production

# 查找服务器（支持别名和描述模糊查找）
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_config_manager_v3.py" find "<关键词>"

# 创建配置
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_config_manager_v3.py" create --alias <别名> --host <IP> --user <用户名> --key <密钥文件> --environment <环境>

# 更新配置（只更新提供的字段，其他字段保持不变）
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_config_manager_v3.py" update <别名> --description "新描述" --tags tag1 tag2 tag3
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_config_manager_v3.py" update <别名> --environment production --location "新位置"
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_config_manager_v3.py" update <别名> --host <新IP> --port <新端口>

# 删除配置
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_config_manager_v3.py" delete <别名>
```

## 配置文件

### 存储位置

`~/.ssh/config`（标准 OpenSSH 配置文件）

### 配置格式

每个服务器由 Host 块和注释元数据组成：

```ssh-config
# ===== prod-web-01 =====
# description: 生产环境 Web 服务器
# environment: production
# tags: web,nginx,production
# location: 阿里云-北京
# password:
# created_at: 2026-03-01 12:00:00
# updated_at: 2026-03-01 12:00:00
Host prod-web-01
    HostName 192.168.1.100
    User root
    IdentityFile ~/.ssh/id_rsa
    Port 22
```

### 密码认证配置

密码存储在注释中（SSH config 不原生支持密码字段）：

```ssh-config
# ===== dev-server =====
# description: 开发服务器
# environment: development
# password: your-password
Host dev-server
    HostName 192.168.1.200
    User root
    Port 22
```

注意：密码认证性能较低，建议升级为密钥认证。

### 跳板机配置

使用标准 ProxyJump：

```ssh-config
Host bastion
    HostName bastion.example.com
    User jumpuser
    IdentityFile ~/.ssh/jump_key

Host internal-server
    HostName 10.0.1.100
    User appuser
    IdentityFile ~/.ssh/id_rsa
    ProxyJump bastion
```

AI 只需要知道 `internal-server` 这个别名，底层自动处理跳转。

## 守护进程（长连接模式）

### 工作原理

守护进程在本地维护到远程服务器的 Paramiko 长连接，通过本地 TCP 接受命令请求。
ssh_execute.py 自动检测守护进程：有则复用长连接，无则自动启动。

### 自动模式（推荐）

ssh_execute.py 首次调用时会自动启动守护进程，无需手动操作。
守护进程空闲 30 分钟后自动退出。

### 手动管理

```bash
# 启动守护进程（通常不需要手动启动）
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_daemon.py" start <别名>

# 查看单个守护进程状态
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_daemon.py" status <别名>

# 停止守护进程
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_daemon.py" stop <别名>

# 列出所有活跃守护进程（全局概览）
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_daemon.py" list

# 停止所有守护进程
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_daemon.py" stop --all
```

`list` 输出示例：
```
| 别名    | PID   | 端口 | 主机              | 在线 | SSH存活 | 空闲时间 |
|---------|-------|------|-------------------|------|---------|----------|
| gpu-01  | 12345 | 9001 | root@192.2.29.9   | ✅   | ✅      | 2m       |
| gpu-02  | 12346 | 9002 | root@192.7.30.193 | ✅   | ✅      | 5m       |
```

可选参数：`--idle-timeout <秒>`（默认 1800，即 30 分钟）

### 守护进程特性

- 每台服务器独立守护进程，按别名隔离
- 多个对话（多个 codex Code 实例）可共享同一守护进程
- SSH 连接断开自动重连（最多 3 次）
- 每 60 秒心跳检测连接状态
- 空闲超时自动退出，无需手动清理

### 性能对比

| 模式 | 单次命令 | 连续 10 条 | 连续 30 条 |
|------|----------|-----------|-----------|
| 直连 | ~0.45s | ~4.5s | ~13.5s |
| 守护进程 | ~0.12s | ~1.2s | ~3.6s |

## 性能优化建议

### 命令合并

对同一服务器的多个独立查询，优先合并为一次调用：

```bash
# 好：一次调用获取多个信息
python "SCRIPTS/ssh_execute.py" DEV-002 "hostname && uptime && df -h && free -m"

# 差：多次调用分别获取
python "SCRIPTS/ssh_execute.py" DEV-002 "hostname"
python "SCRIPTS/ssh_execute.py" DEV-002 "uptime"
python "SCRIPTS/ssh_execute.py" DEV-002 "df -h"
python "SCRIPTS/ssh_execute.py" DEV-002 "free -m"
```

### 何时合并，何时分开

- 合并：多个只读查询、状态检查、信息收集
- 分开：命令之间有依赖关系、需要根据前一个结果决定下一步、需要独立的错误处理

## 输出格式

所有脚本输出 JSON 格式：

```json
{
  "success": true,
  "exit_code": 0,
  "stdout": "命令输出",
  "stderr": ""
}
```

## 故障排查

### 连接超时

检查：网络连接、服务器是否在线、防火墙规则、跳板机是否可达。

长命令可加 `--timeout 300` 延长超时。

### 守护进程问题

如果守护进程异常，可手动停止后重试：

```bash
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_daemon.py" stop <别名>
```

或使用 `--no-daemon` 参数跳过守护进程直连：

```bash
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_execute.py" <别名> "<命令>" --no-daemon
```

### 别名不存在

如果提示别名不存在，可通过配置管理工具查找：

```bash
python "~/.workbuddy/skills/ssh-skill/scripts/ssh_config_manager_v3.py" find "<关键词>"
```

## 强制规则

- 所有 SSH 操作必须通过本 skill 的 Python 脚本
- 禁止直接写 `ssh` 或 `scp` 命令（首次配置公钥除外）
- 路径必须使用正斜杠 `/`，不要使用反斜杠 `\`
- 不要用 `cd` 切换到脚本目录，直接用完整路径调用
- 使用别名（alias）标识服务器，不再使用 JSON 配置文件路径
- 对同一服务器的多个只读查询，优先合并为一次调用
- 持久 Shell 会话适合多步关联操作，单次独立命令用 `ssh_execute.py`**
- 隧道依赖密钥认证，密码认证暂不支持**

## 依赖

- Python 3.8+
- paramiko（SSH 连接和文件传输）
