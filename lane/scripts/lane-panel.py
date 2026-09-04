#!/usr/bin/env python3
"""Lane 泳道协同控制面板 (lane-panel.py)

单文件零外部依赖的轻量级 Web 控制台（基于 Python 内置 http.server）。
提供三大 Agent（agy / qoder / CodeBuddy）的会话可视化管理：
  - 批量查看、搜索与过滤（工作区、Agent、标签、状态）
  - 行内快速改名、打标签、置顶与归档
  - 安全软删除 (移入回收站) 与物理彻底删除
  - 一键清理 0 轮次空会话与控制指令残留
  - 右侧抽屉式会话详情与对话流实时预览
  - 一键复制现场接管命令 (lane resume)
"""
import os
import sys
import json
import glob
import sqlite3
import argparse
import subprocess
import urllib.parse
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import importlib.util

PORT = 3457
META_FILE = "/root/.agent/lane-meta.json"
AGY_CONV_DIR = "/root/.gemini/antigravity-cli/conversations"
QODER_PROJ_DIR = "/root/.qoder/projects"
CB_PROJ_DIR = "/root/.codebuddy/projects"

# ============ Agent 元信息（技能管理 / Agent.md 管理） ============
# agent: { 名称, skills_dir 技能根目录, agent_md 专属 Agent.md, fallback_md 兜底全局规范 }
AGENTS = {
    "agy": {
        "name": "agy (架构研发)",
        "skills_dir": "/root/.gemini/antigravity-cli/skills",
        "agent_md": "/root/.gemini/config/AGENTS.md",
    },
    "qoder": {
        "name": "qoder (前端交互)",
        "skills_dir": "/root/.qoder/skills",
        "agent_md": "/root/.qoder/AGENTS.md",
    },
    "codebuddy": {
        "name": "CodeBuddy (审查归档)",
        "skills_dir": "/root/.codebuddy/skills",
        "agent_md": "/root/.codebuddy/AGENTS.md",
        "fallback_md": "/root/AGENT.md",
    },
    "cline": {
        "name": "Cline (全能执行)",
        "skills_dir": "/root/.cline/skills",
        "agent_md": "/root/.cline/AGENTS.md",
        "fallback_md": "/root/AGENT.md",
    },
}

# ============ Memory（记忆）管理 ============
# 记忆按项目工作区隔离存放：/root/.codebuddy/projects/<工作区>/memory/
CB_PROJECTS_ROOT = "/root/.codebuddy/projects"

# 动态加载查看器与管理器
def get_modules():
    spec_lv = importlib.util.spec_from_file_location("log_viewer", "/root/agent-log-viewer.py")
    lv = importlib.util.module_from_spec(spec_lv)
    spec_lv.loader.exec_module(lv)

    spec_mgr = importlib.util.spec_from_file_location("agent_manager", "/root/agent-manager.py")
    mgr = importlib.util.module_from_spec(spec_mgr)
    spec_mgr.loader.exec_module(mgr)
    return lv, mgr


def get_all_sessions_data(lv, mgr):
    agy_mod = lv.load_module_from_file("agy_mod", "/root/agy-log-viewer.py")
    qoder_mod = lv.load_module_from_file("qoder_mod", "/root/qoder-log-viewer.py")
    cb_mod = lv.load_module_from_file("cb_mod", "/root/codebuddy-log-viewer.py")
    cline_mod = lv.load_module_from_file("cline_mod", "/root/cline-log-viewer.py")
    meta = mgr.load_meta()

    raw_sessions = []
    raw_sessions.extend(lv.get_agy_sessions(agy_mod, show_all=True))
    raw_sessions.extend(lv.get_qoder_sessions(qoder_mod, show_all=True))
    raw_sessions.extend(lv.get_cb_sessions(cb_mod, show_all=True))
    raw_sessions.extend(lv.get_cline_sessions(cline_mod, show_all=True))

    out = []
    all_tags = set()
    for s in raw_sessions:
        sid = s["sid"]
        m = meta.get(sid, {})
        custom_title = m.get("title")
        tags = m.get("tags", [])
        for t in tags:
            all_tags.add(t)

        size_bytes = s.get("size_bytes", 0)
        size_str = s.get("size_str", "-")
        is_noisy = lv.is_noisy_session(s["title"])
        out.append({
            "sid": sid,
            "agent": s["agent"],
            "ws": s["ws"],
            "size_bytes": size_bytes,
            "size_str": size_str,
            "title": custom_title or s["title"],
            "raw_title": s["title"],
            "has_custom_title": bool(custom_title),
            "is_noisy": is_noisy,
            "ts": s["ts"],
            "mtime": s["mtime"],
            "tags": tags,
            "pinned": bool(m.get("pinned", False)),
            "archived": bool(m.get("archived", False)),
            "deleted": bool(m.get("deleted", False)),
            "path": s["path"]
        })

    # 排序：置顶在前，其余按修改时间倒序
    out.sort(key=lambda x: (not x["pinned"], -x["mtime"]))
    return out, sorted(list(all_tags))


def get_session_detail_text(sid):
    lv, mgr = get_modules()
    agent_type, path = lv.find_session_agent(sid)
    if not agent_type or not path:
        return "会话不存在或已删除"

    cmd = ["python3"]
    if agent_type == "agy":
        cmd.extend(["/root/agy-log-viewer.py", path, "-s", "-T"])
    elif agent_type == "qoder":
        cmd.extend(["/root/qoder-log-viewer.py", path, "-s"])
    elif agent_type == "codebuddy":
        cmd.extend(["/root/codebuddy-log-viewer.py", path, "-s"])
    elif agent_type == "cline":
        cmd.extend(["/root/cline-log-viewer.py", path, "-s", "-T"])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return proc.stdout or proc.stderr or "暂无详细对话记录"
    except Exception as e:
        return f"读取详情异常: {e}"


# ============ Skill 管理与 Agent.md 管理 后端逻辑 ============

def get_agent_meta():
    """返回各 agent 的展示信息（不含文件内容）。"""
    out = {}
    for key, cfg in AGENTS.items():
        agent_md = cfg.get("agent_md")
        md_path = agent_md
        if md_path and not os.path.exists(md_path) and cfg.get("fallback_md"):
            md_path = cfg["fallback_md"]  # 专属缺失时退回全局规范
        out[key] = {
            "name": cfg["name"],
            "skills_dir": cfg["skills_dir"],
            "agent_md": cfg.get("agent_md"),
            "fallback_md": cfg.get("fallback_md"),
            "md_path": md_path,
            "md_exists": bool(md_path and os.path.exists(md_path)),
        }
    return out


def _list_dir_files(root, base=""):
    """递归列出目录下所有文件相对路径（忽略隐藏/缓存目录）。"""
    files = []
    try:
        entries = sorted(os.listdir(root))
    except Exception:
        return files
    for e in entries:
        if e.startswith(".") or e in ("__pycache__", "node_modules"):
            continue
        full = os.path.join(root, e)
        rel = os.path.join(base, e) if base else e
        try:
            if os.path.isdir(full) and not os.path.islink(full):
                files.extend(_list_dir_files(full, rel))
            else:
                files.append(rel)
        except Exception:
            files.append(rel)
    return files


def get_skills_list(agent_key):
    """列出某 agent 的 skill 目录及其内部文件树。"""
    cfg = AGENTS.get(agent_key)
    if not cfg:
        return {"ok": False, "error": f"未知 agent: {agent_key}"}
    root = cfg["skills_dir"]
    skills = []
    if os.path.isdir(root):
        for entry in sorted(os.listdir(root)):
            full = os.path.join(root, entry)
            try:
                is_link = os.path.islink(full)
                is_dir = os.path.isdir(full)
                files = _list_dir_files(full) if is_dir else []
                skills.append({
                    "name": entry,
                    "path": full,
                    "is_link": is_link,
                    "link_target": os.path.realpath(full) if is_link else None,
                    "is_dir": is_dir,
                    "files": files,
                })
            except Exception:
                continue
    return {"ok": True, "agent": agent_key, "skills_dir": root, "skills": skills}


def _resolve_within(root, rel_path):
    """将 rel_path 限定在 root 目录内，返回安全绝对路径（支持穿透顶层符号链接）。"""
    base = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root, rel_path))
    # 允许根 = 技能根目录 + 顶层各条目的真实路径（处理 lane -> /root/.agent 这类软链）
    allowed = [base]
    try:
        for entry in os.listdir(root):
            full = os.path.join(root, entry)
            if os.path.islink(full):
                allowed.append(os.path.realpath(full))
    except Exception:
        pass
    for a in allowed:
        if target == a or target.startswith(a + os.sep):
            return target
    raise ValueError(f"路径越界: {rel_path}")


