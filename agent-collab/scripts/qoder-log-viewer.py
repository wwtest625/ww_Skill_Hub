#!/usr/bin/env python3
"""qoder 会话记录查看器：把 /root/.qoder/projects/-root/*.jsonl 解析为可读对话流。

用法:
  python3 qoder-log-viewer.py                   # 列出所有会话
  python3 qoder-log-viewer.py -g 关键词         # 全局搜索包含关键词的会话列表
  python3 qoder-log-viewer.py <会话ID或路径>     # 完整对话流（默认）
  python3 qoder-log-viewer.py <会话> -s         # 摘要模式：只留用户消息+助手文本+工具名
  python3 qoder-log-viewer.py <会话> -g 关键词   # 过滤模式：只显示包含关键词的对话轮
  选项可组合: -s -g 关键词  → 过滤后按摘要显示
"""
import json
import sys
import glob
import os
import re
import signal
import argparse
from datetime import datetime

# 被 head/管道截断时静默退出，不打印 BrokenPipeError
signal.signal(signal.SIGPIPE, signal.SIG_DFL)

PROJ_DIR = "/root/.qoder/projects/-root"
TOOL_USE_MAP = {}


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
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo:
                dt = dt.astimezone()  # 转本地时区
            return dt.strftime("%m-%d %H:%M:%S")
        except Exception:
            return ts
    return str(ts)


def truncate(s, n=1500):
    s = str(s).rstrip()
    if len(s) > n:
        return s[:n] + f" ... [截断，原文共 {len(s)} 字]"
    return s


def tool_detail(inp):
    desc = inp.get("description") or ""
    cmd = inp.get("command") or ""
    url = inp.get("url") or ""
    return desc or (cmd[:150] + ("…" if len(cmd) > 150 else "") if cmd else "") or (url[:120] if url else "")


def result_text(b):
    """从 tool_result 块提取要展示的文本。"""
    out = b.get("content") or ""
    res = b.get("toolUseResult") or {}
    if res.get("stdout") and len(str(res["stdout"])) > len(str(out)):
        out = res["stdout"]
    return str(out)


def list_sessions(grep=None):
    paths = sorted(glob.glob(os.path.join(PROJ_DIR, "*.jsonl")),
                   reverse=True, key=os.path.getmtime)
    if not paths:
        print("未找到任何会话记录")
        return
    
    matches = []
    for p in paths:
        events = load_events(p)
        title = next((e.get("aiTitle") or "" for e in events
                      if e.get("type") == "ai-title"), "")
        model = next((e.get("model") or "" for e in events
                      if e.get("type") == "runtime-config"), "")
        ts = datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")
        sid = os.path.basename(p)[:36]
        
        if grep:
            kw = grep.lower()
            if kw not in title.lower() and kw not in sid.lower() and not file_contains_keyword(p, grep):
                continue
        matches.append((ts, model, title, sid))
        
    if not matches:
        if grep:
            print(f"未找到包含关键词 '{grep}' 的 qoder 会话记录")
        else:
            print("未找到任何会话记录")
        return

    print(f"{'修改时间':<17} {'模型':<22} {'标题':<34} 会话ID")
    print("-" * 120)
    for ts, model, title, sid in matches:
        print(f"{ts:<17} {model:<22} {title[:34]:<34} {sid}")


