---
name: metax-stream-download
description: 沐曦(MetaX)软件下载 - 通过浏览器获取下载链接，流式传输到远程服务器。适用于沐曦GPU驱动/SDK下载，服务器无外网的场景。
agent_created: true
disable: true
---

# metax-stream-download

沐曦软件下载中心(developer.metax-tech.com/softnova)的自动化下载工具。
通过 opencli 浏览器适配器获取最新驱动/SDK 下载链接，然后通过 SSH 管道流式传输到远程服务器——数据不落本地磁盘。

## 适用场景

- 需要下载沐曦 GPU 最新驱动或 SDK
- 远程服务器无外网访问能力
- 希望利用本地网络下载，但不占用本地磁盘空间
- 下载文件很大（SDK 约 2.9GB），流式传输避免本地 I/O

## 前置条件

1. opencli daemon 已运行且 Chrome Browser Bridge 已连接（`opencli daemon status` 检查）
2. 在浏览器登录 `developer.metax-tech.com`（opencli 会复用浏览器 cookie）
3. 远程服务器已通过 ssh-skill 配置（`python ssh_config_manager_v3.py find <ip>` 检查）
4. Python 3.8+，依赖：`paramiko`

## 工作流程

### 1. 获取最新下载链接

使用 opencli metax adapter 的 `pkgs` 命令获取下载 URL：

```bash
# 获取驱动下载信息
opencli metax pkgs --chip "曦云C500系列" --version 3.7.2.x --kind "Driver"

# 获取 SDK 下载信息
opencli metax pkgs --chip "曦云C500系列" --version 3.7.2.x --kind "SDK"
```

输出包含文件名、大小和带签名的下载 URL（有效期 8 小时）。

### 2. 流式传输到远程服务器

用本 skill 自带的 `scripts/stream_download.py` 脚本：

```bash
python scripts/stream_download.py <下载URL> <远程路径>
```

示例：
```bash
python scripts/stream_download.py \
  "https://metax-pub.tos-cn-shanghai.volces.com/mxmaca2.0/..." \
  /opt/maca-sdk-3.7.2.0-deb-x86_64.tar.xz
```

### 3. 工作原理

```
沐曦TOS(Volcengine) ──HTTP──→ 本机(不落盘) ──SSH管道──→ 远程服务器:/opt/file
        ↑                        ↑                        ↑
   签名URL(8h有效)          stream_download.py         paramiko SSH
                              urllib + chunked read     cat > 远程文件
```

- 本机用 `urllib` 分块下载（每块 1MB）
- 通过 paramiko SSH channel 的 `sendall()` 实时写入远程文件
- 显示进度：`已下载/总大小 (百分比)`
- 数据始终在内存中流转，不写入本地磁盘

## 脚本说明

### `scripts/stream_download.py`

```
用法: python scripts/stream_download.py <下载URL> <远程路径>
示例: python scripts/stream_download.py https://... /opt/maca-sdk-3.7.2.0.tar.xz
```

- 从 SSH config 自动读取 `tmp-192.2.0.82` 的服务器配置（IP、端口、密码）
- 用 `urlopen` 流式读取 HTTP 响应
- 通过 paramiko SSH `exec_command("cat > 远程路径")` 写入远程文件
- 每 1MB 显示一次进度

## 注意事项

- 下载 URL 带 Volcengine TOS 签名，**8 小时内有效**，过期需重新获取
- 大文件传输（如 2.9GB SDK）耗时较长，建议后台运行
- SSH 连接需要稳定，断开会导致传输失败（文件不完整）
- 远程目标路径需要有写权限（通常需 root）
- 依赖 `paramiko` 库，若未安装：`pip install paramiko`

## 完成检查

| 步骤 | 检查条件 |
|------|----------|
| 获取下载链接 | opencli 输出包含文件名、大小和 URL |
| 流式传输 | 远程文件大小与沐曦官网标注一致 |
| 文件完整性 | 远程服务器上 `sha256sum <file>` 验证通过 |
