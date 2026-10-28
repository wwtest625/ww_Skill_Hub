#!/usr/bin/env python3
"""Trae CN 会话查看器。

优先读取解密后的明文镜像（~/.agent/trae/trae.db），展示完整对话；
若解密不可用，则回退到 Trae 自动生成的 memory 摘要。

用法:
  python3 trae-log-viewer.py <会话ID>             # 查看完整对话（默认摘要）
  python3 trae-log-viewer.py <会话ID> --full      # 完整模式（含思考与工具调用）
  python3 trae-log-viewer.py <会话ID> -s          # 摘要模式：只显示用户与最终回复
  python3 trae-log-viewer.py <会话ID> -g 词       # 只显示命中关键词的消息
  python3 trae-log-viewer.py <会话ID> -m 3        # 只输出第 3 条消息全文
"""
import argparse
import glob
import json
import os
import re
import signal
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import trae_db
except Exception:
    trae_db = None

signal.signal(signal.SIGPIPE, signal.SIG_DFL)

MEM_DIR = "/Users/siri/.trae-cn/memory/projects"
EXPORT_DIR = "/Users/siri/.agent/trae/projects"


# ---------- memory 摘要回退 ----------

def clean_project_name(raw):
    if not raw:
        return "root"
    name = raw[1:] if raw.startswith("-") else raw
    name = re.split(r"--p2-", name, maxsplit=1)[0]
    return name or "root"


def sid_from_path(path):
    name = os.path.basename(path)
    if name.startswith("session_memory_") and name.endswith(".jsonl"):
        return name[len("session_memory_"):-len(".jsonl")]
    return name[:-6] if name.endswith(".jsonl") else name


def iter_session_files():
    pattern = os.path.join(MEM_DIR, "*", "*", "session_memory_*.jsonl")
    best = {}
    for path in glob.glob(pattern):
        sid = sid_from_path(path)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if sid not in best or mtime > best[sid][1]:
            date_name = os.path.basename(os.path.dirname(path))
            project_name = os.path.basename(os.path.dirname(os.path.dirname(path)))
            best[sid] = (path, mtime, project_name, date_name)
    return [(v[0], sid, v[2], v[3]) for sid, v in best.items()]


def load_records(path):
    records = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    records.append(obj)
    except OSError:
        pass
    return records


def clean_text(text, limit=None):
    if text is None:
        return ""
    s = str(text).replace("\r", " ").replace("\n", " ").strip()
    if limit and len(s) > limit:
        s = s[:limit] + "…"
    return s


def session_title(path):
    for rec in load_records(path):
        intent = clean_text(rec.get("intent"), 40)
        if intent:
            return intent
    return "(空会话)"


def memory_session_dict(path, sid, project, date_name):
    mtime = os.path.getmtime(path)
    return {
        "kind": "memory",
        "agent": "trae",
        "sid": sid,
        "title": session_title(path),
        "ws": clean_project_name(project),
        "mtime": mtime,
        "ts": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
        "size_bytes": os.path.getsize(path),
        "msg_count": 0,
        "path": path,
    }


# ---------- DB 完整对话 ----------

def ensure_db(quiet=True):
    if trae_db is None:
        return False
    try:
        trae_db.ensure_mirror(quiet=quiet)
        return True
    except Exception:
        return False


def db_sessions():
    if not ensure_db():
        return []
    try:
        return trae_db.list_sessions()
    except Exception:
        return []


def file_contains_keyword(path, kw):
    kw_lower = kw.lower()
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return kw_lower in f.read().lower()
    except OSError:
        return False


def db_contains_keyword(sid, kw):
    if trae_db is None:
        return False
    try:
        return trae_db.db_contains_keyword(sid, kw)
    except Exception:
        return False


def get_sessions():
    sessions = db_sessions()
    if sessions:
        return sessions
    return [memory_session_dict(*item) for item in
            [(p, sid, project, date) for p, sid, project, date in iter_session_files()]]


def resolve_target(arg):
    if trae_db is not None and ensure_db():
        try:
            found = trae_db.find_session(arg)
            if found:
                found.setdefault("kind", "db" if found.get("path", "").startswith(EXPORT_DIR) else "memory")
                return found
        except Exception:
            pass
    if os.path.isfile(arg):
        return {"kind": "memory", "path": arg, "sid": sid_from_path(arg)}
    matches = [(p, sid) for p, sid, _, _ in iter_session_files() if arg in sid]
    if not matches:
        return None
    sids = {sid for _, sid in matches}
    if len(sids) > 1:
        print(f"匹配到多个 Trae 会话（{len(sids)} 个），请提供更精确的前缀：")
        for p, sid in matches:
            print(f"  {sid}  {p}")
        sys.exit(1)
    path = max((p for p, _ in matches), key=os.path.getmtime)
    return {"kind": "memory", "path": path, "sid": sid_from_path(path)}


def fmt_ts(ts):
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def message_blob(msg):
    return json.dumps(msg, ensure_ascii=False).lower()


