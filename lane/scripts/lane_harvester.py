#!/usr/bin/env python3
"""Lane Card Harvester & Summarizer (P3a)

负责：
1. 独立数据库 /root/.agent/lane-summary.db 初始化与维护；
2. 收割会话中由 Agent 主动产出的 <!-- LANE_CARD: {...} --> 标记块；
3. 纯规则 4 域实体提取 (HOST / MODEL / TOOL / ENV)；
4. 存量会话与无标记会话的惰性 rule_fallback 兜底生成；
5. 遵循 append-only 规范，同一会话支持多个里程碑节点。
"""

import os
import sys
import json
import glob
import re
import sqlite3
import time

DB_PATH = "/root/.agent/lane-summary.db"

HOST_PATTERNS = [
    re.compile(r'\b(146|76|105|test03|WSL)\b', re.IGNORECASE),
    re.compile(r'\b192\.2\.\d+\.\d+\b')
]
MODEL_PATTERNS = [
    re.compile(r'\b(DeepSeek(?:-[A-Za-z0-9.]+)*|Qwen(?:-[A-Za-z0-9.]+)*|MiniMax(?:-[A-Za-z0-9.]+)*|GLM(?:-[A-Za-z0-9.]+)*|0731|W8A8)\b', re.IGNORECASE)
]
TOOL_PATTERNS = [
    re.compile(r'\b(zig|zml|bazel|vllm|sglang|docker|herdr|lane|xssh|sqlite3|git|pnpm|cargo|pip)\b', re.IGNORECASE)
]
ENV_PATTERNS = [
    re.compile(r'\b(maca|cuda|rocm|metax|ubuntu|linux)\b', re.IGNORECASE)
]

LANE_CARD_REGEX = re.compile(r'<!--\s*LANE_CARD:\s*(\{.*?\})\s*-->', re.DOTALL)


def init_db(db_path=DB_PATH):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = sqlite3.connect(db_path, timeout=10.0)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS lane_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        agent TEXT NOT NULL,
        ws TEXT NOT NULL,
        mtime INTEGER NOT NULL,
        action TEXT NOT NULL,
        conclusion TEXT NOT NULL,
        status TEXT NOT NULL,
        entities_json TEXT NOT NULL,
        card_source TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lane_cards_sid ON lane_cards(session_id, mtime);")
    con.commit()
    return con


def extract_entities(text):
    entities = []
    if not text:
        return entities
    for p in HOST_PATTERNS:
        for m in p.findall(text):
            val = m.upper() if m.lower() in ('wsl', 'test03') else m
            if val not in entities:
                entities.append(val)
    for p in MODEL_PATTERNS:
        for m in p.findall(text):
            if m not in entities:
                entities.append(m)
    for p in TOOL_PATTERNS:
        for m in p.findall(text):
            val = m.lower()
            if val not in entities:
                entities.append(val)
    for p in ENV_PATTERNS:
        for m in p.findall(text):
            val = m.lower()
            if val not in entities:
                entities.append(val)
    return entities


def clean_text_snippet(text, max_len=60):
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'[\r\n\t]+', ' ', text).strip()
    if len(text) > max_len:
        return text[:max_len] + "…"
    return text


def harvest_from_transcript_text(text, sid, agent, ws, mtime, con):
    """从会话文本中提取所有 <!-- LANE_CARD: ... --> 标记块并 append 入库"""
    cur = con.cursor()
    matches = LANE_CARD_REGEX.findall(text)
    inserted = 0
    now_ts = int(time.time())

    for raw_json in matches:
        data = None
        try:
            data = json.loads(raw_json)
        except Exception:
            try:
                unescaped = raw_json.replace(r'\"', '"').replace(r'\n', '\n').replace(r'\t', '\t').replace(r'\\', '\\')
                data = json.loads(unescaped)
            except Exception:
                continue

        if not isinstance(data, dict):
            continue

        try:
            action = clean_text_snippet(data.get("action", ""))
            conclusion = clean_text_snippet(data.get("conclusion", ""))
            status = data.get("status", "done").lower()
            if status not in ("done", "ongoing", "aborted"):
                status = "done"

            entities = data.get("entities")
            if not isinstance(entities, list) or not entities:
                entities = extract_entities(action + " " + conclusion)

            # 查重防重复添加相同的卡片内容
            cur.execute("""
                SELECT id FROM lane_cards 
                WHERE session_id = ? AND action = ? AND conclusion = ?
            """, (sid, action, conclusion))
            if cur.fetchone():
                continue

            cur.execute("""
                INSERT INTO lane_cards 
                (session_id, agent, ws, mtime, action, conclusion, status, entities_json, card_source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'agent_self', ?)
            """, (sid, agent, ws, int(mtime), action, conclusion, status, json.dumps(entities, ensure_ascii=False), now_ts))
            inserted += 1
        except Exception:
            continue

    if inserted > 0:
        con.commit()
    return inserted


