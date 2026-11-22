#!/usr/bin/env python3
"""Lane Jev —— 跨 Agent 会话价值秒级打标 (System One / 单步 Prefill Logprobs)

两级漏斗:
  L1 规则硬过滤 (0ms): 空会话/纯 /clear/超微型 → low，秒定
  L2 Jev Prefill (10~30ms): 特征抽取 → 146 vLLM Qwen3.8-27B 单步 Logprobs
     三档概率 P(high)/P(medium)/P(low)，Temperature Scaling 校准 (T=1.35)

结果回写 /root/.agent/lane-meta.json:
  entry["jev_level"] = high|medium|low
  entry["jev_prob"]  = 0.87 (档位置信度)
  entry["jev_at"]    = "2026-09-20 18:10:00"

用法:
  lane jev [--dry-run] [--limit N] [--rescore] [--agent agy|codebuddy|qoder|reasonix]
  lane jev --list [--level high|medium|low]      # lane -l high 同义
"""
import os
import re
import sys
import json
import math
import glob
import time
import argparse
import sqlite3
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# ───────── 常量 ─────────
META_FILE = "/root/.agent/lane-meta.json"
# agy 两套数据目录（结构一致）：桌面 IDE 版 + CLI 版
AGY_DATA_DIRS = ["/root/.gemini/antigravity", "/root/.gemini/antigravity-cli"]

SUMMARY_DB = "/root/.agent/lane-summary.db"
VLLM_URL = os.environ.get("VLLM_URL", "http://192.2.0.146:8000/v1")
MODEL_NAME = os.environ.get("MODEL_NAME", "/data/model/Qwen3.8-27B")

LEVEL_ICON = {"high": "⭐", "medium": "📝", "low": "🗑️"}
LEVEL_TAG = {"high": "#high", "medium": "#medium", "low": "#low"}

SYS_PROMPT = (
    "You are a session value classifier (System One). "
    "Given cross-agent CLI session features, judge its value into exactly three levels: "
    "A=high (concrete code delivery, core bug fixes, architecture refactors, full technical decisions, milestones), "
    "B=medium (normal technical Q&A, config tweaks, lightweight checks, regular completed work), "
    "C=low (empty runs, greetings/probes, pure /clear, sessions that died immediately, zero useful output). "
    "Answer with the single best option letter."
)

# 固化公共前缀，最大化 146 节点 vLLM Prefix Cache 命中
PREFIX_TMPL = (
    f"<|im_start|>system\n{SYS_PROMPT}<|im_end|>\n"
    "<|im_start|>user\n[Session Features]\n"
)
SUFFIX_TMPL = (
    "\n\n[Question: value_level]\nWhich value level is this session?\n\n"
    "Options:\nA. high\nB. medium\nC. low<|im_end|>\n"
    "<|im_start|>assistant\n<think>\n</think>\nBest Option: "
)

SESSION = requests.Session()


# ───────── 会话枚举 (与 lane_harvester 同源四路) ─────────
def enumerate_sessions(agent_filter=None):
    """返回 [{sid, agent, ws, path, size, mtime}]，排除 meta 中已软删除的会话"""
    meta = load_meta()
    out = []

    def add(agent, sid, ws, path):
        if agent_filter and agent != agent_filter:
            return
        ent = meta.get(sid) or {}
        if ent.get("deleted"):
            return
        try:
            st = os.stat(path)
        except OSError:
            return
        out.append({
            "sid": sid, "agent": agent, "ws": ws, "path": path,
            "size": st.st_size, "mtime": int(st.st_mtime * 1000),
        })

    for agy_root in AGY_DATA_DIRS:  # CLI 版 + 桌面 IDE 版
        for p in glob.glob(f"{agy_root}/brain/*/.system_generated/logs/transcript.jsonl"):
            add("agy", p.split("/")[-4], "metax-workbench", p)
    for p in glob.glob("/root/.codebuddy/projects/*/*.jsonl"):
        parts = p.split("/")
        add("codebuddy", parts[-1][:-6], parts[-2], p)
    for p in glob.glob("/root/.qoder/projects/*/*.jsonl"):
        parts = p.split("/")
        add("qoder", parts[-1][:-6], parts[-2], p)
    for p in glob.glob("/root/.reasonix/projects/*/sessions/*.json"):
        parts = p.split("/")
        add("reasonix", parts[-1][:-5], parts[-3], p)

    # 同 SID 去重 (会话文件可能软链/复制到多个 project 目录)：保留 mtime 最新
    uniq = {}
    for s in out:
        prev = uniq.get(s["sid"])
        if prev is None or s["mtime"] > prev["mtime"]:
            uniq[s["sid"]] = s
    result = list(uniq.values())
    result.sort(key=lambda s: -s["mtime"])
    return result


