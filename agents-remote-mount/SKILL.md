---
name: agents-remote-mount
description: 把本地文件夹挂载到远程服务器。rclone WebDAV + SSH 反向隧道，初始配置、启动管理、故障排查全流程。
agent_created: true
disable: true
---

# agents-remote-mount

## 概述

将 Windows 本地的 `C:\Users\sys49169\.agents` 文件夹通过 rclone WebDAV + SSH 反向隧道挂载到远程服务器 `/app/.agent`。

```
Windows (.agents) ──rclone serve webdav──→ localhost:24831
                                            ↓ SSH -R 隧道（加密）
192.2.29.9:24831   ──rclone mount──→ /app/.agent
```

## 前置条件

- 本机已安装 scoop + rclone（`scoop install rclone`）
- 远程服务器 root SSH 可达
- 远程 Ubuntu 22.04+（脚本安装 fuse3）

## 完整配置流程

### 1. Windows 侧 — 启动 WebDAV 服务

```powershell
rclone serve webdav "C:\Users\sys49169\.agents" --addr localhost:24831 --user agents --pass agents123 --read-only
```

> 端口 `24831` 为冷门端口。绑定 `localhost` 不暴露到外网。

### 2. Windows 侧 — 建立 SSH 反向隧道

```powershell
ssh -fN -R 24831:localhost:24831 -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes root@192.2.29.9
```

> 隧道将远端 `127.0.0.1:24831` 转发回本机 `localhost:24831`。

### 3. 远程侧 — 安装依赖并配置

```bash
# 安装 fuse3（新版 rclone 需要 fusermount3）
apt-get install -y fuse3

# 配置 rclone WebDAV remote
rclone config create agents_webdav webdav url http://127.0.0.1:24831 vendor other user agents pass agents123

# 创建挂载点
mkdir -p /app/.agent

# 挂载
rclone mount agents_webdav: /app/.agent \
  --daemon \
  --vfs-cache-mode writes \
  --allow-other \
  --allow-non-empty \
  --umask 022 \
  --dir-cache-time 60s \
  --log-file /var/log/rclone-mount.log
```

### 4. 验证

```bash
mount | grep "/app"
ls -la /app/.agent/
```

预期输出应包含 `grok-skill/`, `plugins/`, `skills/`, `.skill-lock.json` 等。

## 管理脚本

### 本机启停 — mount-manager.bat

路径：`C:\Users\sys49169\.agents\mount-manager.bat`

```
mount-manager.bat start     # 启动 webdav + SSH 隧道
mount-manager.bat stop      # 停止
mount-manager.bat status    # 检查两端状态
mount-manager.bat restart   # 重启
```

### 远端重挂 — mount-agents.sh

路径：`/usr/local/bin/mount-agents.sh`

SSH 隧道还在、远端 mount 掉了时，直接跑：
```bash
/usr/local/bin/mount-agents.sh
```

## 故障排查

| 问题 | 原因 | 解决 |
|------|------|------|
| `fusermount3 not found` | 系统只有 fuse2 | `apt install -y fuse3` |
| `Connection reset by peer` | SSH 隧道断开 | 本机重跑 `ssh -fN -R ...` |
| WebDAV 返回空目录 | 路径格式不对 | 用 Windows 路径 `C:\Users\...` 而非 `/c/Users/...` |
| 挂载后看不到原 `/app` 内容 | 挂载点选错了 | `umount` 后原文件恢复，挂到子目录 `/app/.agent` |

## 重建步骤

如果远程机器重置了，在远端执行：

```bash
apt-get install -y fuse3
rclone config create agents_webdav webdav url http://127.0.0.1:24831 vendor other user agents pass agents123
mkdir -p /app/.agent
```
