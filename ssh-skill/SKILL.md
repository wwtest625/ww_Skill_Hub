---
name: ssh-skill
version: 3.8.0
description: "SSH 统一 dispatch 入口。核心命令(xssh)已用 Go 重写(v0.2)，性能更高、exit code 准确。禁止直接用 bash ssh/scp。触发词：SSH/远程/服务器/部署/隧道/Docker/K8s/多连接。快捷入口：xssh。"
allowed-tools: Bash, Read, Write, Glob
keywords: SSH,服务器,远程,连接,命令,上传,下载,文件传输,跳板机,批量,集群,deploy,部署,多连接,多节点,工作区,workspace,xssh,docker,容器,k8s,kubernetes,pod,go,重寫
---

# SSH Dispatch v3.8

远程操作统一 dispatch：`xssh`（Go 二进制）→ 核心命令执行 / 文件传输 / 多连接管理。
高级功能（Docker/K8s/PTY/守护进程）仍走 Python 脚本。

> **版本说明**：`xssh` 核心（execute/upload/download/shell/multi/list）已从 Python 迁移到 Go（v0.2），解决 exit code 误报、性能问题。Python 版本（v3.7.1）保留在 `~/.workbuddy/skills/ssh-skill/`，Docker/K8s/PTY/守护进程等高级功能仍依赖它。

## xssh 入口（Go 版 v0.2）

```bash
xssh <alias> "<cmd>"                        # 默认：远程执行命令（exit code 精准）
xssh <alias> --stdin                       # 从管道读命令（长脚本，绕过参数长度限制）
cat deploy.sh | xssh <alias> --stdin
xssh shell|s <alias> ["<cmd>"]             # 持久 Shell 会话（状态保持）
xssh multi|m <create|add|exec|status|note>  # 多连接管理
xssh upload|up <alias> <local> <remote>    # 上传文件（--resume 断点续传）
xssh download|dl <alias> <remote> <local>  # 下载文件（--resume 断点续传）
xssh list                                  # 列出所有服务器别名
xssh -h                                    # 查看完整帮助
```

**xssh 特点**：
- exit code 精准（base64 编码 + marker 机制，支持 `exit N` 命令）
- 上传/下载带实时进度条
- `--resume` 断点续传
- 无守护进程，每次新建连接（性能损耗约 0.3s）

`xssh --help` 或 `xssh -h` 查看全部子命令和选项。

**长命令原则**：命令保持"一行触发器"，长脚本走文件或 `--stdin` 管道。

## 关键决策：执行模式选择

| 模式 | 命令 | 何时用 |
|------|------|--------|
| 一次性执行 | `xssh <alias> "<cmd>"` | 独立命令、健康检查、单次查询（**AI 默认用这个**） |
| 持久 Shell（状态保持） | `xssh s <alias> "<cmd>"` | 多步操作需保持 cwd/env（如部署流水线） |

**规则**：AI agent 默认用 `xssh <alias> "<cmd>"`。多步关联操作用 `xssh s`。

## 高级功能（Python 脚本）

Docker、K8s、PTY 交互式终端仍通过 Python 脚本实现。xssh 已路由到对应脚本：

```bash
xssh docker|d <alias> <container> "<cmd>"   # Docker 容器执行
xssh k8s|k <alias> <pod> "<cmd>"            # K8s Pod 执行
xssh pty|p <alias> "<cmd>"                  # PTY 交互式终端（pyte 模拟）
```

> 见 `xssh d --help` / `xssh k --help` / `xssh p --help`

## 多连接管理（`xssh m`）

场景：多台机器同时操作，需要**有名字、有状态、不会忘**。

```bash
xssh m create dsv4-test                     # 创建工作区
xssh m add dsv4-test leader gpu-01          # 添加命名连接
xssh m exec dsv4-test leader "nvidia-smi"   # 单节点执行
xssh m exec dsv4-test --all "df -h"         # 所有节点执行
xssh m status dsv4-test                     # 全局状态面板
xssh m note dsv4-test leader "vllm已启动"    # 贴便签
```

> 完整命令见 `xssh m --help`

## 上传 / 下载（Go 版 v0.2）

```bash
xssh upload|up <alias> <local> <remote>    # 上传文件
xssh download|dl <alias> <remote> <local>  # 下载文件
```

- 实时进度条（stderr）
- `--resume` 断点续传（上传/下载均支持）
- `--recursive` 递归目录传输
- 自动处理 Windows 路径转换（`MSYS_NO_PATHCONV=1`）

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
- AI agent 默认用 `xssh <alias> "<cmd>"` 一次性执行，多步操作用 `xssh s`
- 多机场景用 `xssh m` 管理连接和便签
- 命令参数用 `xssh <子命令> --help` 查询，不要死记
- `xssh` 不可用时回退：`python ~/.workbuddy/skills/ssh-skill/scripts/ssh_execute.py <alias> "<cmd>"`

## 依赖

- Go 1.24+（核心 xssh 二进制）
- Python 3.8+ / paramiko（Docker/K8s/PTY 高级功能）
