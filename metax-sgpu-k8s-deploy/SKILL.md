---
name: metax-sgpu-k8s-deploy
description: 沐曦 C500 sGPU 软切分部署到 K8s。GPU Operator + HAMi 调度器安装配置全流程。
agent_created: true
---

# Metax sGPU K8s Deployment Skill

## Overview

This skill documents the complete workflow for deploying **沐曦 MetaX C500 sGPU (Soft GPU slicing)** on a Kubernetes cluster using **GPU Operator + HAMi** scheduler.

**Architecture:**
- **GPU Operator** (metax-operator) — manages device discovery, container runtime setup, GPU label/labeling, and sGPU device registration
- **HAMi** — handles sGPU resource scheduling (extender mode alongside kube-scheduler)
- sGPU is a software-based GPU slicing technology — no SR-IOV or hardware virtualization required

**End result:** 8 × MetaX C500 GPUs → 128 sGPU virtual instances registered in K8s.

## When to Use

- User asks to set up sGPU/vGPU/MIG-like GPU slicing on MetaX C500 hardware
- User wants to deploy MetaX GPU Operator with sGPU mode
- User needs HAMi scheduler for managing sGPU resource allocation
- User needs to bypass Docker Hub connectivity issues (use Huawei Cloud mirror)

## Prerequisites

- Kubernetes 1.29+ cluster (tested with kubeadm + containerd)
- containerd v2.x as CRI runtime
- Helm v3 installed
- MetaX GPU Operator offline package (`metax-gpu-k8s-package.X.Y.Z.tar.gz`) from the Metax developer portal
- `mx-smi` already installed on nodes (drivers pre-installed)

## Workflow

### 1. Upload and Extract Offline Package

Upload the `.tar.gz` to the target server (e.g. `/tmp/`) and extract:

```bash
cd /tmp
tar xzf metax-gpu-k8s-package.X.Y.Z.tar.gz
```

Package contents:
- `metax-k8s-images.X.Y.Z.run` — self-extracting container image archive
- `metax-operator-X.Y.Z.tgz` — Helm chart for GPU Operator
- `metax-gpu-extensions-X.Y.Z.tgz` — extensions (CMON, etc.)
- `deployment/grafana-dashboard/sGPU.json` — Grafana dashboard

### 2. Load Container Images

Extract the `.run` archive and load images into containerd (`k8s.io` namespace automatically used):

```bash
cd /tmp
bash metax-k8s-images.X.Y.Z.run --target /tmp/metax-images --noexec
cd /tmp/metax-images
/tmp/metax-k8s-images.X.Y.Z.run ctr load
```

Verify images are loaded:
```bash
ctr -n k8s.io images ls | grep metax
```

### 3. Install GPU Operator

Create a custom `values.yaml`:

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

Install:
```bash
helm upgrade --install metax-operator metax-operator-X.Y.Z.tgz \
  --namespace metax-operator --create-namespace \
  -f values.yaml --timeout 10m
```

### 4. Bypass Driver/MACA Dependency

After installation, the `metax-gpu-device` DaemonSet depends on `maca.ready=true` label. If the driver/MACA pods cannot pull their payload images (they require extra images not in the offline package), manually label the node:

```bash
kubectl label node <node-name> metax-tech.com/maca.ready=true --overwrite
```

This triggers `metax-gpu-device` to start in sGPU mode.

Verify sGPU resource registration:
```bash
kubectl get node <node-name> -o json | jq '.status.capacity'
# Expected output: "metax-tech.com/sgpu": 128
```

### 5. Install HAMi Scheduler

HAMi v2.9.0 is used. If Docker Hub is unreachable, use the Huawei Cloud mirror.

#### 5a. Pull Images from Huawei Cloud Mirror (when Docker Hub is blocked)

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

#### 5b. Install HAMi (Scheduler Only, No Device Plugin)

Key configuration rules:
- `resourceName` and `metaxResourceName` MUST be different (avoids "duplicate extender managed resource name" error)
- `devicePlugin.enabled=false` — MetaX GPU Operator's own gpu-device handles device registration
- `scheduler.admissionWebhook.enabled=false` — if certgen images are unavailable
- `scheduler.patch.enabled=false` — same reason

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

Add node label for HAMi device plugin scheduling (if DaemonSet has `gpu=on` selector):
```bash
kubectl label node <node-name> gpu=on --overwrite
```

### 6. Test sGPU Pod

Create a test Pod that requests 1 sGPU and uses `hami-scheduler`:

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
# Expected: 1/1 Running
```

Verify allocation:
```bash
kubectl describe pod test-sgpu | grep -E 'sgpu|vgpu|allocated'
# Output should show: hami.io/metax-sgpu-devices-allocated
```

### 7. Verify Node GPU Resources

```bash
kubectl describe node <node-name> | grep -A15 'Allocated resources'
# Expected: metax-tech.com/sgpu showing allocated count
ctr -n k8s.io images ls | grep -E 'hami|metax'
```

## Troubleshooting

### "duplicate extender managed resource name"

Cause: `resourceName` and `metaxResourceName` set to the same value.
Fix: Set `resourceName` to a placeholder like `hami.io/unused`.

### ImagePullBackOff on docker.io images

Cause: Docker Hub is blocked in the network environment.
Fix: Use Huawei Cloud mirror `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/` to pull images, then retag.

### metax-gpu-device not starting

Cause: Missing `maca.ready=true` label on node.
Fix: `kubectl label node <node> metax-tech.com/maca.ready=true --overwrite`

### kube-scheduler container crashing (Error)

Check logs: `kubectl logs -n hami-system <scheduler-pod> -c kube-scheduler`
Common cause: duplicate resource names in extender config.

## File Structure

```
metax-sgpu-k8s-deploy/
├── SKILL.md              # This file
├── references/
│   └── api_reference.md  # (placeholder, not used)
├── scripts/
│   └── example.py        # (placeholder, not used)
└── assets/
    └── example_asset.txt # (placeholder, not used)
```
