# 沐曦 GPU 模型推理部署轮

> 标准化推理部署工作流：评估 → 确认 → 编排 → 压测 → 固化
> 适配场景：直通 GPU、sGPU、多机推理

---

## 总览

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        推理部署轮                                        │
│                                                                          │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────┐ │
│  │ ①环境评估  │ → │ ②模型确认  │ → │ ③容器编排  │ → │ ④压测调优  │ → │ ⑤固化交付│ │
│  │ ────────  │   │ ────────  │   │ ────────  │   │ ────────  │   │ ──────  │ │
│  │ GPU健康   │   │ 架构匹配  │   │ compose  │   │ bench    │   │ 脚本归档│ │
│  │ 驱动版本  │   │ 量化类型  │   │ sGPU YAML│   │ 参数迭代  │   │ 基线记录│ │
│  │ 网络连通  │   │ 镜像版本  │   │ 多机配置  │   │ 通信验证  │   │ 配置冻结│ │
│  │ 拓扑检测  │   │ GPU数量   │   │           │   │          │   │        │ │
│  └────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘   └───┬────┘ │
│       └───────┬──────┴──────┬───────┴──────┬───────┴──────┬────────┘      │
│               ↓             ↓              ↓              ↓              │
│         失败回溯：任一阶段失败 → 回退到上一阶段修正                        │
└──────────────────────────────────────────────────────────────────────────┘
```

### 使用方式

每个阶段末尾有 Checkpoint。通过则进入下一阶段，失败则回溯修正后重试。新模型首次部署走完整轮，已有模型的变体部署（如换镜像版本、改 tp/dp）可跳过阶段一、二从阶段三开始。

---

## 阶段一：环境评估

目标：确认宿主机 GPU 硬件、驱动、网络、拓扑一切正常。

### 1.1 GPU 健康检查

```bash
# 所有 GPU 在线？
ht-smi

# 硬件事件检查
ht-smi --show-event all

# 驱动日志检查
dmesg | grep -iE "m[ar][rs]|htcd|mxcd" | tail -30
```

### 1.2 驱动/SDK 版本确认

```bash
# 全量固件版本
ht-smi --show-version

# MACA SDK 版本
cat /opt/maca/version.txt

# HPCC SDK 版本
ls /opt/hpcc*/version.txt 2>/dev/null && cat /opt/hpcc*/version.txt
```

### 1.3 网络配置

```bash
# 查看网卡和 IP
ip addr show

# IB/RoCE 网卡状态
ibstat 2>/dev/null || echo "无 IB 网卡"

# 多机连通测试（替换对端 IP）
ping -c 3 -W 2 <peer-ip> && echo "✅ 可达" || echo "❌ 不可达"
```

### 1.4 GPU 拓扑检测

```bash
# 拓扑树
ht-smi topo -t

# 通信矩阵（P2P 带宽等级）
ht-smi topo -m

# 含 NIC 的全景矩阵
ht-smi topo -n

