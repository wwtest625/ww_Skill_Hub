#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH 远程文件精准编辑工具 v1.1

通过 SSH skill 的 ssh_execute/ssh_download/ssh_upload 实现远程文件精准编辑
复用守护进程连接，无需直连 Paramiko

用法：
    # 基本替换
    python ssh_edit.py <alias> <remote_path> --old "旧文本" --new "新文本"

    # 全局替换（替换所有匹配）
    python ssh_edit.py <alias> <remote_path> --old "旧文本" --new "新文本" --replace-all

    # 预览模式（只显示 diff，不上传）
    python ssh_edit.py <alias> <remote_path> --old "旧文本" --new "新文本" --dry-run

    # 追加内容到文件末尾
    python ssh_edit.py <alias> <remote_path> --append "要追加的内容"

    # 在指定行号后插入
    python ssh_edit.py <alias> <remote_path> --after 10 --insert "插入的内容"

    # 删除匹配行
    python ssh_edit.py <alias> <remote_path> --delete-line "要删除的行内容"

    # 查看远程文件内容（前 N 行）
    python ssh_edit.py <alias> <remote_path> --head 20

    # 查看远程文件指定行范围
    python ssh_edit.py <alias> <remote_path> --lines 10-25

示例：
    # 修复缺失的续行符
    python ssh_edit.py prod-web-01 /home/app/start.sh --old "--data-parallel-size 2" --new "--data-parallel-size 2 \\\\"

    # 修改端口
    python ssh_edit.py prod-web-01 /etc/app/config.yaml --old "port: 8080" --new "port: 9090"

    # 预览修改
    python ssh_edit.py prod-web-01 /home/app/start.sh --old "PORT=8000" --new "PORT=9000" --dry-run
