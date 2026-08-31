#!/usr/bin/env python3
"""Lane 会话管理内核 (agent-manager.py)

负责三方 Agent（agy / qoder / CodeBuddy）的会话元数据增强与全生命周期管理：
  1. 元数据持久化 (~/.agent/lane-meta.json)：自定义标题、标签(Tags)、置顶(Pin)、归档(Archive)、软删除(Trash)
  2. 物理与逻辑删除：支持安全放入回收站 (软删除) 与物理彻底销毁底层库文件 (.db / .jsonl)
  3. 一键清道夫：批量扫描并安全清理 0 轮次空会话与控制指令残留
"""
import os
import sys
import glob
import json
import sqlite3
import argparse
import importlib.util
from datetime import datetime

META_FILE = "/root/.agent/lane-meta.json"
AGY_CONV_DIR = "/root/.gemini/antigravity-cli/conversations"
AGY_SUMMARY_DB = "/root/.gemini/antigravity-cli/conversation_summaries.db"
QODER_PROJ_DIR = "/root/.qoder/projects"
CB_PROJ_DIR = "/root/.codebuddy/projects"


def load_meta():
    if not os.path.exists(META_FILE):
        return {}
    try:
        with open(META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_meta(meta):
    os.makedirs(os.path.dirname(META_FILE), exist_ok=True)
    temp_file = META_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, META_FILE)


def resolve_session(session_arg):
    """解析会话前缀，返回 (agent, sid, filepath)"""
    if not session_arg:
        return None, None, None
    sid_clean = session_arg.strip()

    agy_files = [p for p in glob.glob(os.path.join(AGY_CONV_DIR, "*.db")) if sid_clean in os.path.basename(p)]
    qoder_files = [p for p in glob.glob(os.path.join(QODER_PROJ_DIR, "*", "*.jsonl")) if sid_clean in os.path.basename(p)]
    cb_files = [p for p in glob.glob(os.path.join(CB_PROJ_DIR, "*", "*.jsonl")) if sid_clean in os.path.basename(p)]

    total = len(agy_files) + len(qoder_files) + len(cb_files)
    if total == 0:
        return None, None, None
    if total > 1:
        print(f"[-] 匹配到多个会话 ({total} 个)，请提供更详细的会话 ID 前缀。")
        sys.exit(1)

    if agy_files:
        p = agy_files[0]
        return "agy", os.path.basename(p)[:-3], p
    elif qoder_files:
        p = qoder_files[0]
        return "qoder", os.path.basename(p)[:36], p
    elif cb_files:
        p = cb_files[0]
        return "codebuddy", os.path.basename(p)[:-6], p

    return None, None, None