# NUMA 拓扑
/opt/hwloc/bin/lstopo-no-graphics --of console 2>/dev/null || lstopo-no-graphics --of console
```

### 1.5 Checkpoint ✅

| 检查项 | 通过 | 失败处理 |
|---|---|---|
| `ht-smi` 列出所有 GPU | 继续 | 检查驱动加载(`lsmod \| grep mars`)、内核版本 |
| 无硬件事件报错 | 继续 | `ht-smi --reset` 或 `ht-smi --flr` 重置 |
| 驱动版本符合镜像要求 | 继续 | 升级/降级驱动或换镜像 |
| 多机 ping 通（如有） | 继续 | 检查网线/交换机/VLAN/防火墙 |
| IB 端口 LinkUp（如有） | 继续 | `ibstatus` 检查物理层 |
| `/opt/maca` 存在 | 继续 | 安装 HPCC SDK |

---

## 阶段二：模型确认

目标：确认模型规格、选择对应的推理框架和镜像。

### 2.1 模型配置速查表

| 模型 | 参数量 | 量化 | TP | DP/PP | 最小GPU数 | 框架 | 特殊配置 |
|---|---|---|---|---|---|---|---|
| MiniMax-M2.5 | 420B-MoE | W8A8 | 8 | 1+ | 8 | vllm | `MACA_GRAPH_LAUNCH_MODE=5`, MoE融合 |
| Qwen3.5-397B | 397B-A17B-MoE | W8A8 | 8 | 1+ | 8 | vllm | `gpu-memory-util 0.84`(多模态) |
| Qwen3.5-122B | 122B-A10B | W8A8 | 8 | 1+ | 8 | vllm | — |
| Qwen3.5-35B | 35B-A3B | W8A8 | 4 | 1+ | 4 | vllm | 小模型 |
| GLM5 | MoE | W8A8 | 4 | 4 | 16 | vllm | 推测解码(`--speculative_config`) |
| Kimi-K2.5 | MoE | W4A16 | 16 | 1+ | 16+ | vllm(ray) | `RAY_EXPERIMENTAL_NOSET_CUDA_VISIBLE_DEVICES=1` |
| DeepSeek R1/V3.2 | 671B-MoE | W8A8 | 16 | 4 | 16 | sglang | 双机, dp-attention, 推测解码 |
| Qwen3-32B | 32B-Dense | BF16 | 2 | 1+ | 2 | vllm | 基础场景 |
| Qwen3-235B | 235B-A22B-MoE | W8A8 | 8 | 1+ | 8 | vllm(ray) | Triton 优化变量 |

### 2.2 镜像速查

```bash
# 推理镜像
MINIMAX="mxcr.io/ai-release/maca/vllm-metax:0.13.0-maca.ai3.3.0.303-torch2.8-py312-ubuntu22.04-amd64"
QWEN35="pub-registry1.metax-tech.com/ai-opentest/master/maca/vllm-metax:0.15.0-maca.ai20260227-177-torch2.8-py312-ubuntu22.04-amd64"
GLM5="pub-registry1.metax-tech.com/ai-opentest/dev/vllm-metax:0.14.0-maca.ai3.5.3.102-torch2.8-py310-ubuntu22.04-amd64_glm_w4a8_full"
SGLANG="sglang:0806-632-torch2.6-py310"

# 训练镜像
MEGATRON="cr.metax-tech.com/public-ai-release/maca/megatron-lm:maca.ai3.0.0.5-torch2.4-py310-ubuntu22.04-amd64"
```

### 2.3 镜像版本与 MACA 版本匹配规则

```
vllm-metax:0.13.0  → maca.ai3.3.0.x   → MiniMax-M2.5 专用
vllm-metax:0.14.0  → maca.ai3.5.3.x   → GLM5 专用
vllm-metax:0.15.0  → maca.ai20260227  → Qwen3.5/Kimi-K2.5 通用
vllm-metax:0.17.0  → 更新版           → 不再需要 MACA_DIRECT_DISPATCH
```

> 规则：镜像越新性能越好，但特定模型可能依赖旧版。如果在最新镜像上跑不动，回退到该模型索引表中标记的镜像版本。

### 2.4 模型文件确认

```bash
# 确认模型路径
ls <model-path>/config.json
ls <model-path>/tokenizer.json  || ls <model-path>/tokenizer.model

# 检查模型大小（对照 GPU 显存容量）
du -sh <model-path>
```

### 2.5 Checkpoint ✅

- [ ] 模型架构类型（Dense/MoE）确认
- [ ] 量化格式（W8A8/W4A16/BF16）确认
- [ ] 镜像版本已知、本地已拉取或可拉取
- [ ] GPU 数量满足模型最小需求
- [ ] 模型文件完整（config.json + tokenizer + 权重）
- [ ] 确认使用 vllm 还是 sglang

---

## 阶段三：容器编排

目标：生成可在目标环境中直接使用的容器配置。

### 3.1 选择部署模式

```
直通 GPU（单机） ──→ 模板 A（docker-compose）
sGPU（软切）    ──→ 模板 B（K8s YAML）
多机推理       ──→ 模板 C（多节点 compose）
```

### 3.2 模板 A：直通 GPU 单机（docker-compose）

```yaml
# docker-compose-{model}.yml
version: "3.8"
services:
  vllm:
    image: ${IMAGE}
    container_name: vllm-${MODEL_NAME}
    privileged: true
    network_mode: host
    shm_size: "100gb"
    ulimits:
      memlock: -1
    volumes:
      - /models:/models
      - /data:/data              # 按需
    environment: &env_base
      # 必设
      - MACA_SMALL_PAGESIZE_ENABLE=1
      - MACA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
      # 按模型追加（覆写方式见 .env 文件）
    command: >
      vllm serve /models/${MODEL_PATH}
      --tp ${TP}
      --trust-remote-code
      --max-num-batched-tokens ${MAX_BATCHED_TOKENS}
      --gpu-memory-utilization ${GPU_MEM_UTIL}
      --max-num-seqs ${MAX_SEQS}
