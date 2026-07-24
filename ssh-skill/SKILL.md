---
name: ssh-skill
version: 3.6.0
description: "SSH 统一 dispatch 入口。禁止直接用 bash ssh/scp。触发词：SSH/远程/服务器/部署/隧道/Docker/K8s/多连接。快捷入口：xssh。"
allowed-tools: Bash, Read, Write, Glob
keywords: SSH,服务器,远程,连接,命令,上传,下载,文件传输,跳板机,批量,集群,deploy,部署,多连接,多节点,工作区,workspace,xssh,docker,容器,k8s,kubernetes,pod
---

# SSH Dispatch v3.5

远程操作统一 dispatch：`xssh` 入口 → 自动路由到对应 Python 脚本。内核能力：守护进程长连接(~0.12s)、文件传输、跳板机、多连接管理。

## xssh 入口

```bash
xssh <alias> "<cmd>"                        # 默认：远程执行命令
xssh docker|d <alias> <container> "<cmd>"   # Docker 容器执行
xssh k8s|k <alias> <pod> "<cmd>"            # K8s Pod 执行
xssh shell|s <alias> ["<cmd>"]             # 持久 Shell 会话（状态保持）
xssh connect|c <alias> ["<cmd>"]           # 交互式终端（需 TTY）
xssh multi|m <create|add|exec|status|note|...>  # 多连接管理
xssh upload|up <alias> <local> <remote>    # 上传文件
xssh download|dl <alias> <remote> <local>  # 下载文件
xssh daemon|tunnel|transfer|config|cluster  # 守护进程/隧道/传输/配置/批量
```

`xssh -h` 查看完整子命令。各子命令 `--help` 可见详细参数。实现文件：`~/bin/xssh`。

## 关键决策：四个执行模式

| 模式 | 命令 | 何时用 |
|------|------|--------|
| 一次性执行 | `xssh <alias> "<cmd>"` | 独立命令、健康检查、单次查询 |
| 持久 Shell（状态保持） | `xssh s <alias> "<cmd>"` | 多步操作需保持 cwd/env（如部署流水线） |
| PTY 交互式 | `xssh p <alias> "<cmd>"` | mysql/REPL/交互式问答（pyte 终端模拟） |
| PTY Follow（长连接） | `xssh p <alias> "<cmd>" --follow` | 启动 vLLM 等长运行服务 + 盯输出 |

**规则**：AI agent 默认用一次性执行。多步关联操作用 `xssh s`。交互式问答用 `xssh p`。长运行服务用 `xssh p --follow`。不要用 `xssh c`（无 TTY）。

## Docker 容器执行（`xssh d`）

核心原理：`docker exec` 默认 non-interactive → 不加载 `.bashrc` → PATH/LD_LIBRARY_PATH 丢失。

**`xssh d` 自动用 `bash -l -c` 包裹**，加载完整 login shell 环境。

```bash
xssh d gpu-node my_container "nvidia-smi"
xssh d gpu-node my_container "python train.py" -e CUDA_VISIBLE_DEVICES=0
```

> 更多参数见 `xssh d --help`

## K8s Pod 执行（`xssh k`）

核心原理：Pod 容器可能没有 bash → 默认用 `sh -c` 包装。

```bash
xssh k gpu-node my-pod "nvidia-smi"
xssh k gpu-node my-pod --shell          # 交互式进入
xssh k gpu-node my-pod --logs --tail 50 # 查看日志
```

> 快捷命令 `k8s-get-pods` / `k8s-get-gpu` 见 `xssh k --help`

## PTY 交互式终端（`xssh p`）

基于 pyte 终端模拟器，支持 mysql CLI、python REPL、tail -f、交互式问答等多轮对话场景。

与 `xssh s`（持久 Shell）的区别：`xssh p` 有 pyte 终端模拟层，能正确解析 ANSI 色彩和光标控制序列。

**三大模式**：

| 模式 | 命令 | 用途 |
|------|------|------|
| 发命令+拿结果 | `xssh p <alias> "<cmd>"` | mysql/REPL 问答 |
| 发命令+持续盯 | `xssh p <alias> "<cmd>" --follow` | 启动 vLLM 等长运行服务 |
| 只盯不发 | `xssh p <alias> --watch` | 监控已启动的服务 |

```bash
# 启动 vLLM + 自动盯输出（Ctrl+C 只退出盯，服务继续跑）
xssh p gpu-01 "python -m vllm.entrypoints.openai.api_server ..." --follow

# 继续盯已启动的服务
xssh p gpu-01 --watch

# 发现问题 → Ctrl+C 终止远程进程 → 改参数 → 重新 follow
xssh p gpu-01 -k ctrl+c
xssh p gpu-01 "python -m vllm ... --max-model-len 4096" --follow

# 一次性命令（mysql/REPL 多轮交互）
xssh p gpu-01 "mysql -u root -p"
xssh p gpu-01 "SELECT * FROM users LIMIT 5;"
xssh p gpu-01 "exit"

# 屏幕快照（看当前终端画面）
xssh p gpu-01 --snapshot

# 发送特殊按键
xssh p gpu-01 -k ctrl+c
xssh p gpu-01 -k enter
```

## 多连接管理（`xssh m`）

场景：多台机器同时操作，需要**有名字、有状态、不会忘**。

```bash
xssh m create dsv4-test                     # 创建工作区
xssh m add dsv4-test leader gpu-01          # 添加命名连接
xssh m exec dsv4-test leader "nvidia-smi"   # 单节点执行
xssh m exec dsv4-test --all "df -h"         # 所有节点执行
xssh m status dsv4-test                     # 全局状态面板
xssh m status dsv4-test --watch             # 自动刷新（Ctrl+C 退出）
xssh m note dsv4-test leader "vllm已启动"    # 贴便签
```

> 完整命令见 `xssh m --help`

## 上传 / 下载

```bash
xssh up <alias> "<本地>" "<远程>"
xssh dl <alias> "<远程>" "<本地>"
```

xssh 自动处理 `MSYS_NO_PATHCONV=1`（Windows 路径转换防护）。支持 `--resume` 断点续传。

## 守护进程

自动运行：`xssh` 首次执行时自动启动参数长连接守护进程。空 30 分钟自动退出。

手动管理：`xssh daemon <start|stop|status|list>`（日常不需要）。

性能：直连 ~0.45s/次，守护进程 ~0.12s/次。

## 配置文件

`~/.ssh/config`（标准 OpenSSH 格式）：

```ssh-config
# description: GPU 推理服务器
# tags: n300,dsv4
Host gpu-01
    HostName 192.2.0.146
    User root
    IdentityFile ~/.ssh/id_rsa
```

密码认证存储在注释中（`# password: xxx`）。跳板机用标准 `ProxyJump`。AI 只需知道 `Host` 别名。

## 强制规则

- 所有远程操作走 `xssh`，禁止直接写 `ssh`/`scp`
- AI agent 默认用 `xssh` 一次性执行，多步操作用 `xssh s`
- 多机场景用 `xssh m` 管理连接和便签
- 命令参数用 `xssh <子命令> --help` 查询，不要死记
- `xssh` 不可用时回退：`python ~/.workbuddy/skills/ssh-skill/scripts/ssh_execute.py <alias> "<cmd>"`

## 依赖

- Python 3.8+ / paramiko
