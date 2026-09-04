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
import subprocess
import urllib.request
import importlib.util
from datetime import datetime

META_FILE = "/root/.agent/lane-meta.json"
AGY_CONV_DIR = "/root/.gemini/antigravity-cli/conversations"
AGY_SUMMARY_DB = "/root/.gemini/antigravity-cli/conversation_summaries.db"
QODER_PROJ_DIR = "/root/.qoder/projects"
CB_PROJ_DIR = "/root/.codebuddy/projects"
CLINE_DATA_DIR = "/root/.cline/data"
LOCAL_VLLM_URL = "http://127.0.0.1:18080/v1/chat/completions"


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
    cline_files = [p for p in glob.glob(os.path.join(CLINE_DATA_DIR, "sessions", "*", "*.messages.json")) if sid_clean in os.path.basename(os.path.dirname(p))]

    total = len(agy_files) + len(qoder_files) + len(cb_files) + len(cline_files)
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
    elif cline_files:
        p = cline_files[0]
        sid = os.path.basename(os.path.dirname(p))
        return "cline", sid, p

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


def call_llm_title_generation(snippet_text):
    """调用本地模型提取简明标题（遵循 Conventional Commit 规范），优先走本地 vLLM，失败时回退到 codebuddy -p"""
    prompt = (
        "你是一个会话标题精炼助手。请根据提供的会话内容片段，将其精炼为一个类似 Conventional Git Commit 规范的会话标题。\n\n"
        "格式严格遵循：\n"
        "<type>(<scope>): <简明中文任务描述>\n\n"
        "规范要求：\n"
        "1. <type> 必须是以下之一：\n"
        "   - feat: 新增功能、新需求开发、搭建系统\n"
        "   - fix: 修复 Bug、错误纠正\n"
        "   - debug: 排障诊断、问题定位、分析排查\n"
        "   - perf: 性能压测、推理优化、显存监控\n"
        "   - test: 功能测试、验证跑通、代码评测\n"
        "   - refactor: 代码重构、结构调整\n"
        "   - docs: 文档梳理、方案设计、新手指南\n"
        "   - chore: 环境配置、依赖安装、日常清理、进程操作\n"
        "2. <scope> 代表所属项目、组件或技术栈（全小写英文或技术简称，如 herdr, redfish, vllm, agy, gpu, panel, demo 等）\n"
        "3. 描述必须是简明清晰的中文，字数控制在 6 到 14 个汉字之间\n"
        "4. 严禁包含引号、思考过程、前缀说明或多余解释，只输出单行标题！\n\n"
        f"会话内容片段：\n{snippet_text[:800]}\n"
    )

    # 1. 优先尝试本地 vLLM (Qwen3.8-27B)
    try:
        data = {
            "model": "Qwen3.8-27B",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 40
        }
        req = urllib.request.Request(
            LOCAL_VLLM_URL,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer local"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            raw = res["choices"][0]["message"]["content"].strip()
            title = clean_generated_title(raw)
            if title:
                return title
    except Exception:
        pass

    # 2. 兜底尝试 codebuddy -p (glm-5.3-flash)
    try:
        proc = subprocess.run(
            ["timeout", "15", "/root/.local/bin/codebuddy", "-p", prompt],
            capture_output=True, text=True, timeout=16
        )
        if proc.returncode == 0 and proc.stdout.strip():
            raw = proc.stdout.strip().splitlines()[-1]
            title = clean_generated_title(raw)
            if title:
                return title
    except Exception:
        pass

    return None


def clean_generated_title(raw):
    if not raw:
        return ""
    import re
    t = raw.strip().strip('"\'`“”‘’')
    t = re.sub(r"^(标题|推荐标题|会话标题|新标题)[:：]\s*", "", t)
    t = t.splitlines()[0].strip()
    return t[:48]


def get_session_snippet(agent, sid, path):
    """提取会话前部关键摘要用于生成标题"""
    cmd = ["python3"]
    if agent == "agy":
        cmd.extend(["/root/agy-log-viewer.py", path, "-s", "-T"])
    elif agent == "qoder":
        cmd.extend(["/root/qoder-log-viewer.py", path, "-s"])
    elif agent == "codebuddy":
        cmd.extend(["/root/codebuddy-log-viewer.py", path, "-s"])
    elif agent == "cline":
        cmd.extend(["/root/cline-log-viewer.py", path, "-s", "-T"])
    else:
        return ""

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        text = proc.stdout or proc.stderr or ""
        return text[:1000].strip()
    except Exception:
        return ""


def retitle_session(sid, dry_run=False):
    agent, full_sid, path = resolve_session(sid)
    if not full_sid:
        print(f"[-] 未找到会话: {sid}")
        return False, "", ""
    
    meta = load_meta()
    old_title = meta.get(full_sid, {}).get("title", "")
    
    snippet = get_session_snippet(agent, full_sid, path)
    if not snippet:
        print(f"[-] 无法读取会话 {full_sid[:8]} 的内容摘要。")
        return False, old_title, ""
    
    new_title = call_llm_title_generation(snippet)
    if not new_title:
        print(f"[-] AI 标题生成失败（请确认 vLLM 或 CodeBuddy 可用）。")
        return False, old_title, ""

    if dry_run:
        print(f"[*] [预览模式] [{agent}] {full_sid[:8]}: \"{old_title}\" ➔ \"{new_title}\"")
        return True, old_title, new_title

    entry = meta.setdefault(full_sid, {})
    if not entry.get("original_title"):
        entry["original_title"] = old_title
    entry["title"] = new_title
    entry["ai_titled"] = True
    entry["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_meta(meta)
    print(f"[+] [{agent}] 会话 {full_sid[:8]} 已智能重命名:")
    print(f"    原标题: \"{old_title}\"")
    print(f"    新标题: \"{new_title}\"")
    return True, old_title, new_title


def batch_retitle_sessions(agent_filter="all", limit=20, dry_run=False, force=False):
    """批量对含糊/未命名的会话进行 AI 智能重命名"""
    spec = importlib.util.spec_from_file_location("log_viewer", "/root/agent-log-viewer.py")
    lv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lv)

    meta = load_meta()
    all_sessions = []
    if agent_filter in ("all", "agy"):
        mod = lv.load_module_from_file("agy_mod", "/root/agy-log-viewer.py")
        all_sessions.extend(lv.get_agy_sessions(mod))
    if agent_filter in ("all", "qoder"):
        mod = lv.load_module_from_file("qoder_mod", "/root/qoder-log-viewer.py")
        all_sessions.extend(lv.get_qoder_sessions(mod))
    if agent_filter in ("all", "codebuddy"):
        mod = lv.load_module_from_file("cb_mod", "/root/codebuddy-log-viewer.py")
        all_sessions.extend(lv.get_cb_sessions(mod))
    if agent_filter in ("all", "cline"):
        mod = lv.load_module_from_file("cline_mod", "/root/cline-log-viewer.py")
        all_sessions.extend(lv.get_cline_sessions(mod))

    candidates = []
    for s in all_sessions:
        sid = s["sid"]
        m = meta.get(sid, {})
        if m.get("deleted") or m.get("pinned"):
            continue
        if lv.is_noisy_session(s.get("title", "")):
            continue
        # 如果已经人工/AI改过名且不是强制重新生成，则跳过
        if not force and (m.get("ai_titled") or m.get("title")):
            continue
        candidates.append(s)

    if not candidates:
        print(f"[*] 太棒了！未发现需要重新命名的会话（共检查 {len(all_sessions)} 个）。")
        return 0

    to_process = candidates[:limit]
    print(f"[*] 准备对 {len(to_process)} 个会话进行 AI 智能重命名 (总候选: {len(candidates)}):")
    print("-" * 80)
    count = 0
    for s in to_process:
        ok, old_t, new_t = retitle_session(s["sid"], dry_run=dry_run)
        if ok:
            count += 1
    print("-" * 80)
    print(f"[+] 批量处理完成！成功重命名 {count} 个会话。")
    return count


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
    elif agent == "cline":
        session_dir = os.path.join(CLINE_DATA_DIR, "sessions", full_sid)
        if os.path.isdir(session_dir):
            import shutil
            shutil.rmtree(session_dir, ignore_errors=True)
            deleted_items.append(session_dir)
        db_path = os.path.join(CLINE_DATA_DIR, "db", "sessions.db")
        if os.path.isfile(db_path):
            try:
                conn = sqlite3.connect(db_path)
                conn.execute("DELETE FROM sessions WHERE session_id=?", (full_sid,))
                conn.commit()
                conn.close()
                deleted_items.append("sessions.db")
            except Exception:
                pass

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


def clean_empty_sessions(dry_run=False, force=False, max_kb=20):
    """扫描并清理所有 0 步空会话、(/clear) 控制指令残留，以及体积小于 max_kb (默认 20KB) 的轻量会话"""
    # 动态加载查看器提取标题与元数据
    spec = importlib.util.spec_from_file_location("log_viewer", "/root/agent-log-viewer.py")
    lv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lv)

    agy_mod = lv.load_module_from_file("agy_mod", "/root/agy-log-viewer.py")
    qoder_mod = lv.load_module_from_file("qoder_mod", "/root/qoder-log-viewer.py")
    cb_mod = lv.load_module_from_file("cb_mod", "/root/codebuddy-log-viewer.py")
    cline_mod = lv.load_module_from_file("cline_mod", "/root/cline-log-viewer.py")

    all_sessions = []
    all_sessions.extend(lv.get_agy_sessions(agy_mod, show_all=True))
    all_sessions.extend(lv.get_qoder_sessions(qoder_mod, show_all=True))
    all_sessions.extend(lv.get_cb_sessions(cb_mod, show_all=True))
    all_sessions.extend(lv.get_cline_sessions(cline_mod, show_all=True))

    meta = load_meta()
    noisy_list = []
    max_bytes = max_kb * 1024
    for s in all_sessions:
        sid = s["sid"]
        title = s["title"]
        m = meta.get(sid, {})
        if m.get("pinned"):  # 置顶的手工保留
            continue
        size_bytes = s.get("size_bytes", 0)
        is_empty_or_cmd = title == "(空会话)" or (title.startswith("(/") and title.endswith(")"))
        is_small = size_bytes < max_bytes

        if is_empty_or_cmd or is_small:
            reason = f"体积<{max_kb}KB({s.get('size_str')})" if is_small else "空会话/指令"
            s["clean_reason"] = reason
            noisy_list.append(s)

    if not noisy_list:
        print(f"[*] 太棒了！当前没有任何无用、空会话或小于 {max_kb}KB 的残留会话。")
        return 0

    mode_str = "[预览模式]" if dry_run else ("[彻底销毁模式]" if force else "[回收站软删除模式]")
    print(f"[*] 发现 {len(noisy_list)} 个无用/空/小于{max_kb}KB 会话 {mode_str}:")
    print("-" * 110)
    for s in noisy_list:
        print(f"  - [{s['agent']:<9}] {s['ws']:<18} {s.get('size_str','-'):<8} {s['title'][:22]:<22} ({s.get('clean_reason')}) ID: {s['sid']}")
    print("-" * 110)

    if dry_run:
        print(f"[*] 预览完毕，共 {len(noisy_list)} 条记录待清理。加上 --empty 即可执行真实清理。")
        return len(noisy_list)

    cleaned_count = 0
    for s in noisy_list:
        delete_session(s["sid"], force=force)
        cleaned_count += 1

    print(f"[+] 清理完成！共处理 {cleaned_count} 个垃圾/微型会话。")
    return cleaned_count