# ───────── 特征抽取 ─────────
_TEXT_KEYS = ("text", "content")

# 系统注入噪音前缀 (codebuddy user 行常被 system-reminder/command 包装，非真实用户输入)
_NOISE_PREFIXES = (
    "<system-reminder", "<command-name>", "<command-message>",
    "<local-command-stdout>", "Caveat: the messages below",
)
_USER_WRAPPER_PAT = re.compile(r"<(?:user_query|USER_REQUEST)>([\s\S]*?)(?:</user_query>|</USER_REQUEST>)")

def _real_user_text(txt):
    """剥离系统包装，提取真实用户输入；纯噪音返回空串"""
    if not txt:
        return ""
    m = _USER_WRAPPER_PAT.search(txt)
    if m:
        txt = m.group(1).strip()
    if not txt:
        return ""
    low = txt.lstrip()[:64].lower()
    if low.startswith(tuple(p.lower() for p in _NOISE_PREFIXES)):
        return ""
    return txt

def _dig_text(obj, depth=0):
    """递归提取对象中的可读文本 (content 数组/字符串/dict 均兼容)"""
    if depth > 4 or obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        return " ".join(t for t in (_dig_text(x, depth + 1) for x in obj) if t)
    if isinstance(obj, dict):
        for k in _TEXT_KEYS:
            if k in obj and isinstance(obj[k], (str, list)):
                t = _dig_text(obj[k], depth + 1)
                if t.strip():
                    return t
        msg = obj.get("message")
        if isinstance(msg, dict):
            return _dig_text(msg, depth + 1)
    return ""


def extract_features(sess):
    """读取 transcript，提取 user_turns / first_user / last_asst / has_card"""
    path, size = sess["path"], sess["size"]
    first_user, last_asst, user_turns = "", "", 0
    has_card = False
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            if sess["agent"] == "reasonix":
                try:
                    data = json.load(f)
                except Exception:
                    data = None
                msgs = data.get("messages", []) if isinstance(data, dict) else []
                for m in msgs:
                    role = m.get("role", "")
                    txt = _dig_text(m.get("content") or m.get("text") or "")
                    txt = _real_user_text(txt) if role == "user" else txt
                    if role == "user" and txt.strip():
                        user_turns += 1
                        if not first_user:
                            first_user = txt
                    elif role == "assistant" and txt.strip():
                        last_asst = txt
            else:
                for line in f:
                    if "LANE_CARD:" in line:
                        has_card = True
                    if len(line) < 20:
                        continue
                    obj = None
                    try:
                        obj = json.loads(line)
                    except Exception:
                        if '"role":"user"' in line or '"type":"USER_INPUT"' in line or '"type":"user"' in line:
                            m = re.search(r'"text":"(.*?)"', line) or re.search(r'"content":"(.*?)"', line)
                            if m:
                                real = _real_user_text(m.group(1))
                                if real:
                                    user_turns += 1
                                    if not first_user:
                                        first_user = real
                        elif '"role":"assistant"' in line or '"type":"PLANNER_RESPONSE"' in line:
                            m = re.search(r'"text":"(.*?)"', line) or re.search(r'"content":"(.*?)"', line)
                            if m:
                                last_asst = m.group(1)
                        continue
                    if not isinstance(obj, dict):
                        continue
                    typ = str(obj.get("type", "")).lower()
                    role = str(obj.get("role", "")).lower()
                    is_user = role == "user" or typ in ("user", "user_input")
                    is_asst = role == "assistant" or typ in ("assistant", "planner_response", "ai_message")
                    if not (is_user or is_asst):
                        msg = obj.get("message")
                        if isinstance(msg, dict):
                            r = str(msg.get("role", "")).lower()
                            is_user = r == "user"
                            is_asst = r == "assistant"
                            if is_user or is_asst:
                                txt = _dig_text(msg)
                            else:
                                continue
                        else:
                            continue
                    else:
                        txt = _dig_text(obj)
                    txt = txt.strip()
                    if not txt:
                        continue
                    if is_user:
                        real = _real_user_text(txt)
                        if not real:
                            continue
                        user_turns += 1
                        if not first_user:
                            first_user = real
                    elif is_asst:
                        last_asst = txt
    except OSError:
        pass
    return {
        "user_turns": user_turns,
        "first_user": first_user[:600],
        "last_asst": last_asst[:400],
        "has_card": has_card,
        "size": size,
    }


# ───────── L1: 规则硬过滤 ─────────
_CLEAR_PAT = re.compile(r"^\s*(/clear|<command-name>/clear|clear|hi|hello|test|测试|你好|在吗)[\s\S]{0,40}$", re.I)

