---
name: metax-inference-loop
description: "沐曦 GPU 模型推理部署标准化工作流（5 阶段 spool：环境评估→模型确认→容器编排→压测调优→固化交付）。每次部署新模型从头 spool 一遍。适用于 C500/C550/Mars X203 等沐曦 GPU 上的 vllm/sglang 推理部署，覆盖直通 GPU、sGPU（K8s）、多机三种部署模式。"
agent_created: true
---

# 沐曦 GPU 推理部署 spool

标准化推理部署工作流。每次部署新模型从头 spool 一遍，确保不遗漏、可复现。

## 使用时机

- 老大要求在新模型/新机器上部署推理服务时
- 已有模型的部署配置需要调优时
- 排查部署问题时

## 总览

```
① 环境评估 → ② 模型确认 → ③ 容器编排 → ④ 压测调优 → ⑤ 固化交付
```

任一阶段失败 → 回退到上一阶段，重新 spool。

## 详细参考

完整的 docker-compose 模板、K8s YAML 模板、基准记录模板、调优参数矩阵、故障回溯矩阵见 `references/deployment-loop-full.md`。

---

## 阶段一：环境评估

目的：确认宿主机 GPU 硬件、驱动、网络、拓扑正常。

### 执行步骤

```bash
# GPU 健康
ht-smi
ht-smi --show-event all
dmesg | grep -iE "m[ar][rs]|htcd|mxcd"

# 版本确认
ht-smi --show-version
cat /opt/maca/version.txt

# 网络
ip addr show
ibstat 2>/dev/null
ping -c 3 <peer-ip>

# 拓扑
ht-smi topo -t
ht-smi topo -m
```

### Checkpoint
- [ ] 所有 GPU 在线，无硬件事件报错
- [ ] 驱动版本与镜像兼容
- [ ] 多机 ping 通（如适用）
- [ ] IB 端口 LinkUp（如适用）

---

## 阶段二：模型确认

目的：确认模型规格，选择推理框架和镜像。

### 快速索引

| 模型 | 量化 | TP | 最小GPU | 框架 | 特殊配置 |
|---|---|---|---|---|---|
| MiniMax-M2.5 | W8A8 | 8 | 8 | vllm | `MACA_GRAPH_LAUNCH_MODE=5`, MoE融合 |
| Qwen3.5-397B | W8A8 | 8 | 8 | vllm | 多模态可选 |
| Qwen3.5-35B | W8A8 | 4 | 4 | vllm | 小模型 |
| GLM5 | W8A8 | 4 | 16 | vllm | 推测解码 |
| Kimi-K2.5 | W4A16 | 16 | 16+ | vllm(ray) | 双机 |
| DeepSeek R1/V3.2 | W8A8 | 16 | 16 | sglang | 双机, dp-attention |
| Qwen3-32B | BF16 | 2 | 2 | vllm | 基础 |
| Qwen3-235B | W8A8 | 8 | 8 | vllm(ray) | Triton 优化 |

### 镜像速查

- MiniMax: `mxcr.io/ai-release/maca/vllm-metax:0.13.0-maca.ai3.3.0.303-...`
- Qwen3.5/通用: `pub-registry1.metax-tech.com/ai-opentest/master/maca/vllm-metax:0.15.0-...`
- GLM5: `pub-registry1.metax-tech.com/ai-opentest/dev/vllm-metax:0.14.0-..._glm_w4a8_full`
- sglang: `sglang:0806-632-torch2.6-py310`（docker load）
- Megatron 训练: `cr.metax-tech.com/public-ai-release/maca/megatron-lm:maca.ai3.0.0.5-...`

### Checkpoint
- [ ] 模型文件完整（config.json + tokenizer + 权重）
- [ ] 量化格式与镜像版本匹配
- [ ] GPU 数量满足模型需求
- [ ] 镜像已拉取或可拉取

---

## 阶段三：容器编排

选择对应模板：

### 直通 GPU → docker-compose

