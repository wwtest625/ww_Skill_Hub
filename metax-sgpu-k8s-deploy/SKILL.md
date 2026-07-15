---
name: metax-sgpu-k8s-deploy
description: 沐曦 C500 sGPU 软切分部署到 K8s。GPU Operator + HAMi 调度器安装配置全流程。
agent_created: true
disable: true
---

# Metax sGPU K8s 部署

## 概述

沐曦 MetaX C500 sGPU（软切分）在 Kubernetes 集群上的完整部署流程。使用 **GPU Operator + HAMi** 调度器。

**架构：**
- **GPU Operator**（metax-operator）— 管理设备发现、容器运行时设置、GPU 标签注册和 sGPU 设备注册
- **HAMi** — 处理 sGPU 资源调度（extender 模式，与 kube-scheduler 配合）
- sGPU 是基于软件的 GPU 切分技术，无需 SR-IOV 或硬件虚拟化

**最终结果：** 8 × MetaX C500 GPU → K8s 中注册 128 个 sGPU 虚拟实例。

## 使用时机

- User asks to set up sGPU/vGPU/MIG-like GPU slicing on MetaX C500 hardware
- User wants to deploy MetaX GPU Operator with sGPU mode
- User needs HAMi scheduler for managing sGPU resource allocation
- User needs to bypass Docker Hub connectivity issues (use Huawei Cloud mirror)

## 前置条件

- Kubernetes 1.29+ 集群（基于 kubeadm + containerd）
- containerd v2.x CRI 运行时
- Helm v3 已安装
- MetaX GPU Operator 离线包（`metax-gpu-k8s-package.X.Y.Z.tar.gz`，沐曦开发者门户下载）
- 节点上 `mx-smi` 已安装（驱动已就绪）

## Workflow

### 1. 上传并解压离线包

将 `.tar.gz` 上传到目标服务器（如 `/tmp/`）并解压：

```bash
cd /tmp
tar xzf metax-gpu-k8s-package.X.Y.Z.tar.gz
```

包内容：
- `metax-k8s-images.X.Y.Z.run` — 自解压容器镜像归档
- `metax-operator-X.Y.Z.tgz` — GPU Operator Helm chart
- `metax-gpu-extensions-X.Y.Z.tgz` — 扩展组件（CMON 等）
- `deployment/grafana-dashboard/sGPU.json` — Grafana 监控面板

> ✅ 完成检查：`ls /tmp/metax-operator*` 确认解压成功

### 2. 加载容器镜像

解压 `.run` 归档并加载镜像到 containerd（自动使用 `k8s.io` 命名空间）：

```bash
cd /tmp
bash metax-k8s-images.X.Y.Z.run --target /tmp/metax-images --noexec
cd /tmp/metax-images
/tmp/metax-k8s-images.X.Y.Z.run ctr load
```

验证镜像已加载：
```bash
ctr -n k8s.io images ls | grep metax
```

> ✅ 完成检查：`ctr -n k8s.io images ls | grep metax` 输出包含 metax 相关镜像

### 3. 安装 GPU Operator

创建自定义 `values.yaml`：

```yaml
registry: cr.metax-tech.com/cloud
pullPolicy: IfNotPresent

controller:
  replicaCount: 1

driver:
  deployPolicy: IfNotExist

gpuDevice:
  config:
    mode: sgpu          # Enable sGPU mode
    shareNums: 2        # Virtual instances per physical card (8 cards → 16)
  log: {}

gpuScheduler:
  deploy: false         # Use HAMi instead

dataExporter:
  deploy: false

topoDiscovery:
  deploy: false

cluster:
  type: k8s
```

安装：
```bash
helm upgrade --install metax-operator metax-operator-X.Y.Z.tgz \
  --namespace metax-operator --create-namespace \
  -f values.yaml --timeout 10m
```

> ✅ 完成检查：`kubectl -n metax-operator get pods` 所有 Pod 处于 Running 状态

### 4. 绕过驱动/MACA 依赖

安装后 `metax-gpu-device` DaemonSet 依赖 `maca.ready=true` 标签。如果驱动/MACA Pod 无法拉取镜像（离线包外的额外镜像），手动标记节点：

```bash
kubectl label node <node-name> metax-tech.com/maca.ready=true --overwrite
```

此操作触发 `metax-gpu-device` 以 sGPU 模式启动。

验证 sGPU 资源注册：
```bash
kubectl get node <node-name> -o json | jq '.status.capacity'
# 预期输出："metax-tech.com/sgpu": 128
```

> ✅ 完成检查：节点 capacity 中 `metax-tech.com/sgpu` 数量符合预期（每卡 shareNums × 卡数）

### 5. 安装 HAMi 调度器

使用 HAMi v2.9.0。如果 Docker Hub 不可用，使用华为云镜像。

