#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH 持久 Shell 会话管理 CLI 工具 v1.0 - AI Agent 专用

**注意：这不是交互式终端。** 需要交互式 TTY 终端请用 `ssh_connect.py`。

本工具适用于 AI agent 自动化场景：
- 打开一个持久 Shell 会话（参数 `invoke_shell`）
- 通过本地 socket daemon 接收命令请求
- 每次执行保持状态（cwd、环境变量、别名等）
- 无 TTY 分配，通过标记法提取退出码

与 ssh_execute.py 的区别（无状态 vs 有状态）：
- ssh_execute.py：每次开新 channel，独立环境
- ssh_shell.py：共享同一 shell 通道，cwd/env 跨命令持久

用法：
    python ssh_shell.py <alias> ["<命令>"] [--session SESSION_ID]
    python ssh_shell.py session <alias> [--idle-timeout SECONDS]
    python ssh_shell.py exec <session_id> "<命令>"
    python ssh_shell.py stop <alias|session_id>
    python ssh_shell.py list

示例：
    # 启动会话并执行命令
    python ssh_shell.py prod-web-01 "cd /app && git pull && systemctl restart app"
    python ssh_shell.py prod-web-01 "cd /app && pwd"  # 会看到 /app

    # 先启动会话，再多次执行
    python ssh_shell.py session dev-server
    python ssh_shell.py exec <session_id> "cd /data && ls"
    python ssh_shell.py exec <session_id> "pwd"  # 输出 /data
    python ssh_shell.py exec <session_id> "cat config.yml"

    # 列出/停止
    python ssh_shell.py list
    python ssh_shell.py stop dev-server