def create_fallback_card(sid, agent, ws, mtime, first_user, last_asst, con):
    """兜底生成卡片"""
    cur = con.cursor()
    cur.execute("SELECT id FROM lane_cards WHERE session_id = ?", (sid,))
    if cur.fetchone():
        return False  # 已有卡片，不覆盖

    action = clean_text_snippet(first_user or "(无输入)", 40)
    conclusion = clean_text_snippet(last_asst or "(进行中/无总结)", 50)
    
    # 状态推断
    status = "done"
    sample = (first_user or "") + " " + (last_asst or "")
    sample_low = sample.lower()
    if any(w in sample for w in ("完成", "通过", "已修复", "成功", "释放", "已上线", "已解决")):
        status = "done"
    elif any(w in sample_low for w in ("error", "fail", "中断", "报错", "崩溃", "panic")):
        status = "aborted"
    elif not last_asst:
        status = "ongoing"

    entities = extract_entities(sample)
    now_ts = int(time.time())

    cur.execute("""
        INSERT INTO lane_cards 
        (session_id, agent, ws, mtime, action, conclusion, status, entities_json, card_source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'rule_fallback', ?)
    """, (sid, agent, ws, int(mtime), action, conclusion, status, json.dumps(entities, ensure_ascii=False), now_ts))
    con.commit()
    return True


