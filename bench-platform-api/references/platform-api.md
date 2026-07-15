# 平台 API 参考

## 基础信息

- 服务器：192.2.29.9
- API 地址：http://127.0.0.1:9001
- 认证：`X-Token: 1`
- 代码位置：`C:\Users\admin\Documents\GitHub\spug\spug_api\apps\model_storage\`

## API 端点速查

### 测试任务 (TestTask)

| 方法 | 端点 | 用途 |
|------|------|------|
| POST | `/test-tasks/` | 创建任务 |
| GET | `/test-tasks/<id>/` | 查看任务 |
| PATCH | `/test-tasks/<id>/` | 更新任务（机型、框架、启动命令） |

**创建必填字段：** `model_name`, `tester`, `gpu_device_id`, `model_type`

### 测试配置 (TestConfiguration)

| 方法 | 端点 | 用途 |
|------|------|------|
| POST | `/test-configurations/` | 创建配置 |
| GET | `/test-configurations/?test_task_id=<id>` | 查询配置 |
| PUT | `/test-configurations/<id>/` | 更新配置 |

**关键字段：** `test_task_id`, `gpu_device_name`, `machine_model`, `gpu_count`, `cpu`, `network_card`, `framework`, `framework_version`, `data_type`

### 性能测试数据 (PerformanceTestData) — 主表

| 方法 | 端点 | 用途 |
|------|------|------|
| GET | `/performance-test-data/?test_task_id=<id>` | 查询 |
| POST | `/performance-test-data/` | 单条创建 |
| POST | `/performance-test-data/batch/` | 批量创建 |

### 日志导入

| 方法 | 端点 | 用途 |
|------|------|------|
| POST | `/log-import-analysis/` | 分析 tar（预览，不入库） |
| POST | `/log-import-direct/` | 直接导入 tar 到主表 |

`log-import-direct` 参数（multipart/form-data）：
- `task_id` (必填)
- `file` (必填，tar/zip/tar.gz/tgz，最大 100MB)
- `gpu_device_id` (可选)
- `overwrite` (默认 true)

### 副表 (TaskDataSheet)

| 方法 | 端点 | 用途 |
|------|------|------|
| GET | `/task-data-sheets/?test_task_id=<id>` | 查询副表列表 |
| POST | `/task-data-sheets/` | 创建副表 |

**创建字段：** `test_task_id`, `name` (必填), `sort_order`, `description`

### 副表数据 (TaskDataSheetRecord)

| 方法 | 端点 | 用途 |
|------|------|------|
| GET | `/task-data-sheet-records/?sheet_id=<id>` | 查询记录 |
| POST | `/task-data-sheet-records/` | 单条创建 |
| POST | `/task-data-sheet-records/batch/` | 批量覆盖写入 |

**批量上传格式：**
```json
{
  "sheet_id": 30,
  "data": [
    {
      "filename": "il1047552_ol1024_np1_mc1.log",
      "model_name": "DeepSeek-V4-Flash-W8A8",
      "success_requests": 1,
      "concurrency": 1,
      "requests_count": 1,
      "benchmark_duration": 1918.09,
      "input_tokens": 1047552,
      "output_tokens": 1024,
      "request_throughput": 0.0,
      "output_token_throughput": 0.53,
      "total_token_throughput": 546.68,
      "avg_ttft": 1895124.49,
      "median_ttft": 1895124.49,
      "p99_ttft": 1895124.49,
      "avg_tpot": 22.45,
      "median_tpot": 22.45,
      "p99_tpot": 22.45,
      "input_length": 1047552,
      "output_length": 1024,
      "gpu_device_id": 42,
      "gpu_count": 16,
      "machine_model": "5330 G7 utrla",
      "dataset": "random",
      "data_type": "w8a8",
      "framework": "vllm",
      "framework_version": "0.20.0"
    }
  ]
}
```

## 文件名解析规则

平台自动从文件名提取参数，格式：`il<输入长度>_ol<输出长度>_np<请求数>_mc<并发数>.log`

正则：`r'il(\d+)_ol(\d+)_np(\d+)_mc(\d+)'`

## GPU 设备列表

通过 `GET /api/model-storage/gpus/` 获取完整列表。本项目常用：

| 设备名称 | 设备 ID | 厂商 |
|----------|---------|------|
| N300-A | 42 | MetaX |