def read_skill_file(agent_key, rel_path):
    """读取 skill 目录内任意文本文件。"""
    cfg = AGENTS.get(agent_key)
    if not cfg:
        return {"ok": False, "error": f"未知 agent: {agent_key}"}
    try:
        target = _resolve_within(cfg["skills_dir"], rel_path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not os.path.isfile(target):
        return {"ok": False, "error": f"文件不存在: {rel_path}"}
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return {"ok": False, "error": f"读取失败: {e}"}
    return {"ok": True, "agent": agent_key, "path": rel_path, "abs_path": target, "content": content}


def save_skill_file(agent_key, rel_path, content):
    """保存 skill 目录内文本文件（自动创建父目录）。"""
    cfg = AGENTS.get(agent_key)
    if not cfg:
        return {"ok": False, "error": f"未知 agent: {agent_key}"}
    try:
        target = _resolve_within(cfg["skills_dir"], rel_path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        return {"ok": False, "error": f"保存失败: {e}"}
    return {"ok": True, "agent": agent_key, "path": rel_path, "abs_path": target}


def get_agent_md(agent_key):
    """读取某 agent 的 Agent.md（专属缺失时退回全局规范）。"""
    cfg = AGENTS.get(agent_key)
    if not cfg:
        return {"ok": False, "error": f"未知 agent: {agent_key}"}
    agent_md = cfg.get("agent_md")
    path = agent_md
    using_fallback = False
    if path and not os.path.exists(path) and cfg.get("fallback_md"):
        path = cfg["fallback_md"]
        using_fallback = True
    if not path or not os.path.exists(path):
        return {"ok": True, "agent": agent_key, "path": None, "content": "", "using_fallback": False, "absent": True}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return {"ok": False, "error": f"读取失败: {e}"}
    return {"ok": True, "agent": agent_key, "path": path, "content": content, "using_fallback": using_fallback, "absent": False}


def save_agent_md(agent_key, content):
    """保存某 agent 的 Agent.md（写入专属路径；若专属缺失且无 fallback 则创建专属）。"""
    cfg = AGENTS.get(agent_key)
    if not cfg:
        return {"ok": False, "error": f"未知 agent: {agent_key}"}
    agent_md = cfg.get("agent_md")
    if not agent_md:
        return {"ok": False, "error": "该 agent 未配置专属 Agent.md 路径"}
    path = agent_md
    # 仅当专属存在时才写专属；否则写入 fallback（避免误新建专属空文件）
    if not os.path.exists(path) and cfg.get("fallback_md"):
        path = cfg["fallback_md"]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        return {"ok": False, "error": f"保存失败: {e}"}
    return {"ok": True, "agent": agent_key, "path": path}


# ============ Memory（记忆）管理 后端逻辑 ============

def get_memory_projects():
    """列出所有含 memory 目录的项目工作区。"""
    projects = []
    if not os.path.isdir(CB_PROJECTS_ROOT):
        return projects
    for ws in sorted(os.listdir(CB_PROJECTS_ROOT)):
        mdir = os.path.join(CB_PROJECTS_ROOT, ws, "memory")
        if os.path.isdir(mdir):
            projects.append({"ws": ws, "path": mdir})
    return projects


def _list_memory_files(root):
    """列出 memory 目录内全部 .md 文件相对路径。"""
    files = []
    try:
        for base, dirs, fs in os.walk(root):
            for f in sorted(fs):
                if f.endswith((".md", ".markdown", ".txt", ".json")):
                    rel = os.path.relpath(os.path.join(base, f), root)
                    files.append(rel)
    except Exception:
        pass
    return sorted(files)


def _resolve_memory_file(ws, rel_path):
    """将 rel_path 限定在指定工作区的 memory 目录内。"""
    mdir = os.path.join(CB_PROJECTS_ROOT, ws, "memory")
    base = os.path.realpath(mdir)
    target = os.path.realpath(os.path.join(mdir, rel_path))
    if target != base and not target.startswith(base + os.sep):
        raise ValueError(f"路径越界: {rel_path}")
    return target, mdir


def read_memory_file(ws, rel_path):
    """读取某项目记忆文件。"""
    if not rel_path:
        return {"ok": False, "error": "缺少文件路径"}
    try:
        target, _ = _resolve_memory_file(ws, rel_path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not os.path.isfile(target):
        return {"ok": False, "error": f"文件不存在: {rel_path}"}
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return {"ok": False, "error": f"读取失败: {e}"}
    return {"ok": True, "ws": ws, "path": rel_path, "abs_path": target, "content": content}


def save_memory_file(ws, rel_path, content):
    """保存某项目记忆文件（自动建目录）。"""
    if not rel_path:
        return {"ok": False, "error": "缺少文件路径"}
    try:
        target, _ = _resolve_memory_file(ws, rel_path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        return {"ok": False, "error": f"保存失败: {e}"}
    return {"ok": True, "ws": ws, "path": rel_path, "abs_path": target}


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>🏊‍♂️ Lane Studio — 多 Agent 泳道控制台</title>
  <style>
    :root {
      --bg: #0d1117;
      --card-bg: #161b22;
      --border: #30363d;
      --text: #c9d1d9;
      --text-muted: #8b949e;
      --text-bright: #f0f6fc;
      --primary: #58a6ff;
      --primary-hover: #388bfd;
      --success: #3fb950;
      --danger: #f85149;
      --warning: #d29922;
      --purple: #bc8cff;
      --tag-bg: rgba(88, 166, 255, 0.15);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    /* 顶部导航 */
    header {
      background: var(--card-bg);
      border-bottom: 1px solid var(--border);
      padding: 12px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-shrink: 0;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 18px;
      font-weight: 700;
      color: var(--text-bright);
    }
    .badge-v {
      font-size: 11px;
      background: rgba(88, 166, 255, 0.2);
      color: var(--primary);
      padding: 2px 8px;
      border-radius: 12px;
      border: 1px solid rgba(88, 166, 255, 0.4);
    }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .search-box {
      background: #0d1117;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 6px 12px;
      border-radius: 6px;
      width: 260px;
      font-size: 13px;
    }
    .search-box:focus { outline: none; border-color: var(--primary); }
    .btn {
      background: #21262d;
      color: var(--text-bright);
      border: 1px solid var(--border);
      padding: 6px 14px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 500;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.2s;
    }
    .btn:hover { background: #30363d; border-color: #8b949e; }
    .btn-primary { background: #238636; border-color: #2ea043; }
    .btn-primary:hover { background: #2ea043; }
    .btn-danger { background: rgba(248, 81, 73, 0.15); border-color: rgba(248, 81, 73, 0.4); color: var(--danger); }
    .btn-danger:hover { background: var(--danger); color: #fff; }
    .btn-sm { padding: 3px 10px; font-size: 12px; }

    /* 视图切换 Tab */
    .view-tabs {
      display: flex;
      gap: 4px;
      background: #0d1117;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 3px;
    }
    .view-tab {
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 6px 14px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 500;
      transition: all 0.15s;
    }
    .view-tab:hover { color: var(--text-bright); }
    .view-tab.active { background: #21262d; color: var(--primary); font-weight: 600; }

    /* 子视图页（技能管理 / Agent.md） */
    .view-page {
      flex: 1;
      display: none;
      overflow: hidden;
    }
    .view-page.active { display: flex; }

    /* 技能管理 三栏布局 */
    .skill-layout {
      display: flex;
      flex: 1;
      overflow: hidden;
      gap: 0;
    }
    .skill-sidebar {
      width: 240px;
      background: var(--card-bg);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      flex-shrink: 0;
    }
    .skill-sidebar-head {
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      font-size: 13px;
      font-weight: 600;
      color: var(--text-muted);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .skill-sidebar-body { flex: 1; overflow-y: auto; padding: 8px; }
    .skill-item {
      padding: 8px 10px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      margin-bottom: 2px;
    }
    .skill-item:hover { background: #21262d; }
    .skill-item.active { background: rgba(88, 166, 255, 0.15); color: var(--primary); font-weight: 600; }
    .skill-item .link-mark { font-size: 10px; color: var(--warning); background: rgba(210, 153, 34, 0.15); padding: 1px 5px; border-radius: 4px; }
    .skill-item .fcount { font-size: 11px; color: var(--text-muted); background: #21262d; padding: 1px 6px; border-radius: 8px; }

    /* 文件列表（中栏） */
    .skill-files {
      width: 280px;
      background: var(--bg);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      flex-shrink: 0;
    }
    .skill-files-head {
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      font-size: 13px;
      font-weight: 600;
      color: var(--text-muted);
      word-break: break-all;
    }
    .skill-files-body { flex: 1; overflow-y: auto; padding: 8px; }
    .file-item {
      padding: 6px 10px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 12.5px;
      font-family: monospace;
      color: var(--text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      margin-bottom: 1px;
    }
    .file-item:hover { background: #21262d; }
    .file-item.active { background: rgba(88, 166, 255, 0.15); color: var(--primary); }

    /* 编辑器（右栏） */
    .editor-pane {
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      background: var(--bg);
    }
    .editor-head {
      padding: 10px 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      font-size: 13px;
      color: var(--text-muted);
    }
    .editor-head .path-text { font-family: monospace; color: var(--text); word-break: break-all; }
    .editor-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
    .editor-box {
      flex: 1;
      background: #0d1117;
      color: #e6edf3;
      border: none;
      padding: 16px;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
      font-size: 13px;
      line-height: 1.6;
      resize: none;
      outline: none;
      white-space: pre;
      overflow: auto;
      tab-size: 2;
    }
    .editor-empty {
      flex: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--text-muted);
      font-size: 14px;
    }

    /* Agent.md 管理 两栏布局 */
    .md-layout {
      display: flex;
      flex: 1;
      overflow: hidden;
    }
    .md-sidebar {
      width: 220px;
      background: var(--card-bg);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      overflow-y: auto;
      flex-shrink: 0;
      padding: 10px;
    }
    .md-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; background: var(--bg); }
    .md-item {
      padding: 8px 10px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 2px;
    }
    .md-item:hover { background: #21262d; }
    .md-item.active { background: rgba(88, 166, 255, 0.15); color: var(--primary); font-weight: 600; }

    /* 主体布局 */
    .layout {
      display: flex;
      flex: 1;
      overflow: hidden;
      position: relative;
    }
    /* 侧边栏 */
    aside {
      width: 230px;
      background: var(--card-bg);
      border-right: 1px solid var(--border);
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 20px;
      overflow-y: auto;
      flex-shrink: 0;
    }
    .sidebar-section h4 {
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-muted);
      margin-bottom: 8px;
    }
    .nav-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 10px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
      color: var(--text);
      margin-bottom: 2px;
    }
    .nav-item:hover { background: #21262d; }
    .nav-item.active { background: rgba(88, 166, 255, 0.15); color: var(--primary); font-weight: 600; }
    .nav-item .count { font-size: 11px; background: #21262d; padding: 2px 6px; border-radius: 10px; }

    .tag-cloud { display: flex; flex-wrap: wrap; gap: 6px; }
    .tag-pill {
      font-size: 11px;
      background: var(--tag-bg);
      color: var(--primary);
      padding: 3px 8px;
      border-radius: 12px;
      cursor: pointer;
      border: 1px solid transparent;
    }
    .tag-pill:hover, .tag-pill.active { border-color: var(--primary); background: rgba(88, 166, 255, 0.3); }

    /* 列表区域 */
    main {
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      background: var(--bg);
    }
    .table-header {
      padding: 12px 20px;
      background: var(--card-bg);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 13px;
    }
    .batch-bar { display: flex; align-items: center; gap: 10px; }
    .table-container {
      flex: 1;
      overflow-y: auto;
      padding: 12px 20px;
    }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th { text-align: left; padding: 10px 12px; color: var(--text-muted); border-bottom: 1px solid var(--border); font-weight: 600; }
    td { padding: 12px; border-bottom: 1px solid #21262d; vertical-align: middle; }
    tr:hover td { background: rgba(255, 255, 255, 0.02); }

    /* 徽章 */
    .badge {
      display: inline-block;
      padding: 2px 7px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
    }
    .badge-agy { background: rgba(88, 166, 255, 0.15); color: #58a6ff; border: 1px solid rgba(88, 166, 255, 0.3); }
    .badge-qoder { background: rgba(210, 153, 34, 0.15); color: #d29922; border: 1px solid rgba(210, 153, 34, 0.3); }
    .badge-cb { background: rgba(188, 140, 255, 0.15); color: #bc8cff; border: 1px solid rgba(188, 140, 255, 0.3); }
    .badge-cline { background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); }
    .badge-ws { background: #21262d; color: var(--text-muted); border: 1px solid var(--border); }

    .title-cell {
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
    }
    .title-text { font-weight: 500; color: var(--text-bright); }
    .title-input {
      background: #0d1117;
      border: 1px solid var(--primary);
      color: #fff;
      padding: 4px 8px;
      border-radius: 4px;
      font-size: 13px;
      width: 100%;
    }
    .tag-item {
      background: var(--tag-bg);
      color: var(--primary);
      font-size: 11px;
      padding: 1px 6px;
      border-radius: 4px;
      margin-right: 4px;
    }

    .action-icons { display: flex; gap: 8px; }
    .icon-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 15px;
      padding: 4px;
      border-radius: 4px;
    }
    .icon-btn:hover { color: var(--text-bright); background: #21262d; }
    .icon-btn.active-star { color: #e3b341; }

    /* 抽屉式侧边栏 */
    .drawer {
      position: absolute;
      top: 0;
      right: -600px;
      width: 580px;
      height: 100%;
      background: var(--card-bg);
      border-left: 1px solid var(--border);
      box-shadow: -10px 0 30px rgba(0,0,0,0.5);
      transition: right 0.3s cubic-bezier(0.16, 1, 0.3, 1);
      display: flex;
      flex-direction: column;
      z-index: 100;
    }
    .drawer.open { right: 0; }
    .drawer-header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
    }
    .drawer-body {
      flex: 1;
      padding: 20px;
      overflow-y: auto;
      font-family: monospace;
      font-size: 13px;
      line-height: 1.6;
      white-space: pre-wrap;
      background: #0d1117;
      color: #c9d1d9;
    }
    .close-btn { font-size: 20px; cursor: pointer; color: var(--text-muted); }
    .close-btn:hover { color: #fff; }
  </style>
</head>
<body>

  <header>
    <div class="brand">
      <span>🏊‍♂️</span>
      <span>Lane Studio</span>
      <span class="badge-v">v2.2.0</span>
    </div>
    <div class="view-tabs">
      <button class="view-tab active" id="tab-view-sessions" onclick="switchView('sessions')">📋 会话管理</button>
      <button class="view-tab" id="tab-view-skills" onclick="switchView('skills')">🧩 技能管理</button>
      <button class="view-tab" id="tab-view-agentmd" onclick="switchView('agentmd')">📄 Agent.md</button>
      <button class="view-tab" id="tab-view-memory" onclick="switchView('memory')">🧠 记忆管理</button>
    </div>
    <div class="header-actions">
      <input type="text" id="search" class="search-box" placeholder="搜索会话标题、ID、关键词..." oninput="renderTable()">
      <button class="btn btn-primary" onclick="cleanEmpty()">🧹 一键清理空会话</button>
      <button class="btn" onclick="fetchData()">🔄 刷新</button>
    </div>
  </header>

  <div class="view-page active" id="view-sessions">
  <div class="layout">
    <!-- 侧边栏 -->
    <aside>
      <div class="sidebar-section">
        <h4>会话视图</h4>
        <div class="nav-item active" onclick="setTab('active')" id="tab-active">
          <span>📥 活跃会话</span>
          <span class="count" id="count-active">0</span>
        </div>
        <div class="nav-item" onclick="setTab('pinned')" id="tab-pinned">
          <span>⭐ 收藏置顶</span>
          <span class="count" id="count-pinned">0</span>
        </div>
        <div class="nav-item" onclick="setTab('archived')" id="tab-archived">
          <span>📦 已归档</span>
          <span class="count" id="count-archived">0</span>
        </div>
        <div class="nav-item" onclick="setTab('trash')" id="tab-trash">
          <span>🗑️ 回收站</span>
          <span class="count" id="count-trash">0</span>
        </div>
      </div>

      <div class="sidebar-section">
        <h4>Agent 泳道</h4>
        <div class="nav-item active" onclick="setAgent('all')" id="agent-all">
          <span>全部 Agent</span>
          <span class="count" id="count-agent-all">0</span>
        </div>
        <div class="nav-item" onclick="setAgent('agy')" id="agent-agy">
          <span>agy (架构研发)</span>
          <span class="count" id="count-agent-agy">0</span>
        </div>
        <div class="nav-item" onclick="setAgent('qoder')" id="agent-qoder">
          <span>qoder (前端交互)</span>
          <span class="count" id="count-agent-qoder">0</span>
        </div>
        <div class="nav-item" onclick="setAgent('codebuddy')" id="agent-codebuddy">
          <span>CodeBuddy (审查归档)</span>
          <span class="count" id="count-agent-codebuddy">0</span>
        </div>
        <div class="nav-item" onclick="setAgent('cline')" id="agent-cline">
          <span>Cline (全能执行)</span>
          <span class="count" id="count-agent-cline">0</span>
        </div>
      </div>

      <div class="sidebar-section">
        <h4>工作空间</h4>
        <div id="workspace-list"></div>
      </div>

      <div class="sidebar-section">
        <h4>标签筛选</h4>
        <div class="tag-cloud" id="tag-cloud"></div>
      </div>
    </aside>

    <!-- 列表主区域 -->
    <main>
      <div class="table-header">
        <div class="batch-bar">
          <input type="checkbox" id="select-all" onclick="toggleSelectAll()">
          <span id="selected-hint">已选 0 项</span>
          <button class="btn btn-danger" id="btn-batch-del" style="display:none;" onclick="batchDelete()">批量删除</button>
          <button class="btn btn-danger" id="btn-empty-trash" style="display:none;" onclick="emptyAllTrash()">🔥 清空回收站</button>
        </div>
        <div id="stats-info" style="color: var(--text-muted);">正在加载数据...</div>
      </div>

      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th width="40"></th>
              <th width="100">Agent</th>
              <th width="130">工作空间</th>
              <th width="95" style="cursor:pointer; user-select:none;" onclick="toggleSort('size')" title="点击切换体积排序 (升序/降序)">
                体积 <span id="sort-icon-size" style="font-size:11px; color:var(--text-muted);">↕</span>
              </th>
              <th>标题 / 主题</th>
              <th width="155" style="cursor:pointer; user-select:none;" onclick="toggleSort('time')" title="点击切换时间排序 (升序/降序)">
                修改时间 <span id="sort-icon-time" style="font-size:11px; color:var(--primary);">▼</span>
              </th>
              <th width="150" style="text-align: right;">操作</th>
            </tr>
          </thead>
          <tbody id="session-tbody"></tbody>
        </table>
      </div>
    </main>

    <!-- 右侧抽屉预览 -->
    <div class="drawer" id="drawer">
      <div class="drawer-header">
        <div>
          <h3 id="drawer-title" style="color: #fff; margin-bottom: 6px;">会话详情</h3>
          <div id="drawer-meta" style="font-size: 12px; color: var(--text-muted);"></div>
        </div>
        <div style="display:flex; align-items:center; gap: 10px;">
          <button class="btn" id="btn-copy-resume" onclick="copyResumeCmd()">📋 复制接管命令</button>
          <span class="close-btn" onclick="closeDrawer()">✕</span>
        </div>
      </div>
      <div class="drawer-body" id="drawer-content">正在加载详情...</div>
    </div>
  </div>
  </div><!-- /view-sessions -->

  <!-- 🧩 技能管理视图 -->
  <div class="view-page" id="view-skills">
    <div class="skill-layout">
      <!-- 左侧：agent 选择 + skill 列表 -->
      <div class="skill-sidebar">
        <div class="skill-sidebar-head">
          <span>🧩 Agent 技能</span>
          <span class="fcount" id="skills-count" style="background:#21262d;color:var(--text-muted);padding:1px 8px;border-radius:8px;font-size:11px;">-</span>
        </div>
        <div style="padding: 8px 12px; display: flex; gap: 6px; border-bottom: 1px solid var(--border);">
          <button class="view-tab active" id="skill-agent-agy" onclick="loadSkills('agy')">agy</button>
          <button class="view-tab" id="skill-agent-qoder" onclick="loadSkills('qoder')">qoder</button>
          <button class="view-tab" id="skill-agent-codebuddy" onclick="loadSkills('codebuddy')">codebuddy</button>
          <button class="view-tab" id="skill-agent-cline" onclick="loadSkills('cline')">cline</button>
        </div>
        <div class="skill-sidebar-body" id="skill-list">选择左侧 Agent 加载技能列表...</div>
      </div>
      <!-- 中间：skill 文件列表 -->
      <div class="skill-files">
        <div class="skill-files-head" id="skill-files-head">👆 选择技能查看文件</div>
        <div class="skill-files-body" id="skill-files-body">暂无文件</div>
      </div>
      <!-- 右侧：文件编辑器 -->
      <div class="editor-pane">
        <div class="editor-head">
          <span class="path-text" id="editor-path">未选择文件</span>
          <div class="editor-actions">
            <span id="editor-status" style="font-size:12px;"></span>
            <button class="btn btn-primary btn-sm" id="btn-save-skill" style="display:none;" onclick="saveSkillFile()">💾 保存</button>
          </div>
        </div>
        <textarea class="editor-box" id="skill-editor" placeholder="点击中间文件列表加载内容，编辑后点击「保存」写入磁盘。" spellcheck="false"></textarea>
      </div>
    </div>
  </div>

  <!-- 📄 Agent.md 管理视图 -->
  <div class="view-page" id="view-agentmd">
    <div class="md-layout">
      <div class="md-sidebar" id="md-sidebar">
        <div style="padding: 10px 12px; font-size: 13px; font-weight: 600; color: var(--text-muted);">📄 Agent.md 管理</div>
      </div>
      <div class="md-main">
        <div class="editor-head">
          <span class="path-text" id="md-path">未选择 Agent</span>
          <div class="editor-actions">
            <span id="md-status" style="font-size:12px;"></span>
            <button class="btn btn-primary btn-sm" id="btn-save-md" style="display:none;" onclick="saveAgentMd()">💾 保存</button>
          </div>
        </div>
        <textarea class="editor-box" id="md-editor" placeholder="选择左侧 Agent 查看/编辑其 Agent.md 配置。" spellcheck="false"></textarea>
      </div>
    </div>
  </div>

  <!-- 🧠 记忆管理视图 -->
  <div class="view-page" id="view-memory">
    <div class="md-layout">
      <!-- 左侧：项目工作区列表 -->
      <div class="md-sidebar" id="memory-projects">
        <div style="padding: 10px 12px; font-size: 13px; font-weight: 600; color: var(--text-muted);">🧠 项目记忆</div>
        <div style="padding: 0 12px 10px; font-size: 11px; color: var(--text-muted);">按工作区隔离的记忆目录</div>
      </div>
      <!-- 中部：记忆文件列表 -->
      <div class="skill-files" style="width: 260px;">
        <div class="skill-files-head" id="memory-files-head">👆 选择项目查看记忆文件</div>
        <div class="skill-files-body" id="memory-files-body">暂无文件</div>
      </div>
      <!-- 右侧：编辑器 -->
      <div class="editor-pane">
        <div class="editor-head">
          <span class="path-text" id="memory-editor-path">未选择记忆文件</span>
          <div class="editor-actions">
            <span id="memory-editor-status" style="font-size:12px;"></span>
            <button class="btn btn-primary btn-sm" id="btn-save-memory" style="display:none;" onclick="saveMemoryFile()">💾 保存</button>
          </div>
        </div>
        <textarea class="editor-box" id="memory-editor" placeholder="选择左侧项目与文件，查看/编辑记忆内容（可删减）。" spellcheck="false"></textarea>
      </div>
    </div>
  </div>

  <script>
    let allSessions = [];
    let currentTab = 'active';
    let currentAgent = 'all';
    let currentWs = 'all';
    let currentTag = 'all';
    let selectedSids = new Set();
    let currentDrawerSid = null;
    let currentDrawerAgent = null;
    let currentDrawerWs = null;
    let currentSortField = 'time';
    let currentSortOrder = 'desc';

    // ---- 视图切换 ----
    function switchView(view) {
      ['sessions', 'skills', 'agentmd', 'memory'].forEach(v => {
        const page = document.getElementById('view-' + v);
        const tab = document.getElementById('tab-view-' + v);
        if (page) page.classList.toggle('active', v === view);
        if (tab) tab.classList.toggle('active', v === view);
      });
      if (view === 'skills' && !window._skillsLoaded) loadSkills('agy');
      if (view === 'agentmd' && !window._mdLoaded) loadAgentMd();
      if (view === 'memory' && !window._memoryLoaded) loadMemoryProjects();
    }

    // ==================== 🧩 技能管理 ====================
    let _skillsAgent = 'agy';
    let _skillsList = [];
    let _currentSkillFiles = [];
    let _currentSkillFile = null;

    async function loadSkills(agent) {
      _skillsAgent = agent;
      window._skillsLoaded = true;
      document.querySelectorAll('[id^="skill-agent-"]').forEach(el => el.classList.remove('active'));
      document.getElementById('skill-agent-' + agent).classList.add('active');
      document.getElementById('skill-files-head').innerText = '👆 选择技能查看文件';
      document.getElementById('skill-files-body').innerText = '暂无文件';
      document.getElementById('editor-path').innerText = '未选择文件';
      document.getElementById('editor-status').innerText = '';
      document.getElementById('btn-save-skill').style.display = 'none';
      document.getElementById('skill-editor').value = '';
      _currentSkillFiles = [];
      _currentSkillFile = null;

      try {
        const res = await fetch('/api/skills?agent=' + agent);
        const data = await res.json();
        if (!data.ok) { document.getElementById('skill-list').innerText = data.error || '加载失败'; return; }
        _skillsList = data.skills || [];
        document.getElementById('skills-count').innerText = data.skills_dir || '';
        const listEl = document.getElementById('skill-list');
        if (_skillsList.length === 0) {
          listEl.innerHTML = '<div style="color: var(--text-muted); padding: 16px;">该 Agent 暂无技能</div>';
          return;
        }
        listEl.innerHTML = _skillsList.map(s => `
          <div class="skill-item" data-name="${escapeHtml(s.name)}" onclick="selectSkill('${s.name.replace(/'/g, "\\'")}')">
            <span>${s.is_link ? '🔗' : '📁'} ${escapeHtml(s.name)}</span>
            <span class="fcount">${s.files.length}</span>
          </div>
        `).join('');
        // 自动选中第一个
        if (_skillsList[0]) selectSkill(_skillsList[0].name);
      } catch (e) {
        document.getElementById('skill-list').innerText = '加载失败: ' + e;
      }
    }

    function selectSkill(name) {
      const skill = _skillsList.find(s => s.name === name);
      if (!skill) return;
      // 高亮 skill
      document.querySelectorAll('.skill-item').forEach(el => el.classList.remove('active'));
      const itemEl = document.querySelector(`.skill-item[data-name="${name.replace(/"/g, '\\"')}"]`);
      if (itemEl) itemEl.classList.add('active');

      _currentSkillFiles = skill.files || [];
      document.getElementById('skill-files-head').innerHTML = `${escapeHtml(name)} <span style="font-weight:400;color:var(--text-muted);">${skill.is_link ? '🔗 符号链接' : ''}</span>`;
      const filesEl = document.getElementById('skill-files-body');
      if (_currentSkillFiles.length === 0) {
        filesEl.innerHTML = '<div style="color: var(--text-muted); padding: 16px;">该技能目录为空</div>';
      } else {
        filesEl.innerHTML = _currentSkillFiles.map(f => `
          <div class="file-item ${f === _currentSkillFile ? 'active' : ''}" onclick="openSkillFile('${f.replace(/'/g, "\\'")}')">${escapeHtml(f)}</div>
        `).join('');
      }
      // 默认打开 SKILL.md
      if (_currentSkillFiles.includes('SKILL.md')) openSkillFile('SKILL.md');
      else if (_currentSkillFiles.length > 0) openSkillFile(_currentSkillFiles[0]);
    }

    async function openSkillFile(path) {
      _currentSkillFile = path;
      document.querySelectorAll('.file-item').forEach(el => {
        el.classList.toggle('active', el.innerText.trim() === path);
      });
      document.getElementById('editor-path').innerText = `${_skillsAgent}/${path}`;
      document.getElementById('editor-status').innerText = '加载中...';
      document.getElementById('btn-save-skill').style.display = 'none';
      try {
        const res = await fetch('/api/skill-file?agent=' + _skillsAgent + '&path=' + encodeURIComponent(path));
        const data = await res.json();
        if (!data.ok) { document.getElementById('editor-status').innerText = data.error || '读取失败'; return; }
        document.getElementById('skill-editor').value = data.content;
        document.getElementById('editor-status').innerText = `📄 ${data.abs_path}`;
        document.getElementById('btn-save-skill').style.display = 'inline-flex';
      } catch (e) {
        document.getElementById('editor-status').innerText = '读取失败: ' + e;
      }
    }

    async function saveSkillFile() {
      if (!_currentSkillFile) return;
      const content = document.getElementById('skill-editor').value;
      document.getElementById('editor-status').innerText = '保存中...';
      try {
        const res = await fetch('/api/save-skill-file', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ agent: _skillsAgent, path: _currentSkillFile, content })
        });
        const data = await res.json();
        if (data.ok) document.getElementById('editor-status').innerText = `✅ 已保存 → ${data.abs_path}`;
        else document.getElementById('editor-status').innerText = '❌ ' + (data.error || '保存失败');
      } catch (e) {
        document.getElementById('editor-status').innerText = '保存失败: ' + e;
      }
    }

    // ==================== 📄 Agent.md 管理 ====================
    let _mdAgent = null;

    async function loadAgentMd() {
      window._mdLoaded = true;
      try {
        const res = await fetch('/api/agents');
        const data = await res.json();
        const agents = data.agents || {};
        const sidebar = document.getElementById('md-sidebar');
        let html = `<div style="padding: 10px 12px; font-size: 13px; font-weight: 600; color: var(--text-muted);">📄 Agent.md 管理</div>`;
        Object.keys(agents).forEach(key => {
          const a = agents[key];
          const label = a.name || key;
          html += `<div class="md-item ${_mdAgent === key ? 'active' : ''}" id="md-item-${key}" onclick="openAgentMd('${key}')">
            <span>${escapeHtml(label)}</span>
            <span style="font-size:10px; color:var(--text-muted);">${a.md_exists ? '' : '⚠️'}</span>
          </div>`;
        });
        sidebar.innerHTML = html;
        // 默认打开第一个
        const keys = Object.keys(agents);
        if (keys.length > 0 && !_mdAgent) openAgentMd(keys[0]);
      } catch (e) {
        document.getElementById('md-sidebar').innerText = '加载失败: ' + e;
      }
    }

    async function openAgentMd(agent) {
      _mdAgent = agent;
      document.querySelectorAll('.md-item').forEach(el => el.classList.remove('active'));
      const item = document.getElementById('md-item-' + agent);
      if (item) item.classList.add('active');
      document.getElementById('md-status').innerText = '加载中...';
      document.getElementById('btn-save-md').style.display = 'none';
      try {
        const res = await fetch('/api/agentmd?agent=' + agent);
        const data = await res.json();
        if (!data.ok) { document.getElementById('md-status').innerText = data.error || '读取失败'; return; }
        if (data.absent) {
          document.getElementById('md-path').innerText = agent + ' / Agent.md (尚未创建)';
          document.getElementById('md-editor').value = '';
          document.getElementById('md-status').innerText = '该 Agent 尚无 Agent.md，填写后保存即可创建';
          document.getElementById('btn-save-md').style.display = 'inline-flex';
          return;
        }
        document.getElementById('md-path').innerText = data.path;
        document.getElementById('md-editor').value = data.content;
        document.getElementById('md-status').innerText = data.using_fallback ? '（专属缺失，当前编辑全局规范）' : '';
        document.getElementById('btn-save-md').style.display = 'inline-flex';
      } catch (e) {
        document.getElementById('md-status').innerText = '读取失败: ' + e;
      }
    }

    async function saveAgentMd() {
      if (!_mdAgent) return;
      const content = document.getElementById('md-editor').value;
      document.getElementById('md-status').innerText = '保存中...';
      try {
        const res = await fetch('/api/save-agentmd', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ agent: _mdAgent, content })
        });
        const data = await res.json();
        if (data.ok) document.getElementById('md-status').innerText = '✅ 已保存 → ' + data.path;
        else document.getElementById('md-status').innerText = '❌ ' + (data.error || '保存失败');
      } catch (e) {
        document.getElementById('md-status').innerText = '保存失败: ' + e;
      }
    }

    // ==================== 🧠 记忆管理 ====================
    let _memoryWs = null;
    let _memoryFiles = [];
    let _memoryCurrentFile = null;

    async function loadMemoryProjects() {
      window._memoryLoaded = true;
      try {
        const res = await fetch('/api/memories');
        const data = await res.json();
        if (!data.ok) { document.getElementById('memory-projects').innerHTML = data.error || '加载失败'; return; }
        const projects = data.projects || [];
        const sidebar = document.getElementById('memory-projects');
        let html = `<div style="padding: 10px 12px; font-size: 13px; font-weight: 600; color: var(--text-muted);">🧠 项目记忆</div>
                    <div style="padding: 0 12px 10px; font-size: 11px; color: var(--text-muted);">按工作区隔离的记忆目录</div>`;
        if (projects.length === 0) {
          html += '<div style="padding: 16px; color: var(--text-muted);">暂无项目记忆</div>';
        }
        projects.forEach(p => {
          html += `<div class="md-item ${_memoryWs === p.ws ? 'active' : ''}" id="memory-ws-${p.ws}" onclick="selectMemoryWs('${p.ws.replace(/'/g, "\\'")}')">
            <span>📁 ${escapeHtml(p.ws)}</span>
          </div>`;
        });
        sidebar.innerHTML = html;
        // 默认选中当前工作区
        const cur = projects.find(p => p.ws === 'root-metax-workbench');
        const first = cur || projects[0];
        if (first) selectMemoryWs(first.ws);
      } catch (e) {
        document.getElementById('memory-projects').innerHTML = '加载失败: ' + e;
      }
    }

    async function selectMemoryWs(ws) {
      _memoryWs = ws;
      document.querySelectorAll('#memory-projects .md-item').forEach(el => el.classList.remove('active'));
      const el = document.getElementById('memory-ws-' + ws);
      if (el) el.classList.add('active');
      document.getElementById('memory-files-head').innerText = `📁 ${ws} / memory`;
      document.getElementById('memory-editor-path').innerText = '未选择记忆文件';
      document.getElementById('memory-editor-status').innerText = '';
      document.getElementById('btn-save-memory').style.display = 'none';
      document.getElementById('memory-editor').value = '';
      _memoryCurrentFile = null;
      try {
        const res = await fetch('/api/memory-files?ws=' + encodeURIComponent(ws));
        const data = await res.json();
        if (!data.ok) { document.getElementById('memory-files-body').innerText = data.error || '加载失败'; return; }
        _memoryFiles = data.files || [];
        const body = document.getElementById('memory-files-body');
        if (_memoryFiles.length === 0) {
          body.innerHTML = '<div style="color: var(--text-muted); padding: 16px;">该项目无记忆文件</div>';
          return;
        }
        body.innerHTML = _memoryFiles.map(f => `
          <div class="file-item ${f === _memoryCurrentFile ? 'active' : ''}" onclick="openMemoryFile('${f.replace(/'/g, "\\'")}')">${escapeHtml(f)}</div>
        `).join('');
        // 默认打开 MEMORY.md
        if (_memoryFiles.includes('MEMORY.md')) openMemoryFile('MEMORY.md');
        else if (_memoryFiles.length > 0) openMemoryFile(_memoryFiles[0]);
      } catch (e) {
        document.getElementById('memory-files-body').innerText = '加载失败: ' + e;
      }
    }

    async function openMemoryFile(path) {
      _memoryCurrentFile = path;
      document.querySelectorAll('#memory-files-body .file-item').forEach(el => {
        el.classList.toggle('active', el.innerText.trim() === path);
      });
      document.getElementById('memory-editor-path').innerText = `${_memoryWs}/${path}`;
      document.getElementById('memory-editor-status').innerText = '加载中...';
      document.getElementById('btn-save-memory').style.display = 'none';
      try {
        const res = await fetch('/api/memory-file?ws=' + encodeURIComponent(_memoryWs) + '&path=' + encodeURIComponent(path));
        const data = await res.json();
        if (!data.ok) { document.getElementById('memory-editor-status').innerText = data.error || '读取失败'; return; }
        document.getElementById('memory-editor').value = data.content;
        document.getElementById('memory-editor-status').innerText = `📄 ${data.abs_path}`;
        document.getElementById('btn-save-memory').style.display = 'inline-flex';
      } catch (e) {
        document.getElementById('memory-editor-status').innerText = '读取失败: ' + e;
      }
    }

    async function saveMemoryFile() {
      if (!_memoryCurrentFile) return;
      const content = document.getElementById('memory-editor').value;
      document.getElementById('memory-editor-status').innerText = '保存中...';
      try {
        const res = await fetch('/api/save-memory-file', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ws: _memoryWs, path: _memoryCurrentFile, content })
        });
        const data = await res.json();
        if (data.ok) document.getElementById('memory-editor-status').innerText = '✅ 已保存 → ' + data.abs_path;
        else document.getElementById('memory-editor-status').innerText = '❌ ' + (data.error || '保存失败');
      } catch (e) {
        document.getElementById('memory-editor-status').innerText = '保存失败: ' + e;
      }
    }

    function toggleSort(field) {
      if (currentSortField === field) {
        currentSortOrder = currentSortOrder === 'desc' ? 'asc' : 'desc';
      } else {
        currentSortField = field;
        currentSortOrder = 'desc';
      }
      updateSortIcons();
      renderTable();
    }

    function updateSortIcons() {
      const iconTime = document.getElementById('sort-icon-time');
      const iconSize = document.getElementById('sort-icon-size');
      if (iconTime && iconSize) {
        iconTime.innerText = currentSortField === 'time' ? (currentSortOrder === 'desc' ? '▼' : '▲') : '↕';
        iconTime.style.color = currentSortField === 'time' ? 'var(--primary)' : 'var(--text-muted)';
        iconSize.innerText = currentSortField === 'size' ? (currentSortOrder === 'desc' ? '▼' : '▲') : '↕';
        iconSize.style.color = currentSortField === 'size' ? 'var(--primary)' : 'var(--text-muted)';
      }
    }

    async function fetchData() {
      try {
        const res = await fetch('/api/sessions');
        const data = await res.json();
        allSessions = data.sessions;
        updateCounts();
        renderWorkspaces();
        renderTags(data.tags);
        renderTable();
      } catch (e) {
        alert('加载失败: ' + e);
      }
    }

    function updateCounts() {
      let active = 0, pinned = 0, archived = 0, trash = 0;
      let cAll = 0, cAgy = 0, cQoder = 0, cCb = 0, cCline = 0;
      allSessions.forEach(s => {
        if (s.deleted) trash++;
        else if (s.archived) archived++;
        else {
          active++;
          if (s.pinned) pinned++;
          cAll++;
          const a = (s.agent || '').toLowerCase();
          if (a === 'agy') cAgy++;
          else if (a === 'qoder') cQoder++;
          else if (a.includes('buddy')) cCb++;
          else if (a === 'cline') cCline++;
        }
      });
      document.getElementById('count-active').innerText = active;
      document.getElementById('count-pinned').innerText = pinned;
      document.getElementById('count-archived').innerText = archived;
      document.getElementById('count-trash').innerText = trash;
      if (document.getElementById('count-agent-all')) document.getElementById('count-agent-all').innerText = cAll;
      if (document.getElementById('count-agent-agy')) document.getElementById('count-agent-agy').innerText = cAgy;
      if (document.getElementById('count-agent-qoder')) document.getElementById('count-agent-qoder').innerText = cQoder;
      if (document.getElementById('count-agent-codebuddy')) document.getElementById('count-agent-codebuddy').innerText = cCb;
      if (document.getElementById('count-agent-cline')) document.getElementById('count-agent-cline').innerText = cCline;
    }

    function renderWorkspaces() {
      const wsSet = new Set(allSessions.map(s => s.ws));
      let html = `<div class="nav-item ${currentWs === 'all' ? 'active' : ''}" onclick="setWs('all')">全部工作区</div>`;
      wsSet.forEach(ws => {
        html += `<div class="nav-item ${currentWs === ws ? 'active' : ''}" onclick="setWs('${ws}')">${ws}</div>`;
      });
      document.getElementById('workspace-list').innerHTML = html;
    }

    function renderTags(tags) {
      let html = `<span class="tag-pill ${currentTag === 'all' ? 'active' : ''}" onclick="setTag('all')">全部</span>`;
      tags.forEach(t => {
        html += `<span class="tag-pill ${currentTag === t ? 'active' : ''}" onclick="setTag('${t}')">${t}</span>`;
      });
      document.getElementById('tag-cloud').innerHTML = html;
    }

    function setTab(tab) {
      currentTab = tab;
      selectedSids.clear();
      if (document.getElementById('select-all')) document.getElementById('select-all').checked = false;
      updateSelectedBar();
      document.querySelectorAll('.sidebar-section:first-child .nav-item').forEach(el => el.classList.remove('active'));
      document.getElementById('tab-' + tab).classList.add('active');
      renderTable();
    }

    function setAgent(agent) {
      currentAgent = agent;
      document.querySelectorAll('[id^="agent-"]').forEach(el => el.classList.remove('active'));
      document.getElementById('agent-' + agent).classList.add('active');
      renderTable();
    }

    function setWs(ws) {
      currentWs = ws;
      renderWorkspaces();
      renderTable();
    }

    function setTag(tag) {
      currentTag = tag;
      document.querySelectorAll('.tag-pill').forEach(el => el.classList.remove('active'));
      event.target.classList.add('active');
      renderTable();
    }

    function renderTable() {
      const query = document.getElementById('search').value.toLowerCase().trim();
      const filtered = allSessions.filter(s => {
        // Tab 过滤
        if (currentTab === 'active') {
          if (s.deleted || s.archived) return false;
        } else if (currentTab === 'pinned') {
          if (s.deleted || s.archived || !s.pinned) return false;
        } else if (currentTab === 'archived') {
          if (s.deleted || !s.archived) return false;
        } else if (currentTab === 'trash') {
          if (!s.deleted) return false;
        }

        // Agent 过滤 (大小写不敏感匹配)
        if (currentAgent !== 'all') {
          const sAgentLower = (s.agent || '').toLowerCase();
          const targetLower = currentAgent.toLowerCase();
          if (targetLower === 'codebuddy') {
            if (!sAgentLower.includes('buddy')) return false;
          } else {
            if (sAgentLower !== targetLower) return false;
          }
        }
        // 工作区过滤
        if (currentWs !== 'all' && s.ws !== currentWs) return false;
        // 标签过滤
        if (currentTag !== 'all' && !s.tags.includes(currentTag)) return false;

        // 关键词搜索
        if (query) {
          const matchTitle = s.title.toLowerCase().includes(query);
          const matchSid = s.sid.toLowerCase().includes(query);
          const matchTag = s.tags.some(t => t.toLowerCase().includes(query));
          if (!matchTitle && !matchSid && !matchTag) return false;
        }
        return true;
      });

      filtered.sort((a, b) => {
        if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
        if (currentSortField === 'size') {
          const valA = a.size_bytes || 0;
          const valB = b.size_bytes || 0;
          return currentSortOrder === 'desc' ? valB - valA : valA - valB;
        } else {
          const valA = a.mtime || 0;
          const valB = b.mtime || 0;
          return currentSortOrder === 'desc' ? valB - valA : valA - valB;
        }
      });

      currentFiltered = filtered;
      document.getElementById('stats-info').innerText = `共匹配到 ${filtered.length} 条记录`;

      const tbody = document.getElementById('session-tbody');
      if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding: 40px; color: var(--text-muted);">未找到匹配的会话记录</td></tr>`;
        return;
      }

      tbody.innerHTML = filtered.map(s => {
        let agentBadge = '';
        const ag = (s.agent || '').toLowerCase();
        if (ag === 'agy') agentBadge = '<span class="badge badge-agy">agy</span>';
        else if (ag === 'qoder') agentBadge = '<span class="badge badge-qoder">qoder</span>';
        else if (ag === 'cline') agentBadge = '<span class="badge badge-cline">cline</span>';
        else agentBadge = '<span class="badge badge-cb">CodeBuddy</span>';

        const starClass = s.pinned ? 'active-star' : '';
        const tagsHtml = s.tags.map(t => `<span class="tag-item">#${t}</span>`).join('');
        const isChecked = selectedSids.has(s.sid) ? 'checked' : '';

        return `
          <tr id="row-${s.sid}">
            <td><input type="checkbox" onchange="toggleSelect('${s.sid}')" ${isChecked}></td>
            <td>${agentBadge}</td>
            <td><span class="badge badge-ws">${s.ws}</span></td>
            <td><span style="color: var(--text-muted); font-size: 12px; font-family: monospace;">${s.size_str}</span></td>
            <td>
              <div class="title-cell" id="title-container-${s.sid}">
                <span class="title-text" onclick="previewSession('${s.sid}')">${s.pinned ? '⭐ ' : ''}${escapeHtml(s.title)}</span>
                <span style="font-size: 11px; color: #58a6ff; cursor:pointer;" onclick="startEditTitle('${s.sid}')" title="修改标题">✏️</span>
                <span style="font-size: 11px; color: var(--purple); cursor:pointer;" onclick="promptAddTag('${s.sid}')" title="添加标签">🏷️</span>
                ${tagsHtml}
              </div>
            </td>
            <td style="color: var(--text-muted); font-size: 12px;">${s.ts}</td>
            <td style="text-align: right;">
              <div class="action-icons" style="justify-content: flex-end;">
                <button class="icon-btn ${starClass}" onclick="togglePin('${s.sid}', ${!s.pinned})" title="${s.pinned?'取消置顶':'置顶'}">⭐</button>
                <button class="icon-btn" onclick="toggleArchive('${s.sid}', ${!s.archived})" title="${s.archived?'取消归档':'归档'}">📦</button>
                ${s.deleted ? `
                  <button class="icon-btn" onclick="restoreSession('${s.sid}')" title="还原">↩️</button>
                  <button class="icon-btn" onclick="deleteSession('${s.sid}', true)" title="彻底粉碎" style="color:var(--danger)">🔥</button>
                ` : `
                  <button class="icon-btn" onclick="deleteSession('${s.sid}', false)" title="移入回收站">🗑️</button>
                `}
                <button class="icon-btn" onclick="previewSession('${s.sid}')" title="查看对话流">👁️</button>
              </div>
            </td>
          </tr>
        `;
      }).join('');
    }

    function escapeHtml(str) {
      return str.replace(/[&<>"']/g, m => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[m]));
    }

    let currentFiltered = [];

    function toggleSelect(sid) {
      if (selectedSids.has(sid)) selectedSids.delete(sid);
      else selectedSids.add(sid);
      updateSelectedBar();
    }

    function toggleSelectAll() {
      const chk = document.getElementById('select-all').checked;
      currentFiltered.forEach(s => {
        if (chk) selectedSids.add(s.sid);
        else selectedSids.delete(s.sid);
      });
      renderTable();
      updateSelectedBar();
    }

    function updateSelectedBar() {
      const count = selectedSids.size;
      const isTrash = currentTab === 'trash';
      document.getElementById('selected-hint').innerText = `已选 ${count} 项`;
      const btnDel = document.getElementById('btn-batch-del');
      btnDel.style.display = count > 0 ? 'inline-flex' : 'none';
      btnDel.innerText = isTrash ? `🔥 彻底粉碎选中 (${count})` : `🗑️ 批量移入回收站 (${count})`;

      const btnEmpty = document.getElementById('btn-empty-trash');
      if (btnEmpty) {
        const trashCount = parseInt(document.getElementById('count-trash').innerText || '0');
        btnEmpty.style.display = (isTrash && trashCount > 0) ? 'inline-flex' : 'none';
      }
    }

    // 操作 API
    async function startEditTitle(sid) {
      const s = allSessions.find(x => x.sid === sid);
      const container = document.getElementById(`title-container-${sid}`);
      container.innerHTML = `
        <input type="text" class="title-input" id="input-title-${sid}" value="${escapeHtml(s.title)}"
               onkeydown="if(event.key==='Enter') saveTitle('${sid}'); if(event.key==='Escape') renderTable();">
        <button class="btn btn-primary" style="padding: 2px 8px; font-size: 11px;" onclick="saveTitle('${sid}')">保存</button>
      `;
      document.getElementById(`input-title-${sid}`).focus();
    }

    async function saveTitle(sid) {
      const newTitle = document.getElementById(`input-title-${sid}`).value.trim();
      if (!newTitle) return;
      await fetch('/api/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: sid, title: newTitle })
      });
      fetchData();
    }

    async function promptAddTag(sid) {
      const tag = prompt("请输入要添加的标签名称:");
      if (!tag || !tag.trim()) return;
      await fetch('/api/tag', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: sid, action: 'add', tags: [tag.trim()] })
      });
      fetchData();
    }

    async function togglePin(sid, state) {
      await fetch('/api/pin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: sid, state })
      });
      fetchData();
    }

    async function toggleArchive(sid, state) {
      await fetch('/api/archive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: sid, state })
      });
      fetchData();
    }

    async function deleteSession(sid, force) {
      const confirmMsg = force ? "确定彻底物理粉碎该会话底层文件吗？不可撤销！" : "移入回收站？";
      if (!confirm(confirmMsg)) return;
      await fetch('/api/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: sid, force })
      });
      selectedSids.delete(sid);
      fetchData();
    }

    async function batchDelete() {
      const isTrash = currentTab === 'trash';
      const confirmMsg = isTrash
        ? `【高危操作】确定彻底物理粉碎选中的 ${selectedSids.size} 个会话吗？底层数据库/JSONL 将被彻底抹除且不可恢复！`
        : `确定将选中的 ${selectedSids.size} 个会话移入回收站吗？`;
      if (!confirm(confirmMsg)) return;
      await fetch('/api/batch-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: Array.from(selectedSids), force: isTrash })
      });
      selectedSids.clear();
      if (document.getElementById('select-all')) document.getElementById('select-all').checked = false;
      updateSelectedBar();
      fetchData();
    }

    async function emptyAllTrash() {
      if (!confirm("【高危操作】确定清空回收站中所有的会话吗？所有放入回收站的文件将被彻底物理粉碎！")) return;
      const res = await fetch('/api/empty-trash', { method: 'POST' });
      const data = await res.json();
      alert(`回收站已清空！共物理销毁 ${data.cleaned} 个会话。`);
      selectedSids.clear();
      if (document.getElementById('select-all')) document.getElementById('select-all').checked = false;
      fetchData();
    }

    async function restoreSession(sid) {
      await fetch('/api/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: sid })
      });
      fetchData();
    }

    async function cleanEmpty() {
      if (!confirm("一键扫描并清理所有 0 步空会话与控制指令？")) return;
      const res = await fetch('/api/clean-empty', { method: 'POST' });
      const data = await res.json();
      alert(`清理完成！共清理 ${data.cleaned_count} 个垃圾会话。`);
      fetchData();
    }

    async function previewSession(sid) {
      const s = allSessions.find(x => x.sid === sid);
      currentDrawerSid = s.sid;
      currentDrawerAgent = s.agent;
      currentDrawerWs = s.ws;

      document.getElementById('drawer-title').innerText = s.title;
      document.getElementById('drawer-meta').innerText = `Agent: [${s.agent}] | 工作空间: ${s.ws} | 体积: ${s.size_str} | ID: ${s.sid}`;
      document.getElementById('drawer-content').innerText = "正在读取详情与对话流...";
      document.getElementById('drawer').classList.add('open');

      const res = await fetch(`/api/detail?id=${sid}`);
      const text = await res.text();
      document.getElementById('drawer-content').innerText = text;
    }

    function closeDrawer() {
      document.getElementById('drawer').classList.remove('open');
    }

    function copyResumeCmd() {
      if (!currentDrawerSid) return;
      let cmd = '';
      if (currentDrawerAgent === 'qoder') {
        cmd = currentDrawerWs !== 'root' ? `qoder --cwd /root/${currentDrawerWs} -r ${currentDrawerSid}` : `qoder -r ${currentDrawerSid}`;
      } else if (currentDrawerAgent === 'codebuddy') {
        cmd = `codebuddy -r ${currentDrawerSid}`;
      } else if (currentDrawerAgent === 'agy') {
        cmd = `agy --conversation ${currentDrawerSid}`;
      } else if (currentDrawerAgent === 'cline') {
        cmd = `cline --id ${currentDrawerSid} -i`;
      }
      navigator.clipboard.writeText(cmd).then(() => alert(`接管命令已复制到剪贴板:\n${cmd}`));
    }

    fetchData();
  </script>
</body>
</html>
"""


class LanePanelHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
            return

        if path == "/api/sessions":
            lv, mgr = get_modules()
            sessions, tags = get_all_sessions_data(lv, mgr)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"sessions": sessions, "tags": tags}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/detail":
            qs = urllib.parse.parse_qs(parsed.query)
            sid = qs.get("id", qs.get("sid", [""]))[0]
            detail_text = get_session_detail_text(sid)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(detail_text.encode("utf-8"))
            return

        # ---- Skill 管理与 Agent.md 管理 GET 路由 ----
        if path == "/api/agents":
            self.send_json({"ok": True, "agents": get_agent_meta()})
            return

        if path == "/api/skills":
            qs = urllib.parse.parse_qs(parsed.query)
            agent = qs.get("agent", [""])[0]
            self.send_json(get_skills_list(agent))
            return

        if path == "/api/skill-file":
            qs = urllib.parse.parse_qs(parsed.query)
            agent = qs.get("agent", [""])[0]
            rel = qs.get("path", [""])[0]
            self.send_json(read_skill_file(agent, rel))
            return

        if path == "/api/agentmd":
            qs = urllib.parse.parse_qs(parsed.query)
            agent = qs.get("agent", [""])[0]
            self.send_json(get_agent_md(agent))
            return

        # ---- Memory（记忆）管理 GET 路由 ----
        if path == "/api/memories":
            self.send_json({"ok": True, "projects": get_memory_projects()})
            return

        if path == "/api/memory-files":
            qs = urllib.parse.parse_qs(parsed.query)
            ws = qs.get("ws", [""])[0]
            mdir = os.path.join(CB_PROJECTS_ROOT, ws, "memory")
            if not os.path.isdir(mdir):
                self.send_json({"ok": False, "error": f"项目 {ws} 无 memory 目录"})
                return
            self.send_json({"ok": True, "ws": ws, "files": _list_memory_files(mdir)})
            return

        if path == "/api/memory-file":
            qs = urllib.parse.parse_qs(parsed.query)
            ws = qs.get("ws", [""])[0]
            rel = qs.get("path", [""])[0]
            self.send_json(read_memory_file(ws, rel))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        lv, mgr = get_modules()

        if path == "/api/rename":
            mgr.rename_session(data.get("id"), data.get("title", ""))
            self.send_json({"ok": True})
            return

        if path == "/api/tag":
            action = data.get("action")
            tags = data.get("tags", [])
            if action == "add":
                mgr.tag_session(data.get("id"), add_tags=tags)
            elif action == "rm":
                mgr.tag_session(data.get("id"), rm_tags=tags)
            self.send_json({"ok": True})
            return

        if path == "/api/pin":
            mgr.pin_session(data.get("id"), state=data.get("state", True))
            self.send_json({"ok": True})
            return

        if path == "/api/archive":
            mgr.archive_session(data.get("id"), state=data.get("state", True))
            self.send_json({"ok": True})
            return

        if path == "/api/delete":
            mgr.delete_session(data.get("id"), force=data.get("force", False))
            self.send_json({"ok": True})
            return

        if path == "/api/batch-delete":
            ids = data.get("ids", [])
            force = data.get("force", False)
            for sid in ids:
                mgr.delete_session(sid, force=force)
            self.send_json({"ok": True, "count": len(ids)})
            return

        if path == "/api/restore":
            mgr.restore_session(data.get("id"))
            self.send_json({"ok": True})
            return

        if path == "/api/clean-empty":
            cleaned = mgr.clean_empty_sessions(force=False)
            self.send_json({"ok": True, "cleaned_count": cleaned})
            return

        if path == "/api/empty-trash":
            cleaned = mgr.clean_trash_sessions(dry_run=False)
            self.send_json({"ok": True, "cleaned": cleaned})
            return

        # ---- Skill 管理与 Agent.md 管理 POST 路由 ----
        if path == "/api/save-skill-file":
            agent = data.get("agent", "")
            rel = data.get("path", "")
            content = data.get("content", "")
            self.send_json(save_skill_file(agent, rel, content))
            return

        if path == "/api/save-agentmd":
            agent = data.get("agent", "")
            content = data.get("content", "")
            self.send_json(save_agent_md(agent, content))
            return

        if path == "/api/save-memory-file":
            ws = data.get("ws", "")
            rel = data.get("path", "")
            content = data.get("content", "")
            self.send_json(save_memory_file(ws, rel, content))
            return

        self.send_response(404)
        self.end_headers()

    def send_json(self, obj):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def log_message(self, format, *args):
        # 静默控制台日志，保持界面干净
        pass


def main():
    p = argparse.ArgumentParser(description="Lane 泳道控制面板服务")
    p.add_argument("--port", "-p", type=int, default=PORT, help=f"监听端口 (默认 {PORT})")
    args = p.parse_args()

    HTTPServer.allow_reuse_address = True
    server = HTTPServer(("0.0.0.0", args.port), LanePanelHandler)
    print(f"============================================================")
    print(f"🏊‍♂️ Lane Studio 控制面板已启动！")
    print(f"👉 本地访问地址: http://localhost:{args.port}")
    print(f"👉 Windows 访问: http://127.0.0.1:{args.port} (WSL mirrored网络)")
    print(f"============================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] 控制面板已退出。")


if __name__ == "__main__":
    main()