def build_turns(events):
    """把事件流组织成轮次。每轮: user 文本 + items(assistant text/tool_use/tool_result)。

    item 元组: ("text", ts, str) | ("tool", ts, (name, input)) | ("result", ts, block)
    """
    turns = []
    cur = None
    for e in events:
        t = e.get("type")
        if t == "ai-title":
            continue
        ts = e.get("timestamp")
        if t == "user":
            content = (e.get("message") or {}).get("content")
            if isinstance(content, str):
                text = content.strip()
                m = re.search(r"<command-name>([^<]+)</command-name>", text)
                if m:
                    text = m.group(1) + " (命令)"
                text = text.strip()
                if not text:
                    continue
                if cur:
                    turns.append(cur)
                cur = {"user": (ts, text), "items": []}
            elif isinstance(content, list) and cur is not None:
                for b in content:
                    if b.get("type") == "tool_result":
                        cur["items"].append(("result", ts, b))
        elif t == "assistant" and cur is not None:
            content = (e.get("message") or {}).get("content") or []
            if isinstance(content, list):
                for b in content:
                    btype = b.get("type")
                    if btype == "text":
                        txt = (b.get("text") or "").strip()
                        if txt:
                            cur["items"].append(("text", ts, txt))
                    elif btype == "tool_use":
                        name = b.get("name")
                        TOOL_USE_MAP[b.get("id")] = name
                        cur["items"].append(("tool", ts, (name, b.get("input") or {})))
    if cur:
        turns.append(cur)
    return turns


def print_turn_summary(turn):
    ts, user_text = turn["user"]
    print(f"\n[用户 {fmt_ts(ts)}]")
    for line in user_text.splitlines()[:5]:
        print(f"  {line}")
    if len(user_text.splitlines()) > 5:
        print(f"  … 还有 {len(user_text.splitlines()) - 5} 行")
    for kind, ts, val in turn["items"]:
        if kind == "text":
            print(f"\n[助手 {fmt_ts(ts)}]")
            lines = val.splitlines()
            for line in lines[:30]:
                print(f"  {line}")
            if len(lines) > 30:
                print(f"  … 还有 {len(lines) - 30} 行")
        elif kind == "tool":
            name, inp = val
            print(f"    → 调用 {name}: {tool_detail(inp)}")


def print_turn_full(turn):
    ts, user_text = turn["user"]
    print(f"\n[用户 {fmt_ts(ts)}]")
    for line in user_text.splitlines():
        print(f"  {line}")
    for kind, ts, val in turn["items"]:
        if kind == "text":
            print(f"\n[助手 {fmt_ts(ts)}]")
            for line in val.splitlines():
                print(f"  {line}")
        elif kind == "tool":
            name, inp = val
            detail = tool_detail(inp)
            print(f"    → 调用 {name}: {detail}")
        elif kind == "result":
            name = TOOL_USE_MAP.get(val.get("tool_use_id"), "?")
            out = truncate(result_text(val))
            print(f"\n  ↳ {name} 执行结果 [{fmt_ts(ts)}]")
            lines = out.splitlines()
            for line in lines[:30]:
                print(f"    │ {line}")
            if len(lines) > 30:
                print(f"    │ … 还有 {len(lines) - 30} 行")


def turn_contains(turn, kw):
    kw_lower = kw.lower()
    if turn["user"][1] and kw_lower in turn["user"][1].lower():
        return True
    for kind, ts, val in turn["items"]:
        if kind == "text" and kw_lower in val.lower():
            return True
        if kind == "result" and kw_lower in result_text(val).lower():
            return True
    return False


def show_session(path, summary=False, grep=None):
    events = [e for e in load_events(path) if e.get("type") in ("user", "assistant", "ai-title")]
    events.sort(key=lambda e: e.get("timestamp") or "")
    sid = os.path.basename(path)[:36]
    print(f"═══════ qoder 会话 {sid} ═══════")
    for e in events:
        if e.get("type") == "ai-title":
            print(f"\n# {e.get('aiTitle')}")
            break

    turns = build_turns(events)
    for turn in turns:
        if grep and not turn_contains(turn, grep):
            continue
        if summary or grep:
            print_turn_summary(turn)
        else:
            print_turn_full(turn)


def main():
    p = argparse.ArgumentParser(description="qoder 会话记录查看器")
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
        matches = [p for p in glob.glob(os.path.join(PROJ_DIR, "*.jsonl"))
                   if arg in os.path.basename(p)]
        if len(matches) == 1:
            path = matches[0]
        else:
            print(f"找不到会话: {arg}（匹配到 {len(matches)} 个）\n可先不带参数运行，列出所有会话 ID")
            sys.exit(1)
    show_session(path, summary=args.summary, grep=args.grep)


if __name__ == "__main__":
    main()
