#!/usr/bin/env python3
"""agy (Antigravity CLI) 会话记录查看器。

数据位置: /root/.gemini/antigravity-cli/
  - conversations/<id>.db  每个会话一个 SQLite 库（steps 表，protobuf 存于 step_payload BLOB）
  - conversation_summaries.db  会话预览/时间索引

用法:
  python3 agy-log-viewer.py                  # 列出所有会话
  python3 agy-log-viewer.py -g 关键词        # 全局搜索包含关键词的会话列表
  python3 agy-log-viewer.py <会话ID或路径>    # 完整对话流
  python3 agy-log-viewer.py <会话> -s        # 摘要模式
  python3 agy-log-viewer.py <会话> -g 关键词  # 过滤模式（只显示匹配轮次）
  python3 agy-log-viewer.py <会话> -T        # 跳过思考流（英文段），只看中文回复
"""
import json
import sys
import glob
import os
import re
import signal
import argparse
import sqlite3
from datetime import datetime

signal.signal(signal.SIGPIPE, signal.SIG_DFL)

DATA_DIR = "/root/.gemini/antigravity-cli"
CONV_DIR = os.path.join(DATA_DIR, "conversations")
SUMMARY_DB = os.path.join(DATA_DIR, "conversation_summaries.db")


def load_summaries():
    """conversation_id -> {preview, last_modified_time, step_count, workspace}"""
    out = {}
    if not os.path.isfile(SUMMARY_DB):
        return out
    conn = sqlite3.connect(f"file:{SUMMARY_DB}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT conversation_id, preview, last_modified_time, step_count, workspace_uris "
                    "FROM conversation_summaries")
        for cid, preview, mtime, steps, ws in cur.fetchall():
            out[cid] = {"preview": preview or "", "mtime": mtime, "steps": steps or 0,
                        "workspace": ws or ""}
    finally:
        conn.close()
    return out


def count_steps(db_path):
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return "?"


def db_contains_keyword(db_path, kw):
    """快速检查 SQLite 数据库中是否包含指定关键词。"""
    try:
        kw_bytes = kw.encode("utf-8")
        with open(db_path, "rb") as f:
            content = f.read()
            return kw_bytes.lower() in content.lower()
    except Exception:
        return False


def list_sessions(grep=None):
    files = sorted(glob.glob(os.path.join(CONV_DIR, "*.db")),
                   reverse=True, key=os.path.getmtime)
    if not files:
        print("未找到任何 agy 会话记录")
        return
    sums = load_summaries()
    
    matches = []
    for p in files:
        sid = os.path.basename(p)[:-3]
        ts = datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")
        steps = sums.get(sid, {}).get("steps")
        if steps is None:
            steps = count_steps(p)
        preview = sums.get(sid, {}).get("preview", "")
        
        if grep:
            kw = grep.lower()
            if kw not in preview.lower() and kw not in sid.lower() and not db_contains_keyword(p, grep):
                continue
        matches.append((ts, steps, preview, sid))
    
    if not matches:
        if grep:
            print(f"未找到包含关键词 '{grep}' 的 agy 会话记录")
        else:
            print("未找到任何 agy 会话记录")
        return

    print(f"{'修改时间':<17} {'步数':<6} {'主题':<42} 会话ID")
    print("-" * 120)
    for ts, steps, preview, sid in matches:
        print(f"{ts:<17} {str(steps):<6} {preview[:42]:<42} {sid}")


# ---------- protobuf 字符串提取与清洗 ----------

def _clean_proto_str(s):
    """清理 protobuf 前缀 tag 字节残留。"""
    s = s.strip().rstrip('"').strip()
    if not s:
        return ""
    # 单字节 ASCII tag 紧跟中文字符（如 'Q你知道' -> '你知道'）
    if len(s) >= 2 and ord(s[0]) < 128 and ('\u4e00' <= s[1] <= '\u9fff'):
        s = s[1:].strip()
    # 单字节 ASCII tag 紧跟 JSON 或列表前缀（如 'z{"AbsolutePath"...' -> '{"AbsolutePath"...'）
    if len(s) >= 2 and ord(s[0]) < 128 and s[1] in ('{', '['):
        s = s[1:].strip()
    return s