"""

import sys
import os
import json
import argparse
import difflib
import re
import subprocess
import tempfile
import base64

# SSH skill 脚本目录（自动检测）
_SKILL_DIRS = [
    os.path.expanduser("~/.workbuddy/skills/ssh-skill/scripts"),
    os.path.expanduser("~/.codex/skills/ssh-skill/scripts"),
]

def _find_skill_dir():
    for d in _SKILL_DIRS:
        if os.path.isfile(os.path.join(d, "ssh_execute.py")):
            return d
    return None

SKILL_DIR = _find_skill_dir()
if not SKILL_DIR:
    print(json.dumps({
        'success': False,
        'error': 'Cannot find ssh-skill scripts directory'
    }), file=sys.stderr)
    sys.exit(1)


def _fix_remote_path(path):
    """修复被 MSYS bash 转换的远程路径"""
    if re.match(r'^[A-Za-z]:[/\\]', path):
        print(json.dumps({
            'success': False,
            'error': f'Remote path looks like a Windows path (MSYS conversion): {path}'
        }, ensure_ascii=True, indent=2), file=sys.stderr)
        sys.exit(1)
    return path


def ssh_execute(alias, command, timeout=30):
    """通过 ssh_execute.py 执行远程命令"""
    cmd = [sys.executable, os.path.join(SKILL_DIR, "ssh_execute.py"),
           alias, command, "--timeout", str(timeout)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+10)

    try:
        data = json.loads(result.stdout)
        return data.get('stdout', ''), data.get('stderr', ''), data.get('exit_code', -1)
    except (json.JSONDecodeError, KeyError):
        return result.stdout, result.stderr, result.returncode


def read_remote_file(alias, remote_path):
    """读取远程文件内容（通过 base64 编码传输，避免特殊字符问题）"""
    stdout, stderr, exit_code = ssh_execute(
        alias,
        f'base64 "{remote_path}"',
        timeout=30
    )
    if exit_code != 0:
        raise Exception(f"Failed to read {remote_path}: {stderr}")

    # 解码 base64
    b64_data = stdout.strip()
    content = base64.b64decode(b64_data).decode('utf-8', errors='replace')
    return content


def write_remote_file(alias, remote_path, content):
    """写入远程文件内容（通过 base64 编码传输）"""
    b64_data = base64.b64encode(content.encode('utf-8')).decode('ascii')

    # 分块传输，避免命令行过长
    chunk_size = 4000
    chunks = [b64_data[i:i+chunk_size] for i in range(0, len(b64_data), chunk_size)]

    # 先清空文件
    _, stderr, exit_code = ssh_execute(alias, f'truncate -s 0 "{remote_path}"', timeout=10)
    if exit_code != 0:
        raise Exception(f"Failed to truncate {remote_path}: {stderr}")

    # 逐块追加
    for chunk in chunks:
        _, stderr, exit_code = ssh_execute(
            alias,
            f'echo "{chunk}" | base64 -d >> "{remote_path}"',
            timeout=15
        )
        if exit_code != 0:
            raise Exception(f"Failed to write chunk to {remote_path}: {stderr}")


def show_diff(old_content, new_content, remote_path, context=3):
    """显示 diff，返回是否有变化"""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f'a/{remote_path}',
        tofile=f'b/{remote_path}',
        n=context
    ))

    if not diff:
        return False

    for line in diff:
        if line.startswith('+') and not line.startswith('+++'):
            print(f"\033[32m{line}\033[0m", end='')
        elif line.startswith('-') and not line.startswith('---'):
            print(f"\033[31m{line}\033[0m", end='')
        elif line.startswith('@@'):
            print(f"\033[36m{line}\033[0m", end='')
        else:
            print(line, end='')

    return True


def do_replace(content, old_str, new_str, replace_all=False):
    """执行字符串替换，返回 (新内容, 替换次数)"""
    count = content.count(old_str)
    if count == 0:
        return content, 0

    if not replace_all and count > 1:
        return content, -count  # -count 表示多处匹配

    new_content = content.replace(old_str, new_str) if replace_all else content.replace(old_str, new_str, 1)
    return new_content, count


def do_append(content, append_str):
    """追加内容到文件末尾"""
    if content and not content.endswith('\n'):
        content += '\n'
    content += append_str
    if not append_str.endswith('\n'):
        content += '\n'
    return content


def do_insert_after_line(content, line_num, insert_str):
    """在指定行号后插入内容"""
    lines = content.splitlines(keepends=True)
    if line_num < 1 or line_num > len(lines):
        raise ValueError(f"行号 {line_num} 超出范围 (1-{len(lines)})")

    insert_text = insert_str
    if not insert_text.endswith('\n'):
        insert_text += '\n'

    lines.insert(line_num, insert_text)
    return ''.join(lines)


def do_delete_line(content, pattern):
    """删除匹配的行"""
    lines = content.splitlines(keepends=True)
    new_lines = [l for l in lines if pattern not in l]
    return ''.join(new_lines), len(lines) - len(new_lines)


def do_show_lines(content, start, end):
    """显示指定行范围"""
    lines = content.splitlines()
    result = []
    for i in range(start - 1, min(end, len(lines))):
        result.append(f"{i+1:6d}\t{lines[i]}")
    return '\n'.join(result)


def main():
    parser = argparse.ArgumentParser(
        description='SSH 远程文件精准编辑工具 v1.1',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument('alias', help='SSH host alias from ~/.ssh/config')
    parser.add_argument('remote_path', help='Remote file path to edit')

    # 替换操作
    parser.add_argument('--old', dest='old_str', help='Old string to replace')
    parser.add_argument('--new', dest='new_str', default='', help='New string to replace with')
    parser.add_argument('--replace-all', action='store_true',
                        help='Replace all occurrences (default: fail if multiple matches)')

    # 追加/插入/删除
    parser.add_argument('--append', help='Append text to end of file')
    parser.add_argument('--after', type=int, metavar='LINE_NUM',
                        help='Insert text after this line number (use with --insert)')
    parser.add_argument('--insert', help='Text to insert (use with --after)')
    parser.add_argument('--delete-line', dest='delete_line',
                        help='Delete lines containing this text')

    # 查看操作
    parser.add_argument('--head', type=int, metavar='N',
                        help='Show first N lines of remote file')
    parser.add_argument('--lines', metavar='START-END',
                        help='Show lines from START to END (e.g., 10-25)')

    # 通用选项
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview changes without uploading')
    parser.add_argument('--backup', action='store_true',
                        help='Create backup (.bak) on remote server before editing')
    parser.add_argument('--context', type=int, default=3,
                        help='Diff context lines (default: 3)')

    args = parser.parse_args()
    remote_path = _fix_remote_path(args.remote_path)

    # 检查参数组合
    edit_ops = sum([
        args.old_str is not None,
        args.append is not None,
        args.after is not None,
        args.delete_line is not None,
        args.head is not None,
        args.lines is not None,
    ])

    if edit_ops == 0:
        print(json.dumps({
            'success': False,
            'error': 'No operation specified. Use --old/--new, --append, --after/--insert, --delete-line, --head, or --lines'
        }, ensure_ascii=True, indent=2), file=sys.stderr)
        sys.exit(1)

    try:
        # ===== 查看操作 =====
        if args.head or args.lines:
            content = read_remote_file(args.alias, remote_path)

            if args.head:
                print(do_show_lines(content, 1, args.head))
            elif args.lines:
                parts = args.lines.split('-')
                start, end = int(parts[0]), int(parts[1])
                print(do_show_lines(content, start, end))

            sys.exit(0)

        # ===== 编辑操作 =====
        content = read_remote_file(args.alias, remote_path)
        original_content = content
        change_desc = ""

        if args.old_str is not None:
            new_content, count = do_replace(content, args.old_str, args.new_str, args.replace_all)

            if count == 0:
                print(json.dumps({
                    'success': False,
                    'error': f'Old string not found in {remote_path}',
                    'hint': 'Check whitespace, line endings, or use --head to preview the file'
                }, ensure_ascii=True, indent=2))
                sys.exit(1)

            if count < 0:
                print(json.dumps({
                    'success': False,
                    'error': f'Found {-count} matches in {remote_path}. Use --replace-all to replace all, or make --old more specific.',
                    'matches': -count
                }, ensure_ascii=True, indent=2))
                sys.exit(1)

            content = new_content
            change_desc = f"Replaced {count} occurrence(s)"

        elif args.append is not None:
            content = do_append(content, args.append)
            change_desc = "Appended text to end of file"

        elif args.after is not None:
            if args.insert is None:
                print(json.dumps({
                    'success': False,
                    'error': '--after requires --insert to specify the text to insert'
                }, ensure_ascii=True, indent=2))
                sys.exit(1)
            content = do_insert_after_line(content, args.after, args.insert)
            change_desc = f"Inserted text after line {args.after}"

        elif args.delete_line is not None:
            content, deleted = do_delete_line(content, args.delete_line)
            if deleted == 0:
                print(json.dumps({
                    'success': False,
                    'error': f'No lines matching "{args.delete_line}" found in {remote_path}'
                }, ensure_ascii=True, indent=2))
                sys.exit(1)
            change_desc = f"Deleted {deleted} line(s)"

        # 显示 diff
        has_changes = show_diff(original_content, content, remote_path, args.context)

        if not has_changes:
            print(json.dumps({
                'success': True,
                'message': 'No changes detected',
                'file': remote_path
            }, ensure_ascii=True, indent=2))
            sys.exit(0)

        if args.dry_run:
            print(json.dumps({
                'success': True,
                'message': 'Dry run - no changes uploaded',
                'file': remote_path,
                'change': change_desc
            }, ensure_ascii=True, indent=2))
            sys.exit(0)

        # 备份
        if args.backup:
            _, stderr, exit_code = ssh_execute(
                args.alias,
                f'cp "{remote_path}" "{remote_path}.bak"',
                timeout=10
            )
            if exit_code != 0:
                print(f"Warning: backup failed: {stderr}", file=sys.stderr)

        # 上传修改后的内容
        write_remote_file(args.alias, remote_path, content)

        print(json.dumps({
            'success': True,
            'message': change_desc,
            'file': remote_path,
            'backup': f'{remote_path}.bak' if args.backup else None
        }, ensure_ascii=True, indent=2))

    except ValueError as e:
        print(json.dumps({
            'success': False,
            'error': f'Invalid alias: {e}'
        }, ensure_ascii=True, indent=2), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({
            'success': False,
            'error': f'Edit error: {e}'
        }, ensure_ascii=True, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