```

配套 `.env` 文件：

```bash
# .env-{model}
MODEL_NAME=qwen3-5-35b
MODEL_PATH=Qwen3.5-35B-A3B-W8A8
IMAGE=pub-registry1.metax-tech.com/ai-opentest/master/maca/vllm-metax:0.15.0-...
TP=4
MAX_BATCHED_TOKENS=16384
GPU_MEM_UTIL=0.9
MAX_SEQS=384
```

一键启动：

```bash
# 生成最终配置并启动
export $(cat .env-${MODEL_NAME} | xargs) && \
docker compose -f docker-compose-${MODEL_NAME}.yml up -d

# 查看日志
docker logs -f vllm-${MODEL_NAME}
```

### 3.3 模板 B：sGPU（K8s YAML）

```yaml
# sgpu-{model}.yaml
apiVersion: v1
kind: Pod
metadata:
  name: vllm-${MODEL_NAME}
spec:
  schedulerName: hami-scheduler
  containers:
  - name: vllm
    image: ${IMAGE}
    command:
    - vllm
    - serve
    - /models/${MODEL_PATH}
    - --tp
    - "${TP}"
    - --trust-remote-code
    - --max-num-batched-tokens
    - "${MAX_BATCHED_TOKENS}"
    - --gpu-memory-utilization
    - "${GPU_MEM_UTIL}"
    env:
    - name: MCCL_P2P_DISABLE
      value: "1"
    - name: MACA_SMALL_PAGESIZE_ENABLE
      value: "1"
    - name: MACA_VISIBLE_DEVICES
      value: "0,1,2,3,4,5,6,7"
    resources:
      limits:
        metax-tech.com/sgpu: ${SGPU_COUNT}
        metax-tech.com/vcore: ${VCORE_PER_SGPU}
        metax-tech.com/vmemory: ${VMEM_PER_SGPU}
    volumeMounts:
    - name: dshm
      mountPath: /dev/shm
    - name: models
      mountPath: /models
  volumes:
  - name: dshm
    emptyDir:
      medium: Memory
      sizeLimit: "32Gi"
  - name: models
    hostPath:
      path: /models
```

sGPU 关键差异速记：

```
┌────────────────┬────────────────────┬─────────────────────┐
│     项目       │     sGPU           │     直通 GPU        │
├────────────────┼────────────────────┼─────────────────────┤
│ P2P 通信       │ 不支持             │ 支持                │
│ 容器特权       │ 禁止 privileged    │ 推荐 privileged     │
│ 设备注入       │ 自动注入 /dev/sgpu │ 手动 --device       │
│ 资源声明       │ sgpu/vcore/vmemory │ 无                  │
│ 调度器         │ hami-scheduler     │ 默认调度器          │
│ MCCL_P2P       │ 必须 DISABLE=1     │ 不用设              │
│ /dev/shm       │ 必须手动挂载       │ --shm-size 即可     │
└────────────────┴────────────────────┴─────────────────────┘
```

### 3.4 模板 C：多机推理

**主节点（node-rank 0）：**

```yaml
# docker-compose-{model}-node0.yml
version: "3.8"
services:
  sglang:
    image: ${IMAGE}
    container_name: sglang-${MODEL_NAME}-n0
    privileged: true
    network_mode: host
    shm_size: "100gb"
    volumes:
      - /models:/models
    environment:
      - GLOO_SOCKET_IFNAME=${NET_IFACE}
      - MCCL_SOCKET_IFNAME=${NET_IFACE}
      - MCCL_IB_HCA==${IB_PORTS}
      - MACA_SMALL_PAGESIZE_ENABLE=1
      - MACA_GRAPH_LAUNCH_QUEUE_POLICY=3
      - MCDBG_GRAPH_LAUNCH_QUEUE_POLICY=3
      - PYTORCH_ENABLE_PG_HIGH_PRIORITY_STREAM=1
    command: >
      python3 -m sglang.launch_server
      --model-path /models/${MODEL_PATH}
      --tp ${TP} --dp ${DP}
      --dist-init-addr ${MASTER_IP}:5000
      --nnodes ${NNODES} --node-rank 0
      --attention-backend flashinfer
      --enable-dp-attention
      --quantization ${QUANT}
