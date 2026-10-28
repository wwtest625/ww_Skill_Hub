#!/usr/bin/env python3
"""Trae CN 加密会话库的只读解密与导出。

- 用固定派生密钥打开 SQLCipher 库（密钥在 ~/.agent/trae/key.hex，0600）
- 导出明文镜像 ~/.agent/trae/trae.db（0600），供 Python 查看与 lane-fast 索引
- 导出会话 JSONL 到 ~/.agent/trae/projects/<project>/<sid>.jsonl，供 lane-fast 列表/检索
"""
import argparse
import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime

HOME = os.path.expanduser("~")
SRC_DIR = os.path.join(HOME, "Library/Application Support/Trae CN/ModularData/ai-agent")
SRC_DB = os.path.join(SRC_DIR, "database.db")
TRAE_DIR = os.path.join(HOME, ".agent/trae")
WORK_DIR = os.path.join(HOME, ".agent/trae-work")
MIRROR_DB = os.path.join(TRAE_DIR, "trae.db")
KEY_FILE = os.path.join(TRAE_DIR, "key.hex")
STATE_FILE = os.path.join(TRAE_DIR, "state.json")
LOCK_FILE = os.path.join(TRAE_DIR, ".sync.lock")
EXPORT_ROOT = os.path.join(TRAE_DIR, "projects")


class TraeDBError(Exception):
    pass


def read_key():
    try:
        key = open(KEY_FILE, "r", encoding="utf-8").read().strip()
    except OSError:
        raise TraeDBError(f"缺少密钥文件: {KEY_FILE}")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", key):
        raise TraeDBError(f"密钥格式错误: {KEY_FILE}")
    return key.lower()


def source_stamp():
    parts = []
    for suffix in ("", "-wal"):
        path = SRC_DB + suffix
        try:
            st = os.stat(path)
            parts.append([suffix, st.st_mtime_ns, st.st_size])
        except OSError:
            parts.append([suffix, 0, 0])
    return parts


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_FILE)


def export_path(project, sid):
    return os.path.join(EXPORT_ROOT, project, sid + ".jsonl")


def clean_project_name(project_name, abs_path=""):
    name = (project_name or "").strip()
    if not name and abs_path:
        name = os.path.basename(abs_path.rstrip("/"))
    if not name:
        name = "Assistant"
    name = name.replace("/", "-").replace(":", "-")
    name = re.split(r"--p2-", name, maxsplit=1)[0]
    name = name.lstrip("-") or "Assistant"
    return name


def safe_project_dir(project_name, abs_path, project_id, used):
    base = clean_project_name(project_name, abs_path)
    candidate = base
    if candidate in used and used[candidate] != project_id:
        candidate = f"{base}-{(project_id or 'project')[:8]}"
    used[candidate] = project_id
    return candidate


def _sqlcipher_bin():
    return shutil.which("sqlcipher") or "/Users/siri/homebrew/bin/sqlcipher"


def _copy_source_to_snapshot():
    os.makedirs(WORK_DIR, exist_ok=True)
    try:
        os.chmod(WORK_DIR, 0o700)
    except OSError:
        pass
    snap = os.path.join(WORK_DIR, "snapshot.db")
    for suffix in ("", "-wal", "-shm"):
        dst = snap + suffix
        try:
            os.remove(dst)
        except FileNotFoundError:
            pass
    shutil.copy2(SRC_DB, snap)
    for suffix in ("-wal", "-shm"):
        src = SRC_DB + suffix
        if os.path.exists(src):
            shutil.copy2(src, snap + suffix)
    return snap


