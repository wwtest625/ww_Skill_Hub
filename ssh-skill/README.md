# SSH Skill v3.5

> 多连接远程操作管理工具，支持守护进程长连接、PTY 交互式终端、Docker/K8s 容器执行、多节点编排。

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

## 快速开始

```bash
# 执行远程命令
xssh gpu-01 "nvidia-smi"

# Docker 容器内执行
xssh d gpu-01 vllm "python -c 'import torch'"

# 多连接管理（四机部署场景）
xssh m create dsv4-test
xssh m add dsv4-test leader gpu-01
xssh m add dsv4-test worker1 gpu-02
xssh m exec dsv4-test --all "df -h"
xssh m status dsv4-test --watch    # 自动刷新，Ctrl+C 退出

# 上传 / 下载
xssh up gpu-01 ./app.tar.gz /tmp/
xssh dl gpu-01 /var/log/app.log ./
```

`xssh -h` 查看完整命令列表。

## 核心能力

| 能力 | 命令 | 说明 |
|------|------|------|
| 远程执行 | `xssh <alias> "<cmd>"` | 守护进程长连接，~0.12s/次 |
| Docker 容器 | `xssh d <alias> <container> "<cmd>"` | 自动 `bash -l -c`，环境变量完整 |
| K8s Pod | `xssh k <alias> <pod> "<cmd>"` | 自动 `sh -c`，支持 namespace |
| 多连接管理 | `xssh m <create\|add\|exec\|status\|note>` | Workspace + Note，有名字有状态 |
| 持久 Shell | `xssh s <alias> "<cmd>"` | 跨命令保持 cwd/env |
| PTY 交互式 | `xssh p <alias> "<cmd>"` | mysql/REPL/tail -f，pyte 终端模拟 |
| 文件传输 | `xssh up` / `xssh dl` / `xssh tx` | SFTP 断点续传 + 服务器间直传 |
| 端口转发 | `xssh t <start\|stop\|list>` | local/remote/dynamic 三种模式 |
| 批量操作 | `xssh cluster "<cmd>" --parallel` | 按环境/标签过滤并发执行 |

## 文件传输特性

- **智能切换**：≤80MB 用原生 SCP（快），>80MB 用 Paramiko SFTP（实时进度）
- **实时进度**：百分比、速度、ETA 显示
- **断点续传**：`--resume` 支持中断后继续
- **目录递归**：`--recursive` 整目录上传/下载
- **智能超时**：按文件大小自动计算（1MB/s + 60s 缓冲，60~3600s）
- **服务器间直传**：4 种模式（auto/direct/stream/hybrid），支持 rsync 增量同步
- **大文件无超时限制**：传输通道 settimeout(None)

## 守护进程特性

- 首次执行自动启动，空闲 30 分钟自动退出
- 多个实例共享同一守护进程
- 断线自动重连（最多 3 次）
- 60 秒心跳检测
- `xssh daemon list` 全局概览，`xssh daemon stop --all` 一键停止

## 安全特性

- 密钥认证 + 密码认证（密码存注释中）
- SSH Agent Forwarding（`xssh c` 自动检测 ssh-agent 并启用 `-A`）
- 跳板机 ProxyJump 多级跳转
- 密钥密码保护支持

## 四种执行模式

| 模式 | 命令 | 场景 |
|------|------|------|
| 一次性执行 | `xssh <alias> "<cmd>"` | 独立命令、健康检查 |
| 持久 Shell | `xssh s <alias> "<cmd>"` | 多步操作保持状态（部署流水线） |
| PTY 交互式 | `xssh p <alias> "<cmd>"` | mysql/REPL/交互式问答（pyte 终端模拟） |
| 交互式终端 | `xssh c <alias>` | 人类手动调试（需 TTY） |

## 安装

```bash
# 依赖
pip install paramiko

# skill 路径
~/.workbuddy/skills/ssh-skill/

# xssh 快捷入口（自动安装到 ~/bin/xssh，需在 PATH 中）
```

## 配置

`~/.ssh/config`（标准 OpenSSH 格式）：

```ssh-config
# description: GPU 推理服务器
# tags: n300,dsv4
# environment: production
Host gpu-01
    HostName 192.2.0.146
    User root
    IdentityFile ~/.ssh/id_rsa
```

密码认证存储在注释中。跳板机用标准 `ProxyJump`。

## 性能

| 模式 | 单次命令 | 连续 10 条 |
|------|----------|-----------|
| 直连 | ~0.45s | ~4.5s |
| 守护进程 | ~0.12s | ~1.2s |

## 版本历史

详见 [CHANGELOG.md](CHANGELOG.md)。

| 版本 | 主要特性 |
|------|----------|
| v3.5 | ssh_pty PTY 交互式终端（pyte 终端模拟）、特殊按键注入 |
| v3.4 | xssh 快捷入口、ssh_multi 多连接管理、--watch 自动刷新 |
| v3.3 | 隧道、持久 Shell、交互式连接、Docker/K8s 容器执行 |
| v3.2 | 守护进程长连接、SFTP 断点续传、服务器间直传、批量并发 |

## 依赖

- Python 3.8+
- paramiko

## 许可证

MIT
