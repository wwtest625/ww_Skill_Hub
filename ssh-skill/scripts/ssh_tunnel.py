#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH 端口转发（隧道）管理 CLI 工具 v1.0

支持三种隧道模式：
- 本地转发 (local):  将本地端口流量转发到远程目标
- 远程转发 (remote): 将远程端口流量转发到本地目标
- 动态转发 (dynamic): 本地 SOCKS 代理

用法：
    # 启动隧道
    python ssh_tunnel.py <alias> local <local_port>:<remote_host>:<remote_port> [--name NAME]
    python ssh_tunnel.py <alias> remote <remote_port>:<local_host>:<local_port> [--name NAME]
    python ssh_tunnel.py <alias> dynamic <local_port> [--name NAME]

    # 管理隧道
    python ssh_tunnel.py list
    python ssh_tunnel.py status <tunnel_id>
    python ssh_tunnel.py stop <tunnel_id>

示例：
    python ssh_tunnel.py prod-web-01 local 8080:127.0.0.1:3306
    python ssh_tunnel.py dev-server remote 2222:127.0.0.1:22 --name dev-tunnel
    python ssh_tunnel.py bastion dynamic 1080
"""

import sys
import os
import json
import time
import socket
import struct
import signal
import tempfile
import hashlib
import subprocess
import argparse
import threading
from typing import Optional, Dict, List
from pathlib import Path

# 添加 lib 到路径
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_script_dir, 'lib'))

TUNNEL_DIR = os.path.join(tempfile.gettempdir(), 'ssh_tunnels')
HEARTBEAT_INTERVAL = 30


def get_tunnel_id(alias: str, tunnel_type: str, local_port: int) -> str:
    """生成隧道唯一标识"""
    raw = f"{alias}@{tunnel_type}:{local_port}"
    return hashlib.md5(raw.lower().encode('utf-8')).hexdigest()[:12]


def get_tunnel_info_path(tunnel_id: str) -> str:
    """获取隧道信息文件路径"""
    os.makedirs(TUNNEL_DIR, exist_ok=True)
    return os.path.join(TUNNEL_DIR, f'{tunnel_id}.json')


def read_tunnel_info(tunnel_id: str) -> Optional[dict]:
    """读取隧道信息，返回 None 表示不存在或已失效"""
    info_path = get_tunnel_info_path(tunnel_id)
    if not os.path.exists(info_path):
        return None
    try:
        with open(info_path, 'r', encoding='utf-8') as f:
            info = json.load(f)
        pid = info.get('pid')
        if pid and _is_process_alive(pid):
            return info
        os.remove(info_path)
        return None
    except Exception:
        return None


def read_all_tunnels() -> List[dict]:
    """读取所有活跃隧道"""
    if not os.path.exists(TUNNEL_DIR):
        return []
    tunnels = []
    for fname in os.listdir(TUNNEL_DIR):
        if not fname.endswith('.json'):
            continue
        try:
            with open(os.path.join(TUNNEL_DIR, fname), 'r', encoding='utf-8') as f:
                info = json.load(f)
            pid = info.get('pid')
            if pid and _is_process_alive(pid):
                tunnels.append(info)
            else:
                os.remove(os.path.join(TUNNEL_DIR, fname))
        except Exception:
            pass
    return tunnels


def _is_process_alive(pid: int) -> bool:
    """检查进程是否存活"""
    try:
        if os.name == 'nt':
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (OSError, PermissionError):
        return False


def _parse_tunnel_spec(spec: str) -> tuple:
    """解析端口映射字符串，返回 (listen_port, target_host, target_port)"""
    parts = spec.split(':')
    if len(parts) == 2:
        # local_port:remote_port → remote_host=127.0.0.1
        return int(parts[0]), '127.0.0.1', int(parts[1])
    elif len(parts) == 3:
        # local_port:remote_host:remote_port
        return int(parts[0]), parts[1], int(parts[2])
    else:
        raise ValueError(f"无法解析端口映射: {spec}，格式应为 listen:target:port 或 listen:port")


class SSHTunnel:
    """SSH 隧道管理器"""

    def __init__(self, alias: str, tunnel_type: str, listen_port: int,
                 target_host: str = '127.0.0.1', target_port: int = 0,
                 name: str = ''):
        self.alias = alias
        self.tunnel_type = tunnel_type  # 'local', 'remote', 'dynamic'
        self.listen_port = listen_port
        self.target_host = target_host
        self.target_port = target_port
        self.name = name or f"{alias}-{tunnel_type}-{listen_port}"
        self.tunnel_id = get_tunnel_id(alias, tunnel_type, listen_port)
        self._process: Optional[subprocess.Popen] = None

    def _get_connection_params(self) -> dict:
        """从 SSH config 获取连接参数"""
        from config_v3 import SSHConfigLoaderV3
        loader = SSHConfigLoaderV3()
        return loader.get_connection_params(self.alias)

    def start(self) -> dict:
        """启动隧道"""
        params = self._get_connection_params()
        host = params['hostname']
        user = params['user']
        port = params.get('port', 22)
        key_file = params.get('key_file')
        proxy_jump = params.get('proxy_jump')

        # 检查端口是否已被占用
        if self._is_port_in_use(self.listen_port):
            return {
                'success': False,
                'error': f"本地端口 {self.listen_port} 已被占用"
            }

        # 构建 SSH 隧道命令
        args = ['ssh', '-N']  # -N: 不执行远程命令，仅建立隧道

        # SSH 基础参数
        args.extend(['-p', str(port)])
        args.extend(['-o', 'StrictHostKeyChecking=no'])
        args.extend(['-o', 'UserKnownHostsFile=/dev/null'])
        args.extend(['-o', f'ConnectTimeout={params.get("timeout", 30)}'])
        args.extend(['-o', 'ExitOnForwardFailure=yes'])

        # 密钥文件
        if key_file:
            key_file = os.path.expanduser(key_file)
            args.extend(['-i', key_file])

        # ProxyJump（跳板机）
        if proxy_jump:
            args.extend(['-o', f'ProxyJump={proxy_jump}'])

        # 隧道类型参数
        tunnel_arg = ''
        if self.tunnel_type == 'local':
            tunnel_arg = f"-L{self.listen_port}:{self.target_host}:{self.target_port}"
        elif self.tunnel_type == 'remote':
            tunnel_arg = f"-R{self.listen_port}:{self.target_host}:{self.target_port}"
        elif self.tunnel_type == 'dynamic':
            tunnel_arg = f"-D{self.listen_port}"
            if self.target_port == 0:
                self.target_port = 0  # SOCKS 不需要 target
        else:
            return {'success': False, 'error': f"不支持的隧道类型: {self.tunnel_type}"}

        args.append(tunnel_arg)
        args.append(f"{user}@{host}")

        # 启动后台进程
        try:
            if os.name == 'nt':
                CREATE_NO_WINDOW = 0x08000000
                self._process = subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    creationflags=CREATE_NO_WINDOW,
                    text=False
                )
            else:
                self._process = subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                    text=False
                )

            # 等待一小段时间检查隧道是否建立成功
            time.sleep(1.5)

            # 检查进程状态
            retcode = self._process.poll()
            if retcode is not None and retcode != 0:
                stderr_out = ''
                try:
                    stderr_out = self._process.stderr.read().decode('utf-8', errors='replace') if self._process.stderr else ''
                except Exception:
                    pass
                return {
                    'success': False,
                    'error': f"隧道启动失败 (exit code {retcode}): {stderr_out}"
                }

            # 再次确认端口已监听
            if not self._is_port_listening(self.listen_port, wait=2):
                return {
                    'success': False,
                    'error': f"隧道进程已启动但端口 {self.listen_port} 未监听"
                }

            # 保存隧道信息
            info = {
                'tunnel_id': self.tunnel_id,
                'name': self.name,
                'alias': self.alias,
                'type': self.tunnel_type,
                'listen_port': self.listen_port,
                'target_host': self.target_host,
                'target_port': self.target_port,
                'pid': self._process.pid,
                'user': user,
                'host': host,
                'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'command': ' '.join(args)
            }

            info_path = get_tunnel_info_path(self.tunnel_id)
            with open(info_path, 'w', encoding='utf-8') as f:
                json.dump(info, f, ensure_ascii=False, indent=2)

            return {
                'success': True,
                'tunnel_id': self.tunnel_id,
                'name': self.name,
                'pid': self._process.pid,
                'listen_port': self.listen_port,
                'type': self.tunnel_type,
                'target': f"{self.target_host}:{self.target_port}" if self.tunnel_type != 'dynamic' else 'SOCKS proxy',
                'remote': f"{user}@{host}"
            }

        except FileNotFoundError:
            return {'success': False, 'error': 'ssh 命令未找到，请确保已安装 OpenSSH'}
        except Exception as e:
            return {'success': False, 'error': f"启动隧道失败: {str(e)}"}

    def stop(self) -> dict:
        """停止隧道"""
        info = read_tunnel_info(self.tunnel_id)
        if not info:
            return {'success': False, 'error': f"隧道 {self.tunnel_id} 未运行"}

        pid = info.get('pid')
        if pid:
            try:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(pid), '/F'],
                                   capture_output=True, timeout=5)
                else:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(0.5)
                    if _is_process_alive(pid):
                        os.kill(pid, signal.SIGKILL)
            except Exception as e:
                pass

        info_path = get_tunnel_info_path(self.tunnel_id)
        if os.path.exists(info_path):
            os.remove(info_path)

        return {'success': True, 'message': f"隧道 {self.name} 已停止"}

    @staticmethod
    def _is_port_in_use(port: int) -> bool:
        """检查端口是否已被占用"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            return result == 0
        except Exception:
            return False

    @staticmethod
    def _is_port_listening(port: int, wait: int = 0) -> bool:
        """检查端口是否在监听（可选等待）"""
        for _ in range(wait * 2):
            if SSHTunnel._is_port_in_use(port):
                return True
            time.sleep(0.5)
        return SSHTunnel._is_port_in_use(port)


