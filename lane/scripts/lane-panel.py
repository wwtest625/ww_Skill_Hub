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
    meta = mgr.load_meta()

    raw_sessions = []
    raw_sessions.extend(lv.get_agy_sessions(agy_mod, show_all=True))
    raw_sessions.extend(lv.get_qoder_sessions(qoder_mod, show_all=True))
    raw_sessions.extend(lv.get_cb_sessions(cb_mod, show_all=True))

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

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return proc.stdout or proc.stderr or "暂无详细对话记录"
    except Exception as e:
        return f"读取详情异常: {e}"


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
    <div class="header-actions">
      <input type="text" id="search" class="search-box" placeholder="搜索会话标题、ID、关键词..." oninput="renderTable()">
      <button class="btn btn-primary" onclick="cleanEmpty()">🧹 一键清理空会话</button>
      <button class="btn" onclick="fetchData()">🔄 刷新</button>
    </div>
  </header>

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
        <div class="nav-item active" onclick="setAgent('all')" id="agent-all">全部 Agent</div>
        <div class="nav-item" onclick="setAgent('agy')" id="agent-agy">agy (架构研发)</div>
        <div class="nav-item" onclick="setAgent('qoder')" id="agent-qoder">qoder (前端交互)</div>
        <div class="nav-item" onclick="setAgent('codebuddy')" id="agent-codebuddy">CodeBuddy (审查归档)</div>
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
              <th width="85">体积</th>
              <th>标题 / 主题</th>
              <th width="150">修改时间</th>
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
      allSessions.forEach(s => {
        if (s.deleted) trash++;
        else if (s.archived) archived++;
        else {
          active++;
          if (s.pinned) pinned++;
        }
      });
      document.getElementById('count-active').innerText = active;
      document.getElementById('count-pinned').innerText = pinned;
      document.getElementById('count-archived').innerText = archived;
      document.getElementById('count-trash').innerText = trash;
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

        // Agent 过滤
        if (currentAgent !== 'all' && s.agent !== currentAgent) return false;
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

      document.getElementById('stats-info').innerText = `共匹配到 ${filtered.length} 条记录`;

      const tbody = document.getElementById('session-tbody');
      if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding: 40px; color: var(--text-muted);">未找到匹配的会话记录</td></tr>`;
        return;
      }

      tbody.innerHTML = filtered.map(s => {
        let agentBadge = '';
        if (s.agent === 'agy') agentBadge = '<span class="badge badge-agy">agy</span>';
        else if (s.agent === 'qoder') agentBadge = '<span class="badge badge-qoder">qoder</span>';
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

    function toggleSelect(sid) {
      if (selectedSids.has(sid)) selectedSids.delete(sid);
      else selectedSids.add(sid);
      updateSelectedBar();
    }

    function toggleSelectAll() {
      const chk = document.getElementById('select-all').checked;
      allSessions.forEach(s => {
        if (chk) selectedSids.add(s.sid);
        else selectedSids.delete(s.sid);
      });
      renderTable();
      updateSelectedBar();
    }

    function updateSelectedBar() {
      const count = selectedSids.size;
      document.getElementById('selected-hint').innerText = `已选 ${count} 项`;
      document.getElementById('btn-batch-del').style.display = count > 0 ? 'inline-flex' : 'none';
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
      const confirmMsg = force ? "确定彻底粉碎该会话底层文件吗？不可撤销！" : "移入回收站？";
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
      if (!confirm(`确定将选中的 ${selectedSids.size} 个会话移入回收站吗？`)) return;
      await fetch('/api/batch-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: Array.from(selectedSids), force: false })
      });
      selectedSids.clear();
      updateSelectedBar();
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
            sid = qs.get("id", [""])[0]
            detail_text = get_session_detail_text(sid)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(detail_text.encode("utf-8"))
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