def extract_strings(b, minlen=4):
    """从 BLOB 中切出连续可读字节段（UTF-8）。"""
    out, cur_s = [], bytearray()
    for byte in b:
        if 32 <= byte < 127 or byte >= 0x80:
            cur_s.append(byte)
        else:
            if len(cur_s) >= minlen:
                out.append(cur_s.decode("utf-8", errors="replace"))
            cur_s = bytearray()
    if len(cur_s) >= minlen:
        out.append(cur_s.decode("utf-8", errors="replace"))
    return out


def _is_noise(s):
    s = s.strip()
    if not s or "\ufffd" in s:
        return True
    cn = len(re.findall(r"[\u4e00-\u9fff]", s))
    if len(s) < 8 and cn == 0:  # 短英文串多为标识符；中文短句保留
        return True
    if s.startswith(("{", "(", ":", "*", "b$", "$", '"$')):
        return True
    if re.match(r"^(call_\d+|sessionID|-3750\d+)$", s):
        return True
    if re.search(r"\bbot-[0-9a-f-]{10,}", s):
        return True
    if re.match(r"^[0-9a-zA-Z_\-]{16,}$", s):  # 随机 base64/串
        return True
    if re.match(r"^[\W_]+$", s):  # 纯符号
        return True
    return False


def is_meaningful(s):
    """严格判定：真正的自然语言（中文>=2 字或英文单词>=4 个）。"""
    s = s.strip()
    if _is_noise(s):
        return False
    # 工具参数 JSON 丢弃
    if s.startswith("{") and ("toolAction" in s or "AbsolutePath" in s or "TargetFile" in s or "CommandLine" in s):
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", s))
    if cn >= 2:
        return True
    words = re.findall(r"[A-Za-z]{2,}", s)
    return len(words) >= 4


def extract_texts(b, strict=False, no_thinking=False):
    """提取文本段（相邻重复段去重）。"""
    out = []
    seen = set()
    in_listing = False
    for raw in extract_strings(b):
        s = _clean_proto_str(raw)
        if not s or _is_noise(s):
            continue
        if not strict and s.startswith("#"):  # 系统标记
            continue
        if strict and not is_meaningful(s):
            continue
        if strict and no_thinking and not re.search(r"[\u4e00-\u9fff]", s):
            continue  # 英文段视为思考流，跳过
        if s in seen:
            continue
        if "(*)" in s:
            in_listing = True
            continue
        if in_listing and len(s) < 30 and " " not in s and not re.search(r"[\u4e00-\u9fff]", s):
            continue
        seen.add(s)
        out.append(s)
    return out


def extract_tool_call(b):
    """从 type132 BLOB 提取工具调用信息 -> (call_id, tool_name, command, action)"""
    strs = [_clean_proto_str(s) for s in extract_strings(b, minlen=3)]
    call_id = next((s for s in strs if re.match(r"^call_\d+$", s)), "")
    tool = ""
    try:
        i = strs.index(call_id) if call_id else -1
        for s in strs[i + 1:]:
            if re.match(r"^[a-z_]{3,40}$", s):
                tool = s
                break
    except ValueError:
        pass
    command = action = ""
    for s in strs:
        m = re.search(r'"CommandLine":"((?:[^"\\]|\\.)*)"', s)
        if m:
            command = m.group(1).replace("\\u0026", "&").replace("\\\"", '"')
        a = re.search(r'"toolAction":"((?:[^"\\]|\\.)*)"', s)
        if a:
            action = a.group(1)
        if command and action:
            break
    return call_id, tool, command, action


