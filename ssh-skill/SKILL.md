---
name: ssh-skill
version: 3.4.0
description: "SSH 操作统一 dispatch 入口。将命令/文件/隧道经由脚本层 dispatch 到远程服务器。禁止直接用 bash ssh/scp。触发词：任何 SSH/远程服务器/部署/隧道/多连接相关操作。快捷入口：xssh。"
allowed-tools: Bash, Read, Write, Glob
keywords: SSH,服务器,远程,连接,命令,上传,下载,文件传输,跳板机,批量,集群,deploy,部署,运维,登录,执行,查看,检查,管理,操作,访问,传输,迁移,服务器间,隧道,端口转发,socks,shell,会话,交互式,docker,容器,docker exec,k8s,kubernetes,kubectl,pod,容器编排,多连接,多节点,工作区,workspace,xssh
---

# SSH Dispatch v3.4

将所有远程操作（命令、文件、隧道）经由脚本层 dispatch 到目标服务器。支持守护进程长连接、端口转发/隧道、持久 Shell 会话、多连接管理（Workspace + Note）、自动连接复用、跳板机、批量并发、服务器间直接传输、自动错误恢复。

## 快捷入口 `xssh`

**全局可用**，无需加 `python ~/.workbuddy/skills/...` 前缀：

```bash
# 默认 = ssh_execute（最常用）
xssh <alias> "<command>"

# 子命令（全称 或 简写）
xssh docker|d <alias> <container> "<command>"
xssh k8s|k <alias> <pod> "<command>"
xssh shell|s <alias> ["<command>"]
xssh connect|c <alias> ["<command>"]
xssh upload|up <alias> <local> <remote>
xssh download|dl <alias> <remote> <local>
xssh transfer|tx <src-alias> <src-path> <dst-alias> <dst-path>
xssh multi|m <create|add|exec|status|note|...>
xssh daemon <start|stop|status|list>
xssh tunnel|t <start|stop|status|list>
xssh config <list|create|update|delete|find>
xssh cluster <command> --parallel

# 示例
xssh gpu-01 "nvidia-smi"
xssh d gpu-01 vllm "python -c 'import torch'"
xssh m create dsv4-test
xssh daemon list
```

`xssh -h` 查看完整帮助。实现文件：`~/bin/xssh`。

## 调用规则

### xssh 入口（首选）

**所有远程操作优先使用 `xssh`**，URL 长度短、心智负担低。`xssh` 自动路由到正确的 Python 脚本。

### 调用格式

```bash
# 执行远程命令
xssh <别名> "<命令>"

# Docker 容器执行
xssh docker <别名> <容器名> "<命令>"
xssh d <别名> <容器名> "<命令>"          # 简写

# K8s Pod 执行
xssh k8s <别名> <pod名> "<命令>"
xssh k <别名> <pod名> "<命令>"           # 简写

# 多连接管理
xssh multi create <工作区名>
xssh m add <工作区> <连接名> <别名>       # 简写
xssh m status <工作区>

# 其他入口
xssh shell <别名> ["<cmd>"]              # 持久 Shell 会话
xssh connect <别名>                      # 交互式终端
xssh upload <别名> <本地> <远程>          # 上传文件
xssh download <别名> <远程> <本地>        # 下载文件
xssh transfer <源> <源路径> <目标> <路径> # 服务器间传输
xssh daemon list                         # 守护进程管理
xssh tunnel list                         # 隧道管理
xssh config list-servers                 # 配置管理
xssh cluster "<cmd>" --parallel          # 批量操作
```

### 兼容格式（备选）

如果 `xssh` 不可用，可回退到原始 Python 路径：

```bash
python ~/.workbuddy/skills/ssh-skill/scripts/ssh_execute.py <别名> "<命令>"
```

脚本默认路径：`~/.workbuddy/skills/ssh-skill/scripts`（Windows/Linux 通用）。

### 执行远程命令

```bash
xssh <别名> "<命令>"
```

可选参数：`--timeout <秒>` `--no-daemon`

ssh_execute.py 会自动检测守护进程：有则走长连接（~0.12s），无则自动启动守护进程。

### 上传文件

```bash
xssh upload <别名> "<本地路径>" "<远程路径>"
```

可选参数：`--resume`（断点续传） `--recursive`（目录递归上传） `--no-progress`（禁用进度输出）

### 下载文件

```bash
xssh download <别名> "<远程路径>" "<本地路径>"
```

可选参数：`--resume`（断点续传） `--recursive`（目录递归下载） `--no-progress`（禁用进度输出）

