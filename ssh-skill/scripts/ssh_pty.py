#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH PTY 交互式终端 v1.0

基于 Paramiko invoke_shell + pyte 终端模拟器。
支持 mysql CLI、python REPL、tail -f、交互式问答等。

与 ssh_shell.py 的区别：
  ssh_shell.py — 标记法退出码检测，适合"发命令→拿结果"
  ssh_pty.py   — pyte 终端模拟，适合"多轮交互式对话"（mysql/REPL/问答）

用法：
    python ssh_pty.py <alias>                          启动 PTY 会话
    python ssh_pty.py <alias> "<command>"              发送命令并获取输出
    python ssh_pty.py <alias> --send-keys "ctrl+c"     发送特殊按键
    python ssh_pty.py <alias> --snapshot               获取当前屏幕快照
    python ssh_pty.py <alias> --interactive "<command>" 交互式执行（等待 prompt）
"""

import sys
import os
import json
import time
import socket
import struct
import tempfile
import hashlib
import threading
import argparse
import re
import subprocess
from typing import Optional, List, Dict

_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_script_dir, 'lib'))

PTY_DIR = os.path.join(tempfile.gettempdir(), 'ssh_pty')
PTY_IDLE_TIMEOUT = 600
PTY_COLS = 160
PTY_ROWS = 40
SCROLLBACK = 5000
QUIET_TIMEOUT = 3.0  # 输出静止 3 秒认为命令执行完
READ_CHUNK = 65536


# === 按键编码映射 ===

KEY_MAP = {
    'ctrl+c': b'\x03',
    'ctrl+d': b'\x04',
    'ctrl+z': b'\x1a',
    'ctrl+l': b'\x0c',
    'ctrl+a': b'\x01',
    'ctrl+e': b'\x05',
    'ctrl+k': b'\x0b',
    'ctrl+u': b'\x15',
    'ctrl+w': b'\x17',
    'ctrl+r': b'\x12',
    'ctrl+s': b'\x13',
    'ctrl+q': b'\x11',
    'tab': b'\t',
    'enter': b'\r',
    'esc': b'\x1b',
    'up': b'\x1b[A',
    'down': b'\x1b[B',
    'right': b'\x1b[C',
    'left': b'\x1b[D',
    'home': b'\x1b[H',
    'end': b'\x1b[F',
    'pageup': b'\x1b[5~',
    'pagedown': b'\x1b[6~',
    'delete': b'\x1b[3~',
    'backspace': b'\x7f',
    'space': b' ',
    'f1': b'\x1bOP',
    'f2': b'\x1bOQ',
    'f3': b'\x1bOR',
    'f4': b'\x1bOS',
    'f5': b'\x1b[15~',
    'f6': b'\x1b[17~',
    'f7': b'\x1b[18~',
    'f8': b'\x1b[19~',
    'f9': b'\x1b[20~',
    'f10': b'\x1b[21~',
    'f11': b'\x1b[23~',
    'f12': b'\x1b[24~',
}


def encode_keys(keys_str: str) -> bytes:
    """将按键描述字符串转为原始字节"""
    keys_str = keys_str.strip().lower()
    if keys_str in KEY_MAP:
        return KEY_MAP[keys_str]
    # 支持十六进制：0x1b 0x5b 0x41
    if keys_str.startswith('0x') or keys_str.startswith('\\x'):
        parts = keys_str.replace('\\x', '0x').split()
        try:
            return bytes(int(p, 16) for p in parts)
        except ValueError:
            pass
    # 直接当文本发送
    return keys_str.encode('utf-8')


# === PTY 会话管理 ===

def get_pty_id(alias: str) -> str:
    return hashlib.md5(alias.lower().encode('utf-8')).hexdigest()[:12]


def get_pty_info_path(pty_id: str) -> str:
    os.makedirs(PTY_DIR, exist_ok=True)
    return os.path.join(PTY_DIR, f'{pty_id}.json')


def read_pty_info(pty_id: str) -> Optional[dict]:
    path = get_pty_info_path(pty_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            info = json.load(f)
        pid = info.get('pid')
        if pid and _is_process_alive(pid):
            return info
        os.remove(path)
        return None
    except Exception:
        return None


def _is_process_alive(pid: int) -> bool:
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


# === Socket 通信 ===

def _send_message(sock, data: dict):
    payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
    header = struct.pack('!I', len(payload))
    sock.sendall(header + payload)


def _recv_message(sock, timeout=None) -> dict:
    if timeout:
        sock.settimeout(timeout)
    header = b''
    while len(header) < 4:
        chunk = sock.recv(4 - len(header))
        if not chunk:
            raise ConnectionError("连接已关闭")
        header += chunk
    length = struct.unpack('!I', header)[0]
    if length > 10 * 1024 * 1024:
        raise ValueError(f"消息过大: {length} bytes")
    body = b''
    while len(body) < length:
        chunk = sock.recv(min(65536, length - len(body)))
        if not chunk:
            raise ConnectionError("连接已关闭")
        body += chunk
    return json.loads(body.decode('utf-8'))


# === PTY 守护进程 ===

class PTYDaemon:
    """PTY 交互式会话守护进程"""

    def __init__(self, alias: str, idle_timeout: int = PTY_IDLE_TIMEOUT):
        self.alias = alias
        self.pty_id = get_pty_id(alias)
        self.idle_timeout = idle_timeout
        self._running = False
        self._server_sock = None
        self._channel = None
        self._client = None
        self._screen = None
        self._stream = None
        self._lock = threading.Lock()
        self._last_activity = time.time()
        self._read_thread = None

    def start(self) -> dict:
        # 检查已有会话
        existing = read_pty_info(self.pty_id)
        if existing:
            return {
                'success': True,
                'pty_id': self.pty_id,
                'port': existing.get('port'),
                'alias': self.alias,
                'status': 'already_running'
            }

        try:
            import pyte
            import paramiko
        except ImportError as e:
            return {'success': False, 'error': f'缺少依赖: {e}'}

        # 加载配置
        from config_v3 import SSHConfigLoaderV3
        loader = SSHConfigLoaderV3()
        try:
            params = loader.get_connection_params(self.alias)
        except Exception as e:
            return {'success': False, 'error': f'配置加载失败: {e}'}

        host = params['hostname']
        user = params['user']
        port = params.get('port', 22)
        key_file = params.get('key_file')
        password = params.get('password')

        # 建立 SSH 连接
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs = {
                'hostname': host, 'port': port, 'username': user,
                'timeout': 30,
            }
            if password:
                connect_kwargs['password'] = password
                connect_kwargs['look_for_keys'] = False
                connect_kwargs['allow_agent'] = False
            elif key_file:
                key_file = os.path.expanduser(key_file)
                pkey = None
                for kc in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]:
                    try:
                        pkey = kc.from_private_key_file(key_file)
                        break
                    except Exception:
                        continue
                if pkey is None:
                    return {'success': False, 'error': f'无法加载密钥: {key_file}'}
                connect_kwargs['pkey'] = pkey
                connect_kwargs['look_for_keys'] = False
                connect_kwargs['allow_agent'] = False
            else:
                return {'success': False, 'error': '未提供认证方式'}

            client.connect(**connect_kwargs)

            # 创建 PTY 通道
            channel = client.invoke_shell(
                term='xterm-256color',
                width=PTY_COLS,
                height=PTY_ROWS
            )

            # 创建 pyte 终端模拟器
            screen = pyte.Screen(PTY_COLS, PTY_ROWS)
            stream = pyte.Stream(screen)

            self._client = client
            self._channel = channel
            self._screen = screen
            self._stream = stream
            self._running = True

            # 启动读取线程（持续 feed pyte）
            self._read_thread = threading.Thread(
                target=self._read_loop, daemon=True
            )
            self._read_thread.start()

            # 启动 socket 服务
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind(('127.0.0.1', 0))
            self._server_sock.listen(5)
            self._server_sock.settimeout(3.0)
            server_port = self._server_sock.getsockname()[1]

            # 保存会话信息
            info = {
                'pty_id': self.pty_id,
                'alias': self.alias,
                'pid': os.getpid(),
                'port': server_port,
                'remote': f"{user}@{host}",
                'started_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'idle_timeout': self.idle_timeout,
                'cols': PTY_COLS,
                'rows': PTY_ROWS
            }
            info_path = get_pty_info_path(self.pty_id)
            with open(info_path, 'w', encoding='utf-8') as f:
                json.dump(info, f, ensure_ascii=False, indent=2)

            # 启动空闲检测
            idle_thread = threading.Thread(
                target=self._idle_check, daemon=True
            )
            idle_thread.start()

            # 主循环
            try:
                while self._running:
                    try:
                        client_sock, _ = self._server_sock.accept()
                        t = threading.Thread(
                            target=self._handle_client,
                            args=(client_sock,), daemon=True
                        )
                        t.start()
                    except socket.timeout:
                        continue
                    except OSError:
                        if self._running:
                            raise
                        break
            except KeyboardInterrupt:
                pass
            finally:
                self._shutdown()

            return {'success': True, 'status': 'started', 'pty_id': self.pty_id}

        except Exception as e:
            return {'success': False, 'error': f'启动失败: {e}'}

    def _read_loop(self):
        """持续读取 PTY 输出并 feed 到 pyte"""
        while self._running and self._channel and not self._channel.closed:
            try:
                if self._channel.recv_ready():
                    data = self._channel.recv(READ_CHUNK)
                    if data:
                        try:
                            text = data.decode('utf-8', errors='replace')
                            with self._lock:
                                self._stream.feed(text)
                        except Exception:
                            pass
                else:
                    time.sleep(0.02)
            except Exception:
                break

    def _handle_client(self, client_sock: socket.socket):
        """处理客户端请求"""
        try:
            client_sock.settimeout(300)
            req = _recv_message(client_sock, timeout=300)
            action = req.get('action', '')

            if action == 'send':
                self._last_activity = time.time()
                command = req.get('command', '')
                wait = req.get('wait', True)
                quiet_timeout = req.get('quiet_timeout', QUIET_TIMEOUT)
                result = self._send_and_wait(command, wait, quiet_timeout)
                _send_message(client_sock, result)

            elif action == 'send_keys':
                self._last_activity = time.time()
                keys = req.get('keys', '')
                wait = req.get('wait', True)
                quiet_timeout = req.get('quiet_timeout', QUIET_TIMEOUT)
                raw_bytes = encode_keys(keys)
                result = self._send_bytes_and_wait(raw_bytes, wait, quiet_timeout)
                _send_message(client_sock, result)

            elif action == 'snapshot':
                result = self._get_snapshot()
                _send_message(client_sock, result)

            elif action == 'ping':
                _send_message(client_sock, {
                    'status': 'ok',
                    'pty_id': self.pty_id,
                    'channel_alive': self._channel and not self._channel.closed,
                    'idle_seconds': int(time.time() - self._last_activity)
                })

            elif action == 'shutdown':
                _send_message(client_sock, {'status': 'shutting_down'})
                self._running = False

            else:
                _send_message(client_sock, {
                    'success': False, 'error': f'未知操作: {action}'
                })

        except Exception as e:
            try:
                _send_message(client_sock, {
                    'success': False, 'error': str(e)
                })
            except Exception:
                pass
        finally:
            try:
                client_sock.close()
            except Exception:
                pass

    def _send_and_wait(self, command: str, wait: bool, quiet_timeout: float) -> dict:
        """发送命令文本并等待输出静止"""
        with self._lock:
            if not self._channel or self._channel.closed:
                return {'success': False, 'error': 'PTY 通道已关闭'}

            # 记录发送前的屏幕状态（用于计算 diff）
            prev_lines = list(self._screen.display)

            # 发送命令
            self._channel.send(command + '\r')

        if not wait:
            return {'success': True, 'output': '', 'message': '命令已发送（不等待）'}

        # 等待输出静止
        return self._wait_for_quiet(prev_lines, quiet_timeout)

    def _send_bytes_and_wait(self, raw_bytes: bytes, wait: bool, quiet_timeout: float) -> dict:
        """发送原始字节并等待"""
        with self._lock:
            if not self._channel or self._channel.closed:
                return {'success': False, 'error': 'PTY 通道已关闭'}

            prev_lines = list(self._screen.display)
            self._channel.send(raw_bytes)

        if not wait:
            return {'success': True, 'output': '', 'message': '按键已发送（不等待）'}

        return self._wait_for_quiet(prev_lines, quiet_timeout)

    def _wait_for_quiet(self, prev_lines: list, quiet_timeout: float) -> dict:
        """等待输出静止（quiet_timeout 秒无新输出）"""
        max_wait = 30.0  # 最多等 30 秒
        deadline = time.time() + max_wait
        last_change = time.time()
        last_lines = prev_lines

        while time.time() < deadline:
            time.sleep(0.1)
            with self._lock:
                current_lines = list(self._screen.display)

            if current_lines != last_lines:
                last_lines = current_lines
                last_change = time.time()
            elif time.time() - last_change >= quiet_timeout:
                # 输出静止了
                break

        # 提取屏幕内容
        with self._lock:
            screen_text = self._extract_screen(prev_lines)

        return {
            'success': True,
            'output': screen_text,
            'elapsed': round(time.time() - (deadline - max_wait), 2)
        }

    def _extract_screen(self, prev_lines: list = None) -> str:
        """从 pyte screen 提取干净文本"""
        lines = list(self._screen.display)

        # 如果有前序状态，只返回 diff
        if prev_lines:
            diff_start = 0
            for i, (a, b) in enumerate(zip(prev_lines, lines)):
                if a != b:
                    diff_start = i
                    break
            else:
                diff_start = len(prev_lines)
            lines = lines[diff_start:]

        # 清理空行
        result = []
        for line in lines:
            stripped = line.rstrip()
            if stripped:
                result.append(stripped)

        return '\n'.join(result)

    def _get_snapshot(self) -> dict:
        """获取当前屏幕完整快照"""
        with self._lock:
            lines = list(self._screen.display)

        # 清理尾部空行
        while lines and not lines[-1].strip():
            lines.pop()

        return {
            'success': True,
            'screen': '\n'.join(line.rstrip() for line in lines),
            'cols': PTY_COLS,
            'rows': PTY_ROWS,
            'cursor': {
                'x': self._screen.cursor.x,
                'y': self._screen.cursor.y
            } if self._screen else None
        }

    def _idle_check(self):
        """空闲超时检测"""
        while self._running:
            time.sleep(10)
            if not self._running:
                break
            idle = time.time() - self._last_activity
            if idle >= self.idle_timeout:
                self._running = False
                try:
                    wake = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    wake.connect(self._server_sock.getsockname())
                    wake.close()
                except Exception:
                    pass
                break

    def _shutdown(self):
        self._running = False
        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
        info_path = get_pty_info_path(self.pty_id)
        try:
            if os.path.exists(info_path):
                os.remove(info_path)
        except Exception:
            pass


# === CLI 客户端 ===

def _connect_daemon(pty_id: str, action: str, payload: dict = None,
                    timeout: int = 30) -> dict:
    info = read_pty_info(pty_id)
    if not info:
        return {'success': False, 'error': f"PTY 会话 '{pty_id}' 未运行"}

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout + 5)
        sock.connect(('127.0.0.1', info['port']))
        msg = {'action': action}
        if payload:
            msg.update(payload)
        _send_message(sock, msg)
        result = _recv_message(sock, timeout=timeout + 5)
        sock.close()
        return result
    except Exception as e:
        return {'success': False, 'error': str(e)}


def cmd_start(alias: str) -> dict:
    """启动 PTY 会话"""
    # 如果已有会话，直接返回
    pty_id = get_pty_id(alias)
    existing = read_pty_info(pty_id)
    if existing:
        return {
            'success': True,
            'pty_id': pty_id,
            'port': existing.get('port'),
            'alias': alias,
            'status': 'already_running'
        }

    # 后台启动守护进程
    daemon_script = os.path.join(_script_dir, 'ssh_pty.py')
    try:
        if os.name == 'nt':
            CREATE_NO_WINDOW = 0x08000000
            subprocess.Popen(
                [sys.executable, daemon_script, 'daemon', alias],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW
            )
        else:
            subprocess.Popen(
                [sys.executable, daemon_script, 'daemon', alias],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        # 等待启动
        for _ in range(15):
            time.sleep(0.3)
            if read_pty_info(pty_id):
                return {
                    'success': True,
                    'pty_id': pty_id,
                    'alias': alias,
                    'status': 'started'
                }
        return {'success': False, 'error': '守护进程启动超时'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def cmd_send(alias: str, command: str, wait: bool = True,
             quiet_timeout: float = QUIET_TIMEOUT) -> dict:
    """发送命令"""
    pty_id = get_pty_id(alias)
    info = read_pty_info(pty_id)
    if not info:
        # 自动启动
        start_result = cmd_start(alias)
        if not start_result.get('success'):
            return start_result
        info = read_pty_info(pty_id)

    if not info:
        return {'success': False, 'error': f'无法启动 PTY 会话: {alias}'}

    return _connect_daemon(pty_id, 'send', {
        'command': command,
        'wait': wait,
        'quiet_timeout': quiet_timeout
    }, timeout=60)


def cmd_send_keys(alias: str, keys: str, wait: bool = True,
                  quiet_timeout: float = QUIET_TIMEOUT) -> dict:
    """发送特殊按键"""
    pty_id = get_pty_id(alias)
    info = read_pty_info(pty_id)
    if not info:
        start_result = cmd_start(alias)
        if not start_result.get('success'):
            return start_result
        info = read_pty_info(pty_id)

    if not info:
        return {'success': False, 'error': f'无法启动 PTY 会话: {alias}'}

    return _connect_daemon(pty_id, 'send_keys', {
        'keys': keys,
        'wait': wait,
        'quiet_timeout': quiet_timeout
    }, timeout=60)


def cmd_snapshot(alias: str) -> dict:
    """获取屏幕快照"""
    pty_id = get_pty_id(alias)
    return _connect_daemon(pty_id, 'snapshot', timeout=10)


def cmd_stop(alias: str) -> dict:
    """停止 PTY 会话"""
    pty_id = get_pty_id(alias)
    info = read_pty_info(pty_id)
    if not info:
        return {'success': False, 'error': f"PTY 会话 '{alias}' 未运行"}

    result = _connect_daemon(pty_id, 'shutdown', timeout=5)
    info_path = get_pty_info_path(pty_id)
    try:
        if os.path.exists(info_path):
            os.remove(info_path)
    except Exception:
        pass
    return {'success': True, 'message': f'PTY 会话已停止: {alias}'}


def cmd_list() -> list:
    """列出所有 PTY 会话"""
    if not os.path.exists(PTY_DIR):
        return []
    result = []
    for fname in os.listdir(PTY_DIR):
        if not fname.endswith('.json'):
            continue
        try:
            with open(os.path.join(PTY_DIR, fname), 'r', encoding='utf-8') as f:
                info = json.load(f)
            pid = info.get('pid')
            if pid and _is_process_alive(pid):
                result.append({
                    'pty_id': info['pty_id'],
                    'alias': info['alias'],
                    'remote': info.get('remote', ''),
                    'started_at': info.get('started_at', ''),
                    'pid': pid
                })
            else:
                os.remove(os.path.join(PTY_DIR, fname))
        except Exception:
            pass
    return result


# === CLI ===

def main():
    # 检测兼容模式：第一个参数不是子命令时走兼容模式
    known_subcommands = {'start', 'send', 'send-keys', 'snapshot', 'stop', 'list',
                         'daemon', '-h', '--help', 'help'}
    raw_args = sys.argv[1:]
    compat_mode = len(raw_args) > 0 and raw_args[0] not in known_subcommands

    if compat_mode:
        # 兼容模式：ssh_pty.py <alias> ["<command>"] [--snapshot] [-k keys]
        parser = argparse.ArgumentParser(
            description='SSH PTY 交互式终端 v1.0（兼容模式）',
            formatter_class=argparse.RawDescriptionHelpFormatter
        )
        parser.add_argument('alias', help='SSH host 别名')
        parser.add_argument('command', nargs='?', default=None, help='要发送的命令')
        parser.add_argument('--send-keys', '-k', default=None, help='发送特殊按键')
        parser.add_argument('--snapshot', '-s', action='store_true', help='获取屏幕快照')
        parser.add_argument('--no-wait', action='store_true', help='不等待输出')
        parser.add_argument('--quiet-timeout', type=float, default=QUIET_TIMEOUT,
                            help=f'输出静止超时（秒）')
        args = parser.parse_args()

        try:
            if args.snapshot:
                result = cmd_snapshot(args.alias)
                if result.get('success'):
                    print(result.get('screen', ''))
                else:
                    print(json.dumps(result, ensure_ascii=True))
                    sys.exit(1)
            elif args.send_keys:
                result = cmd_send_keys(args.alias, args.send_keys,
                                       wait=not args.no_wait)
                print(json.dumps(result, ensure_ascii=True, indent=2))
            elif args.command:
                result = cmd_send(args.alias, args.command,
                                  wait=not args.no_wait,
                                  quiet_timeout=args.quiet_timeout)
                print(json.dumps(result, ensure_ascii=True, indent=2))
                sys.exit(0 if result.get('success') else 1)
            else:
                result = cmd_start(args.alias)
                print(json.dumps(result, ensure_ascii=True, indent=2))
                sys.exit(0 if result.get('success') else 1)
        except Exception as e:
            print(json.dumps({'success': False, 'error': str(e)},
                             ensure_ascii=True), file=sys.stderr)
            sys.exit(1)
        return

    # 子命令模式
    parser = argparse.ArgumentParser(
        description='SSH PTY 交互式终端 v1.0 — pyte 终端模拟 + Paramiko PTY',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='操作命令')

    # start
    p_start = subparsers.add_parser('start', help='启动 PTY 会话')
    p_start.add_argument('alias', help='SSH host 别名')

    # send
    p_send = subparsers.add_parser('send', help='发送命令并获取输出')
    p_send.add_argument('alias', help='SSH host 别名')
    p_send.add_argument('command', help='要发送的命令')
    p_send.add_argument('--no-wait', action='store_true', help='不等待输出（立即返回）')
    p_send.add_argument('--quiet-timeout', type=float, default=QUIET_TIMEOUT,
                        help=f'输出静止超时（秒），默认 {QUIET_TIMEOUT}')

    # send-keys
    p_keys = subparsers.add_parser('send-keys', help='发送特殊按键')
    p_keys.add_argument('alias', help='SSH host 别名')
    p_keys.add_argument('keys', help='按键名称（ctrl+c, enter, up, down 等）')
    p_keys.add_argument('--no-wait', action='store_true', help='不等待输出')

    # snapshot
    p_snap = subparsers.add_parser('snapshot', help='获取当前屏幕快照')
    p_snap.add_argument('alias', help='SSH host 别名')

    # stop
    p_stop = subparsers.add_parser('stop', help='停止 PTY 会话')
    p_stop.add_argument('alias', help='SSH host 别名')

    # list
    p_list = subparsers.add_parser('list', help='列出所有 PTY 会话')

    # daemon（内部使用，后台运行）
    p_daemon = subparsers.add_parser('daemon', help='后台运行守护进程（内部使用）')
    p_daemon.add_argument('alias', help='SSH host 别名')

    args = parser.parse_args()

    try:
        if args.command == 'start':
            result = cmd_start(args.alias)
            print(json.dumps(result, ensure_ascii=True, indent=2))
            sys.exit(0 if result.get('success') else 1)

        elif args.command == 'send':
            result = cmd_send(args.alias, args.command,
                              wait=not args.no_wait,
                              quiet_timeout=args.quiet_timeout)
            print(json.dumps(result, ensure_ascii=True, indent=2))
            sys.exit(0 if result.get('success') else 1)

        elif args.command == 'send-keys':
            result = cmd_send_keys(args.alias, args.keys,
                                   wait=not args.no_wait)
            print(json.dumps(result, ensure_ascii=True, indent=2))
            sys.exit(0 if result.get('success') else 1)

        elif args.command == 'snapshot':
            result = cmd_snapshot(args.alias)
            if result.get('success'):
                print(result.get('screen', ''))
            else:
                print(json.dumps(result, ensure_ascii=True))
                sys.exit(1)

        elif args.command == 'stop':
            result = cmd_stop(args.alias)
            print(json.dumps(result, ensure_ascii=True))
            sys.exit(0 if result.get('success') else 1)

        elif args.command == 'list':
            sessions = cmd_list()
            print(json.dumps(sessions, ensure_ascii=True, indent=2))
            sys.exit(0)

        elif args.command == 'daemon':
            # 内部模式：直接运行守护进程（阻塞）
            daemon = PTYDaemon(args.alias)
            result = daemon.start()
            if not result.get('success'):
                print(json.dumps(result, ensure_ascii=True), file=sys.stderr)
            sys.exit(0 if result.get('success') else 1)

        else:
            parser.print_help()
            sys.exit(1)

    except Exception as e:
        print(json.dumps({'success': False, 'error': str(e)},
                         ensure_ascii=True), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
