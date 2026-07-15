---
name: bench-platform-api
description: |
  沐曦 GPU 推理压测平台 API 操作工具。用于创建测试任务、补全硬件配置和启动命令、上传压测结果（主表/副表）。
  平台地址：192.2.29.9:9001，X-Token: 1。
  触发词：创建测试任务、上传压测结果、补全硬件配置、创建副表、上传日志到平台、log-import、性能数据入库。
agent_created: true
---

# 沐曦 GPU 推理压测平台 API 操作

## 概述

此 skill 封装沐曦推理压测平台（192.2.29.9:9001）的全部 API 操作，不包括压测执行本身。

## ⚠️ 铁三角规则（强制）

**每次创建任务必须完成以下三项，缺一不可：**

| 序号 | 项目 | API | 检查方式 |
|------|------|-----|----------|
| 1 | 硬件配置 | POST `/test-configurations/` | `gpu_count`, `machine_model`, `framework` 等不为空 |
| 2 | 启动语句 | PATCH `/test-tasks/<id>/` | `startup_command` 不为空 |
| 3 | 性能数据 | POST `/log-import-direct/` 或副表批次 | `performance-test-data` 记录数 > 0 |

**任何一项缺失，任务视为不完整。上传数据时如果发现缺硬件配置或启动语句，必须先补全。**

## 平台固定参数

- API 地址：`http://127.0.0.1:9001`（所有 curl 在 **192.2.29.9** 上执行）
- Header：`X-Token: 1`
- 测试人员：盛炜炜
- SSH 别名：`192.2.29.9`

## GPU 设备速查

| GPU 型号 | 设备 ID | 机器型号 | CPU |
|----------|---------|----------|-----|
| N300-A (MetaX) | 42 | 5330 G7 utrla | Hygon C86-4G (OPN:7490) |

---

## 完整工作流：创建任务 → 补配置 → 上传数据

这是最常用的流程，必须按顺序执行全部三步。

### 步骤 1：创建任务 + 硬件配置 + 启动命令

**1a. 创建任务**

```bash
ssh_execute 192.2.29.9 "curl -s -X POST 'http://127.0.0.1:9001/api/model-storage/test-tasks/' \
  -H 'X-Token: 1' -H 'Content-Type: application/json' \
  -d '{\"model_name\": \"N300-A-<模型简称>\", \"tester\": \"盛炜炜\", \"gpu_device_id\": 42, \"model_type\": \"inference\"}'"
```
记录返回的 `task_id`。

**1b. 补全硬件配置**（POST `/test-configurations/`）

```bash
ssh_execute 192.2.29.9 "curl -s -X POST 'http://127.0.0.1:9001/api/model-storage/test-configurations/' \
  -H 'X-Token: 1' -H 'Content-Type: application/json' \
  -d '{
    \"test_task_id\": <task_id>,
    \"gpu_device_name\": \"N300-A\",
    \"machine_model\": \"5330 G7 utrla\",
    \"gpu_count\": <TP×DP>,
    \"cpu\": \"Hygon C86-4G (OPN:7490)\",
    \"network_card\": \"/\",
    \"framework\": \"<vllm|sglang>\",
    \"framework_version\": \"<版本>\",
    \"data_type\": \"<BF16|w8a8|...>\"
  }'"
```

**自动获取硬件信息：**
- GPU 数量：从启动脚本的 TP/DP 推算（`gpu_count = TP × DP`）
- 框架版本：从压测日志中 grep `Vllm Op Version` 或 `SGlang Op Version`
- 数据类型：从模型 `config.json` 读取；无量化配置 → BF16
- CPU：`ssh_execute <服务器> "cat /proc/cpuinfo | grep 'model name' | head -1"`

**1c. 补全启动命令**（PATCH `/test-tasks/<id>/`）

启动脚本通常位于压测服务器的 `/home/workspace/start_vllm_<模型>.sh`。用 Python 在 192.2.29.9 上执行 PATCH（避免 shell 转义问题）：

```bash
# 先将脚本内容存到 192.2.29.9 的 /tmp/
ssh_execute <压测服务器> "cat /home/workspace/start_vllm_<模型>.sh" > /tmp/startup.sh
# 上传到 192.2.29.9
ssh_upload 192.2.29.9 /tmp/startup.sh /tmp/startup.sh

# 用 Python 调 PATCH（自动处理 JSON 转义）
ssh_execute 192.2.29.9 "python3 -c \"
import json, urllib.request
with open('/tmp/startup.sh') as f:
    cmd = f.read()
data = json.dumps({
    'server_model': '5330 G7 utrla',
    'inference_framework': '<vllm|sglang>',
    'startup_command': cmd
}).encode()
req = urllib.request.Request(
    'http://127.0.0.1:9001/api/model-storage/test-tasks/<task_id>/',
    data=data, method='PATCH',
    headers={'X-Token': '1', 'Content-Type': 'application/json'}
)
resp = urllib.request.urlopen(req)
print(resp.read().decode())
\""
```