#### 5a. 从华为云镜像拉取（Docker Hub 不可用时）

```bash
# Pull main HAMi image
ctr -n k8s.io images pull \
  swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/projecthami/hami:v2.9.0
ctr -n k8s.io images tag \
  swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/projecthami/hami:v2.9.0 \
  docker.io/projecthami/hami:v2.9.0

# Pull webhook certgen images (optional, only if admission webhook is enabled)
ctr -n k8s.io images pull \
  swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/liangjw/kube-webhook-certgen:v1.1.1
ctr -n k8s.io images tag \
  swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/liangjw/kube-webhook-certgen:v1.1.1 \
  docker.io/liangjw/kube-webhook-certgen:v1.1.1
```

#### 5b. 安装 HAMi（仅调度器，不装设备插件）

关键配置规则：
- `resourceName` 和 `metaxResourceName` **必须不同**（避免 "duplicate extender managed resource name" 错误）
- `devicePlugin.enabled=false` — MetaX GPU Operator 的 gpu-device 负责设备注册
- `scheduler.admissionWebhook.enabled=false` — 如果 certgen 镜像不可用
- `scheduler.patch.enabled=false` — 同上

```bash
helm upgrade --install hami hami-2.9.0.tgz --namespace hami-system --create-namespace \
  --set global.imageTag=v2.9.0 \
  --set resourceName=hami.io/unused \
  --set metaxResourceName=metax-tech.com/sgpu \
  --set metaxResourceCore=metax-tech.com/vcore \
  --set metaxResourceMem=metax-tech.com/vmemory \
  --set schedulerName=hami-scheduler \
  --set scheduler.kubeScheduler.image.registry=registry.cn-hangzhou.aliyuncs.com \
  --set scheduler.kubeScheduler.image.repository=google_containers/kube-scheduler \
  --set scheduler.kubeScheduler.image.tag=v1.29.15 \
  --set scheduler.kubeScheduler.image.pullPolicy=IfNotPresent \
  --set scheduler.extender.image.registry=docker.io \
  --set scheduler.extender.image.repository=projecthami/hami \
  --set scheduler.extender.image.tag=v2.9.0 \
  --set scheduler.extender.image.pullPolicy=IfNotPresent \
  --set scheduler.admissionWebhook.enabled=false \
  --set scheduler.patch.enabled=false \
  --set devicePlugin.enabled=false \
  --set prometheus.enabled=false
```

添加节点标签供 HAMi 调度（如 DaemonSet 使用 `gpu=on` 选择器）：
```bash
kubectl label node <node-name> gpu=on --overwrite
```

> ✅ 完成检查：`kubectl -n hami-system get pods` 显示 hami-scheduler 和 hami-extender 均 Running

### 6. 测试 sGPU Pod

创建测试 Pod，请求 1 个 sGPU 并使用 `hami-scheduler`：

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: test-sgpu
  annotations:
    hamit.io/gpu-device-plugin: metax
spec:
  schedulerName: hami-scheduler
  containers:
  - name: test
    image: cr.metax-tech.com/cloud/gpu-label:0.15.2
    command: ["sleep", "3600"]
    resources:
      limits:
        metax-tech.com/sgpu: 1
  restartPolicy: Never
```

```bash
kubectl apply -f test-sgpu.yaml
kubectl get pods -o wide | grep test-sgpu
# 预期：1/1 Running
```

验证资源分配：
```bash
kubectl describe pod test-sgpu | grep -E 'sgpu|vgpu|allocated'
# 输出应包含：hami.io/metax-sgpu-devices-allocated
```

> ✅ 完成检查：Pod 状态为 Running，`describe` 显示已分配 sGPU 设备

### 7. 验证节点 GPU 资源

```bash
kubectl describe node <node-name> | grep -A15 'Allocated resources'
# 预期：metax-tech.com/sgpu 显示已分配数量
ctr -n k8s.io images ls | grep -E 'hami|metax'
```

> ✅ 完成检查：节点 Allocated resources 显示 sGPU 分配记录，镜像列表包含 hami/metax 镜像

## 故障排查

### "duplicate extender managed resource name"

原因：`resourceName` 和 `metaxResourceName` 设为相同值。
解决：`resourceName` 设为占位值如 `hami.io/unused`。

### ImagePullBackOff on docker.io images

原因：网络环境屏蔽了 Docker Hub。
解决：使用华为云镜像 `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/` 拉取后重新打标签。

### metax-gpu-device 未启动

原因：节点缺少 `maca.ready=true` 标签。
解决：`kubectl label node <node> metax-tech.com/maca.ready=true --overwrite`

### kube-scheduler 容器崩溃（Error）

检查日志：`kubectl logs -n hami-system <scheduler-pod> -c kube-scheduler`
常见原因：extender 配置中存在重复资源名称。
