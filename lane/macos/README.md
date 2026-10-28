# Lane macOS runtime

本目录是 Lane 在本机 macOS 上的运行时副本，路径以 `/Users/siri` 为准；Linux 继续使用上一级 `scripts/` 与 `lane-fast/`，互不覆盖。

## 安装

```bash
# Python 调度与查看器
cp macos/scripts/* ~/.agent/scripts/
chmod +x ~/.agent/scripts/lane ~/.agent/scripts/*.py

# CLI 入口
ln -sf ~/.agent/scripts/lane ~/.local/bin/lane

# Zig 引擎（macOS 27 SDK 只给 arm64e 提供 libsqlite3.tbd，需静态链接 Homebrew SQLite）
cd macos/lane-fast-src/src
zig build-exe main.zig -target aarch64-macos.15.0 -lc \
  /opt/homebrew/opt/sqlite/lib/libsqlite3.a -O ReleaseFast -femit-bin=/tmp/lane-fast
install -m 755 /tmp/lane-fast ~/.local/bin/lane-fast
```

依赖：`zig 0.14.1`、`sqlcipher 4.20+`（`brew install sqlcipher`）。

## Trae 解密

- 密钥：`~/.agent/trae/key.hex`（0600，不入库）
- 明文镜像：`~/.agent/trae/trae.db`（0600，不入库）
- 索引 JSONL：`~/.agent/trae/projects/`（0600，不入库）
- 增量同步：`lane` 启动时自动执行；手动 `lane sync`
- 解密失败会回退到 Trae memory 摘要

## 数据源

- Qoder：`~/.qoder-cn/projects`
- Antigravity：`~/.gemini/antigravity{,-cli}/conversations`
- Trae：`~/Library/Application Support/Trae CN/ModularData/ai-agent/database.db`（SQLCipher）
- CodeBuddy / Cline：原有目录
