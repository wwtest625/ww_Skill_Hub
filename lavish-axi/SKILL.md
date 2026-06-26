---
name: lavish-axi
description: Human-AI 协作 HTML 编辑器。用于改善 Agent 生成的 HTML 工件的人机协作体验。在生成 HTML 工件后需要人类评审/标注/修改时触发，或在老大明确说"用 lavish"时触发。
agent_created: true
---

# Lavish Axi

## 概述

lavish-axi 是一个本地运行的 HTML 编辑器，专为 AI Agent 与人类协作设计。Agent 生成 HTML 工件 → human 直接在浏览器中标注元素、选择文本、发送反馈 → Agent 通过 CLI 轮询获取修改意见。

**已安装**: v0.1.31，全局安装，node 管理的 bin 目录下。

## 何时触发

- 老大要求打开 HTML 文件做协作编辑
- 生成 HTML artifact 后需要人类评审或反馈
- 老大直接说"用 lavish"或"lavish"或"/lavish"

## CLI 命令参考

| 命令 | 说明 |
|------|------|
| `lavish-axi` | 显示当前会话和使用指南 |
| `lavish-axi <html-file>` | 打开或恢复 Lavish Editor 会话 |
| `lavish-axi poll <html-file>` | 长轮询等待用户反馈或布局警告 |
| `lavish-axi end <html-file>` | 结束会话 |
| `lavish-axi stop` | 关闭后台服务器 |
| `lavish-axi playbook [id]` | 列出或查看剧本指南 |
| `lavish-axi design` | 显示 Tailwind + DaisyUI CDN 回退方案 |

已知 playbook id: `diagram`, `table`, `comparison`, `plan`, `code`, `input`, `slides`

## 使用流程

### 1. 生成 HTML 后交给人类评审

生成 HTML 工件后，调用 lavish-axi 打开：

```bash
lavish-axi path/to/artifact.html
```

浏览器自动打开，显示布局审计帘幕（检测水平溢出、文本截断、重叠等）。

### 2. 轮询获取反馈

Agent 侧用 poll 命令等待：

```bash
lavish-axi poll path/to/artifact.html
```

阻塞等待用户标注、聊天消息或布局警告，返回结构化反馈。

### 3. 编写时嵌入 lavish 支持

在生成 HTML 时，可以：
- 用 `data-lavish-action` 标记可点击元素
- 原生控件（input/select/button/textarea）自动可交互
- 用 `queuePrompt()` API 排队提交反馈

### 4. 配合 playbook 使用

生成结构化的 HTML 模板：

```bash
lavish-axi playbook diagram    # 图表
lavish-axi playbook plan        # 计划
lavish-axi playbook slides      # 幻灯片
lavish-axi playbook code         # 代码 diff
```

Playbook 输出可直接传给 Agent 指令。

## 布局审计系统

打开 HTML 时自动执行：
1. **布局帘幕**：审计完成前遮罩工件
2. **自动检测**：水平溢出、元素溢出、文本截断、文本重叠
3. **警告级别**：error（阻断面罩） / warning（仅提示）

## 配置

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `LAVISH_AXI_HOST` | 服务绑定地址 | `127.0.0.1` |
| `LAVISH_AXI_IDLE_TIMEOUT_MS` | 空闲超时 | 30min |
| `LAVISH_AXI_DEBUG` | 调试模式 | 未设置 |

## 合作方式

- 让 Agent 自行调用 lavish-axi 打开 HTML，然后 poll 等反馈
- 老大直接在浏览器中标注/编辑/写反馈
- Agent 获取反馈后迭代修改 HTML
- 循环直至老大满意，用 `lavish-axi end <file>` 结束会话