def clean_trash_sessions(dry_run=False):
    """一键清空回收站：彻底物理销毁所有处于 deleted 状态的会话及其底层文件"""
    meta = load_meta()
    trash_sids = [sid for sid, data in meta.items() if data.get("deleted")]
    if not trash_sids:
        print("[*] 回收站为空，没有需要清理的会话。")
        return 0
    print(f"[*] 回收站中共有 {len(trash_sids)} 个会话待彻底销毁:")
    for sid in trash_sids:
        print(f"  - ID: {sid} | 标题: {meta[sid].get('title', '-')}")
    if dry_run:
        print("[*] 预览模式，未执行物理删除。去掉 --dry-run 即可彻底粉碎。")
        return len(trash_sids)
    cleaned = 0
    for sid in trash_sids:
        delete_session(sid, force=True)
        cleaned += 1
    print(f"[+] 回收站已彻底清空！共物理销毁 {cleaned} 个会话。")
    return cleaned


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
    p_clean.add_argument("--empty", action="store_true", help="扫描并清理所有空会话、控制指令残留及微型会话")
    p_clean.add_argument("--trash", action="store_true", help="一键清空回收站（彻底物理销毁所有软删除会话）")
    p_clean.add_argument("--max-kb", type=int, default=20, help="清理体积小于指定 KB 的会话 (默认 20KB)")
    p_clean.add_argument("--dry-run", action="store_true", help="仅预览待清理清单，不实际删除")
    p_clean.add_argument("-f", "--force", action="store_true", help="彻底物理删除，而非软删除")

    p_title = sub.add_parser("title", help="AI 智能生成高质量规范会话标题")
    p_title.add_argument("session", nargs="?", help="目标会话 ID（单个）")
    p_title.add_argument("--all", action="store_true", help="批量处理所有未命名的会话")
    p_title.add_argument("-a", "--agent", choices=["all", "agy", "qoder", "codebuddy", "cline"], default="all", help="筛选特定 Agent")
    p_title.add_argument("-n", "--limit", type=int, default=20, help="批量重命名最大数量 (默认 20)")
    p_title.add_argument("--dry-run", action="store_true", help="仅预览生成的新标题，不实际保存")
    p_title.add_argument("-f", "--force", action="store_true", help="覆盖已有自定义标题强制重新生成")

    args = p.parse_args()

    if args.cmd == "rename":
        rename_session(args.session, args.title)
    elif args.cmd in ("title", "retitle"):
        if args.all or not args.session:
            batch_retitle_sessions(agent_filter=args.agent, limit=args.limit, dry_run=args.dry_run, force=args.force)
        else:
            retitle_session(args.session, dry_run=args.dry_run)
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
        if args.trash:
            clean_trash_sessions(dry_run=args.dry_run)
        elif args.empty:
            clean_empty_sessions(dry_run=args.dry_run, force=args.force, max_kb=args.max_kb)
        else:
            p.print_help()
    else:
        p.print_help()


if __name__ == "__main__":
    main()