def rule_level(feat):
    """返回 level 或 None (需进入 L2)"""
    if feat["size"] < 1024:
        return "low"
    if feat["user_turns"] == 0:
        return "low"
    if feat["user_turns"] <= 1 and not feat["has_card"] and not feat["last_asst"]:
        return "low"
    fu = feat["first_user"].strip()
    if feat["user_turns"] <= 1 and fu and _CLEAR_PAT.match(fu) and not feat["has_card"]:
        return "low"
    return None


# ───────── L2: Jev 单步 Prefill ─────────
def build_features_block(sess, feat):
    lines = [
        f"Agent: {sess['agent']}",
        f"Workspace: {sess['ws']}",
        f"Size: {feat['size']} bytes",
        f"User Turns: {feat['user_turns']}",
        f"Has Milestone Card: {'yes' if feat['has_card'] else 'no'}",
    ]
    if feat["first_user"]:
        lines.append(f"First User Message: {feat['first_user']}")
    if feat["last_asst"]:
        lines.append(f"Last Assistant Conclusion: {feat['last_asst']}")
    return "\n".join(lines)


def jev_classify(context_block):
    """单步 Prefill 返回 (level, prob)；失败返回 (None, 0.0)"""
    prompt = PREFIX_TMPL + context_block + SUFFIX_TMPL
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "max_tokens": 1,
        "temperature": 0.0,
        "logprobs": 10,
    }
    try:
        resp = SESSION.post(f"{VLLM_URL}/completions", json=payload, timeout=12)
        if resp.status_code != 200:
            return None, 0.0
        raw_lps = resp.json()["choices"][0]["logprobs"]["top_logprobs"][0]
    except Exception:
        return None, 0.0

    top = {}
    for k, v in raw_lps.items():
        k = k.strip()
        if k and (k not in top or v > top[k]):
            top[k] = v

    # 选项字母 + 别名增强
    aliases = [
        ["A", "High", "HIGH", "高"],
        ["B", "Medium", "MEDIUM", "中"],
        ["C", "Low", "LOW", "低"],
    ]
    raw = []
    for group in aliases:
        best = top.get(group[0], -20.0)
        for a in group[1:]:
            if a in top and top[a] > best:
                best = top[a]
        raw.append(best)

    T = 1.35  # Temperature Scaling 校准 (与 jev_gateway 同参)
    scaled = [s / T for s in raw]
    mx = max(scaled)
    exps = [math.exp(s - mx) for s in scaled]
    probs = [e / sum(exps) for e in exps]
    idx = probs.index(max(probs))
    return ("high", "medium", "low")[idx], round(probs[idx], 4)