# === CLI 命令 ===

def cmd_start(args) -> dict:
    """启动隧道"""
    alias = args.alias
    tunnel_type = args.tunnel_type
    spec = args.spec
    name = args.name or ''

    try:
        if tunnel_type == 'dynamic':
            listen_port = int(spec)
            target_host = ''
            target_port = 0
        else:
            listen_port, target_host, target_port = _parse_tunnel_spec(spec)
    except ValueError as e:
        return {'success': False, 'error': str(e)}

    tunnel = SSHTunnel(
        alias=alias,
        tunnel_type=tunnel_type,
        listen_port=listen_port,
        target_host=target_host,
        target_port=target_port,
        name=name
    )

    return tunnel.start()


def cmd_stop(tunnel_id: str) -> dict:
    """停止隧道"""
    # 先尝试通过 tunnel_id 查找
    info = read_tunnel_info(tunnel_id)
    if info:
        tunnel = SSHTunnel(
            alias=info['alias'],
            tunnel_type=info['type'],
            listen_port=info['listen_port'],
            target_host=info.get('target_host', '127.0.0.1'),
            target_port=info.get('target_port', 0),
            name=info.get('name', '')
        )
        return tunnel.stop()

    # 通过端口查找
    try:
        port = int(tunnel_id)
        all_tunnels = read_all_tunnels()
        for t in all_tunnels:
            if t.get('listen_port') == port:
                tunnel = SSHTunnel(
                    alias=t['alias'],
                    tunnel_type=t['type'],
                    listen_port=t['listen_port'],
                    target_host=t.get('target_host', '127.0.0.1'),
                    target_port=t.get('target_port', 0),
                    name=t.get('name', '')
                )
                return tunnel.stop()
    except ValueError:
        pass

    # 通过名称查找
    all_tunnels = read_all_tunnels()
    for t in all_tunnels:
        if t.get('name') == tunnel_id or t.get('alias') == tunnel_id:
            tunnel = SSHTunnel(
                alias=t['alias'],
                tunnel_type=t['type'],
                listen_port=t['listen_port'],
                target_host=t.get('target_host', '127.0.0.1'),
                target_port=t.get('target_port', 0),
                name=t.get('name', '')
            )
            return tunnel.stop()

    return {'success': False, 'error': f"未找到匹配的隧道: {tunnel_id}"}


