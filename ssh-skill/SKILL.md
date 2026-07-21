---
name: ssh-skill
version: 3.4.0
description: "SSH 统一 dispatch 入口。禁止直接用 bash ssh/scp。触发词：SSH/远程/服务器/部署/隧道/Docker/K8s/多连接。快捷入口：xssh。"
allowed-tools: Bash, Read, Write, Glob
keywords: SSH,服务器,远程,连接,命令,上传,下载,文件传输,跳板机,批量,集群,deploy,部署,多连接,多节点,工作区,workspace,xssh,docker,容器,k8s,kubernetes,pod
---

# SSH Dispatch v3.4

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

## 关键决策：三个执行模式

| 模式 | 命令 | 何时用 |
|------|------|--------|
| 一次性执行 | `xssh <alias> "<cmd>"` | 独立命令、健康检查、单次查询 |
| 持久 Shell（状态保持） | `xssh s <alias> "<cmd>"` | 多步操作需保持 cwd/env（如部署流水线） |
| 交互式终端 | `xssh c <alias>` | 人类手动调试、tail -f、htop |

**规则**：AI agent 默认用一次性执行。多步关联操作用 `xssh s`。不要用 `xssh c`（无 TTY）。

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
