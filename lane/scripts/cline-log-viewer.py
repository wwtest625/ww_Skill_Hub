#!/usr/bin/env python3
"""Cline CLI 会话记录查看器。

数据位置:
  - SQLite 索引: /root/.cline/data/db/sessions.db (sessions 表)
  - 消息详情:   /root/.cline/data/sessions/<session_id>/<session_id>.messages.json

用法:
  python3 cline-log-viewer.py                  # 列出所有 Cline 会话
  python3 cline-log-viewer.py -g 关键词        # 全局搜索包含关键词的会话
  python3 cline-log-viewer.py <会话ID>         # 完整对话流
  python3 cline-log-viewer.py <会话ID> -s      # 摘要模式
  python3 cline-log-viewer.py <会话ID> -T      # 跳过思考流
"""
import os
import sys
import glob
import json
import sqlite3
import argparse
from datetime import datetime

CLINE_DIR = "/root/.cline/data"
SESSIONS_DB = os.path.join(CLINE_DIR, "db", "sessions.db")
SESSIONS_DIR = os.path.join(CLINE_DIR, "sessions")


def load_summaries():
    """返回 session_id -> {title, cwd, mtime, ts, messages_path, status, model}"""
    out = {}
    if not os.path.isfile(SESSIONS_DB):
        return out
    try:
        conn = sqlite3.connect(f"file:{SESSIONS_DB}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT session_id, cwd, prompt, metadata_json, messages_path, updated_at, status, model FROM sessions")
        for sid, cwd, prompt, meta_str, msg_path, updated_at, status, model in cur.fetchall():
            meta = {}
            if meta_str:
                try:
                    meta = json.loads(meta_str)
                except Exception:
                    pass
            title = meta.get("title") or ""
            if not title and prompt:
                # 剥离 XML tag
                import re
                clean_p = re.sub(r"<[^>]+>", "", prompt).strip()
                title = clean_p.replace("\n", " ")[:36]
            if not title:
                title = "(未命名会话)"

            mtime = 0
            ts = ""
            if updated_at:
                try:
                    # ISO 格式转时间戳
                    dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    mtime = dt.timestamp()
                    ts = dt.astimezone().strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass

            if not mtime and msg_path and os.path.isfile(msg_path):
                mtime = os.path.getmtime(msg_path)
                ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")

            out[sid] = {
                "title": title,
                "cwd": cwd or "/root",
                "mtime": mtime,
                "ts": ts,
                "messages_path": msg_path,
                "status": status,
                "model": model or ""
            }
        conn.close()
    except Exception:
        pass
    return out


def find_session_file(sid_arg):
    if os.path.isfile(sid_arg):
        sid = os.path.basename(os.path.dirname(sid_arg))
        return sid, sid_arg
    sid_clean = sid_arg.strip()
    # 1. 查 DB 匹配
    sums = load_summaries()
    for sid, info in sums.items():
        if sid_clean in sid:
            p = info["messages_path"]
            if p and os.path.isfile(p):
                return sid, p
            default_p = os.path.join(SESSIONS_DIR, sid, f"{sid}.messages.json")
            if os.path.isfile(default_p):
                return sid, default_p

    # 2. 查目录匹配
    matches = glob.glob(os.path.join(SESSIONS_DIR, f"*{sid_clean}*", "*.messages.json"))
    if matches:
        sid = os.path.basename(os.path.dirname(matches[0]))
        return sid, matches[0]

    return None, None


def file_contains_keyword(file_path, keyword):
    if not file_path or not os.path.isfile(file_path):
        return False
    kw = keyword.lower()
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if kw in line.lower():
                    return True
    except Exception:
        pass
    return False


def load_messages(file_path):
    if not os.path.isfile(file_path):
        return []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data.get("messages", [])
            elif isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def print_session_stream(file_path, sid, summary=False, grep=None, no_thinking=False):
    msgs = load_messages(file_path)
    if not msgs:
        print("未解析到任何有效消息。")
        return

    kw = grep.lower() if grep else None
    print(f"\n═══════ Cline 会话 {sid} ═══════\n")

    for i, m in enumerate(msgs):
        role = m.get("role", "unknown")
        content = m.get("content", [])
        
        # 提取文本与工具调用
        texts = []
        tools = []
        thinking_text = ""

        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    t = (block.get("text") or "").strip()
                    if t:
                        texts.append(t)
                elif btype == "thinking":
                    thinking_text += block.get("thinking") or ""
                elif btype == "tool_use":
                    tools.append(f"调用 {block.get('name')}")
                elif btype == "tool_result":
                    tools.append(f"返回 {block.get('name')}")

        full_content = "\n".join(texts)
        if kw:
            match = (kw in full_content.lower()) or (kw in thinking_text.lower())
            if not match:
                continue

        if role == "user":
            print(f"[用户]")
            # 清理 user_input 标签
            import re
            clean_text = re.sub(r"<user_input mode=\"[^\"]*\">", "", full_content)
            clean_text = clean_text.replace("</user_input>", "").strip()
            if summary:
                print(f"  {clean_text[:200]}")
            else:
                for line in clean_text.splitlines():
                    print(f"  {line}")
            print()

        elif role == "assistant":
            print(f"[Cline]")
            if thinking_text and not no_thinking and not summary:
                print("  <思考流>")
                for line in thinking_text.strip().splitlines()[:10]:
                    print(f"    {line}")
                if len(thinking_text.strip().splitlines()) > 10:
                    print("    ...")
                print("  </思考流>")

            for t in tools:
                print(f"  → {t}")

            if summary:
                lines = [l for l in full_content.splitlines() if l.strip()]
                if lines:
                    print(f"  {lines[0][:150]}")
            else:
                for line in full_content.splitlines():
                    print(f"  {line}")
            print()


def main():
    p = argparse.ArgumentParser(description="Cline 会话记录查看器")
    p.add_argument("session", nargs="?", help="会话 ID（前缀即可）")
    p.add_argument("-s", "--summary", action="store_true", help="摘要模式")
    p.add_argument("-g", "--grep", metavar="关键词", help="搜索关键词")
    p.add_argument("-T", "--no-thinking", action="store_true", help="跳过思考流")
    args = p.parse_args()

    if args.session:
        sid, path = find_session_file(args.session)
        if not sid or not path:
            print(f"未找到匹配的 Cline 会话: {args.session}")
            sys.exit(1)
        print_session_stream(path, sid, summary=args.summary, grep=args.grep, no_thinking=args.no_thinking)
        return

    # 列表模式
    sums = load_summaries()
    sessions = []
    for sid, info in sums.items():
        if args.grep:
            if args.grep.lower() not in info["title"].lower() and not file_contains_keyword(info["messages_path"], args.grep):
                continue
        sessions.append({
            "sid": sid,
            "ts": info["ts"],
            "mtime": info["mtime"],
            "cwd": info["cwd"],
            "title": info["title"]
        })

    sessions.sort(key=lambda x: x["mtime"], reverse=True)
    print(f"{'修改时间':<17} {'工作空间':<22} {'标题/主题':<40} 会话ID")
    print("-" * 110)
    for s in sessions:
        print(f"{s['ts']:<17} {s['cwd'][:22]:<22} {s['title'][:40]:<40} {s['sid']}")
    print(f"\n共 {len(sessions)} 条 Cline 会话记录。")


if __name__ == "__main__":
    main()