def print_db_message(msg, summary=False):
    role = "用户" if msg["role"] == "user" else "助手"
    print(f"\n--- #{msg['index']} [{role}] {fmt_ts(msg.get('timestamp'))} ---")
    text = (msg.get("text") or "").strip()
    details = msg.get("details") or []
    thoughts = msg.get("thoughts") or []
    if text:
        print(text)
    elif not details and not thoughts:
        print("（没有可读的回复内容）")
    if summary:
        return
    if thoughts:
        print("\n[思考]")
        for t in thoughts:
            t = t.strip()
            if t:
                print(t)
    if details:
        print("\n[工具/步骤]")
        for d in details:
            d = d.strip()
            if d:
                print(f"  {d}")


def show_db(session, summary=False, grep=None, msg_no=None, full=False):
    sid = session["sid"]
    messages = trae_db.load_messages(sid)
    if not messages:
        print(f"该 Trae 会话没有可读消息: {sid}")
        return 1
    print(f"=== Trae 会话: {session.get('title') or sid} ===")
    print(f"会话: {sid} | 项目: {session.get('ws') or '-'} | 消息: {len(messages)}")
    if session.get("project_path"):
        print(f"路径: {session['project_path']}")
    print(f"更新时间: {fmt_ts(session.get('mtime'))}")

    if msg_no:
        target = next((m for m in messages if m["index"] == msg_no), None)
        if target is None and 1 <= msg_no <= len(messages):
            target = messages[msg_no - 1]
        if target is None:
            print(f"\n未找到序号 {msg_no}（该会话消息序号: {[m['index'] for m in messages]}）")
            return 1
        print_db_message(target, summary=False)
        return 0

    kw = grep.lower() if grep else None
    shown = 0
    for msg in messages:
        if kw and kw not in message_blob(msg):
            continue
        shown += 1
        print_db_message(msg, summary=summary and not full)
    if shown == 0:
        print(f"\n没有命中关键词的消息: {grep}")
        return 1
    print(f"\n--- 共 {shown} 条消息 ---")
    return 0


def show_memory(path, summary=False, grep=None, msg_no=None):
    records = load_records(path)
    if not records:
        print(f"没有可读的会话记忆: {path}")
        return 1
    ws = clean_project_name(os.path.basename(os.path.dirname(os.path.dirname(path))))
    date_name = os.path.basename(os.path.dirname(path))
    sid = sid_from_path(path)
    print(f"=== Trae 会话记忆: {sid} ===")
    print(f"项目: {ws} | 日期: {date_name}")
    print(f"文件: {path}")
    print("说明: 完整对话解密不可用，此处展示 Trae 自动提炼的记忆摘要。")

    if msg_no:
        if msg_no < 1 or msg_no > len(records):
            print(f"未找到序号 {msg_no}（该会话共 {len(records)} 条记忆）")
            return 1
        print_memory_record(msg_no, records[msg_no - 1], summary=False)
        return 0

    kw = grep.lower() if grep else None
    shown = 0
    for i, rec in enumerate(records, 1):
        if kw and kw not in json.dumps(rec, ensure_ascii=False).lower():
            continue
        shown += 1
        print_memory_record(i, rec, summary)
    if shown == 0:
        print(f"\n没有命中关键词的记录: {grep}")
        return 1
    print(f"\n--- 共 {shown} 条记忆 ---")
    return 0


def print_memory_record(index, rec, summary=False):
    print(f"\n--- #{index} {clean_text(rec.get('message_summary_time'), 32)} ---")
    intent = clean_text(rec.get("intent"))
    outcome = clean_text(rec.get("outcome"))
    if intent:
        print(f"意图: {intent}")
    if summary:
        if outcome:
            print(f"结果: {outcome}")
        return
    actions = rec.get("actions") or []
    learned = rec.get("learned") or []
    if actions:
        print("动作:")
        for a in actions:
            text = clean_text(a)
            if text:
                print(f"  - {text}")
    if outcome:
        print(f"结果: {outcome}")
    if learned:
        print("经验:")
        for item in learned:
            text = clean_text(item)
            if text:
                print(f"  - {text}")


def main():
    p = argparse.ArgumentParser(description="Trae CN 会话查看器（优先完整对话，回退 memory 摘要）")
    p.add_argument("session", help="会话 ID（前缀即可）或记忆文件路径")
    p.add_argument("-s", "--summary", action="store_true", help="摘要模式：只显示用户与最终回复")
    p.add_argument("--full", action="store_true", help="完整模式：包含思考与工具调用")
    p.add_argument("-g", "--grep", metavar="关键词", help="只显示命中关键词的消息")
    p.add_argument("-m", "--msg", type=int, metavar="序号", help="只输出第 N 条消息全文")
    p.add_argument("-T", "--no-thinking", action="store_true", help="兼容参数，无效果")
    args = p.parse_args()

    target = resolve_target(args.session)
    if not target:
        print(f"未找到 Trae 会话: {args.session}")
        print(f"数据目录: {MEM_DIR}")
        sys.exit(1)
    if target.get("kind") == "memory":
        sys.exit(show_memory(target["path"], summary=args.summary, grep=args.grep, msg_no=args.msg))
    sys.exit(show_db(target, summary=args.summary, grep=args.grep, msg_no=args.msg, full=args.full))


if __name__ == "__main__":
    main()