```

**从节点（node-rank 1-N）：** 复制主节点配置，改 `--node-rank 1/2/...`，并确认 `/etc/hosts` 中主机名解析到业务网 IP（这是最常见踩坑点）。

### 3.5 Checkpoint ✅

- [ ] 容器启动成功（`docker ps -a` 状态为 Up）
- [ ] 容器内 GPU 可见（`docker exec <id> ht-smi`）
- [ ] 模型加载不报错（检查容器日志）
- [ ] 多机时分布式通信正常（logs 中无 timeout 错误）
- [ ] sGPU 时确认设备隔离生效（`ht-smi sgpu`）

---

## 阶段四：压测与调优

目标：找到当前硬件和模型组合的最优参数配置。

### 4.1 基准压测

```bash
# vllm 随机数据压测（推荐）
vllm bench serve \
  --dataset-name random \
  --random-input-len 2048 \
  --random-output-len 1024 \
  --num-prompts 500 \
  --max-concurrency 64

# vllm 固定请求率压测
vllm bench serve \
  --dataset-name random \
  --random-input-len 1024 \
  --random-output-len 512 \
  --num-prompts 1000 \
  --request-rate 128 \
  --max-concurrency 128

# vllm 多模态压测
vllm bench serve \
  --dataset-name random-mm \
  --random-mm-limit-mm-per-prompt '{"image":5,"video":1}' \
  --num-prompts 200

# sglang 压测
python3 -m sglang.bench_serving \
  --backend sglang \
  --dataset-name sharegpt \
  --dataset-path /data/sharegpt_random_500.json \
  --num-prompts 500 \
  --request-rate inf \
  --max-concurrency 64
```

### 4.2 关键指标解读

| 指标 | 含义 | 好 | 差 |
|---|---|---|---|
| **吞吐 (tokens/s)** | 每秒生成 token 数 | 越高越好 | 卡在通信或计算 |
| **请求吞吐 (req/s)** | 每秒完成的请求数 | 越高越好 | 卡在调度或显存 |
| **TTFT (ms)** | 首 token 延迟 | < 1000ms | > 3000ms |
| **TPOT (ms)** | 每 token 生成延迟 | < 50ms | > 200ms |
| **OOM 次数** | 显存溢出 | 0 | 需降 gpu-memory-utilization |

### 4.3 调优参数优先级

```
① TP（张量并行度）
  └─ 影响：显存分布、计算效率、通信开销
  └─ 原则：够用即可（OOM 时加大，通信瓶颈时减小）
  └─ 范围：2/4/8/16

② gpu-memory-utilization
  └─ 影响：可用 KV Cache 大小 → 直接影响最大 batch
  └─ 原则：从 0.9 开始，OOM 则降 0.05，稳定后尝试上提
  └─ 范围：0.8 - 0.95

③ max-num-batched-tokens
  └─ 影响：单次推理最大 token 数 → 吞吐上限
  └─ 原则：从 8192 开始，逐步 16384 → 32768，OOM 退一档
  └─ 范围：4096 - 65536

④ max-num-seqs
  └─ 影响：最大并发请求数
  └─ 原则：先设 256，吞吐有瓶颈时提至 384/512
  └─ 范围：128 - 1024

⑤ swap-space
  └─ 影响：OOM 时 CPU 侧缓存的 KV Cache 量
  └─ 原则：默认 16，OOM 时可扩至 32 或 64
  └─ 范围：4 - 64

⑥ 环境变量
  └─ MACA_SMALL_PAGESIZE_ENABLE=1     → 减少显存碎片（必设）
  └─ MACA_VLLM_ENABLE_MCTLASS_FUSED_MOE=1 → MoE 模型必设
  └─ MACA_GRAPH_LAUNCH_MODE=5         → MiniMax 专用
  └─ MACA_DIRECT_DISPATCH=1           → vllm 0.13.x 需要，0.17+ 不需要
```

### 4.4 调优迭代流程

```
基线压测 → 记录指标 → 调整参数 → 再次压测 → 对比指标 → 决定继续或停止

每次只改一个参数。记录每次的参数和结果到表格。
```

### 4.5 多机通信验证

```bash
# 在容器内执行
# P2P 通信测试（2卡）
mpirun --allow-run-as-root -np 2 -bind-to none \
  /opt/hpcc/samples/hccl_tests/perf/hccl_perf/all_reduce_perf \
  -b 64M -e 64M -f 2 -w 5 -n 10

# 全量通信测试
mpirun --allow-run-as-root -np <总GPU数> -bind-to none \
  /opt/hpcc/samples/hccl_tests/perf/hccl_perf/all_reduce_perf \
  -b 4M -e 512M -f 2 -w 5 -n 10