xssh 自动处理 `MSYS_NO_PATHCONV=1` 防止 Windows MSYS bash 将远程路径转换。

### 服务器间传输

```bash
# 自动模式（推荐）
xssh transfer <源别名> "<源路径>" <目标别名> "<目标路径>"

# 强制直连模式（大文件推荐）
xssh transfer <源别名> "<源路径>" <目标别名> "<目标路径>" --mode direct

# 强制流式转发（小文件或服务器间网络不通时）
xssh transfer <源别名> "<源路径>" <目标别名> "<目标路径>" --mode stream

# 混合模式（先尝试直连，失败后自动降级）
xssh transfer <源别名> "<源路径>" <目标别名> "<目标路径>" --mode hybrid

# 使用 rsync（仅直连模式，支持增量同步）
xssh transfer <源别名> "<源路径>" <目标别名> "<目标路径>" --use-rsync
```

可选参数：`--mode <auto|direct|stream|hybrid>`（传输模式） `--use-rsync`（使用 rsync） `--no-progress`（禁用进度） `--size-threshold <MB>`（大小阈值，默认 10） `--timeout <秒>`（超时，默认 300）

**传输模式说明：**

| 模式 | 适用场景 | 数据流向 | 优点 |
|------|----------|----------|------|
| 直连 (direct) | 大文件、服务器间网络通 | 源服务器 → 目标服务器 | 速度快，不占本地带宽 |
| 流式 (stream) | 小文件、网络不通 | 源 → 本地 → 目标（流式） | 无需服务器间配置 |
| 混合 (hybrid) | 不确定环境 | 先尝试直连，失败降级 | 自动适应 |
| 自动 (auto) | 默认 | 智能判断 | 最优选择 |

### 批量操作

```bash
# 对所有服务器执行
xssh cluster "<命令>" --parallel

# 对指定别名列表执行
xssh cluster "<命令>" --hosts "DEV-002,DEV-003" --parallel

# 按环境过滤
xssh cluster "<命令>" --environment production --parallel

# 按标签过滤
xssh cluster "<命令>" --tags "web,nginx" --parallel
```

可选参数：`--timeout <秒>` `--health-check` `--max-workers <数量>`

### 端口转发/隧道管理

使用 `ssh_tunnel.py` 管理 SSH 端口转发隧道。

**启动本地转发：**
```bash
xssh tunnel start <别名> local <本地端口>:<目标主机>:<目标端口>
# 示例
xssh t start prod-web-01 local 8080:127.0.0.1:3306
xssh t start prod-web-01 local 8080:3306 --name mysql-tunnel
```

**启动远程转发：**
```bash
xssh t start dev-server remote 2222:127.0.0.1:22
```

**启动 SOCKS 代理（动态转发）：**
```bash
xssh t start bastion dynamic 1080
```

**管理隧道：**
```bash
xssh t list
xssh t status <tunnel_id>
xssh t stop <tunnel_id>
```

**隧道类型说明：**

| 类型 | 命令 | 场景 | 数据流向 |
|------|------|------|----------|
| 本地转发 (local) | `-L 本地:目标:端口` | 访问内网服务（DB、Web） | 本地 → SSH → 远程目标 |
| 远程转发 (remote) | `-R 远程:本地:端口` | 暴露本地服务到远程 | 远程 → SSH → 本地目标 |
| 动态转发 (dynamic) | `-D 本地端口` | SOCKS 代理 | 本地 → SSH → 任意目标 |

**重要**: 隧道使用原生 SSH 命令，需要密钥认证。密码认证暂不支持。

### 交互式 Shell 连接（手动操作）

使用 `ssh_connect.py` 打开真正的交互式 SSH 终端。**与 `ssh_execute.py` 和 `ssh_shell.py` 的核心区别：**

| 脚本 | 场景 | 交互方式 | TTY | 典型用途 |
|------|------|----------|-----|----------|
| `ssh_execute.py` | AI agent 执行命令 | 一次性执行，返回结果 | ❌ | 自动化命令、健康检查 |
| `ssh_shell.py` | AI agent 状态保持 | 持久会话，命令式 | ❌ | 多步部署、交叉命令 |
| `ssh_connect.py` | **人类手动操作** | **实时终端交互** | **✅** | **调试、tail -f、htop、vim** |

**用法：**
```bash
# 交互式登录
xssh connect prod-web-01

# 执行命令后退出（仍带 TTY）
xssh connect prod-web-01 "tail -f /var/log/app.log"
xssh c dev-server "htop"

# 干运行：仅显示 ssh 命令
xssh connect prod-web-01 --dry-run
```

