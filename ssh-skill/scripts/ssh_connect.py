#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH 交互式 Shell 连接工具 v1.1

打开一个真正的交互式 SSH 终端（与 ssh_execute.py 的一次性命令不同）。
适用于手动调试、实时操作、查看日志 tail -f 等需要交互的场景。

使用本机原生 ssh 命令，分配 TTY，体验与直接 ssh 连接一致。
支持 SSH Agent Forwarding (-A)：远程服务器可借用本地 ssh-agent 中的密钥。

用法：
    python ssh_connect.py <别名>                 # 交互式 SSH 登录
    python ssh_connect.py <别名> "<命令>"         # 交互式执行命令后退出
    python ssh_connect.py <别名> --cmd "<命令>"   # 同上

示例：
    python ssh_connect.py prod-web-01
    python ssh_connect.py prod-web-01 "tail -f /var/log/app.log"
    python ssh_connect.py dev-server "htop"

兼容模式：
    若当前环境下能获取到 TTY，则直接透传交互式 session。
    若无 TTY（如 AI agent 环境），使用 -t 强制分配伪终端。
"""

import sys
import os
import subprocess
import argparse
import shlex

_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_script_dir, 'lib'))


def _check_ssh_agent() -> bool:
    """检测 ssh-agent 是否在运行且有密钥已加载，决定是否启用 -A"""
    if not os.environ.get('SSH_AUTH_SOCK'):
        return False
    if os.name == 'nt':
        # Windows 下 ssh-agent 机制不同，暂不启用 -A
        return False
    try:
        result = subprocess.run(
            ['ssh-add', '-l'],
            capture_output=True, timeout=5,
            env={**os.environ}
        )
        # ssh-add -l 返回 0 表示有密钥，1 表示 agent 在但无密钥
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def get_ssh_command(alias: str, command: str = None) -> list:
    """
    从 SSH config 读取连接参数，构建原生 ssh 命令

    Returns:
        list of command arguments (suitable for subprocess)
    """
    from config_v3 import SSHConfigLoaderV3
    loader = SSHConfigLoaderV3()
    params = loader.get_connection_params(alias)

    host = params['hostname']
    user = params['user']
    port = params.get('port', 22)
    key_file = params.get('key_file')
    proxy_jump = params.get('proxy_jump')
    forward_agent = params.get('forward_agent', False)

    args = ['ssh']

    # TTY 分配：交互式登录必须
    args.append('-t')

    # SSH Agent Forwarding
    # 条件：config 中 ForwardAgent=yes 或本地 ssh-agent 有密钥
    if forward_agent or _check_ssh_agent():
        args.append('-A')

    # 端口
    if port != 22:
        args.extend(['-p', str(port)])

    # 密钥（有 agent 转发时也带上 -i，作为 fallback）
    if key_file:
        args.extend(['-i', os.path.expanduser(key_file)])

    # 跳板机
    if proxy_jump:
        args.extend(['-o', f'ProxyJump={proxy_jump}'])

    # 超时 & 保活
    args.extend(['-o', f'ConnectTimeout={params.get("timeout", 30)}'])
    args.extend(['-o', 'ServerAliveInterval=30'])
    args.extend(['-o', 'ServerAliveCountMax=3'])

    # StrictHostKeyChecking
    args.extend(['-o', 'StrictHostKeyChecking=no'])
    args.extend(['-o', 'UserKnownHostsFile=/dev/null'])

    # 目标
    args.append(f'{user}@{host}')

    # 附加命令
    if command:
        args.append(command)

    return args


def main():
    parser = argparse.ArgumentParser(description='SSH 交互式 Shell 连接工具 v1.0')
    parser.add_argument('alias', help='SSH host 别名（来自 ~/.ssh/config）')
    parser.add_argument('command', nargs='?', default=None,
                        help='要执行的命令（可选），若不提供则进入交互式 shell')
    parser.add_argument('--cmd', '-c', dest='cmd_opt', default=None,
                        help='要执行的命令（可选），与位置参数互斥')
    parser.add_argument('--dry-run', '-n', action='store_true',
                        help='仅打印将要执行的 ssh 命令，不实际执行')

    args = parser.parse_args()

    # command 优先级：位置参数 > --cmd 选项
    cmd = args.command or args.cmd_opt

    try:
        ssh_args = get_ssh_command(args.alias, cmd)
    except ValueError as e:
        print(f"[错误] 配置加载失败: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"[错误] 配置文件不存在: {e}", file=sys.stderr)
        sys.exit(1)

    # 打印连接信息
    conn_str = ' '.join(
        arg for arg in ssh_args if not arg.startswith('-')
    )
    print(f"[SSH] 正在连接: {conn_str}")

    if args.dry_run:
        print()
        print("完整命令:")
        print(' '.join(shlex.quote(a) for a in ssh_args))
        sys.exit(0)

    try:
        # exec 替换当前进程，直接继承 stdin/stdout/stderr
        os.execvp('ssh', ssh_args)
    except FileNotFoundError:
        print("[错误] ssh 命令未找到，请确保已安装 OpenSSH", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[错误] 启动失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