```

### 4.6 Checkpoint ✅

- [ ] 压测能稳定运行（不 OOM、不 hang）
- [ ] 吞吐/延迟达到预期基线或可接受范围
- [ ] 多机时通信带宽符合预期（参考：8卡 all_reduce 64MB ≈ 240 GB/s）
- [ ] 记录了一次完整调优过程的参数和结果

---

## 阶段五：固化交付

目标：将最优配置归档，做到可复现、可交接、可回溯。

### 5.1 最终产物清单

```
项目目录/
├── docker-compose-{model}.yml     # 最终 docker-compose
├── .env-{model}                   # 环境变量文件
├── serve-{model}.sh               # 一键启动脚本
├── env-{model}.sh                 # 独立环境变量脚本
└── benchmark-{model}.md           # 压测基线记录
```

### 5.2 一键启动脚本

```bash
# serve-{model}.sh
#!/bin/bash
# 模型名：Qwen3.5-35B-A3B
# 镜像：pub-registry1.metax-tech.com/ai-opentest/master/maca/vllm-metax:0.15.0-...
# TP：4  DP：1
# 最终吞吐：XXX tokens/s
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL_NAME="${1:-qwen3-5-35b}"

cd "$SCRIPT_DIR"
source ".env-${MODEL_NAME}"
source "env-${MODEL_NAME}.sh"

docker compose -f "docker-compose-${MODEL_NAME}.yml" up -d
echo "✅ ${MODEL_NAME} 已启动"
echo "日志：docker logs -f vllm-${MODEL_NAME}"
```

```bash
chmod +x serve-{model}.sh
```

### 5.3 基准记录模板

```markdown
# {模型名} 部署基准

## 基本信息

| 项目 | 值 |
|---|---|
| 部署日期 | YYYY-MM-DD |
| 服务器 | H3C XXX / 8×C500 |
| 镜像 | ... |
| 推理框架 | vllm {版本} / sglang {版本} |
| MACA SDK | {版本} |
| 量化 | W8A8 / W4A16 / BF16 |

## 参数配置

| 参数 | 值 |
|---|---|
| TP/DP/PP | 8/1/1 |
| gpu-memory-utilization | 0.9 |
| max-num-batched-tokens | 16384 |
| max-num-seqs | 384 |
| swap-space | 16 |
| 环境变量 | 见 env-{model}.sh |

## 压测结果

| 指标 | 值 |
|---|---|
| 吞吐 (tokens/s) | XXX |
| 请求吞吐 (req/s) | XXX |
| TTFT 平均 (ms) | XXX |
| TTFT P99 (ms) | XXX |
| TPOT 平均 (ms) | XXX |
| TPOT P99 (ms) | XXX |

## 调优记录

| 轮次 | 参数调整 | 吞吐 | TTFT | TPOT | 备注 |
|---|---|---|---|---|---|
| 1 | baseline | X | X | X | — |
| 2 | gpu-mem-util 0.85→0.9 | X | X | X | 无 OOM |
| 3 | max-batched 16384→32768 | X | X | X | 有 OOM，回退 |
```

### 5.4 Checkpoint ✅

- [ ] 所有产物文件已归档
- [ ] 启动脚本在全新宿主机上可复现（拉镜像 + 启动）
- [ ] 基准记录填写完整
- [ ] 所有文件已上传或备份

---

## 快速故障回溯矩阵

| 阶段 | 现象 | 根因 | 处理 |
|---|---|---|---|
| ① | ht-smi 不显示 GPU | 驱动未加载 / 内核不匹配 | `lsmod \| grep mars`，切回 5.15.0-25-generic 内核 |
| ① | GPU 显示但报 AER 错误 | PCIe 链路不稳定 | 检查插槽、重插、`ht-smi --reset` |
| ① | 多机 ping 不通 | VLAN / 防火墙 / 网线 | `ip a` 确认 IP，`traceroute` 查路由 |
| ① | IB 端口 Down | 物理层问题 | `ibstatus`，检查线缆/光模块 |
| ② | 模型加载 OOM | TP 不够 / 量化不匹配 | 加大 TP 或确认量化格式 |
| ② | 模型加载报算子缺失 | 镜像版本不匹配 | 换到该模型已知可用的镜像 |
| ③ | 容器启动挂载失败 | 设备文件不存在 | 检查 `/dev/dri`, `/dev/mxcd`, `/dev/htcd` 是否存在 |
| ③ | 容器内无 GPU | 未加 `--privileged` 或 `--device` | 修正启动参数 |
| ③ | sGPU 容器无设备 | 未指定 `schedulerName: hami-scheduler` | 添加调度器声明 |
| ④ | 压测全超时 | 多机通信不通 | 检查 `MCCL_IB_HCA`、`/etc/hosts`、防火墙 |
| ④ | 压测 OOM | 显存参数过高 | 降 `gpu-memory-utilization` 或 `max-num-batched-tokens` |
| ④ | 推理结果错误 | 量化参数不对 / dtype 不匹配 | 确认 `--quantization` 和 `--dtype` |
| ⑤ | 脚本执行报错 | `.env` 文件未 source | 确认 `set -a; source .env-{model}; set +a` |

---

## 附录

### A. 常用环境变量模板

```bash
# 通用必设
export MACA_SMALL_PAGESIZE_ENABLE=1
export MACA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7