```yaml
version: "3.8"
services:
  vllm:
    image: ${IMAGE}
    privileged: true
    network_mode: host
    shm_size: "100gb"
    ulimits:
      memlock: -1
    volumes:
      - /models:/models
    environment:
      - MACA_SMALL_PAGESIZE_ENABLE=1
      - MACA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
    command: >
      vllm serve /models/${MODEL_PATH}
      --tp ${TP}
      --trust-remote-code
      --max-num-batched-tokens ${MAX_BATCHED_TOKENS}
      --gpu-memory-utilization ${GPU_MEM_UTIL}
```

启动：`docker compose up -d`

### sGPU → K8s YAML

```yaml
spec:
  schedulerName: hami-scheduler
  containers:
  - env:
    - name: MCCL_P2P_DISABLE
      value: "1"
    - name: MACA_SMALL_PAGESIZE_ENABLE
      value: "1"
    resources:
      limits:
        metax-tech.com/sgpu: ${COUNT}
        metax-tech.com/vcore: ${VCORE}
        metax-tech.com/vmemory: ${VMEM}
    volumeMounts:
    - name: dshm
      mountPath: /dev/shm
  volumes:
  - name: dshm
    emptyDir:
      medium: Memory
      sizeLimit: "32Gi"
```

### 多机 → 两节点 docker-compose

主节点设 `--node-rank 0`，从节点设 `--node-rank 1`。

关键环境变量：
- `GLOO_SOCKET_IFNAME`, `MCCL_SOCKET_IFNAME`, `MCCL_IB_HCA`
- `MACA_GRAPH_LAUNCH_QUEUE_POLICY=3`, `MCDBG_GRAPH_LAUNCH_QUEUE_POLICY=3`
- `PYTORCH_ENABLE_PG_HIGH_PRIORITY_STREAM=1`

**16 卡专用（不加则带宽减半）：**
- `MCCL_RING_16P1H=1` — 16 卡专用 Ring 算法
- `FORCE_ACTIVE_WAIT=1` — 主动轮询取代被动睡眠
- `MCCL_P2P_LEVEL=SYS` — 允许跨 Switch P2P

### Checkpoint
- [ ] 容器启动成功，GPU 可见
- [ ] 模型加载不报错
- [ ] 多机通信无 timeout

---

## 阶段四：压测调优

### vllm 压测

```bash
vllm bench serve --dataset-name random --random-input-len 2048 \
  --random-output-len 1024 --num-prompts 500 --max-concurrency 64
```

### sglang 压测

```bash
python3 -m sglang.bench_serving --backend sglang --dataset-name sharegpt \
  --num-prompts 500 --request-rate inf --max-concurrency 64
```

### 调优优先级

1. **TP** — 显存分布和计算效率的基础
2. **gpu-memory-utilization** — 从 0.9 起步，OOM 降 0.05
3. **max-num-batched-tokens** — 从 16384 开始逐步上调到 OOM 后回退
4. **max-num-seqs** — 256 → 384 → 512
5. **环境变量** — `MACA_SMALL_PAGESIZE_ENABLE=1`（必设）、MoE 融合等

口诀：TP 先给够 → gpu-mem 拉到 0.9 → batched 从 16384 调起 → 多机先搞通信 → 跑完一轮 spool 再决定要不要下一轮

### Checkpoint
- [ ] 压测稳定运行（不 OOM、不 hang）
- [ ] 吞吐/延迟达标
- [ ] 多机通信带宽正常

---

## 阶段五：固化交付

### 最终产物

```
docker-compose-{model}.yml    # 最终 compose
.env-{model}                  # 环境变量
serve-{model}.sh              # 一键启动脚本
env-{model}.sh                # 独立环境变量
benchmark-{model}.md          # 压测基线
```

### 基准记录模板

```markdown
| 项目 | 值 |
|---|---|
| 部署日期 | YYYY-MM-DD |
| GPU | C500 × N |
| 镜像 | ... |
| TP/DP | 8/1 |
| 吞吐 (tokens/s) | XXX |
| TTFT (ms) | XXX |
| TPOT (ms) | XXX |
```

### Checkpoint
- [ ] 所有产物已归档
- [ ] 启动脚本在新宿主机可复现
- [ ] 基准记录完整