def scan_and_harvest(incremental=True, backfill=False):
    """扫描所有会话文件，harvest 自述卡片或兜底卡片"""
    con = init_db()
    total_processed = 0
    cards_added = 0

    # 1. 扫描 AGY (brain/*/logs/transcript.jsonl)
    agy_brains = glob.glob("/root/.gemini/antigravity-cli/brain/*/.system_generated/logs/transcript.jsonl")
    for trans_path in agy_brains:
        total_processed += 1
        sid = trans_path.split("/")[-4]
        try:
            mtime = int(os.path.getmtime(trans_path) * 1000)
            with open(trans_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # 先尝试抽取 LANE_CARD
            added = harvest_from_transcript_text(content, sid, "agy", "metax-workbench", mtime, con)
            if added > 0:
                cards_added += added
            elif backfill:
                # 兜底抽取
                lines = [l for l in content.split("\n") if len(l) > 10]
                first_user, last_asst = "", ""
                for l in lines:
                    if '"role":"user"' in l or '"type":"USER_INPUT"' in l:
                        m = re.search(r'"text":"(.*?)"', l) or re.search(r'"content":"(.*?)"', l)
                        if m and not first_user:
                            first_user = m.group(1)
                    if '"role":"assistant"' in l or '"type":"PLANNER_RESPONSE"' in l:
                        m = re.search(r'"text":"(.*?)"', l) or re.search(r'"content":"(.*?)"', l)
                        if m:
                            last_asst = m.group(1)
                if create_fallback_card(sid, "agy", "metax-workbench", mtime, first_user, last_asst, con):
                    cards_added += 1
        except Exception:
            continue

    # 2. 扫描 CodeBuddy (/root/.codebuddy/projects/*/*.jsonl)
    cb_files = glob.glob("/root/.codebuddy/projects/*/*.jsonl")
    for fpath in cb_files:
        total_processed += 1
        parts = fpath.split("/")
        ws = parts[-2]
        fname = parts[-1]
        sid = fname[:-6] if fname.endswith(".jsonl") else fname
        try:
            mtime = int(os.path.getmtime(fpath) * 1000)
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            added = harvest_from_transcript_text(content, sid, "codebuddy", ws, mtime, con)
            if added > 0:
                cards_added += added
            elif backfill:
                lines = [l for l in content.split("\n") if len(l) > 10]
                first_user, last_asst = "", ""
                for l in lines:
                    if '"role":"user"' in l or '"type":"user"' in l:
                        m = re.search(r'"text":"(.*?)"', l) or re.search(r'"content":"(.*?)"', l)
                        if m and not first_user:
                            first_user = m.group(1)
                    if '"role":"assistant"' in l or '"type":"assistant"' in l:
                        m = re.search(r'"text":"(.*?)"', l) or re.search(r'"content":"(.*?)"', l)
                        if m:
                            last_asst = m.group(1)
                if create_fallback_card(sid, "codebuddy", ws, mtime, first_user, last_asst, con):
                    cards_added += 1
        except Exception:
            continue

    # 3. 扫描 Qoder (/root/.qoder/projects/*/*.jsonl)
    qoder_files = glob.glob("/root/.qoder/projects/*/*.jsonl")
    for fpath in qoder_files:
        total_processed += 1
        parts = fpath.split("/")
        ws = parts[-2]
        fname = parts[-1]
        sid = fname[:-6] if fname.endswith(".jsonl") else fname
        try:
            mtime = int(os.path.getmtime(fpath) * 1000)
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            added = harvest_from_transcript_text(content, sid, "qoder", ws, mtime, con)
            if added > 0:
                cards_added += added
            elif backfill:
                lines = [l for l in content.split("\n") if len(l) > 10]
                first_user, last_asst = "", ""
                for l in lines:
                    if '"role":"user"' in l:
                        m = re.search(r'"text":"(.*?)"', l) or re.search(r'"content":"(.*?)"', l)
                        if m and not first_user:
                            first_user = m.group(1)
                    if '"role":"assistant"' in l:
                        m = re.search(r'"text":"(.*?)"', l) or re.search(r'"content":"(.*?)"', l)
                        if m:
                            last_asst = m.group(1)
                if create_fallback_card(sid, "qoder", ws, mtime, first_user, last_asst, con):
                    cards_added += 1
        except Exception:
            continue

    # 4. 扫描 Reasonix (/root/.reasonix/projects/*/sessions/*.json)
    rea_files = glob.glob("/root/.reasonix/projects/*/sessions/*.json")
    for fpath in rea_files:
        total_processed += 1
        parts = fpath.split("/")
        ws = parts[-3]
        fname = parts[-1]
        sid = fname[:-5] if fname.endswith(".json") else fname
        try:
            mtime = int(os.path.getmtime(fpath) * 1000)
            if backfill:
                create_fallback_card(sid, "reasonix", ws, mtime, "(Reasonix 任务)", "(已归档)", con)
        except Exception:
            continue

    con.close()
    return total_processed, cards_added


def harvest_recent(max_age_sec=300):
    """极速增量收割：仅检查近 5 分钟内物理文件发生变化的会话，提取 LANE_CARD 并写入"""
    con = init_db()
    now_ts = time.time()
    min_mtime = now_ts - max_age_sec
    added = 0

    # 检查 AGY 正在进行的会话
    agy_candidates = glob.glob("/root/.gemini/antigravity-cli/brain/*/.system_generated/logs/transcript.jsonl")
    for trans_path in agy_candidates:
        try:
            st = os.stat(trans_path)
            if st.st_mtime >= min_mtime:
                sid = trans_path.split("/")[-4]
                with open(trans_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                added += harvest_from_transcript_text(content, sid, "agy", "metax-workbench", int(st.st_mtime * 1000), con)
        except Exception:
            continue

    # 检查 CodeBuddy 正在进行的会话
    cb_candidates = glob.glob("/root/.codebuddy/projects/*/*.jsonl")
    for fpath in cb_candidates:
        try:
            st = os.stat(fpath)
            if st.st_mtime >= min_mtime:
                parts = fpath.split("/")
                ws = parts[-2]
                sid = parts[-1][:-6]
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                added += harvest_from_transcript_text(content, sid, "codebuddy", ws, int(st.st_mtime * 1000), con)
        except Exception:
            continue

    # 检查 Qoder 正在进行的会话
    qoder_candidates = glob.glob("/root/.qoder/projects/*/*.jsonl")
    for fpath in qoder_candidates:
        try:
            st = os.stat(fpath)
            if st.st_mtime >= min_mtime:
                parts = fpath.split("/")
                ws = parts[-2]
                sid = parts[-1][:-6]
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                added += harvest_from_transcript_text(content, sid, "qoder", ws, int(st.st_mtime * 1000), con)
        except Exception:
            continue

    con.close()
    return added


if __name__ == "__main__":
    is_backfill = "--backfill" in sys.argv
    t0 = time.time()
    if is_backfill:
        tot, added = scan_and_harvest(backfill=True)
        dt = (time.time() - t0) * 1000
        print(f"✔ Harvester: 扫描 {tot} 个会话, 产出/收割 {added} 张卡片 | 耗时: {dt:.1f}ms")
    else:
        added = harvest_recent()
        dt = (time.time() - t0) * 1000
        print(f"✔ Quick Harvester: 增量收割 {added} 张卡片 | 耗时: {dt:.1f}ms")
