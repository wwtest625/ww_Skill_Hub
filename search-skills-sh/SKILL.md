---
name: search-skills-sh
description: 从 skills.sh 社区搜索开源 agent skill。找社区能力、探索技能市场时用。
agent_created: true
---

# Search Skills.sh

通过 `npx skills find <query>` 搜索 skills.sh 开放生态中的社区技能。

## 适用场景

用户说以下内容时触发：
- "skills.sh 上搜一下 xxx"
- "看看 skills.sh 有没有 xxx 技能"
- "社区有没有 xxx 相关的 skill"
- "用 skills.sh 找找 xxx"
- "search skills.sh for xxx"
- "find a skill for xxx"

## 使用方法

1. 调用 `npx skills find <query>` 搜索 skill，不带交互参数（非 TTY 模式不可交互）
2. 解析输出结果，提取以下信息：
   - skill 名称（格式：`owner/repo@skill-name`）
   - 安装量（如 `235K installs`）
   - skills.sh 链接
3. 将结果整理展示给用户

### 命令格式

```
npx skills find "<搜索关键词>"
```

### TTY 限制

本环境为非交互式终端（non-TTY），`npx skills` 的交互式选择器（选 agent、确认安装等）无法使用。**只用于搜索**，不做安装。

## 输出格式示例

搜索 "git" 的结果：
```
xixu-me/skills@github-actions-docs 235K installs
└ https://skills.sh/xixu-me/skills/github-actions-docs

github/awesome-copilot@git-commit 36.3K installs
└ https://skills.sh/github/awesome-copilot/git-commit
...
```

## 注意

- `npx skills` 首次运行会下载 CLI 工具，耗时约 10-20 秒
- 搜索结果是按安装量降序排列的
- 如果用户想安装某个 skill，引导用户手动执行 `npx skills add <owner/repo@skill>` 或检查 WorkBuddy 官方市场是否有同类替代