### 步骤 2：打包 tar → 上传主表

```bash
# 在压测服务器上打包（日志已在 il_ol_np_mc 格式）
ssh_execute <压测服务器> "cd /home/workspace/benchmark_logs && tar -cf <模型>.tar <模型目录>/ && ls -lh <模型>.tar"

# 下载到本地
ssh_download <压测服务器> /home/workspace/benchmark_logs/<模型>.tar results/<模型>.tar

# 上传到 192.2.29.9
ssh_upload 192.2.29.9 results/<模型>.tar /tmp/<模型>.tar

# 调用 log-import-direct
ssh_execute 192.2.29.9 "curl -s -X POST 'http://127.0.0.1:9001/api/model-storage/log-import-direct/' \
  -H 'X-Token: 1' \
  -F 'task_id=<task_id>' \
  -F 'gpu_device_id=42' \
  -F 'overwrite=true' \
  -F 'file=@/tmp/<模型>.tar'"

# 验证
ssh_execute 192.2.29.9 "curl -s 'http://127.0.0.1:9001/api/model-storage/performance-test-data/?test_task_id=<task_id>&limit=5' -H 'X-Token: 1'"
```

### 步骤 3：铁三角完整性检查

```bash
# 检查三项是否齐全
ssh_execute 192.2.29.9 "python3 -c \"
import json, urllib.request
task_id = <task_id>
# 1. 硬件配置
resp = json.loads(urllib.request.urlopen(f'http://127.0.0.1:9001/api/model-storage/test-configurations/?test_task_id={task_id}').read())
has_config = len(resp.get('data', [])) > 0
# 2. 启动命令
resp2 = json.loads(urllib.request.urlopen(f'http://127.0.0.1:9001/api/model-storage/test-tasks/{task_id}/').read())
has_startup = bool(resp2['data'].get('startup_command', ''))
# 3. 性能数据
resp3 = json.loads(urllib.request.urlopen(f'http://127.0.0.1:9001/api/model-storage/performance-test-data/?test_task_id={task_id}&limit=1').read())
has_data = len(resp3.get('data', [])) > 0
print(f'硬件配置: {\"✅\" if has_config else \"❌ 缺失\"}')
print(f'启动命令: {\"✅\" if has_startup else \"❌ 缺失\"}')
print(f'性能数据: {\"✅\" if has_data else \"❌ 缺失\"}')
print('铁三角完整!' if all([has_config, has_startup, has_data]) else '铁三角不完整，请补全!')
\""
```

---

## 副表操作

### 查询副表

```bash
ssh_execute 192.2.29.9 "curl -s 'http://127.0.0.1:9001/api/model-storage/task-data-sheets/?test_task_id=<task_id>' -H 'X-Token: 1'"
```

### 创建副表

```bash
ssh_execute 192.2.29.9 "curl -s -X POST 'http://127.0.0.1:9001/api/model-storage/task-data-sheets/' \
  -H 'X-Token: 1' -H 'Content-Type: application/json' \
  -d '{\"test_task_id\": <task_id>, \"name\": \"<名称>\", \"description\": \"<描述>\"}'"
```

### 批量上传副表数据

使用 `scripts/upload_sub_table.py`：

```bash
python scripts/upload_sub_table.py \
  --remote <SSH别名> --log-dir <远程日志目录> --sheet-id <副表ID> \
  --gpu-device-id 42 --gpu-count <GPU数> --model-name "<模型简称>" \
  --data-type <数据类型> --framework <框架> --framework-version <版本>
```

## 日志文件命名规范

**必须格式：** `il{输入长度}_ol{输出长度}_np{请求数}_mc{并发数}.log`

平台自动从文件名解析：`r'il(\d+)_ol(\d+)_np(\d+)_mc(\d+)'`

---

## 脚本清单

| 脚本 | 用途 |
|------|------|
| `scripts/upload_sub_table.py` | 解析远程日志 → 批量 POST 到副表 |
| `scripts/upload_main_table.py` | 下载 tar → 上传 192.2.29.9 → log-import-direct |

## 参考文档

`references/platform-api.md` — 完整 API 端点参考、GPU 映射表、字段说明