def cmd_status(tunnel_id: str) -> dict:
    """查询隧道状态"""
    info = read_tunnel_info(tunnel_id)
    if not info:
        return {'status': 'not_found', 'tunnel_id': tunnel_id}

    return {
        'status': 'running',
        'tunnel_id': info['tunnel_id'],
        'name': info.get('name', ''),
        'alias': info['alias'],
        'type': info['type'],
        'listen_port': info['listen_port'],
        'target': f"{info.get('target_host', '')}:{info.get('target_port', '')}" if info['type'] != 'dynamic' else 'SOCKS proxy',
        'pid': info['pid'],
        'remote': f"{info.get('user', '')}@{info.get('host', '')}",
        'started_at': info.get('started_at', ''),
        'command': info.get('command', '')
    }


def cmd_list() -> List[dict]:
    """列出所有活跃隧道"""
    tunnels = read_all_tunnels()
    result = []
    for t in tunnels:
        result.append({
            'tunnel_id': t['tunnel_id'],
            'name': t.get('name', ''),
            'alias': t['alias'],
            'type': t['type'],
            'listen_port': t['listen_port'],
            'target': f"{t.get('target_host', '')}:{t.get('target_port', '')}" if t['type'] != 'dynamic' else 'SOCKS proxy',
            'pid': t['pid'],
            'remote': f"{t.get('user', '')}@{t.get('host', '')}",
            'started_at': t.get('started_at', '')
        })
    return result


