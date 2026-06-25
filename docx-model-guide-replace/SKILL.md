---
name: docx-model-guide-replace
description: 沐曦 C500 模型测试指导文档模板替换。为新模型生成测试指导文档。
---

# 模型测试指导文档模板替换 Skill

## 用途
基于沐曦 C500 加速卡模型测试指导模板，为新模型生成测试指导文档。

## 触发条件
用户需要为新模型生成测试指导文档时使用。

## 前置条件
- 模板文件路径：`C:\Users\sys49169\Desktop\测试留档\2026-05-c500\H3C 异构服务器  沐曦_C500加速卡_DeepSeek-V4-Flash-FlexSMQ-AWQ-W8A8模型推理测试指导.docx`
- OfficeCLI 已安装到 PATH：`C:\Users\sys49169\.workbuddy\bin\officecli.exe`
- 新模型名称和环境变量/启动命令内容由用户提供

## 工作流程

### 1. 复制文件
```bash
cp "模板文件路径.docx" "新文件名.docx"
```

### 2. 批量替换模型名（officecli）
```bash
officecli batch "新文件名.docx" --commands '[{"command":"set","path":"/","props":{"find":"DeepSeek-V4-Flash-FlexSMQ-AWQ-W8A8","replace":"<新模型名>"}}]'
```

### 3. 替换环境变量块
```bash
# 删除 CUDA_VISIBLE_DEVICES 行（若原模板有）
# 用 officecli remove 按 paraId 删除
officecli remove /body/p[@paraId=00100196]
```

### 4. 替换启动命令参数
```bash
# 替换路径
officecli set --find "/home/models/" --replace "/mnt/data/"
# 替换 TP 参数
officecli set --find "--tp 8 --dp 2" --replace "--tp 4"
# 移除冗余参数行
officecli remove /body/p[@paraId=0010019E]
```

### 5. 验证
```bash
officecli view "新文件名.docx" outline
officecli view "新文件名.docx" text --start 180 --end 210
```

## 注意事项
- `set --find --replace` 是文本级替换，不会破坏 XML 结构
- `remove` 需要按 paraId 路径删除整段
- 模型名在模板中出现约 9 次（标题、表格、路径等）
- `CUDA_VISIBLE_DEVICES=...` 和 `--trust-remote-code` 通常需要删除（新模型不包含）
- `/home/models/` 可能出现在多个位置，批量替换时注意

## 示例
新模型 `Qwen3.6-35B-A3B`：
```bash
officecli batch "新文件.docx" --commands '[{"command":"set","path":"/","props":{"find":"DeepSeek-V4-Flash-FlexSMQ-AWQ-W8A8","replace":"Qwen3.6-35B-A3B"}}]'
officecli batch "新文件.docx" --commands '[{"command":"set","path":"/","props":{"find":"/home/models/","replace":"/mnt/data/"}},{"command":"set","path":"/","props":{"find":"--tp 8 --dp 2","replace":"--tp 4"}}]'
officecli batch "新文件.docx" --commands '[{"command":"remove","path":"/body/p[@paraId=00100196]"},{"command":"remove","path":"/body/p[@paraId=0010019E]"}]'
```