def build_mirror(force=False):
    if not os.path.isfile(SRC_DB):
        raise TraeDBError(f"未找到 Trae 数据库: {SRC_DB}")
    key = read_key()
    os.makedirs(TRAE_DIR, exist_ok=True)
    os.makedirs(WORK_DIR, exist_ok=True)

    last_err = None
    for attempt in range(2):
        snap = _copy_source_to_snapshot()
        tmp = MIRROR_DB + ".tmp"
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass

        script = (
            f'PRAGMA key = "x\'{key}\'";\n'
            "PRAGMA cipher_page_size = 4096;\n"
            f"ATTACH DATABASE '{tmp}' AS plaintext KEY '';\n"
            "SELECT sqlcipher_export('plaintext');\n"
            "DETACH DATABASE plaintext;\n"
        )
        try:
            proc = subprocess.run(
                [_sqlcipher_bin(), "-batch", snap],
                input=script,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except Exception as e:
            last_err = str(e)
            time.sleep(0.3)
            continue

        if "Error" in (proc.stderr or "") or "Error" in (proc.stdout or ""):
            last_err = (proc.stderr or proc.stdout).strip()[:300]
            time.sleep(0.3)
            continue

        try:
            con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            try:
                ok = con.execute("PRAGMA integrity_check").fetchone()[0]
                if ok != "ok":
                    raise TraeDBError(f"明文镜像完整性检查失败: {ok}")
                count = con.execute("SELECT count(*) FROM chat_session").fetchone()[0]
            finally:
                con.close()
        except Exception as e:
            last_err = str(e)
            time.sleep(0.3)
            continue

        os.chmod(tmp, 0o600)
        os.replace(tmp, MIRROR_DB)
        return count

    raise TraeDBError(f"Trae 数据库解密失败: {last_err}")


def _connect():
    if not os.path.isfile(MIRROR_DB):
        raise TraeDBError("明文镜像不存在")
    con = sqlite3.connect(f"file:{MIRROR_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _json_loads(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def user_text(content):
    data = _json_loads(content or "")
    if isinstance(data, str):
        return data.strip()
    if not isinstance(data, list):
        return (content or "").strip()
    parts = []
    for block in data:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        text = block.get("text_content") or block.get("text") or block.get("content")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts).strip()


def _format_tool(name, params):
    if not isinstance(params, dict):
        return f"[工具] {name}"
    if name == "Shell":
        cmd = params.get("command") or params.get("cmd") or ""
        return f"[工具] Shell: {cmd}".strip()
    if name in ("Read", "Write", "Edit", "DeleteFile", "Grep"):
        target = params.get("file_path") or params.get("path") or params.get("pattern") or ""
        return f"[工具] {name}: {target}".strip()
    if name == "WebSearch":
        return f"[工具] WebSearch: {params.get('query', '')}".strip()
    if name == "TodoWrite":
        return "[工具] TodoWrite"
    if name == "Skill":
        return f"[工具] Skill: {params.get('name', '')}".strip()
    return f"[工具] {name}"


def assistant_parts(content):
    """返回 (final_text, details[], thoughts[])。"""
    data = _json_loads(content or "")
    if not isinstance(data, dict):
        return "", [], []
    finals = []
    details = []
    thoughts = []
    for item in data.get("messages") or []:
        plan = item.get("plan_item") or {}
        thought = (plan.get("thought") or "").strip()
        reasoning = (plan.get("reasoning_content") or "").strip()
        if thought:
            thoughts.append(thought)
        elif reasoning:
            thoughts.append(reasoning)
        tc = plan.get("tool_call_info") or {}
        name = tc.get("name") or ""
        params = tc.get("params") or {}
        if name in ("finish", "ReplyUser"):
            text = ""
            if isinstance(params, dict):
                text = (params.get("summary") or params.get("message") or "").strip()
            if text:
                finals.append(text)
        elif name:
            details.append(_format_tool(name, params))
    final = "\n\n".join(finals).strip()
    return final, details, thoughts


def load_messages(sid):
    con = _connect()
    try:
        rows = con.execute(
            """
            SELECT m.message_id, m.message_role, m.message_index, m.created_at,
                   g.content AS general_content, k.content AS task_content
            FROM chat_message m
            LEFT JOIN chat_message_general g ON g.message_id = m.message_id
            LEFT JOIN chat_message_task k ON k.message_id = m.message_id
            WHERE m.session_id = ? AND m.deleted_at = 0
            ORDER BY m.message_index ASC
            """,
            (sid,),
        ).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        role = r["message_role"]
        if role == "user":
            text = user_text(r["general_content"])
            out.append({
                "message_id": r["message_id"],
                "role": "user",
                "index": r["message_index"],
                "timestamp": r["created_at"],
                "text": text,
                "details": [],
                "thoughts": [],
            })
        else:
            final, details, thoughts = assistant_parts(r["task_content"])
            out.append({
                "message_id": r["message_id"],
                "role": "assistant",
                "index": r["message_index"],
                "timestamp": r["created_at"],
                "text": final,
                "details": details,
                "thoughts": thoughts,
            })
    return out


def _first_user_text(sid, con=None):
    own = con is None
    con = con or _connect()
    try:
        row = con.execute(
            """
            SELECT g.content FROM chat_message m
            LEFT JOIN chat_message_general g ON g.message_id = m.message_id
            WHERE m.session_id = ? AND m.message_role = 'user' AND m.deleted_at = 0
            ORDER BY m.message_index ASC LIMIT 1
            """,
            (sid,),
        ).fetchone()
        return user_text(row["content"]) if row else ""
    finally:
        if own:
            con.close()


def list_sessions():
    if not os.path.isfile(MIRROR_DB):
        return []
    con = _connect()
    try:
        meta_rows = {
            r["session_id"]: r
            for r in con.execute(
                """
                SELECT cs.session_id, cs.session_title, cs.created_at, cs.updated_at,
                       cs.session_type, cs.project_id, cs.hidden_status,
                       p.name AS project_name, p.absolute_path
                FROM chat_session cs
                LEFT JOIN project p ON p.project_id = cs.project_id
                WHERE cs.deleted_at = 0
                """
            ).fetchall()
        }
        projects = con.execute(
            "SELECT project_id, name, absolute_path FROM project"
        ).fetchall()
        msg_rows = con.execute(
            """
            SELECT session_id, count(*) n, min(created_at) mn, max(created_at) mx
            FROM chat_message WHERE deleted_at = 0
            GROUP BY session_id
            """
        ).fetchall()
        firsts = {}
        for r in con.execute(
            """
            SELECT m.session_id, g.content FROM chat_message m
            JOIN chat_message_general g ON g.message_id = m.message_id
            WHERE m.message_role='user' AND m.deleted_at=0
            ORDER BY m.session_id, m.message_index
            """
        ).fetchall():
            firsts.setdefault(r["session_id"], user_text(r["content"]))
    finally:
        con.close()

    def project_for(sid, project_id=""):
        if project_id:
            for p in projects:
                if p["project_id"] == project_id:
                    return p
        # 旧会话可能丢失 chat_session 行，但 project_id 与 session_id 前缀同源
        best = None
        for p in projects:
            pid = p["project_id"] or ""
            if len(pid) >= 18 and len(sid) >= 18 and pid[:18] == sid[:18]:
                best = p
                break
        return best

    used = {}
    out = []
    for r in msg_rows:
        sid = r["session_id"]
        meta = meta_rows.get(sid)
        if meta is not None:
            created = meta["created_at"] or 0
            updated = meta["updated_at"] or r["mx"] or created
            title = (meta["session_title"] or "").strip()
            session_type = meta["session_type"]
            proj = project_for(sid, meta["project_id"])
        else:
            created = r["mn"] or 0
            updated = r["mx"] or created
            title = ""
            session_type = "side_chat"
            proj = project_for(sid)
        title = title or firsts.get(sid, "") or "(空会话)"
        title = title.replace("\n", " ").strip()[:80]
        ws = safe_project_dir(
            (proj["name"] if proj else ""),
            (proj["absolute_path"] if proj else ""),
            (proj["project_id"] if proj else sid),
            used,
        )
        mtime = int(updated or created or 0)
        path = export_path(ws, sid)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        out.append({
            "agent": "trae",
            "sid": sid,
            "title": title,
            "ws": ws,
            "mtime": mtime,
            "ts": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "",
            "size_bytes": size,
            "msg_count": r["n"] or 0,
            "session_type": session_type,
            "path": path,
            "project_path": (proj["absolute_path"] if proj else "") or "",
        })
    out.sort(key=lambda s: s["mtime"], reverse=True)
    return out


def find_session(arg):
    if not arg:
        return None
    sessions = list_sessions()
    matches = [s for s in sessions if arg in s["sid"]]
    for s in matches:
        s["kind"] = "db"
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # 同前缀多命中时，优先精确 SID
        exact = [s for s in matches if s["sid"] == arg]
        return exact[0] if len(exact) == 1 else None
    # 兼容旧 memory 路径 / 导出 JSONL 路径
    if os.path.isfile(arg):
        base = os.path.basename(arg)
        if base.startswith("session_memory_"):
            return {"kind": "memory", "sid": base[len("session_memory_"):-6], "path": arg, "title": "", "ws": "", "mtime": 0}
        if base.endswith(".jsonl"):
            sid = base[:-6]
            for s in sessions:
                if s["sid"] == sid:
                    s["kind"] = "db"
                    return s
    return None


def db_contains_keyword(sid, kw):
    try:
        con = _connect()
    except TraeDBError:
        return False
    try:
        pat = f"%{kw.lower()}%"
        row = con.execute(
            """
            SELECT 1 FROM chat_message m
            LEFT JOIN chat_message_general g ON g.message_id = m.message_id
            LEFT JOIN chat_message_task k ON k.message_id = m.message_id
            WHERE m.session_id = ? AND (
                lower(coalesce(g.content,'')) LIKE ? OR lower(coalesce(k.content,'')) LIKE ?
            ) LIMIT 1
            """,
            (sid, pat, pat),
        ).fetchone()
        return row is not None
    finally:
        con.close()


def _normalized_lines(session):
    lines = [json.dumps({
        "type": "meta",
        "sid": session["sid"],
        "title": session["title"],
        "aiTitle": session["title"],
        "project": session["ws"],
        "updated_at": session["mtime"],
    }, ensure_ascii=False, separators=(",", ":"))]
    for msg in load_messages(session["sid"]):
        text = (msg["text"] or "").replace("\r", " ")
        if msg["role"] == "user":
            # 单行化，避免 lane-fast 标题解析被换行截断
            text = re.sub(r"\s*\n\s*", " ", text).strip()
        details = msg["details"] or []
        thoughts = msg["thoughts"] or []
        if not text and not details and not thoughts:
            continue
        lines.append(json.dumps({
            "type": msg["role"],
            "role": msg["role"],
            "sid": session["sid"],
            "message_id": msg["message_id"],
            "index": msg["index"],
            "timestamp": msg["timestamp"],
            "text": text,
            "details": details,
            "thoughts": thoughts,
        }, ensure_ascii=False, separators=(",", ":")))
    return lines


def export_jsonl():
    sessions = list_sessions()
    os.makedirs(EXPORT_ROOT, exist_ok=True)
    keep = set()
    for s in sessions:
        ws = s["ws"]
        path = export_path(ws, s["sid"])
        lines = _normalized_lines(s)
        if len(lines) <= 1:
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.chmod(os.path.dirname(path), 0o700)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.chmod(tmp, 0o600)
        ts = s["mtime"] or int(time.time())
        os.utime(tmp, (ts, ts))
        os.replace(tmp, path)
        keep.add(path)

    for root, dirs, files in os.walk(EXPORT_ROOT):
        for name in files:
            if not name.endswith(".jsonl"):
                continue
            p = os.path.join(root, name)
            if p not in keep:
                try:
                    os.remove(p)
                except OSError:
                    pass
        if not os.listdir(root) and root != EXPORT_ROOT:
            try:
                os.rmdir(root)
            except OSError:
                pass
    return len(keep)


def ensure_mirror(force=False, quiet=True):
    os.makedirs(TRAE_DIR, exist_ok=True)
    os.makedirs(EXPORT_ROOT, exist_ok=True)
    try:
        os.chmod(TRAE_DIR, 0o700)
        os.chmod(EXPORT_ROOT, 0o700)
    except OSError:
        pass

    lock = open(LOCK_FILE, "w")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False  # 另一个进程正在同步
        state = load_state()
        stamp = source_stamp()
        fresh = (
            not force
            and os.path.isfile(MIRROR_DB)
            and state.get("source") == stamp
            and state.get("mirror_size") == os.path.getsize(MIRROR_DB)
        )
        if not fresh:
            build_mirror(force=force)
            exported = export_jsonl()
            state = {
                "source": stamp,
                "mirror_size": os.path.getsize(MIRROR_DB),
                "exported": exported,
                "synced_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_state(state)
            if not quiet:
                print(f"[trae] 已解密并导出 {exported} 个会话")
            return True
        # 镜像没变但 JSONL 被删/未生成时补一次
        if not os.path.isdir(EXPORT_ROOT) or not any(
            f.endswith(".jsonl") for _, _, fs in os.walk(EXPORT_ROOT) for f in fs
        ):
            exported = export_jsonl()
            state["exported"] = exported
            state["synced_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_state(state)
            if not quiet:
                print(f"[trae] 已补导出 {exported} 个会话")
            return True
        return False
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()


def main():
    p = argparse.ArgumentParser(description="Trae CN 加密会话库同步/导出")
    p.add_argument("command", nargs="?", default="sync", choices=["sync", "status"])
    p.add_argument("--force", action="store_true")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    if args.command == "status":
        state = load_state()
        print(json.dumps({
            "mirror": MIRROR_DB,
            "mirror_exists": os.path.isfile(MIRROR_DB),
            "state": state,
            "key_file": KEY_FILE,
        }, ensure_ascii=False, indent=2))
        return

    try:
        changed = ensure_mirror(force=args.force, quiet=args.quiet)
        if not changed and not args.quiet:
            print("[trae] 已是最新")
    except Exception as e:
        print(f"[trae] 同步失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