def main():
    parser = argparse.ArgumentParser(description='SSH 端口转发（隧道）管理工具 v1.0')
    subparsers = parser.add_subparsers(dest='command', help='操作命令')

    # start
    p_start = subparsers.add_parser('start', help='启动隧道')
    p_start.add_argument('alias', help='SSH host 别名')
    p_start.add_argument('tunnel_type', choices=['local', 'remote', 'dynamic'],
                         help='隧道类型: local(本地转发), remote(远程转发), dynamic(SOCKS代理)')
    p_start.add_argument('spec', help='端口映射: 对于local/remote为 listen:host:port，对于dynamic为 listen_port')
    p_start.add_argument('--name', '-n', default='', help='隧道名称（可选）')

    # stop
    p_stop = subparsers.add_parser('stop', help='停止隧道')
    p_stop.add_argument('tunnel_id', help='隧道 ID、端口号或名称')

    # status
    p_status = subparsers.add_parser('status', help='查询隧道状态')
    p_status.add_argument('tunnel_id', help='隧道 ID')

    # list
    p_list = subparsers.add_parser('list', help='列出所有隧道')

    args = parser.parse_args()

    try:
        if args.command == 'start':
            result = cmd_start(args)
            print(json.dumps(result, ensure_ascii=True, indent=2))
            sys.exit(0 if result.get('success') else 1)

        elif args.command == 'stop':
            result = cmd_stop(args.tunnel_id)
            print(json.dumps(result, ensure_ascii=True, indent=2))
            sys.exit(0 if result.get('success') else 1)

        elif args.command == 'status':
            result = cmd_status(args.tunnel_id)
            print(json.dumps(result, ensure_ascii=True, indent=2))
            sys.exit(0)

        elif args.command == 'list':
            tunnels = cmd_list()
            print(json.dumps(tunnels, ensure_ascii=True, indent=2))
            sys.exit(0)

        else:
            parser.print_help()
            sys.exit(1)

    except Exception as e:
        print(json.dumps({
            'success': False,
            'error': f"执行错误: {str(e)}"
        }, ensure_ascii=True, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
