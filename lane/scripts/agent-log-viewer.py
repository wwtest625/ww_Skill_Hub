#!/usr/bin/env python3
"""三 Agent (agy / qoder / CodeBuddy) 统一会话记录查看器。

聚合查看本机 /root 下三大 Agent 的全部会话历史与对话详情。

用法:
  python3 agent-log-viewer.py                     # 统一按时间列出最近 20 条会话
  python3 agent-log-viewer.py -n 50               # 查看最近 50 条会话
  python3 agent-log-viewer.py -g 关键词           # 全局检索所有 Agent 的匹配会话
  python3 agent-log-viewer.py -w 工作区           # 按工作空间/项目名筛选 (如 metax-workbench)
  python3 agent-log-viewer.py --agent agy         # 仅看 agy 会话 (可选 agy / qoder / codebuddy)
  python3 agent-log-viewer.py <会话ID>            # 自动识别 Agent 并查看完整会话
  python3 agent-log-viewer.py <会话ID> -s         # 摘要模式查看
  python3 agent-log-viewer.py <会话ID> -g 关键词   # 轮次过滤模式查看
"""
import sys
import glob
import os
import json
import signal
import argparse
import subprocess
import importlib.util
from datetime import datetime

signal.signal(signal.SIGPIPE, signal.SIG_DFL)

AGY_CONV_DIR = "/root/.gemini/antigravity-cli/conversations"
QODER_PROJ_DIR = "/root/.qoder/projects"
CB_PROJ_DIR = "/root/.codebuddy/projects"


