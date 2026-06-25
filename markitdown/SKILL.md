---
name: markitdown
description: 微软开源的文件转 Markdown 工具。PDF/DOCX/PPTX/图片等转成 Markdown 给 LLM 用。
---

# MarkItDown — 万物转 Markdown

微软开源的文件→Markdown 转换工具，专为 LLM 管道设计。

## 安装

venv 路径（Windows）：

```bash
"C:/Users/sys49169/.workbuddy/binaries/python/envs/markitdown/Scripts/pip.exe" install 'markitdown[all]'
```

命令路径：

```bash
"C:/Users/sys49169/.workbuddy/binaries/python/envs/markitdown/Scripts/markitdown.exe"
```

Python：

```bash
"C:/Users/sys49169/.workbuddy/binaries/python/envs/markitdown/Scripts/python.exe" -c "from markitdown import MarkItDown; ..."
```

## 支持格式

| 类型 | 格式 | 依赖 |
|------|------|------|
| 文档 | PDF, DOCX, PPTX, XLSX, XLS | 内置 |
| 网页 | HTML | 内置 |
| 数据 | CSV, JSON, XML | 内置 |
| 图片 | JPG, PNG, WEBP, BMP, GIF, TIFF, SVG | [all] |
| 音频 | MP3, WAV, M4A, OGG, FLAC | [all] + ffmpeg |
| 视频 | YouTube 链接（字幕转录） | [all] |
| 电子书 | EPub | 内置 |
| 压缩包 | ZIP（解压后遍历） | 内置 |
| 邮件 | MSG（Outlook） | [all] |
| 其他 | 纯文本、Markdown | 内置 |

## 命令行用法

```bash
# 基本转换
markitdown 文件.pdf > 输出.md
markitdown 文件.docx -o 输出.md

# 管道输入
cat 文件.pdf | markitdown
markitdown < 文件.pdf

# 指定扩展名（管道输入时用）
cat data | markitdown -x .csv

# 版本
markitdown --version

# 列出插件
markitdown --list-plugins
```

## Python API 用法

```python
from markitdown import MarkItDown

# 基础转换
md = MarkItDown()
result = md.convert("test.docx")

result.text_content  # 纯文本（去掉 Markdown 标记）
result.markdown      # Markdown 原文
result.title         # 文档标题（如果有）

# 文件路径、URL、字节流都支持
result = md.convert("https://example.com/doc.pdf")
result = md.convert_local("path/to/file.docx")
result = md.convert_stream(io.BytesIO(data), ".pdf")

# 用 LLM 给图片生成描述
from openai import OpenAI
client = OpenAI()
md = MarkItDown(llm_client=client, llm_model="gpt-4o")
result = md.convert("architecture.png")
# 图片的 EXIF + LLM 描述会写入 Markdown

# 自定义 LLM 提示词
md = MarkItDown(
    llm_client=client,
    llm_model="gpt-4o",
    llm_prompt="Describe this diagram in Chinese, focus on architecture"
)
```

## 常见场景

### 1. 把文档喂给 RAG / LLM 上下文

```bash
markitdown 测试指导.docx > context.md
```

### 2. 批量转文档做知识库

```bash
for f in *.docx; do
    markitdown "$f" -o "${f%.docx}.md"
done
```

### 3. 看图说话（配合 LLM）

```python
md = MarkItDown(llm_client=openai_client, llm_model="gpt-4o")
result = md.convert("screenshot.png")
print(result.markdown)
```

### 4. 音频转文字

```bash
markitdown meeting.mp3 > transcript.md
```

### 5. YouTube 视频转录

```bash
markitdown https://youtu.be/xxx > summary.md
```

## 和 OfficeCLI 对比选型

| 需求 | 用哪个 |
|------|--------|
| 读文档内容 → 喂 LLM | MarkItDown |
| 看文档大纲/结构 | OfficeCLI `view outline` |
| 批量改文档内容 | OfficeCLI `set/add/remove` |
| 转 PDF/图片/音频为文本 | MarkItDown |
| 渲染为 HTML 看排版 | OfficeCLI `view html` |
| 取文档元数据 | OfficeCLI `get --json` |

**互补关系**：MarkItDown 负责"读+转"，OfficeCLI 负责"看+改"。

## 路径速查

- venv：`C:\Users\sys49169\.workbuddy\binaries\python\envs\markitdown`
- pip：`C:\Users\sys49169\.workbuddy\binaries\python\envs\markitdown\Scripts\pip.exe`
- markitdown：`C:\Users\sys49169\.workbuddy\binaries\python\envs\markitdown\Scripts\markitdown.exe`
- python：`C:\Users\sys49169\.workbuddy\binaries\python\envs\markitdown\Scripts\python.exe`
- 版本：0.1.6（截至 2026-06-18）
