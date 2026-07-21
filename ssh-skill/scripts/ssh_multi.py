#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH 多连接管理器 v1.0

多个 SSH 连接，有名字、有状态、不会忘。

核心概念：
  Workspace（工作区）→ 一组相关连接的容器
  Connection（连接）  → 一台机器一个连接，有名字 + SSH 别名
  Note（便签）        → 给连接贴备注，解决"上厕所回来忘了"

用法：
    python ssh_multi.py create <workspace>
    python ssh_multi.py add <workspace> <name> <ssh-alias>
    python ssh_multi.py exec <workspace> <name|--all> "<command>"
    python ssh_multi.py status <workspace>
    python ssh_multi.py note <workspace> <name> "<text>"
    python ssh_multi.py check <workspace>
    python ssh_multi.py list [--workspace <name>]
    python ssh_multi.py remove <workspace> <name>
    python ssh_multi.py delete <workspace>
"""

import sys
import os
import json
import time
import argparse
import subprocess
from datetime import datetime
from typing import Optional, List, Dict

# === 路径常量 ===
_script_dir = os.path.dirname(os.path.abspath(__file__))
ssh_execute_path = os.path.join(_script_dir, 'ssh_execute.py')
STORAGE_DIR = os.path.expanduser('~/.ssh/multi')


# === 存储层 ===

def _ensure_storage_dir():
    os.makedirs(STORAGE_DIR, exist_ok=True)


def _workspace_path(workspace: str) -> str:
    _ensure_storage_dir()
    return os.path.join(STORAGE_DIR, f'{workspace}.json')


def safe_write(path: str, data: dict):
    """原子写入：写临时文件 + rename，Windows/Linux 通用"""
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def load_workspace(workspace: str) -> Optional[dict]:
    path = _workspace_path(workspace)
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_workspace(data: dict):
    path = _workspace_path(data['name'])
    safe_write(path, data)


def list_workspaces() -> List[dict]:
    _ensure_storage_dir()
    result = []
    for fname in os.listdir(STORAGE_DIR):
        if not fname.endswith('.json'):
            continue
        try:
            with open(os.path.join(STORAGE_DIR, fname), 'r', encoding='utf-8') as f:
                data = json.load(f)
            result.append(data)
        except Exception:
            pass
    return result


def delete_workspace(workspace: str) -> bool:
    path = _workspace_path(workspace)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


# === 连接操作 ===

def add_connection(workspace: str, name: str, alias: str) -> dict:
    data = load_workspace(workspace)
    if not data:
        return {'success': False, 'error': f"工作区 '{workspace}' 不存在"}

    # 检查重名
    for conn in data['connections']:
        if conn['name'] == name:
            return {'success': False, 'error': f"连接名 '{name}' 已存在"}

    data['connections'].append({
        'name': name,
        'alias': alias,
        'note': '',
        'last_command': None,
        'last_command_at': None,
        'last_exit_code': None
    })
    data['last_activity'] = now_iso()
    save_workspace(data)
    return {'success': True, 'message': f"已添加连接 '{name}' → {alias}"}


def remove_connection(workspace: str, name: str) -> dict:
    data = load_workspace(workspace)
    if not data:
        return {'success': False, 'error': f"工作区 '{workspace}' 不存在"}

    original_len = len(data['connections'])
    data['connections'] = [c for c in data['connections'] if c['name'] != name]

    if len(data['connections']) == original_len:
        return {'success': False, 'error': f"连接 '{name}' 不存在"}

    data['last_activity'] = now_iso()
    save_workspace(data)
    return {'success': True, 'message': f"已移除连接 '{name}'"}


def set_note(workspace: str, name: str, note: str) -> dict:
    data = load_workspace(workspace)
    if not data:
        return {'success': False, 'error': f"工作区 '{workspace}' 不存在"}

    for conn in data['connections']:
        if conn['name'] == name:
            conn['note'] = note
            data['last_activity'] = now_iso()
            save_workspace(data)
            return {'success': True, 'message': f"便签已更新: {name}"}

    return {'success': False, 'error': f"连接 '{name}' 不存在"}


# === 命令执行 ===

def exec_on_alias(alias: str, command: str, timeout: int = 60) -> dict:
    """调用 ssh_execute.py 执行命令"""
    try:
        result = subprocess.run(
            [sys.executable, ssh_execute_path, alias, command,
             '--timeout', str(timeout)],
            capture_output=True, text=True, timeout=timeout + 15,
            encoding='utf-8', errors='replace'
        )
        if result.stdout:
            return json.loads(result.stdout)
        return {
            'success': False,
            'exit_code': result.returncode,
            'stdout': '',
            'stderr': result.stderr or 'No output from ssh_execute.py'
        }
    except subprocess.TimeoutExpired:
        return {
            'success': False, 'exit_code': -1, 'stdout': '',
            'stderr': f'执行超时 ({timeout}s)'
        }
    except json.JSONDecodeError:
        return {
            'success': False, 'exit_code': -1, 'stdout': result.stdout,
            'stderr': 'ssh_execute.py 输出非 JSON'
        }
    except Exception as e:
        return {
            'success': False, 'exit_code': -1, 'stdout': '',
            'stderr': str(e)
        }


def exec_command(workspace: str, target: str, command: str,
                 timeout: int = 60) -> dict:
    """在工作区的指定连接（或全部连接）上执行命令"""
    data = load_workspace(workspace)
    if not data:
        return {'success': False, 'error': f"工作区 '{workspace}' 不存在"}

    if target == '--all':
        # 批量执行：串行，每台结果立即保存
        results = {}
        for conn in data['connections']:
            r = exec_on_alias(conn['alias'], command, timeout)
            results[conn['name']] = r
            # 更新连接状态
            conn['last_command'] = command
            conn['last_command_at'] = now_iso()
            conn['last_exit_code'] = r.get('exit_code', -1)
            save_workspace(data)  # 每台执行完立即保存

        data['last_activity'] = now_iso()
        save_workspace(data)
        return {'success': True, 'results': results}

    else:
        # 单节点执行
        for conn in data['connections']:
            if conn['name'] == target:
                r = exec_on_alias(conn['alias'], command, timeout)
                conn['last_command'] = command
                conn['last_command_at'] = now_iso()
                conn['last_exit_code'] = r.get('exit_code', -1)
                data['last_activity'] = now_iso()
                save_workspace(data)
                return r

        return {'success': False, 'error': f"连接 '{target}' 不存在"}


def check_online(workspace: str) -> dict:
    """检查所有连接的在线状态"""
    data = load_workspace(workspace)
    if not data:
        return {'success': False, 'error': f"工作区 '{workspace}' 不存在"}

    results = {}
    for conn in data['connections']:
        r = exec_on_alias(conn['alias'], 'echo ok', timeout=5)
        results[conn['name']] = r.get('success', False)

    return {'success': True, 'online': results}


# === 状态面板 ===

def render_status(workspace: str, check: bool = False) -> dict:
    data = load_workspace(workspace)
    if not data:
        return {'success': False, 'error': f"工作区 '{workspace}' 不存在"}

    # 可选：实时检查在线状态
    online_map = {}
    if check:
        for conn in data['connections']:
            r = exec_on_alias(conn['alias'], 'echo ok', timeout=5)
            online_map[conn['name']] = r.get('success', False)

    # 渲染表格
    header = f"{'名称':<12} {'SSH别名':<16} {'在线':<6} {'最后执行':<12} {'退出码':<8} {'备注'}"
    lines = [header, '─' * 80]

    for conn in data['connections']:
        name = conn['name']
        alias = conn['alias']

        if check:
            online = '✅' if online_map.get(name, False) else '❌'
        elif conn.get('last_exit_code') is not None:
            online = '✅' if conn['last_exit_code'] == 0 else '⚠️'
        else:
            online = '？'

        last_at = conn.get('last_command_at')
        last_str = format_time_ago(last_at) if last_at else '-'

        exit_code = conn.get('last_exit_code')
        exit_str = str(exit_code) if exit_code is not None else '-'

        note = conn.get('note', '')

        lines.append(f"{name:<12} {alias:<16} {online:<6} {last_str:<12} {exit_str:<8} {note}")

    return {
        'success': True,
        'workspace': workspace,
        'table': '\n'.join(lines),
        'connection_count': len(data['connections'])
    }


def watch_status(workspace: str, interval: int = 3, check: bool = False):
    """自动刷新状态面板，Ctrl+C 退出"""
    import time
    try:
        while True:
            # 清屏
            os.system('cls' if os.name == 'nt' else 'clear')

            result = render_status(workspace, check=check)
            if not result.get('success'):
                print(json.dumps(result, ensure_ascii=True))
                sys.exit(1)

            now_str = datetime.now().strftime('%H:%M:%S')
            print(result['table'])
            print(f"\n[刷新于 {now_str} | 每 {interval} 秒 | Ctrl+C 退出]")
            time.sleep(interval)

    except KeyboardInterrupt:
        print("\n已退出监控")


def render_workspace_list() -> dict:
    workspaces = list_workspaces()
    if not workspaces:
        return {'success': True, 'table': '没有工作区', 'count': 0}

    lines = [f"{'工作区':<20} {'连接数':<8} {'最后活动':<20}", '─' * 50]
    for ws in workspaces:
        name = ws['name']
        count = len(ws.get('connections', []))
        last = format_time_ago(ws.get('last_activity'))
        lines.append(f"{name:<20} {count:<8} {last:<20}")

    return {'success': True, 'table': '\n'.join(lines), 'count': len(workspaces)}


# === 辅助函数 ===

def now_iso() -> str:
    return datetime.now().strftime('%Y-%m-%dT%H:%M:%S')


def format_time_ago(iso_str: str) -> str:
    """将 ISO 时间转为'N分钟前'格式"""
    if not iso_str:
        return '-'
    try:
        t = datetime.strptime(iso_str, '%Y-%m-%dT%H:%M:%S')
        delta = datetime.now() - t
        seconds = int(delta.total_seconds())

        if seconds < 60:
            return f'{seconds}秒前'
        elif seconds < 3600:
            return f'{seconds // 60}分钟前'
        elif seconds < 86400:
            return f'{seconds // 3600}小时前'
        else:
            return f'{seconds // 86400}天前'
    except Exception:
        return iso_str


def truncate_output(text: str, max_len: int = 2000) -> str:
    """截断输出，防止 JSON 过大"""
    if not text or len(text) <= max_len:
        return text
    return text[:max_len] + f'\n... (截断, 共 {len(text)} 字符)'


# === CLI ===

def main():
    parser = argparse.ArgumentParser(
        description='SSH 多连接管理器 v1.0 — 多个连接，有名字、有状态、不会忘',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='操作命令')

    # create
    p_create = subparsers.add_parser('create', help='创建工作区')
    p_create.add_argument('workspace', help='工作区名称')

    # add
    p_add = subparsers.add_parser('add', help='添加命名连接')
    p_add.add_argument('workspace', help='工作区名称')
    p_add.add_argument('name', help='连接名称（自定义）')
    p_add.add_argument('alias', help='SSH 别名（来自 ~/.ssh/config）')

    # remove
    p_remove = subparsers.add_parser('remove', help='移除连接')
    p_remove.add_argument('workspace', help='工作区名称')
    p_remove.add_argument('name', help='要移除的连接名称')

    # exec
    p_exec = subparsers.add_parser('exec', help='执行命令')
    p_exec.add_argument('workspace', help='工作区名称')
    p_exec.add_argument('target', help='连接名称或 --all')
    p_exec.add_argument('command', help='要执行的命令')
    p_exec.add_argument('--timeout', '-t', type=int, default=60,
                        help='超时（秒），默认 60')

    # status
    p_status = subparsers.add_parser('status', help='全局状态面板')
    p_status.add_argument('workspace', help='工作区名称')
    p_status.add_argument('--check', '-c', action='store_true',
                          help='实时检查在线状态（较慢）')
    p_status.add_argument('--watch', '-w', action='store_true',
                          help='自动刷新模式（Ctrl+C 退出）')
    p_status.add_argument('--interval', type=int, default=3,
                          help='刷新间隔（秒），默认 3')

    # note
    p_note = subparsers.add_parser('note', help='给连接贴便签')
    p_note.add_argument('workspace', help='工作区名称')
    p_note.add_argument('name', help='连接名称')
    p_note.add_argument('text', help='便签内容')

    # check
    p_check = subparsers.add_parser('check', help='检查所有连接在线状态')
    p_check.add_argument('workspace', help='工作区名称')

    # list
    p_list = subparsers.add_parser('list', help='列出工作区')
    p_list.add_argument('--workspace', '-w', default=None,
                        help='指定工作区查看连接列表')

    # delete
    p_delete = subparsers.add_parser('delete', help='删除整个工作区')
    p_delete.add_argument('workspace', help='工作区名称')

    args = parser.parse_args()

    try:
        if args.command == 'create':
            # 检查是否已存在
            if load_workspace(args.workspace):
                print(json.dumps({'success': False,
                                  'error': f"工作区 '{args.workspace}' 已存在"},
                                 ensure_ascii=True))
                sys.exit(1)
            data = {
                'name': args.workspace,
                'created_at': now_iso(),
                'last_activity': now_iso(),
                'connections': []
            }
            save_workspace(data)
            print(json.dumps({'success': True,
                              'message': f"工作区 '{args.workspace}' 已创建"},
                             ensure_ascii=True))

        elif args.command == 'add':
            result = add_connection(args.workspace, args.name, args.alias)
            print(json.dumps(result, ensure_ascii=True))
            sys.exit(0 if result.get('success') else 1)

        elif args.command == 'remove':
            result = remove_connection(args.workspace, args.name)
            print(json.dumps(result, ensure_ascii=True))
            sys.exit(0 if result.get('success') else 1)

        elif args.command == 'exec':
            result = exec_command(args.workspace, args.target,
                                  args.command, args.timeout)
            # 截断输出，防 JSON 过大
            if 'results' in result:
                for name, r in result['results'].items():
                    if 'stdout' in r:
                        r['stdout'] = truncate_output(r['stdout'])
                    if 'stderr' in r:
                        r['stderr'] = truncate_output(r['stderr'])
            elif 'stdout' in result:
                result['stdout'] = truncate_output(result['stdout'])
            elif 'stderr' in result:
                result['stderr'] = truncate_output(result['stderr'])
            print(json.dumps(result, ensure_ascii=True, indent=2))
            sys.exit(0 if result.get('success') else 1)

        elif args.command == 'status':
            if args.watch:
                watch_status(args.workspace, interval=args.interval,
                             check=args.check)
            else:
                result = render_status(args.workspace, check=args.check)
                if result.get('success'):
                    print(result['table'])
                else:
                    print(json.dumps(result, ensure_ascii=True))
                    sys.exit(1)

        elif args.command == 'note':
            result = set_note(args.workspace, args.name, args.text)
            print(json.dumps(result, ensure_ascii=True))
            sys.exit(0 if result.get('success') else 1)

        elif args.command == 'check':
            result = check_online(args.workspace)
            if result.get('success'):
                print(json.dumps(result, ensure_ascii=True, indent=2))
            else:
                print(json.dumps(result, ensure_ascii=True))
                sys.exit(1)

        elif args.command == 'list':
            if args.workspace:
                # 列出指定工作区的连接
                data = load_workspace(args.workspace)
                if not data:
                    print(json.dumps({'success': False,
                                      'error': f"工作区 '{args.workspace}' 不存在"},
                                     ensure_ascii=True))
                    sys.exit(1)
                connections = [{
                    'name': c['name'],
                    'alias': c['alias'],
                    'note': c.get('note', ''),
                    'last_command': c.get('last_command'),
                    'last_exit_code': c.get('last_exit_code')
                } for c in data['connections']]
                print(json.dumps({
                    'workspace': args.workspace,
                    'connections': connections,
                    'count': len(connections)
                }, ensure_ascii=True, indent=2))
            else:
                # 列出所有工作区
                result = render_workspace_list()
                if result.get('success'):
                    print(result['table'])
                sys.exit(0)

        elif args.command == 'delete':
            if delete_workspace(args.workspace):
                print(json.dumps({'success': True,
                                  'message': f"工作区 '{args.workspace}' 已删除"},
                                 ensure_ascii=True))
            else:
                print(json.dumps({'success': False,
                                  'error': f"工作区 '{args.workspace}' 不存在"},
                                 ensure_ascii=True))
                sys.exit(1)

        else:
            parser.print_help()
            sys.exit(1)

    except Exception as e:
        print(json.dumps({'success': False, 'error': str(e)},
                         ensure_ascii=True), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