def load_module_from_file(mod_name, file_path):
    spec = importlib.util.spec_from_file_location(mod_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def normalize_workspace(raw_ws):
    if not raw_ws:
        return "root"
    ws = str(raw_ws).strip()
    if ws.startswith("["):
        try:
            arr = json.loads(ws)
            if arr and isinstance(arr, list):
                ws = arr[0]
        except Exception:
            ws = ws.strip("[]\"'")
    if ws.startswith("file://"):
        ws = ws[7:]
    if ws.startswith("-"):
        ws = ws[1:]
    if ws in ("root", "/root", "/root/"):
        return "root"
    if ws.startswith("root-"):
        ws = ws[5:]
    elif ws.startswith("/root/"):
        ws = ws[6:]
    return ws or "root"


def get_agy_sessions(agy_mod, grep=None, ws_filter=None):
    files = glob.glob(os.path.join(AGY_CONV_DIR, "*.db"))
    sums = agy_mod.load_summaries()
    res = []
    kw = grep.lower() if grep else None
    wskw = ws_filter.lower() if ws_filter else None
    for p in files:
        sid = os.path.basename(p)[:-3]
        mtime = os.path.getmtime(p)
        ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        preview = sums.get(sid, {}).get("preview", "")
        raw_ws = sums.get(sid, {}).get("workspace", "")
        ws = normalize_workspace(raw_ws)
        if wskw and wskw not in ws.lower() and wskw not in str(raw_ws).lower():
            continue
        if kw:
            if kw not in preview.lower() and kw not in sid.lower() and kw not in ws.lower() and not agy_mod.db_contains_keyword(p, grep):
                continue
        res.append({
            "agent": "agy",
            "mtime": mtime,
            "ts": ts,
            "ws": ws,
            "title": preview,
            "sid": sid,
            "path": p
        })
    return res


def get_qoder_sessions(qoder_mod, grep=None, ws_filter=None):
    files = glob.glob(os.path.join(QODER_PROJ_DIR, "*", "*.jsonl"))
    res = []
    kw = grep.lower() if grep else None
    wskw = ws_filter.lower() if ws_filter else None
    for p in files:
        mtime = os.path.getmtime(p)
        ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        sid = os.path.basename(p)[:36]
        raw_folder = os.path.basename(os.path.dirname(p))
        ws = normalize_workspace(raw_folder)
        if wskw and wskw not in ws.lower() and wskw not in raw_folder.lower():
            continue
        events = qoder_mod.load_events(p)
        title = next((e.get("aiTitle") or "" for e in events if e.get("type") == "ai-title"), "")
        if kw:
            if kw not in title.lower() and kw not in sid.lower() and kw not in ws.lower() and not qoder_mod.file_contains_keyword(p, grep):
                continue
        res.append({
            "agent": "qoder",
            "mtime": mtime,
            "ts": ts,
            "ws": ws,
            "title": title,
            "sid": sid,
            "path": p
        })
    return res


def get_cb_sessions(cb_mod, grep=None, ws_filter=None):
    files = glob.glob(os.path.join(CB_PROJ_DIR, "*", "*.jsonl"))
    res = []
    kw = grep.lower() if grep else None
    wskw = ws_filter.lower() if ws_filter else None
    for p in files:
        mtime = os.path.getmtime(p)
        ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        sid = os.path.basename(p)[:-6]
        raw_ws = os.path.basename(os.path.dirname(p))
        ws = normalize_workspace(raw_ws)
        if wskw and wskw not in ws.lower() and wskw not in raw_ws.lower():
            continue
        events = cb_mod.load_events(p)
        title = next((e.get("aiTitle") or "" for e in events if e.get("type") == "ai-title"), "")
        if kw:
            if kw not in title.lower() and kw not in sid.lower() and kw not in ws.lower() and not cb_mod.file_contains_keyword(p, grep):
                continue
        res.append({
            "agent": "CodeBuddy",
            "mtime": mtime,
            "ts": ts,
            "ws": ws,
            "title": title,
            "sid": sid,
            "path": p
        })
    return res


def find_session_agent(session_arg):
    if os.path.isfile(session_arg):
        if session_arg.endswith(".db"):
            return "agy", session_arg
        elif ".codebuddy" in session_arg:
            return "codebuddy", session_arg
        elif ".qoder" in session_arg:
            return "qoder", session_arg

    agy_matches = [p for p in glob.glob(os.path.join(AGY_CONV_DIR, "*.db")) if session_arg in os.path.basename(p)]
    qoder_matches = [p for p in glob.glob(os.path.join(QODER_PROJ_DIR, "*", "*.jsonl")) if session_arg in os.path.basename(p)]
    cb_matches = [p for p in glob.glob(os.path.join(CB_PROJ_DIR, "*", "*.jsonl")) if session_arg in os.path.basename(p)]

    total = len(agy_matches) + len(qoder_matches) + len(cb_matches)
    if total == 0:
        return None, None
    if total > 1:
        print(f"匹配到多个会话（共 {total} 个），请提供更精确的前缀：")
        for p in agy_matches:
            print(f"  [agy] {os.path.basename(p)[:-3]}")
        for p in qoder_matches:
            print(f"  [qoder] {os.path.basename(p)[:36]}")
        for p in cb_matches:
            print(f"  [CodeBuddy] {os.path.basename(p)[:-6]}")
        sys.exit(1)

    if agy_matches:
        return "agy", agy_matches[0]
    if qoder_matches:
        return "qoder", qoder_matches[0]
    if cb_matches:
        return "codebuddy", cb_matches[0]
    return None, None


def main():
    p = argparse.ArgumentParser(description="三 Agent 统一会话记录查看器 (agy / qoder / CodeBuddy)")
    p.add_argument("session", nargs="?", help="会话 ID（前缀即可）或文件路径")
    p.add_argument("-s", "--summary", action="store_true", help="摘要模式")
    p.add_argument("-g", "--grep", metavar="关键词", help="全局搜索或对话内过滤关键词")
    p.add_argument("-w", "--workspace", metavar="工作区", help="按工作区/项目名筛选 (如 metax-workbench)")
    p.add_argument("-n", "--limit", type=int, default=25, help="列表最大显示数量 (默认 25)")
    p.add_argument("-a", "--agent", choices=["agy", "qoder", "codebuddy", "all"], default="all",
                   help="筛选特定 agent 的会话")
    p.add_argument("-T", "--no-thinking", action="store_true", help="agy 会话跳过思考流")
    args = p.parse_args()

    if args.session:
        agent_type, path = find_session_agent(args.session)
        if not agent_type:
            print(f"未找到匹配会话: {args.session}")
            sys.exit(1)
        
        cmd = ["python3"]
        if agent_type == "agy":
            cmd.extend(["/root/agy-log-viewer.py", path])
            if args.summary: cmd.append("-s")
            if args.grep: cmd.extend(["-g", args.grep])
            if args.no_thinking: cmd.append("-T")
        elif agent_type == "qoder":
            cmd.extend(["/root/qoder-log-viewer.py", path])
            if args.summary: cmd.append("-s")
            if args.grep: cmd.extend(["-g", args.grep])
        elif agent_type == "codebuddy":
            cmd.extend(["/root/codebuddy-log-viewer.py", path])
            if args.summary: cmd.append("-s")
            if args.grep: cmd.extend(["-g", args.grep])
        
        subprocess.run(cmd)
        return

    # 聚合列表模式
    agy_mod = load_module_from_file("agy_mod", "/root/agy-log-viewer.py")
    qoder_mod = load_module_from_file("qoder_mod", "/root/qoder-log-viewer.py")
    cb_mod = load_module_from_file("cb_mod", "/root/codebuddy-log-viewer.py")

    all_sessions = []
    if args.agent in ("all", "agy"):
        all_sessions.extend(get_agy_sessions(agy_mod, grep=args.grep, ws_filter=args.workspace))
    if args.agent in ("all", "qoder"):
        all_sessions.extend(get_qoder_sessions(qoder_mod, grep=args.grep, ws_filter=args.workspace))
    if args.agent in ("all", "codebuddy"):
        all_sessions.extend(get_cb_sessions(cb_mod, grep=args.grep, ws_filter=args.workspace))

    all_sessions.sort(key=lambda x: x["mtime"], reverse=True)

    if not all_sessions:
        msg = "未找到任何会话记录"
        if args.grep and args.workspace:
            msg = f"未找到匹配关键词 '{args.grep}' 且工作区包含 '{args.workspace}' 的会话记录"
        elif args.grep:
            msg = f"未找到包含关键词 '{args.grep}' 的任何会话记录"
        elif args.workspace:
            msg = f"未找到工作区包含 '{args.workspace}' 的任何会话记录"
        print(msg)
        return

    total_count = len(all_sessions)
    display_sessions = all_sessions[:args.limit]

    print(f"{'修改时间':<17} {'Agent':<12} {'工作空间':<22} {'标题/主题':<36} 会话ID")
    print("-" * 125)
    for s in display_sessions:
        agent_tag = f"[{s['agent']}]"
        print(f"{s['ts']:<17} {agent_tag:<12} {s['ws'][:22]:<22} {s['title'][:36]:<36} {s['sid']}")
    
    if total_count > args.limit:
        print(f"\n共 {total_count} 条记录，已展示前 {args.limit} 条（可用 -n <数字> 查看更多）。")


if __name__ == "__main__":
    main()