**实现原理：** 通过 `os.execvp` 用原生 `ssh -t` 替换当前进程，直接继承终端。配置参数从 SSH config 自动读取。

**SSH Agent Forwarding：** 自动检测本地 `ssh-agent` 状态：
- 若 `ForwardAgent yes` 已在 SSH config 中设置，强制启用 `-A`
- 若本地 `ssh-agent` 在运行且有密钥已加载（`ssh-add -l` 返回 0），自动启用 `-A`
- 远程服务器可通过 agent 借用本地密钥，适合跳板机场景

**重要**: 
- 需要在**支持 TTY 的终端**中执行（如 Git Bash、CMD、PowerShell）
- AI agent 环境通常无 TTY，此时应使用 `ssh_execute.py` 或 `ssh_shell.py`
- 在 Windows Git Bash 中执行可能需要加 `winpty` 前缀

### 持久 Shell 会话（AI agent 状态保持）

使用 `ssh_shell.py` 管理持久 shell 会话。与 `ssh_execute.py` 的区别：shell 会话**保持状态**（cwd、环境变量、别名等），适合多步操作。与 `ssh_connect.py` 的区别：**无 TTY 交互**，通过 socket daemon 接收命令请求，适合 AI agent 自动化场景。

**常用方式（一行搞定，自动启动会话 + 执行命令）：**
```bash
xssh shell prod-web-01 "cd /app && git pull"
xssh s prod-web-01 "pwd"  # 输出 /app
xssh s prod-web-01 "systemctl restart app"
```

**显式管理会话：**
```bash
xssh s session dev-server
xssh s exec dev-server "cd /data && ls -la"
xssh s list
xssh s stop dev-server
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
# 基本用法
xssh docker gpu-node my_container "nvidia-smi"
xssh d gpu-node my_container "python -c 'import torch; print(torch.cuda.is_available())'"

# 传递额外环境变量
xssh d gpu-node my_container "python train.py" -e CUDA_VISIBLE_DEVICES=0,1 -e OMP_NUM_THREADS=4

# 长时间任务
xssh d gpu-node my_container "python train.py --epochs 100" --timeout 3600

# docker ps（不进容器）
xssh d gpu-node docker-ps

# 交互式进入容器（需 TTY 环境）
xssh d gpu-node my_container --shell

# 干运行
xssh d gpu-node my_container "nvidia-smi" --dry-run
```

**原理说明：**

| 写法 | Shell 类型 | PATH/环境变量 | 适用场景 |
|------|-----------|--------------|----------|
| `docker exec <容器> <命令>` | non-login, non-interactive | **仅容器初始 ENV** | **❌ 环境不全** |
| `docker exec <容器> bash -l -c "<命令>"` | **login shell** | **加载 /etc/profile + .bashrc** | ✅ **推荐** |
| `docker exec -it <容器> bash -l` | login + interactive | **完整** | ✅ **终端调试** |

**注意**: 
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
# Pod 中执行
xssh k8s gpu-node my-pod "nvidia-smi"
xssh k gpu-node my-pod "python -c 'import torch; print(torch.cuda.is_available())'"

# 指定 namespace 和容器
xssh k gpu-node my-pod "ls /workspace" -n ml-ns -c inference

# 传递环境变量
xssh k gpu-node my-pod "python train.py" -e CUDA_VISIBLE_DEVICES=0