# vllm 0.13.x 需要
export MACA_DIRECT_DISPATCH=1

# MiniMax 专用
export MACA_GRAPH_LAUNCH_MODE=5

# MoE 模型（MiniMax/Qwen3.5-MoE/GLM5/Kimi-K2.5）
export MACA_VLLM_ENABLE_MCTLASS_FUSED_MOE=1
export MACA_VLLM_ENABLE_MCTLASS_PYTHON_API=1

# GLM5 专用
export VLLM_DISABLE_SHARED_EXPERTS_STREAM=1
export PYTORCH_CUDA_ALLOC_CONF="max_split_size_mb:128,garbage_collection_threshold:0.6,expandable_segments:True"
export DISABLE_MAP2XPU=1

# 多机通信
export GLOO_SOCKET_IFNAME=ens15np0
export MCCL_SOCKET_IFNAME=ens15np0
export MCCL_IB_HCA="=mlx5_0,mlx5_1"
export MACA_GRAPH_LAUNCH_QUEUE_POLICY=3
export MCDBG_GRAPH_LAUNCE_QUEUE_POLICY=3
export PYTORCH_ENABLE_PG_HIGH_PRIORITY_STREAM=1
export MCCL_DISABLE_MULTI_NODE_FABRIC=0    # 多机必须=0

# Ray 后端（Kimi-K2.5 / Qwen3-235B）
export RAY_EXPERIMENTAL_NOSET_CUDA_VISIBLE_DEVICES=1

# Triton 优化（Qwen3-235B）
export TRITON_ENABLE_MACA_OPT_MOVE_DOT_OPERANDS_OUT_LOOP=1
export TRITON_ENABLE_MACA_CHAIN_DOT_OPT=1
export TRITON_DISABLE_MACA_OPT_MMA_PREFETCH=1

# sGPU
export MCCL_P2P_DISABLE=1
```

### B. Docker 参数释义速查

| 参数 | 作用 | 必选 |
|---|---|---|
| `--privileged=true` | 特权模式，等效于所有 --device + 关闭 seccomp/apparmor | 推荐 |
| `--device=/dev/dri` | DRM 渲染设备（GPU 访问） | 非特权必选 |
| `--device=/dev/mxcd` | MACA 设备 | 非特权必选 |
| `--device=/dev/infiniband` | IB 网卡 | 多机必选 |
| `--group-add video` | video 组权限 | 所有场景必选 |
| `--network=host` | 主机网络模式 | 所有场景必选（多机需要） |
| `--shm-size 100gb` | 共享内存 | 大模型必选 |
| `--ulimit memlock=-1` | 取消内存锁定限制 | 大模型必选 |

### C. 镜像拉取问题

国内环境 Docker Hub / 沐曦 registry 可能被墙或需认证：

```bash
# 私有 registry 认证
docker login pub-registry1.metax-tech.com
# 输入沐曦提供的账号密码

# 本地导入
docker load -i /path/to/image.tar

# 华为云镜像（sGPU 相关组件）
swr.cn-north-4.myhuaweicloud.com/ddn-k8s/gpu-device-plugin:latest
```

### D. MACA 版本与镜像对应关系

| MACA SDK | vllm-metax 版本 | 代表模型 |
|---|---|---|
| 3.3.0.x | 0.13.0 | MiniMax-M2.5 |
| 3.5.3.x | 0.14.0 | GLM5 |
| 3.7.0.x / ai20260227 | 0.15.0 | Qwen3.5, Kimi-K2.5 |
| 新版 | 0.17.0+ | 最新 |

---

> 来福出品 · 沐曦 GPU 推理部署标准化工作流
