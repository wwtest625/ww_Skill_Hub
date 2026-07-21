#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH Kubernetes Pod 执行工具 v1.0

在远程服务器上的 Kubernetes Pod 中执行命令。
处理 kubectl exec 的 shell 环境问题（与 docker exec 类似但更复杂：
容器可能没有 bash，只有 sh，且无法开 login shell）。

执行流程：
  ssh_execute → kubectl exec <pod> [-c <container>] -- <shell> -c "<command>"

用法：
    python ssh_k8s_exec.py <别名> <pod名> "<命令>"
    python ssh_k8s_exec.py <别名> <pod名> "<命令>" -n <namespace>
    python ssh_k8s_exec.py <别名> <pod名> "<命令>" -c <容器名>
    python ssh_k8s_exec.py <别名> <pod名> --shell

示例：
    # 基本执行
    python ssh_k8s_exec.py gpu-node my-pod "nvidia-smi"
    python ssh_k8s_exec.py gpu-node my-pod "python -c 'import torch; print(torch.cuda.is_available())'"

    # 指定 namespace
    python ssh_k8s_exec.py gpu-node my-pod "kubectl get pods" -n kube-system

    # 指定容器（多容器 pod）
    python ssh_k8s_exec.py gpu-node my-pod "python train.py" -c inference

    # 传递环境变量
    python ssh_k8s_exec.py gpu-node my-pod "python train.py" -e CUDA_VISIBLE_DEVICES=0

    # 交互式进入 pod
    python ssh_k8s_exec.py gpu-node my-pod --shell

    # 查看 pod 内环境变量（验证环境）
    python ssh_k8s_exec.py gpu-node my-pod "env | sort"

    # 查看日志（快捷）
    python ssh_k8s_exec.py gpu-node my-pod --logs
    python ssh_k8s_exec.py gpu-node my-pod --logs -n kube-system --tail 100

    # 运行 kubectl 命令（不进 pod）
    python ssh_k8s_exec.py gpu-node k8s-get-pods