# ───────── meta 读写 (与 agent-manager 原子写同款) ─────────
def load_meta():
    if not os.path.exists(META_FILE):
        return {}
    try:
        with open(META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_meta(meta):
    tmp = META_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    os.replace(tmp, META_FILE)


# ───────── 主流程 ─────────
def cmd_score(args):
    t0 = time.perf_counter()
    sessions = enumerate_sessions(args.agent)
    meta = load_meta()

    todo = []
    for s in sessions:
        ent = meta.get(s["sid"]) or {}
        if not args.rescore and ent.get("jev_level"):
            continue
        todo.append(s)
    if args.limit and args.limit > 0:
        todo = todo[:args.limit]

    total = len(sessions)
    print(f"🔎 Jev 价值打标启动 | 会话总数 {total} | 待评 {len(todo)}" + (" | [DRY-RUN]" if args.dry_run else ""))

    # L1 规则漏斗
    rule_results, need_l2 = [], []
    for s in todo:
        feat = extract_features(s)
        lvl = rule_level(feat)
        if lvl:
            rule_results.append((s, feat, lvl, 1.0, 0))
        else:
            need_l2.append((s, feat))
    print(f"⚡ L1 规则漏斗秒定 {len(rule_results)} 条 | 进入 L2 Jev Prefill {len(need_l2)} 条")

    # L2 并发 Prefill (线程池，prefix cache 友好)
    vllm_down = False
    l2_results = []
    if need_l2:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {
                pool.submit(jev_classify, build_features_block(s, f)): (s, f)
                for s, f in need_l2
            }
            done_n = 0
            for fut in as_completed(futs):
                s, f = futs[fut]
                lvl, prob = fut.result()
                done_n += 1
                if lvl is None:
                    vllm_down = True
                    lvl, prob = "medium", 0.0  # 占位，稍后降级规则
                l2_results.append((s, f, lvl, prob, None))
                if done_n % 20 == 0 or done_n == len(need_l2):
                    print(f"   ... L2 进度 {done_n}/{len(need_l2)}")

    # vLLM 不可达 → 纯规则降级 (按体量/轮数启发式)
    if vllm_down:
        print("⚠️  vLLM 146 不可达，降级为纯规则启发式打标")
        downgraded = []
        for s, f, _, _, _ in l2_results:
            if f["size"] > 200_000 or f["user_turns"] >= 8 or f["has_card"]:
                lvl, prob = "high" if (f["has_card"] or f["user_turns"] >= 8) else "medium", 0.5
            elif f["size"] > 20_000:
                lvl, prob = "medium", 0.5
            else:
                lvl, prob = "low", 0.6
            downgraded.append((s, f, lvl, prob, None))
        l2_results = downgraded

    all_results = rule_results + l2_results
    counts = {"high": 0, "medium": 0, "low": 0}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for s, feat, lvl, prob, _ in all_results:
        counts[lvl] += 1
        icon = LEVEL_ICON[lvl]
        print(f"  {icon} {lvl:<6} p={prob:<6} [{s['agent']:<9}] {s['sid'][:8]}  {feat['first_user'][:46]!r}")
        if not args.dry_run:
            ent = meta.setdefault(s["sid"], {})
            ent["jev_level"] = lvl
            ent["jev_prob"] = prob
            ent["jev_at"] = now

    elapsed = round((time.perf_counter() - t0) * 1000)
    if not args.dry_run and all_results:
        save_meta(meta)
        print(f"💾 已回写 {META_FILE} ({len(all_results)} 条)")
    print(f"\n📊 完成 | ⭐high {counts['high']} · 📝medium {counts['medium']} · 🗑️low {counts['low']} | 耗时 {elapsed}ms")
    if not args.dry_run:
        print(f"👉 筛选: lane -l high | lane -l low   清理: lane clean --low [--dry-run]")


# ───────── 筛选展示 ─────────
def cmd_list(args):
    meta = load_meta()
    con = sqlite3.connect(f"file:{SUMMARY_DB}?mode=ro", uri=True)
    cards = {}
    try:
        for sid, agent, ws, mtime, conclusion in con.execute(
            "SELECT session_id, agent, ws, mtime, conclusion FROM lane_cards"
        ):
            try:
                mtime = int(mtime)
            except (TypeError, ValueError):
                mtime = 0
            prev = cards.get(sid)
            # 取最新一张卡 (tuple: agent, ws, mtime, conclusion)
            if prev is None or mtime >= prev[2]:
                cards[sid] = (agent, ws, mtime, conclusion or "")
    finally:
        con.close()

    rows = []
    for sid, ent in meta.items():
        lvl = ent.get("jev_level")
        if not lvl:
            continue
        if args.level and lvl != args.level:
            continue
        if ent.get("deleted"):
            continue
        info = cards.get(sid)
        agent = info[0] if info else "?"
        ws = info[1] if info else "-"
        mtime = info[2] if info else 0
        title = ent.get("title") or ""
        if not title and info:
            title = (info[3] or "")[:60]
        rows.append((mtime, agent, ws, sid, lvl, ent.get("jev_prob", 0), title))

    rows.sort(key=lambda r: -r[0])
    if not rows:
        scope = args.level or "全部档位"
        print(f"(暂无 {scope} 打标数据，先运行: lane jev)")
        return

    print(f"{'AGENT':<11} {'TIME':<16} {'WS':<26} {'SID':<8} {'LVL':<9} {'P':<6} TITLE")
    print("-" * 110)
    for mtime, agent, ws, sid, lvl, prob, title in rows[: args.n or 100]:
        ts = datetime.fromtimestamp(mtime / 1000).strftime("%m-%d %H:%M") if mtime else "?"
        icon = LEVEL_ICON[lvl]
        print(f"{agent:<11} {ts:<16} {ws[:26]:<26} {sid[:8]:<8} {icon}{lvl:<8} {prob:<6} {title[:58]}")
    n = len(rows[: args.n or 100])
    print("-" * 110)
    print(f"✔ {n} 条{' (已按 --level 过滤)' if args.level else ''} | 全量打标: lane jev | 清理: lane clean --low")


def main():
    ap = argparse.ArgumentParser(description="Lane Jev 会话价值打标 (System One)")
    ap.add_argument("--dry-run", action="store_true", help="只评不写")
    ap.add_argument("--limit", type=int, default=0, help="最多评 N 条")
    ap.add_argument("--rescore", action="store_true", help="强制重评已打标会话")
    ap.add_argument("--agent", choices=["agy", "codebuddy", "qoder", "reasonix"], help="按 Agent 过滤")
    ap.add_argument("--list", nargs="?", const="all", default=None, metavar="LEVEL",
                    help="按档位列出会话: high|medium|low，缺省列全部")
    ap.add_argument("--level", choices=["high", "medium", "low"], help="筛选档位 (配合 --list)")
    ap.add_argument("-n", type=int, default=0, help="--list 显示条数 (默认 100)")
    args = ap.parse_args()

    if args.list:
        if args.list in ("high", "medium", "low"):
            args.level = args.list
        cmd_list(args)
    else:
        cmd_score(args)


if __name__ == "__main__":
    main()
