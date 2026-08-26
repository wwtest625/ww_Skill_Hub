#!/usr/bin/env python3
"""CodeBuddy 会话记录查看器：把 /root/.codebuddy/projects/*/*.jsonl 解析为可读对话流。

事件类型: message(user input_text/assistant output_text)、function_call / function_call_result(工具)、
         ai-title(标题)、reasoning(思考，可跳过)、file-history-snapshot/diff 等(忽略)

用法:
  python3 codebuddy-log-viewer.py                  # 列出所有会话
  python3 codebuddy-log-viewer.py -g 关键词        # 全局搜索包含关键词的会话列表
  python3 codebuddy-log-viewer.py <会话ID或路径>    # 完整对话流
  python3 codebuddy-log-viewer.py <会话> -s        # 摘要模式（折叠工具细节）
  python3 codebuddy-log-viewer.py <会话> -g 关键词  # 过滤模式（只显示匹配轮次）
"""
import json
import sys
import glob
import os
import signal
import argparse
from datetime import datetime

signal.signal(signal.SIGPIPE, signal.SIG_DFL)

PROJ_DIR = "/root/.codebuddy/projects"


def load_events(path):
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def file_contains_keyword(path, kw):
    try:
        kw_lower = kw.lower()
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if kw_lower in line.lower():
                    return True
        return False
    except Exception:
        return False


def fmt_ts(ts):
    if not ts:
        return ""
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M:%S")
        except Exception:
            return str(ts)
    return str(ts)


def truncate(s, n=800):
    s = str(s).rstrip()
    if len(s) > n:
        return s[:n] + f" … [截断，共 {len(s)} 字]"
    return s


def list_sessions(grep=None):
    files = sorted(glob.glob(os.path.join(PROJ_DIR, "*", "*.jsonl")),
                   reverse=True, key=os.path.getmtime)
    if not files:
        print("未找到任何 CodeBuddy 会话记录")
        return
    
    matches = []
    for p in files:
        sid = os.path.basename(p)[:-6]
        ws = os.path.basename(os.path.dirname(p))
        ts = datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")
        title = next((e.get("aiTitle") or "" for e in load_events(p)
                      if e.get("type") == "ai-title"), "")
        if grep:
            kw = grep.lower()
            if kw not in title.lower() and kw not in sid.lower() and kw not in ws.lower() and not file_contains_keyword(p, grep):
                continue
        matches.append((ts, ws, title, sid))

    if not matches:
        if grep:
            print(f"未找到包含关键词 '{grep}' 的 CodeBuddy 会话记录")
        else:
            print("未找到任何 CodeBuddy 会话记录")
        return

    print(f"{'修改时间':<17} {'工作区':<22} {'标题':<34} 会话ID")
    print("-" * 120)
    for ts, ws, title, sid in matches:
        print(f"{ts:<17} {ws[:22]:<22} {title[:34]:<34} {sid}")


def build_turns(path):
    """解析事件流为轮次列表。事件: (kind, ts, val)
    kind: user / assistant / tool / title
    """
    events = [e for e in load_events(path)
              if e.get("type") in ("message", "function_call", "function_call_result", "ai-title")]
    events.sort(key=lambda e: e.get("timestamp") or 0)
    turns = []
    cur = None
    for e in events:
        t = e.get("type")
        ts = e.get("timestamp")
        if t == "ai-title":
            if turns:
                turns.insert(0, ("title", ts, e.get("aiTitle") or ""))
            continue
        if t == "message":
            role = e.get("role")
            text = "\n".join(b.get("text") or "" for b in (e.get("content") or [])
                             if b.get("type") in ("input_text", "output_text")).strip()
            if not text:
                continue
            if role == "user":
                if cur:
                    turns.append(cur)
                cur = ("user", ts, text)
            else:
                if cur:
                    turns.append(cur)
                cur = ("assistant", ts, text)
        elif t == "function_call_result":
            name = e.get("name") or ""
            out = e.get("output") or {}
            text = out.get("text") or ""
            if cur:
                turns.append(cur)
            cur = ("tool", ts, (name, truncate(text)))
    if cur:
        turns.append(cur)
    return turns


def show_turn(kind, ts, val, summary, grep):
    if kind == "title":
        print(f"\n# {val}")
    elif kind == "user":
        print(f"\n[用户 {fmt_ts(ts)}]")
        lines = val.splitlines()
        limit = 8 if summary else 40
        for line in lines[:limit]:
            print(f"  {line}")
        if len(lines) > limit:
            print(f"  … 还有 {len(lines) - limit} 行")
    elif kind == "assistant":
        print(f"\n[助手 {fmt_ts(ts)}]")
        for line in val.splitlines():
            print(f"  {line}")
    elif kind == "tool":
        name, out = val
        if summary:
            first = out.splitlines()[0][:60] if out.strip() else ""
            print(f"    → 调用 {name}: {first}")
        else:
            print(f"\n  ↳ {name} 结果 [{fmt_ts(ts)}]")
            lines = out.splitlines()
            for line in lines[:15]:
                print(f"    │ {line}")
            if len(lines) > 15:
                print(f"    │ … 还有 {len(lines) - 15} 行")


def show_session(path, summary=False, grep=None):
    sid = os.path.basename(path)[:-6]
    print(f"═══════ CodeBuddy 会话 {sid} ═══════")
    turns = build_turns(path)
    # 组织成轮组：user 消息为起点
    groups = []
    for turn in turns:
        if turn[0] in ("user", "title"):
            groups.append([turn])
        elif groups:
            groups[-1].append(turn)
        else:
            groups.append([turn])
    for group in groups:
        if grep:
            text = " ".join(str(v) if isinstance(v, str) else " ".join(str(x) for x in v)
                            for k, ts, v in group)
            if grep.lower() not in text.lower():
                continue
        for kind, ts, val in group:
            show_turn(kind, ts, val, summary, grep)


def main():
    p = argparse.ArgumentParser(description="CodeBuddy 会话记录查看器")
    p.add_argument("session", nargs="?", help="会话 ID（前缀即可）或 .jsonl 文件路径")
    p.add_argument("-s", "--summary", action="store_true", help="摘要模式")
    p.add_argument("-g", "--grep", metavar="关键词", help="全局搜索包含关键词的会话（无 session 时），或过滤单会话对话轮")
    args = p.parse_args()

    if not args.session:
        list_sessions(grep=args.grep)
        return

    arg = args.session
    if os.path.isfile(arg):
        path = arg
    else:
        matches = [p for p in glob.glob(os.path.join(PROJ_DIR, "*", "*.jsonl"))
                   if arg in os.path.basename(p)]
        if len(matches) == 1:
            path = matches[0]
        else:
            print(f"找不到会话: {arg}（匹配到 {len(matches)} 个）\n可先不带参数运行，列出所有会话 ID")
            sys.exit(1)
    show_session(path, summary=args.summary, grep=args.grep)


if __name__ == "__main__":
    main()