# 交互式、日志、干运行
xssh k gpu-node my-pod --shell
xssh k gpu-node my-pod --logs --tail 50
xssh k gpu-node my-pod "nvidia-smi" --dry-run
```

**快捷命令：**
```bash
xssh k gpu-node k8s-get-pods -n default
xssh k gpu-node k8s-get-gpu
xssh k gpu-node k8s-get-nodes
```

**环境变量说明：**

| 场景 | 环境变量来源 | exec 时是否继承 |
|------|-------------|--------------|
| Dockerfile `ENV` | 容器镜像构建时 | ✅ 继承 |
| Deployment `env:` | K8s Pod Spec | ✅ 继承 |
| `kubectl run -e` | 运行时注入 | ✅ 继承 |
| `.bashrc` / `/etc/profile` | 容器启动后加载 | **❌ 不加载** |
| K8s Downward API | Pod metadata 注入 | ✅ 继承 |

**重要**:
- 优先用 `sh -c` 包装命令（所有容器都有 sh）
- 如果容器确定有 bash，可在命令前加 `bash -c "..."` 手动指定
- `--shell` 模式底层走 `ssh_connect.py`，需要 TTY 环境
- `--logs` 模式走 `kubectl logs`，不需要进入 Pod

### 多连接管理（Workspace + Note）

使用 `ssh_multi.py` 管理多个 SSH 连接。核心价值：**有名字、有状态、不会忘。**

适用场景：四机推理部署、多机房巡检、A/B 测试对比等任何多台机器操作。

**创建工作区并添加连接：**
```bash
xssh m create dsv4-test
xssh m add dsv4-test leader gpu-node-01
xssh m add dsv4-test worker1 gpu-node-02
xssh m add dsv4-test worker2 gpu-node-03
xssh m add dsv4-test worker3 gpu-node-04
```

**执行命令：**
```bash
xssh m exec dsv4-test leader "nvidia-smi"
xssh m exec dsv4-test --all "df -h && free -m"
```

**状态面板：**
```bash
xssh m status dsv4-test
xssh m status dsv4-test --check  # 实时 ping 检查
```

**便签：**
```bash
xssh m note dsv4-test leader "vllm已启动,端口8000"
xssh m note dsv4-test worker2 "CUDA路径错误,待修复"
```

**其他：**
```bash
xssh m list                              # 列出所有工作区
xssh m list --workspace dsv4-test        # 工作区连接列表
xssh m check dsv4-test                   # 检查在线状态
xssh m remove dsv4-test worker3          # 移除连接
xssh m delete dsv4-test                  # 删除工作区
```

**存储位置：** `~/.ssh/multi/<workspace>.json`，每个工作区一个 JSON 文件，可备份可分享。

**CRITICAL**: 
- `exec --all` 串行执行（非并行），每台执行完立即保存状态
- 命令输出截断到 2000 字符，防止 JSON 过大
- 来福在多机场景下应优先使用 ssh_multi 管理连接，每次操作后自动更新便签

### 配置管理

```bash
xssh config list-servers
xssh config list-servers --environment production
xssh config find "<关键词>"
xssh config create --alias <别名> --host <IP> --user <用户名> --key <密钥文件> --environment <环境>
xssh config update <别名> --description "新描述" --tags tag1 tag2 tag3
xssh config delete <别名>
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
xssh daemon start <别名>
xssh daemon status <别名>
xssh daemon stop <别名>
xssh daemon list              # 所有活跃守护进程
xssh daemon stop --all        # 停止全部
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
xssh DEV-002 "hostname && uptime && df -h && free -m"

# 差：多次调用分别获取
xssh DEV-002 "hostname"
xssh DEV-002 "uptime"
xssh DEV-002 "df -h"
xssh DEV-002 "free -m"
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
xssh daemon stop <别名>
```

或使用 `--no-daemon` 参数跳过守护进程直连：

```bash
xssh <别名> "<命令>" --no-daemon
```

### 别名不存在

如果提示别名不存在，可通过配置管理工具查找：

```bash
xssh config find "<关键词>"
```

## 强制规则

- 所有远程操作必须通过本 skill 的 dispatch 层（Python 脚本）
- 禁止绕过 dispatch 直接写 `ssh` 或 `scp` 命令（首次配置公钥除外）
- 路径必须使用正斜杠 `/`，不要使用反斜杠 `\`
- 不要用 `cd` 切换到脚本目录，直接用完整路径调用
- 使用别名（alias）标识服务器，不再使用 JSON 配置文件路径
- 对同一服务器的多个只读查询，优先合并为一次调用
- 持久 Shell 会话适合多步关联操作，单次独立命令用 `ssh_execute.py`
- 隧道依赖密钥认证，密码认证暂不支持

## 完成检查

每个操作完成后，按以下条件验证结果：

| 操作 | 完成检查条件 |
|------|-------------|
| 执行远程命令 | `success: true` 且 exit_code 为 0，stdout 包含预期内容 |
| 上传文件 | 远程路径下文件存在，大小与本地一致 |
| 下载文件 | 本地路径下文件存在，大小与远程一致 |
| 服务器间传输 | 目标服务器上文件存在，大小与源一致 |
| 批量操作 | 所有目标服务器均返回 `success: true` |
| 隧道启动 | `list` 输出显示隧道状态为活跃 |
| 持久 Shell | 命令输出正确且下次调用能保持状态 |
| Docker 执行 | 命令输出包含预期结果 |
| K8s 执行 | 命令输出包含预期结果 |
| 配置管理 | JSON 返回 `success: true` |

## 依赖

- Python 3.8+
- paramiko（SSH 连接和文件传输）