def rename_session(sid, new_title):
    agent, full_sid, _ = resolve_session(sid)
    if not full_sid:
        print(f"[-] 未找到会话: {sid}")
        return False
    meta = load_meta()
    entry = meta.setdefault(full_sid, {})
    entry["title"] = new_title.strip()
    entry["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_meta(meta)
    print(f"[+] [{agent}] 会话 {full_sid[:8]} 标题已更新为: \"{new_title.strip()}\"")
    return True


def tag_session(sid, add_tags=None, rm_tags=None):
    agent, full_sid, _ = resolve_session(sid)
    if not full_sid:
        print(f"[-] 未找到会话: {sid}")
        return False
    meta = load_meta()
    entry = meta.setdefault(full_sid, {})
    tags = set(entry.get("tags", []))
    if add_tags:
        for t in add_tags:
            tags.add(t.strip())
    if rm_tags:
        for t in rm_tags:
            tags.discard(t.strip())
    entry["tags"] = sorted(list(tags))
    entry["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_meta(meta)
    print(f"[+] [{agent}] 会话 {full_sid[:8]} 当前标签: {entry['tags']}")
    return True


def pin_session(sid, state=True):
    agent, full_sid, _ = resolve_session(sid)
    if not full_sid:
        print(f"[-] 未找到会话: {sid}")
        return False
    meta = load_meta()
    entry = meta.setdefault(full_sid, {})
    entry["pinned"] = bool(state)
    entry["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_meta(meta)
    status_str = "已置顶 🌟" if state else "已取消置顶"
    print(f"[+] [{agent}] 会话 {full_sid[:8]} {status_str}")
    return True


def archive_session(sid, state=True):
    agent, full_sid, _ = resolve_session(sid)
    if not full_sid:
        print(f"[-] 未找到会话: {sid}")
        return False
    meta = load_meta()
    entry = meta.setdefault(full_sid, {})
    entry["archived"] = bool(state)
    entry["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_meta(meta)
    status_str = "已归档 📦" if state else "已移出归档"
    print(f"[+] [{agent}] 会话 {full_sid[:8]} {status_str}")
    return True


def delete_session(sid, force=False):
    agent, full_sid, path = resolve_session(sid)
    if not full_sid:
        print(f"[-] 未找到会话: {sid}")
        return False

    meta = load_meta()

    if not force:
        # 软删除
        entry = meta.setdefault(full_sid, {})
        entry["deleted"] = True
        entry["deleted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_meta(meta)
        print(f"[+] [{agent}] 会话 {full_sid[:8]} 已移入回收站 🗑️（使用 lane restore {full_sid[:8]} 可还原，加 -f 彻底销毁）")
        return True

    # 物理彻底删除
    deleted_items = []
    if agent == "agy":
        # 删除 conversation db
        if path and os.path.exists(path):
            os.remove(path)
            deleted_items.append(path)
        # 从 summaries db 中移除
        if os.path.exists(AGY_SUMMARY_DB):
            try:
                conn = sqlite3.connect(AGY_SUMMARY_DB)
                conn.execute("DELETE FROM conversation_summaries WHERE conversation_id=?", (full_sid,))
                conn.commit()
                conn.close()
            except Exception:
                pass
    elif agent == "qoder":
        # 删除 jsonl 文件与可能同名的目录
        matches = glob.glob(os.path.join(QODER_PROJ_DIR, "*", f"{full_sid}*"))
        for m in matches:
            if os.path.isfile(m):
                os.remove(m)
                deleted_items.append(m)
            elif os.path.isdir(m):
                import shutil
                shutil.rmtree(m, ignore_errors=True)
                deleted_items.append(m)
    elif agent == "codebuddy":
        matches = glob.glob(os.path.join(CB_PROJ_DIR, "*", f"{full_sid}*"))
        for m in matches:
            if os.path.isfile(m):
                os.remove(m)
                deleted_items.append(m)

    if full_sid in meta:
        del meta[full_sid]
        save_meta(meta)

    print(f"[+] [{agent}] 会话 {full_sid[:8]} 已彻底销毁（已清理底层文件: {len(deleted_items)} 个）")
    return True


def restore_session(sid):
    agent, full_sid, _ = resolve_session(sid)
    if not full_sid:
        print(f"[-] 未找到会话: {sid}")
        return False
    meta = load_meta()
    if full_sid in meta and meta[full_sid].get("deleted"):
        meta[full_sid]["deleted"] = False
        meta[full_sid].pop("deleted_at", None)
        save_meta(meta)
        print(f"[+] [{agent}] 会话 {full_sid[:8]} 已从回收站成功还原！")
        return True
    print(f"[-] 会话 {full_sid[:8]} 未处于回收站中。")
    return False


def clean_empty_sessions(dry_run=False, force=False):
    """扫描并清理所有 0 步空会话及 (/clear), (/resume) 等控制指令残留"""
    # 动态加载查看器提取标题与元数据
    spec = importlib.util.spec_from_file_location("log_viewer", "/root/agent-log-viewer.py")
    lv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lv)

    agy_mod = lv.load_module_from_file("agy_mod", "/root/agy-log-viewer.py")
    qoder_mod = lv.load_module_from_file("qoder_mod", "/root/qoder-log-viewer.py")
    cb_mod = lv.load_module_from_file("cb_mod", "/root/codebuddy-log-viewer.py")

    all_sessions = []
    all_sessions.extend(lv.get_agy_sessions(agy_mod, show_all=True))
    all_sessions.extend(lv.get_qoder_sessions(qoder_mod, show_all=True))
    all_sessions.extend(lv.get_cb_sessions(cb_mod, show_all=True))

    meta = load_meta()
    noisy_list = []
    for s in all_sessions:
        sid = s["sid"]
        title = s["title"]
        m = meta.get(sid, {})
        if m.get("pinned"):  # 置顶的手工保留
            continue
        if title == "(空会话)" or (title.startswith("(/") and title.endswith(")")):
            noisy_list.append(s)

    if not noisy_list:
        print("[*] 太棒了！当前没有任何无用或空的残留会话。")
        return 0

    mode_str = "[预览模式]" if dry_run else ("[彻底销毁模式]" if force else "[回收站软删除模式]")
    print(f"[*] 发现 {len(noisy_list)} 个无用/空会话 {mode_str}:")
    print("-" * 90)
    for s in noisy_list:
        print(f"  - [{s['agent']:<9}] {s['ws']:<18} {s['title']:<12} ID: {s['sid']}")
    print("-" * 90)

    if dry_run:
        print(f"[*] 预览完毕，共 {len(noisy_list)} 条记录待清理。加上 --empty 即可执行真实清理。")
        return len(noisy_list)

    cleaned_count = 0
    for s in noisy_list:
        delete_session(s["sid"], force=force)
        cleaned_count += 1

    print(f"[+] 清理完成！共处理 {cleaned_count} 个垃圾会话。")
    return cleaned_count


def main():
    p = argparse.ArgumentParser(description="Lane 会话管理内核工具")
    sub = p.add_subparsers(dest="cmd", help="子命令")

    p_rename = sub.add_parser("rename", help="重命名会话标题")
    p_rename.add_argument("session", help="会话 ID（前缀即可）")
    p_rename.add_argument("title", help="新的会话标题")

    p_tag = sub.add_parser("tag", help="打标签/归类")
    p_tag.add_argument("session", help="会话 ID")
    p_tag.add_argument("action", choices=["add", "rm", "list"], help="操作类型")
    p_tag.add_argument("tags", nargs="*", help="标签名称")

    p_pin = sub.add_parser("pin", help="置顶会话")
    p_pin.add_argument("session", help="会话 ID")
    p_pin.add_argument("--off", action="store_true", help="取消置顶")

    p_arc = sub.add_parser("archive", help="归档会话")
    p_arc.add_argument("session", help="会话 ID")
    p_arc.add_argument("--off", action="store_true", help="取消归档")

    p_rm = sub.add_parser("rm", help="删除会话 (默认移入回收站)")
    p_rm.add_argument("session", help="会话 ID")
    p_rm.add_argument("-f", "--force", action="store_true", help="彻底物理删除底层文件")

    p_res = sub.add_parser("restore", help="从回收站还原会话")
    p_res.add_argument("session", help="会话 ID")

    p_clean = sub.add_parser("clean", help="批量清道夫")
    p_clean.add_argument("--empty", action="store_true", help="扫描并清理所有空会话与控制指令残留")
    p_clean.add_argument("--dry-run", action="store_true", help="仅预览待清理清单，不实际删除")
    p_clean.add_argument("-f", "--force", action="store_true", help="彻底物理删除，而非软删除")

    args = p.parse_args()

    if args.cmd == "rename":
        rename_session(args.session, args.title)
    elif args.cmd == "tag":
        if args.action == "add":
            tag_session(args.session, add_tags=args.tags)
        elif args.action == "rm":
            tag_session(args.session, rm_tags=args.tags)
        elif args.action == "list":
            _, full_sid, _ = resolve_session(args.session)
            meta = load_meta()
            print(meta.get(full_sid, {}).get("tags", []))
    elif args.cmd == "pin":
        pin_session(args.session, state=not args.off)
    elif args.cmd == "archive":
        archive_session(args.session, state=not args.off)
    elif args.cmd == "rm":
        delete_session(args.session, force=args.force)
    elif args.cmd == "restore":
        restore_session(args.session)
    elif args.cmd == "clean":
        if args.empty:
            clean_empty_sessions(dry_run=args.dry_run, force=args.force)
        else:
            p.print_help()
    else:
        p.print_help()


if __name__ == "__main__":
    main()