def load_steps(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT idx, step_type, status, step_payload FROM steps ORDER BY idx")
        return cur.fetchall()
    finally:
        conn.close()


def build_events(db_path, no_thinking=False):
    """把 steps 解析为事件列表，并做用户消息与模型回复去重。"""
    events = []
    for idx, st, status, payload in load_steps(db_path):
        if st == 14:  # 用户消息
            texts = extract_texts(payload)
            if texts:
                full_text = "\n".join(texts)
                # 与前一条 user 消息去重
                if events and events[-1][0] == "user":
                    prev_text = events[-1][1]
                    if full_text == prev_text or full_text in prev_text:
                        continue
                    if prev_text in full_text:
                        events[-1] = ("user", full_text)
                        continue
                events.append(("user", full_text))
        elif st == 132:  # 工具调用
            call_id, tool, command, action = extract_tool_call(payload)
            if call_id or tool:
                events.append(("tool", (call_id, tool, command, action)))
        elif st == 15:  # 模型回复
            texts = extract_texts(payload, strict=True, no_thinking=no_thinking)
            if texts:
                full_text = "\n".join(texts)
                if events and events[-1][0] == "assistant" and events[-1][1] == full_text:
                    continue
                events.append(("assistant", full_text))
    return events


def fmt_command(cmd):
    if len(cmd) > 150:
        return cmd[:150] + "…"
    return cmd


def show_session(db_path, summary=False, grep=None, no_thinking=False):
    sid = os.path.basename(db_path)[:-3]
    print(f"═══════ agy 会话 {sid} ═══════")
    events = build_events(db_path, no_thinking=no_thinking)

    turns = []
    for ev in events:
        if ev[0] == "user":
            turns.append([ev])
        elif turns:
            turns[-1].append(ev)
        else:
            turns.append([ev])

    for turn in turns:
        if grep:
            hit = any(grep.lower() in (x[1] if isinstance(x[1], str) else
                                       " ".join(str(y) for y in x[1])).lower() for x in turn)
            if not hit:
                continue
        for kind, val in turn:
            if kind == "user":
                print(f"\n[用户]")
                lines = val.splitlines()
                limit = 5 if (summary or grep) else 40
                for line in lines[:limit]:
                    print(f"  {line}")
                if len(lines) > limit:
                    print(f"  … 还有 {len(lines) - limit} 行")
            elif kind == "assistant":
                print(f"\n[助手]")
                lines = val.splitlines()
                limit = 60 if (summary or grep) else 100000
                for line in lines[:limit]:
                    print(f"  {line}")
                if len(lines) > limit:
                    print(f"  … 还有 {len(lines) - limit} 行")
            elif kind == "tool":
                call_id, tool, command, action = val
                detail = action or fmt_command(command) or tool
                print(f"    → 调用 {tool} ({call_id}): {detail}")


def main():
    p = argparse.ArgumentParser(description="agy 会话记录查看器")
    p.add_argument("session", nargs="?", help="会话 ID（前缀即可）或 .db 文件路径")
    p.add_argument("-s", "--summary", action="store_true", help="摘要模式")
    p.add_argument("-g", "--grep", metavar="关键词", help="全局搜索包含关键词的会话（无 session 时），或过滤单会话对话轮")
    p.add_argument("-T", "--no-thinking", action="store_true",
                   help="跳过模型思考流（英文段），只保留中文回复")
    args = p.parse_args()

    if not args.session:
        list_sessions(grep=args.grep)
        return

    arg = args.session
    if os.path.isfile(arg):
        path = arg
    else:
        matches = [p for p in glob.glob(os.path.join(CONV_DIR, "*.db"))
                   if arg in os.path.basename(p)]
        if len(matches) == 1:
            path = matches[0]
        else:
            print(f"找不到会话: {arg}（匹配到 {len(matches)} 个）\n可先不带参数运行，列出所有会话 ID")
            sys.exit(1)
    show_session(path, summary=args.summary, grep=args.grep, no_thinking=args.no_thinking)


if __name__ == "__main__":
    main()