"""

import sys
import os
import json
import time
import socket
import struct
import tempfile
import hashlib
import subprocess
import threading
import re
import argparse
import signal
from typing import Optional, Dict, List
from io import StringIO

# 添加 lib 到路径
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_script_dir, 'lib'))

SHELL_DIR = os.path.join(tempfile.gettempdir(), 'ssh_shells')
SHELL_DEFAULT_TIMEOUT = 60
SHELL_IDLE_TIMEOUT = 600  # 10 分钟空闲自动关闭

# Shell prompt 行检测正则
# 匹配常见 prompt 格式：root@host:~#、user@host:~$、bash-5.1#、[user@host]$
_PROMPT_PATTERN = re.compile(
    r'^\s*'                           # 可能的前导空格
    r'(?:'
    r   r'\S+@\S+:[^\n]*[#$]\s*'      # user@host:path#  或 user@host:path$
    r   r'|bash-[^\n]*[#$]\s*'        # bash-5.1#  或 bash-5.1$
    r   r'|sh-[^\n]*[#$]\s*'          # sh-5.1#
    r   r'|\[[^\n]*\][#$]\s*'         # [user@host]#
    r   r'|>\s*'                       # >  (简单 prompt)
    r')'
    r'$'
)


def get_shell_id(alias: str) -> str:
    """根据别名生成 shell 会话唯一标识"""
    return hashlib.md5(alias.lower().encode('utf-8')).hexdigest()[:12]


def get_shell_info_path(shell_id: str) -> str:
    """获取 shell 会话信息文件路径"""
    os.makedirs(SHELL_DIR, exist_ok=True)
    return os.path.join(SHELL_DIR, f'{shell_id}.json')


def read_shell_info(shell_id: str) -> Optional[dict]:
    """读取 shell 会话信息，返回 None 表示不存在或已失效"""
    info_path = get_shell_info_path(shell_id)
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


def read_all_shells() -> List[dict]:
    """读取所有活跃 shell 会话"""
    if not os.path.exists(SHELL_DIR):
        return []
    shells = []
    for fname in os.listdir(SHELL_DIR):
        if not fname.endswith('.json'):
            continue
        try:
            with open(os.path.join(SHELL_DIR, fname), 'r', encoding='utf-8') as f:
                info = json.load(f)
            pid = info.get('pid')
            if pid and _is_process_alive(pid):
                shells.append(info)
            else:
                os.remove(os.path.join(SHELL_DIR, fname))
        except Exception:
            pass
    return shells


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


class SSHShellSession:
    """SSH 持久 Shell 会话"""

    def __init__(self, alias: str, idle_timeout: int = SHELL_IDLE_TIMEOUT):
        self.alias = alias
        self.shell_id = get_shell_id(alias)
        self.idle_timeout = idle_timeout
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._lock = threading.Lock()
        self._last_activity = time.time()
        self._output_buffer = StringIO()

    def start(self) -> dict:
        """启动 shell 会话"""
        # 检查是否已有会话运行
        existing = read_shell_info(self.shell_id)
        if existing:
            return {
                'success': True,
                'session_id': self.shell_id,
                'pid': existing['pid'],
                'alias': self.alias,
                'port': existing.get('port'),
                'status': 'already_running'
            }

        # 加载连接配置
        from config_v3 import SSHConfigLoaderV3
        loader = SSHConfigLoaderV3()
        try:
            params = loader.get_connection_params(self.alias)
        except ValueError as e:
            return {'success': False, 'error': f"配置加载失败: {str(e)}"}
        except FileNotFoundError as e:
            return {'success': False, 'error': f"配置文件不存在: {str(e)}"}

        host = params['hostname']
        user = params['user']
        port = params.get('port', 22)
        key_file = params.get('key_file')
        password = params.get('password')
        proxy_jump = params.get('proxy_jump')

        try:
            import paramiko
        except ImportError:
            return {'success': False, 'error': '需要安装 paramiko: pip install paramiko'}

        # 建立 SSH 连接并打开 shell
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs = {
                'hostname': host,
                'port': port,
                'username': user,
                'timeout': 30,
            }

            if password:
                connect_kwargs['password'] = password
                connect_kwargs['look_for_keys'] = False
                connect_kwargs['allow_agent'] = False
            elif key_file:
                key_file = os.path.expanduser(key_file)
                pkey = None
                for key_class in [paramiko.RSAKey, paramiko.Ed25519Key,
                                  paramiko.ECDSAKey]:
                    try:
                        pkey = key_class.from_private_key_file(key_file)
                        break
                    except Exception:
                        continue
                if pkey is None:
                    return {'success': False, 'error': f"无法加载密钥: {key_file}"}
                connect_kwargs['pkey'] = pkey
                connect_kwargs['look_for_keys'] = False
                connect_kwargs['allow_agent'] = False
            else:
                return {'success': False, 'error': '未提供认证方式'}

            client.connect(**connect_kwargs)

            # 检测远程系统类型，选择合适的 shell
            shell_check = client.exec_command("echo $SHELL", timeout=10)
            remote_shell = shell_check[1].read().decode('utf-8', errors='replace').strip()
            shell_path = remote_shell if remote_shell else '/bin/bash'

            # 打开交互式 shell
            channel = client.invoke_shell(term='vt100', width=160, height=100)

            # 等待 shell 准备就绪
            time.sleep(0.5)
            if channel.recv_ready():
                channel.recv(65536)  # 清空初始输出

            self._client = client
            self._channel = channel
            self._running = True

            # 启动守护 socket 服务，接收命令请求
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind(('127.0.0.1', 0))
            server_sock.listen(5)
            server_sock.settimeout(3.0)
            server_port = server_sock.getsockname()[1]

            # 启动命令处理线程
            handler_thread = threading.Thread(
                target=self._server_loop,
                args=(server_sock,),
                daemon=True
            )
            handler_thread.start()

            # 启动空闲检测线程
            idle_thread = threading.Thread(
                target=self._idle_check_loop,
                args=(server_sock,),
                daemon=True
            )
            idle_thread.start()

            # 保存会话信息
            info = {
                'session_id': self.shell_id,
                'alias': self.alias,
                'pid': os.getpid(),
                'port': server_port,
                'shell': shell_path,
                'remote': f"{user}@{host}",
                'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'idle_timeout': self.idle_timeout
            }
            info_path = get_shell_info_path(self.shell_id)
            with open(info_path, 'w', encoding='utf-8') as f:
                json.dump(info, f, ensure_ascii=False, indent=2)

            return {
                'success': True,
                'session_id': self.shell_id,
                'alias': self.alias,
                'shell': shell_path,
                'remote': f"{user}@{host}",
                'port': server_port,
                'status': 'started'
            }

        except paramiko.AuthenticationException as e:
            return {'success': False, 'error': f"认证失败: {str(e)}"}
        except paramiko.SSHException as e:
            return {'success': False, 'error': f"SSH 连接错误: {str(e)}"}
        except Exception as e:
            return {'success': False, 'error': f"启动 shell 失败: {str(e)}"}

    def _server_loop(self, server_sock: socket.socket):
        """Socket 服务主循环：接收命令执行请求"""
        while self._running:
            try:
                client_sock, addr = server_sock.accept()
                t = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock,),
                    daemon=True
                )
                t.start()
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    raise
                break

    def _handle_client(self, client_sock: socket.socket):
        """处理单个命令执行请求"""
        try:
            client_sock.settimeout(300)
            req = _recv_message(client_sock, timeout=300)
            action = req.get('action', '')

            if action == 'exec':
                self._last_activity = time.time()
                command = req.get('command', '')
                timeout = req.get('timeout', SHELL_DEFAULT_TIMEOUT)
                result = self._exec_in_shell(command, timeout)
                _send_message(client_sock, result)

            elif action == 'ping':
                _send_message(client_sock, {
                    'status': 'ok',
                    'session_id': self.shell_id,
                    'alias': self.alias,
                    'alive': self._channel is not None and not self._channel.closed,
                    'idle_seconds': int(time.time() - self._last_activity)
                })

            elif action == 'shutdown':
                _send_message(client_sock, {'status': 'shutting_down'})
                self._running = False

            else:
                _send_message(client_sock, {
                    'success': False,
                    'error': f'未知操作: {action}'
                })

        except Exception as e:
            try:
                _send_message(client_sock, {
                    'success': False,
                    'error': f"处理错误: {str(e)}"
                })
            except Exception:
                pass
        finally:
            try:
                client_sock.close()
            except Exception:
                pass

    def _exec_in_shell(self, command: str, timeout: int) -> dict:
        """在持久 shell 中执行命令，保持状态"""
        with self._lock:
            if not self._channel or self._channel.closed:
                return {
                    'success': False,
                    'exit_code': -1,
                    'stdout': '',
                    'stderr': 'Shell 会话已关闭'
                }

            try:
                # 发送命令（加换行符触发执行）
                self._channel.send(command + '\n')

                # 使用标记命令来检测输出结束
                marker = f"__SHELL_MARKER_{int(time.time() * 1000)}__"
                self._channel.send(f'echo "{marker}" $?\n')

                # 读取输出
                output = ''
                exit_code = -1
                deadline = time.time() + timeout

                while time.time() < deadline:
                    if self._channel.recv_ready():
                        data = self._channel.recv(65536).decode('utf-8', errors='replace')
                        output += data

                        # 检查是否包含标记行
                        if marker in output:
                            lines = output.split('\n')

                            # 找到标记输出行（以 marker 开头，不是命令回显）
                            # 命令回显行格式: root@host:~# echo "marker" $?
                            # 标记输出行格式: marker 0
                            marker_line_idx = -1
                            for i, line in enumerate(lines):
                                stripped = line.strip().strip('"\'')
                                if stripped.startswith(marker):
                                    marker_line_idx = i
                                    # 提取退出码
                                    parts = stripped.split(' ')
                                    if len(parts) >= 2:
                                        try:
                                            exit_code = int(parts[-1])
                                        except ValueError:
                                            exit_code = -1  # 未知退出码，不应默认为0（成功）
                                    break

                            if marker_line_idx >= 0:
                                # 清理输出：移除命令回显、标记行、标记命令回显、prompt 行
                                cleaned_lines = []
                                command_echo_skipped = False  # 只跳第一个命令回显行
                                for j, l in enumerate(lines):
                                    # 跳过标记输出行
                                    if j == marker_line_idx:
                                        continue
                                    # 跳过原始命令回显（仅首次出现，避免子串误杀输出）
                                    if not command_echo_skipped and command.strip() and command.strip() in l:
                                        command_echo_skipped = True
                                        continue
                                    # 跳过标记命令回显（含 marker 和 echo 的行）
                                    if marker in l and 'echo' in l:
                                        continue
                                    # 跳过 shell prompt 行（root@host:~#、bash-5.1# 等）
                                    clean_l = l.replace('\r', '').strip()
                                    if clean_l and _PROMPT_PATTERN.match(clean_l):
                                        continue
                                    cleaned_lines.append(l)

                                output = '\n'.join(cleaned_lines)
                                break

                    time.sleep(0.05)

                # 清理 shell 提示符等多余内容
                output = self._clean_shell_output(output)

                return {
                    'success': exit_code == 0,
                    'exit_code': exit_code,
                    'stdout': output.strip(),
                    'stderr': ''
                }

            except Exception as e:
                return {
                    'success': False,
                    'exit_code': -1,
                    'stdout': '',
                    'stderr': f"命令执行错误: {str(e)}"
                }

    @staticmethod
    def _clean_shell_output(output: str) -> str:
        """清理 shell 输出中的控制字符和多余内容"""
        lines = output.split('\n')

        # 去除空行开头
        while lines and lines[0].strip() == '':
            lines.pop(0)

        # 去除 ANSI 转义序列
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        cleaned = []
        for line in lines:
            line = ansi_escape.sub('', line)
            # 去除回车符
            line = line.replace('\r', '')
            # 跳过 shell prompt 行
            if line.strip() and _PROMPT_PATTERN.match(line.strip()):
                continue
            if line.strip():
                cleaned.append(line)

        return '\n'.join(cleaned)

    def _idle_check_loop(self, server_sock: socket.socket):
        """空闲检测线程：超时自动关闭"""
        while self._running:
            time.sleep(10)
            if not self._running:
                break
            idle = time.time() - self._last_activity
            if idle >= self.idle_timeout:
                self._running = False
                try:
                    wake = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    wake.connect(server_sock.getsockname())
                    wake.close()
                except Exception:
                    pass
                break

    def stop(self):
        """停止 shell 会话"""
        self._running = False

        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass

        if hasattr(self, '_client') and self._client:
            try:
                self._client.close()
            except Exception:
                pass

        info_path = get_shell_info_path(self.shell_id)
        try:
            if os.path.exists(info_path):
                os.remove(info_path)
        except Exception:
            pass


# === Socket 通信辅助函数 ===

def _send_message(sock: socket.socket, data: dict):
    """发送带长度前缀的 JSON 消息"""
    payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
    header = struct.pack('!I', len(payload))
    sock.sendall(header + payload)


def _recv_message(sock: socket.socket, timeout: float = None) -> dict:
    """接收带长度前缀的 JSON 消息"""
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


# === CLI 命令 ===

def _connect_shell_daemon(shell_id: str, action: str,
                          payload: dict = None, timeout: int = 30) -> dict:
    """连接 shell 守护进程并发送操作请求"""
    info = read_shell_info(shell_id)
    if not info:
        return {'success': False, 'error': f"Shell 会话 '{shell_id}' 未运行"}

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
    except ConnectionRefusedError:
        return {'success': False, 'error': 'Shell 守护进程拒绝连接'}
    except Exception as e:
        return {'success': False, 'error': f"连接失败: {str(e)}"}


def cmd_session(alias: str, idle_timeout: int = SHELL_IDLE_TIMEOUT) -> dict:
    """启动 shell 会话"""
    session = SSHShellSession(alias, idle_timeout)
    return session.start()


def cmd_exec(session_id_or_alias: str, command: str,
             timeout: int = SHELL_DEFAULT_TIMEOUT) -> dict:
    """在 shell 会话中执行命令"""
    # 先尝试作为 session_id 查找
    info = read_shell_info(session_id_or_alias)

    # 再尝试作为 alias 查找
    if not info:
        lookup_id = get_shell_id(session_id_or_alias)
        info = read_shell_info(lookup_id)

    if not info:
        # 自动启动会话
        session = SSHShellSession(session_id_or_alias)
        start_result = session.start()
        if not start_result.get('success'):
            return start_result
        info = read_shell_info(get_shell_id(session_id_or_alias))

    if not info:
        return {'success': False, 'error': f"无法启动或找到会话: {session_id_or_alias}"}

    shell_id = info['session_id']
    result = _connect_shell_daemon(shell_id, 'exec', {
        'command': command,
        'timeout': timeout
    }, timeout=timeout + 10)

    return result


def cmd_stop(session_id_or_alias: str) -> dict:
    """停止 shell 会话"""
    info = read_shell_info(session_id_or_alias)
    if not info:
        lookup_id = get_shell_id(session_id_or_alias)
        info = read_shell_info(lookup_id)

    if info:
        shell_id = info['session_id']
        result = _connect_shell_daemon(shell_id, 'shutdown')
        info_path = get_shell_info_path(shell_id)
        try:
            if os.path.exists(info_path):
                os.remove(info_path)
        except Exception:
            pass
        return {'success': True, 'message': f"Shell 会话已停止: {info.get('alias', shell_id)}"}

    return {'success': False, 'error': f"未找到运行中的会话: {session_id_or_alias}"}


def cmd_list() -> List[dict]:
    """列出所有活跃 shell 会话"""
    shells = read_all_shells()
    result = []
    for s in shells:
        result.append({
            'session_id': s['session_id'],
            'alias': s['alias'],
            'shell': s.get('shell', 'unknown'),
            'remote': s.get('remote', ''),
            'pid': s['pid'],
            'started_at': s.get('started_at', ''),
            'idle_timeout': s.get('idle_timeout', SHELL_IDLE_TIMEOUT)
        })
    return result


def main():
    # 检测兼容模式：第一个参数不是子命令时走兼容模式
    known_subcommands = {'session', 'exec', 'stop', 'list', '-h', '--help', 'help'}
    raw_args = sys.argv[1:]
    compat_mode = len(raw_args) > 0 and raw_args[0] not in known_subcommands

    if compat_mode:
        # 兼容模式：ssh_shell.py <alias> ["<command>"] [--timeout N]
        parser = argparse.ArgumentParser(
            description='SSH 持久 Shell 会话管理工具 v1.0（兼容模式）')
        parser.add_argument('alias', help='SSH host 别名')
        parser.add_argument('command', nargs='?', default=None, help='要执行的命令')
        parser.add_argument('--session', '-s', help='会话 ID')
        parser.add_argument('--timeout', '-t', type=int, default=SHELL_DEFAULT_TIMEOUT,
                            help=f'超时（秒），默认 {SHELL_DEFAULT_TIMEOUT}')
        args = parser.parse_args()

        try:
            if args.alias and args.command:
                result = cmd_exec(args.alias, args.command, args.timeout)
                print(json.dumps(result, ensure_ascii=True, indent=2))
                sys.exit(0 if result.get('success') else 1)
            elif args.alias:
                result = cmd_session(args.alias)
                print(json.dumps(result, ensure_ascii=True, indent=2))
                sys.exit(0 if result.get('success') else 1)
            else:
                parser.print_help()
                sys.exit(1)
        except Exception as e:
            print(json.dumps({
                'success': False,
                'error': f"执行错误: {str(e)}"
            }, ensure_ascii=True, indent=2), file=sys.stderr)
            sys.exit(1)
        return

    # 子命令模式
    parser = argparse.ArgumentParser(
        description='SSH 持久 Shell 会话管理工具 v1.0')
    subparsers = parser.add_subparsers(dest='subcommand', help='操作命令')

    # session - 启动会话
    p_session = subparsers.add_parser('session', help='启动 shell 会话')
    p_session.add_argument('alias', help='SSH host 别名')
    p_session.add_argument('--idle-timeout', type=int, default=SHELL_IDLE_TIMEOUT,
                           help=f'空闲超时（秒），默认 {SHELL_IDLE_TIMEOUT}')

    # exec - 执行命令
    p_exec = subparsers.add_parser('exec', help='在 shell 会话中执行命令')
    p_exec.add_argument('target', help='会话 ID 或别名')
    p_exec.add_argument('command', help='要执行的命令')
    p_exec.add_argument('--timeout', type=int, default=SHELL_DEFAULT_TIMEOUT,
                        help=f'超时（秒），默认 {SHELL_DEFAULT_TIMEOUT}')

    # stop
    p_stop = subparsers.add_parser('stop', help='停止 shell 会话')
    p_stop.add_argument('target', help='别名或会话 ID')

    # list
    p_list = subparsers.add_parser('list', help='列出所有 shell 会话')

    args = parser.parse_args()

    try:
        if args.subcommand == 'session':
            result = cmd_session(args.alias, args.idle_timeout)
            print(json.dumps(result, ensure_ascii=True, indent=2))
            sys.exit(0 if result.get('success') else 1)

        elif args.subcommand == 'exec':
            result = cmd_exec(args.target, args.command, args.timeout)
            print(json.dumps(result, ensure_ascii=True, indent=2))
            sys.exit(0 if result.get('success') else 1)

        elif args.subcommand == 'stop':
            result = cmd_stop(args.target)
            print(json.dumps(result, ensure_ascii=True, indent=2))
            sys.exit(0 if result.get('success') else 1)

        elif args.subcommand == 'list':
            shells = cmd_list()
            print(json.dumps(shells, ensure_ascii=True, indent=2))
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
