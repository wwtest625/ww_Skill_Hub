#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH Docker 容器执行工具 v1.0

在远程服务器上的 Docker 容器中执行命令。
自动处理两层 non-interactive shell 的环境变量问题。

执行流程：
  ssh_execute → docker exec <container> bash -l -c "<command>"
                          ↑ login shell 加载 .bashrc/.bash_profile

用法：
    python ssh_docker_exec.py <别名> <容器名或ID> "<命令>"
    python ssh_docker_exec.py <别名> <容器名或ID> "<命令>" -e VAR1 -e VAR2
    python ssh_docker_exec.py <别名> <容器名或ID> --shell

示例：
    # 基本执行
    python ssh_docker_exec.py gpu-node my_container "nvidia-smi"
    python ssh_docker_exec.py gpu-node my_container "python -c 'import torch; print(torch.cuda.is_available())'"

    # 传递额外环境变量
    python ssh_docker_exec.py gpu-node my_container "python train.py" -e CUDA_VISIBLE_DEVICES=0,1 -e OMP_NUM_THREADS=4

    # 长命令（多行）
    python ssh_docker_exec.py gpu-node my_container "cd /workspace && git pull && python train.py --epochs 100"

    # 进入容器交互式 shell
    python ssh_docker_exec.py gpu-node my_container --shell

    # 查看容器内环境变量（验证）
    python ssh_docker_exec.py gpu-node my_container "env | sort"

    # 直接在远程服务器上执行 docker ps（不进容器）
    python ssh_docker_exec.py gpu-node docker-ps

依赖：
    - ssh_execute.py（执行远程命令）
    - ssh_connect.py（交互式 shell 模式）
"""

import sys
import os
import json
import subprocess
import argparse
import shlex
import re

# 添加脚本目录到路径
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_dir)


def parse_env_vars(env_args: list) -> list:
    """解析 -e 参数，返回 [('-e', 'KEY=VAL'), ...]"""
    result = []
    for e in env_args:
        e = e.strip()
        if '=' not in e:
            print(f"[警告] 环境变量格式应为 KEY=VALUE，跳过: {e}", file=sys.stderr)
            continue
        result.append(('-e', e))
    return result


def build_docker_command(container: str, command: str,
                         env_vars: list = None, shell: bool = False) -> str:
    """
    构建 docker exec 命令

    - 默认用 bash -l -c 保证环境变量完整
    - 支持 -e 传递额外环境变量
    - --shell 模式走交互式
    """
    parts = ['docker', 'exec']

    # 交互式 shell
    if shell:
        parts.extend(['-it'])

    # 额外环境变量
    if env_vars:
        for key, val in env_vars:
            parts.append(key)
            parts.append(val)

    if shell:
        # 交互式：进入 bash login shell
        parts.append(container)
        parts.append('bash')
        parts.extend(['-l'])  # login shell 加载完整 profile
    elif command:
        # 命令模式：bash -l -c 保证环境变量
        parts.append(container)
        parts.append('bash')
        parts.extend(['-l', '-c'])
        parts.append(command)
    else:
        raise ValueError("必须提供命令或 --shell 参数")

    return ' '.join(shlex.quote(p) for p in parts)


def main():
    parser = argparse.ArgumentParser(
        description='SSH Docker 容器执行工具 v1.0',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %%(prog)s gpu-node my_container "nvidia-smi"
  %(prog)s gpu-node my_container "python train.py" -e CUDA_VISIBLE_DEVICES=0
  %(prog)s gpu-node my_container --shell
        """
    )
    parser.add_argument('alias', help='远程服务器 SSH 别名')
    parser.add_argument('container', nargs='?', default=None,
                        help='容器名或ID（省略时相当于在远程执行 docker 命令）')
    parser.add_argument('command', nargs='?', default=None,
                        help='要在容器内执行的命令')
    parser.add_argument('-e', '--env', dest='env_vars', action='append', default=[],
                        help='传递给容器的环境变量 (KEY=VALUE 格式，可多次使用)')
    parser.add_argument('--shell', '-s', action='store_true',
                        help='交互式 shell 模式（通过 ssh_connect.py 进入容器 bash）')
    parser.add_argument('--timeout', '-t', type=int, default=60,
                        help='命令执行超时（秒），默认 60')
    parser.add_argument('--dry-run', '-n', action='store_true',
                        help='仅打印将要执行的命令，不实际执行')

    args = parser.parse_args()

    # 处理 docker-ps 快捷操作
    if args.container == 'docker-ps':
        args.command = 'docker ps'
        args.container = None

    # 构建要远程执行的命令
    if args.container and args.command:
        env_flags = parse_env_vars(args.env_vars)
        remote_cmd = build_docker_command(
            args.container, args.command, env_flags, shell=False
        )
    elif args.shell and args.container:
        # 交互式模式：走 ssh_connect.py
        # 构建 docker exec -it container bash -l 命令
        env_flags = parse_env_vars(args.env_vars)
        docker_cmd = build_docker_command(
            args.container, '', env_flags, shell=True
        )
        # 通过 ssh_connect.py 执行
        connect_script = os.path.join(_script_dir, 'ssh_connect.py')
        cmd = [
            sys.executable, connect_script,
            args.alias, docker_cmd
        ]
        if args.dry_run:
            print("完整命令:")
            print(' '.join(shlex.quote(c) for c in cmd))
        else:
            # 直接 exec 替换进程，进入交互式
            os.execvp(sys.executable, cmd)
        return
    elif args.container is None and args.command:
        # 没有指定容器：直接在远程执行（如 docker ps, docker images 等）
        remote_cmd = args.command
    else:
        parser.print_help()
        sys.exit(1)

    # 通过 ssh_execute.py 执行远程命令
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

    # 调用 ssh_execute.py 执行
    cmd = [
        sys.executable, execute_script,
        args.alias, remote_cmd,
        '--timeout', str(args.timeout)
    ]
    result = subprocess.run(cmd, capture_output=False, text=True)

    sys.exit(result.returncode)


if __name__ == '__main__':
    main()