"""

import sys
import os
import json
import subprocess
import argparse
import shlex

_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_dir)


def detect_shell_chain(command: str) -> str:
    """
    构建 kubectl exec 命令的 shell 链

    策略：
    1. 优先 bash -c（环境最完整）
    2. 降级到 sh -c（通用）
    3. 如果是简单命令，不包装 shell（避免空 shell 问题）

    对于 GPU 训练场景，大部分镜像（pytorch/tensorflow/nvidia）都有 bash。
    """
    # 检查是否需要 shell 包装
    simple_cmds = {'nvidia-smi', 'hostname', 'whoami', 'uptime', 'env',
                   'printenv', 'pwd', 'id', 'ps', 'ls', 'cat', 'top', 'free'}
    # 如果命令只是一个简单命令，不加 shell 包装
    if command.strip() in simple_cmds:
        return command

    # 对复合命令（包含管道、变量、&& 等），需要 shell 包装
    # 先尝试 bash，因为它更常见且环境更完整
    # 但用 sh 作为 fallback
    # 实际执行时 kubectl 会检测容器内有没有 bash
    needs_shell = any(c in command for c in ['&&', '||', '|', ';', '$',
                                               '>', '<', '`', "'", '"',
                                               'cd ', 'source ', 'export '])
    if needs_shell:
        # 用一个内联检测 trick：
        # 尝试 bash -c，如果 bash 不存在，自动 fallback 到 sh
        return command
    return command


def build_kubectl_command(pod: str, command: str,
                          namespace: str = None,
                          container: str = None,
                          env_vars: list = None,
                          shell: bool = False,
                          follow_logs: bool = False,
                          tail_lines: int = None) -> str:
    """构建 kubectl 命令"""
    parts = ['kubectl']

    # namespace
    if namespace:
        parts.extend(['-n', namespace])

    # 日志模式
    if follow_logs:
        parts.append('logs')
        parts.append(pod)
        if container:
            parts.extend(['-c', container])
        if tail_lines:
            parts.extend(['--tail', str(tail_lines)])
        if not follow_logs:
            parts.append('-f')  # 默认 follow
        return ' '.join(shlex.quote(p) for p in parts)

    # exec 模式
    parts.append('exec')
    parts.append(pod)

    # 交互式 shell
    if shell:
        parts.append('-it')

    # 指定容器
    if container:
        parts.extend(['-c', container])

    # env vars
    if env_vars:
        for e in env_vars:
            e = e.strip()
            if '=' not in e:
                print(f"[警告] 环境变量格式应为 KEY=VALUE，跳过: {e}", file=sys.stderr)
                continue
            parts.extend(['--', 'env', e])

    # 分隔符
    parts.append('--')

    if shell:
        # 交互式：进入 shell
        parts.append('sh')
    elif command:
        cmd = command.strip()
        simple_cmds = {'nvidia-smi', 'hostname', 'whoami', 'uptime',
                       'env', 'printenv', 'pwd', 'id', 'ps', 'ls', 'top'}
        if cmd in simple_cmds:
            parts.append(cmd)
        else:
            # 需要 shell 包装的命令
            # 用 sh -c 保证通用性（所有容器都有 sh）
            parts.append('sh')
            parts.append('-c')
            parts.append(cmd)
    else:
        raise ValueError("必须提供命令或 --shell 参数")

    return ' '.join(shlex.quote(p) for p in parts)


def main():
    parser = argparse.ArgumentParser(
        description='SSH Kubernetes Pod 执行工具 v1.0',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s gpu-node my-pod "nvidia-smi"
  %(prog)s gpu-node my-pod "python train.py" -c inference
  %(prog)s gpu-node my-pod --shell -n default
  %(prog)s gpu-node my-pod --logs --tail 50
        """)
    parser.add_argument('alias', help='远程服务器 SSH 别名')
    parser.add_argument('pod', help='Pod 名称或快捷指令（如 k8s-get-pods）')
    parser.add_argument('command', nargs='?', default=None,
                        help='要在 Pod 内执行的命令')
    parser.add_argument('-n', '--namespace', default=None,
                        help='Kubernetes namespace')
    parser.add_argument('-c', '--container', default=None,
                        help='Pod 中的容器名（多容器 pod 需要指定）')
    parser.add_argument('-e', '--env', dest='env_vars', action='append', default=[],
                        help='传递给容器的环境变量 (KEY=VALUE 格式)')
    parser.add_argument('--shell', '-s', action='store_true',
                        help='交互式 shell 模式')
    parser.add_argument('--logs', '-l', action='store_true',
                        help='查看 Pod 日志')
    parser.add_argument('--tail', type=int, default=None,
                        help='日志尾部行数（配合 --logs 使用）')
    parser.add_argument('--timeout', '-t', type=int, default=120,
                        help='命令执行超时（秒），默认 120')
    parser.add_argument('--dry-run', action='store_true',
                        help='仅打印将要执行的命令，不实际执行')

    args = parser.parse_args()

    # 快捷指令处理
    shortcuts = {
        'k8s-get-pods': 'kubectl get pods',
        'k8s-get-nodes': 'kubectl get nodes',
        'k8s-get-svc': 'kubectl get services',
        'k8s-get-deploy': 'kubectl get deployments',
        'k8s-get-ns': 'kubectl get namespaces',
        'k8s-get-events': 'kubectl get events --sort-by=.lastTimestamp',
        'k8s-get-gpu': 'kubectl get pods -o wide | grep -E "nvidia|gpu" || kubectl get nodes -o json | jq ".items[].status.allocatable | select(.\\"nvidia.com/gpu\\")" 2>/dev/null || echo "no GPU info"',
    }
    if args.pod in shortcuts:
        original_pod = args.pod
        args.command = shortcuts[original_pod]
        args.pod = None
        if args.dry_run:
            print(f"[快捷] {original_pod} → {args.command}")

    # 构建要远程执行的命令
    if args.pod and args.command:
        remote_cmd = build_kubectl_command(
            pod=args.pod,
            command=args.command,
            namespace=args.namespace,
            container=args.container,
            env_vars=args.env_vars,
            shell=False
        )
    elif args.pod and args.shell:
        remote_cmd = build_kubectl_command(
            pod=args.pod,
            command='',
            namespace=args.namespace,
            container=args.container,
            shell=True
        )
        # 交互式模式：走 ssh_connect.py
        connect_script = os.path.join(_script_dir, 'ssh_connect.py')
        cmd = [
            sys.executable, connect_script,
            args.alias, remote_cmd
        ]
        if args.dry_run:
            print("将在远程执行（交互式）:")
            print(f"  目标: {args.alias}")
            print(f"  命令: {remote_cmd}")
            print()
            print("实际 SSH 调用:")
            print(' '.join(shlex.quote(c) for c in cmd))
        else:
            os.execvp(sys.executable, cmd)
        return
    elif args.pod and args.logs:
        remote_cmd = build_kubectl_command(
            pod=args.pod,
            command='',
            namespace=args.namespace,
            container=args.container,
            follow_logs=True,
            tail_lines=args.tail
        )
    elif args.pod is None and args.command:
        # 直接在远程执行 kubectl 命令（不进 pod）
        # 快捷指令可能已带 kubectl 前缀，避免重复
        cmd = args.command
        if cmd.startswith('kubectl '):
            cmd = cmd[8:]  # 去掉 "kubectl " 前缀
        ns_flag = f"-n {args.namespace} " if args.namespace else ''
        remote_cmd = f"kubectl {ns_flag}{cmd}"
    else:
        parser.print_help()
        sys.exit(1)

    # 通过 ssh_execute.py 执行
    execute_script = os.path.join(_script_dir, 'ssh_execute.py')

    if args.dry_run:
        print("远程执行命令:")
        print(f"  目标: {args.alias}")
        print(f"  命令: {remote_cmd}")
        print(f"  超时: {args.timeout}s")
        print()
        print("实际 SSH 调用:")
        print(f"  python {execute_script} {args.alias} \"{remote_cmd}\" --timeout {args.timeout}")
        return

    cmd = [
        sys.executable, execute_script,
        args.alias, remote_cmd,
        '--timeout', str(args.timeout)
    ]
    result = subprocess.run(cmd, capture_output=False, text=True)
    sys.exit(result.returncode)


if __name__ == '__main__':
    main()
