#!/usr/bin/env python3
"""跨 Agent 会话注入工具 (agent-inject.py)

允许一个 Agent（或用户）代表特定身份，将包含上下文的结构化提示词直接注入到目标 Agent（qoder / CodeBuddy / agy）的指定会话中。

特性:
  1. 结构化身份包装（自动附带发起者、来源、意图、时间戳）
  2. 支持目标定位：--session <id> (指定会话)、--latest (最近会话)、--new (开辟新会话)
  3. 支持超时保护与各 Agent 底层差异自动适配（qoder stdin管道、CodeBuddy print模式等）
  4. 注入完成后自动在终端打印对方的应答，并提示后续如何接管该会话
"""
import sys
import os
import glob
import time
import argparse
import subprocess
import uuid
from datetime import datetime

AGY_CONV_DIR = "/root/.gemini/antigravity-cli/conversations"
QODER_PROJ_DIR = "/root/.qoder/projects"
CB_PROJ_DIR = "/root/.codebuddy/projects"
CLINE_DATA_DIR = "/root/.cline/data"


def get_latest_session(agent):
    if agent == "qoder":
        files = sorted(glob.glob(os.path.join(QODER_PROJ_DIR, "*", "*.jsonl")),
                       key=os.path.getmtime, reverse=True)
        if files:
            return os.path.basename(files[0])[:36]
    elif agent == "codebuddy":
        files = sorted(glob.glob(os.path.join(CB_PROJ_DIR, "*", "*.jsonl")),
                       key=os.path.getmtime, reverse=True)
        if files:
            return os.path.basename(files[0])[:-6]
    elif agent == "agy":
        files = sorted(glob.glob(os.path.join(AGY_CONV_DIR, "*.db")),
                       key=os.path.getmtime, reverse=True)
        if files:
            return os.path.basename(files[0])[:-3]
    elif agent == "cline":
        files = sorted(glob.glob(os.path.join(CLINE_DATA_DIR, "sessions", "*", "*.messages.json")),
                       key=os.path.getmtime, reverse=True)
        if files:
            return os.path.basename(os.path.dirname(files[0]))
    return None


def resolve_full_id(agent, sid_prefix):
    if not sid_prefix:
        return None
    if agent == "qoder":
        matches = [os.path.basename(p)[:36] for p in glob.glob(os.path.join(QODER_PROJ_DIR, "*", "*.jsonl"))
                   if sid_prefix in os.path.basename(p)]
    elif agent == "codebuddy":
        matches = [os.path.basename(p)[:-6] for p in glob.glob(os.path.join(CB_PROJ_DIR, "*", "*.jsonl"))
                   if sid_prefix in os.path.basename(p)]
    elif agent == "agy":
        matches = [os.path.basename(p)[:-3] for p in glob.glob(os.path.join(AGY_CONV_DIR, "*.db"))
                   if sid_prefix in os.path.basename(p)]
    elif agent == "cline":
        matches = [os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(CLINE_DATA_DIR, "sessions", "*", "*.messages.json"))
                   if sid_prefix in os.path.basename(os.path.dirname(p))]
    else:
        matches = []
    
    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        print(f"匹配到多个 {agent} 会话，请提供更完整 ID: {matches[:3]}")
        sys.exit(1)
    return sid_prefix


def build_injected_prompt(from_agent, intent, message, src_conv=None):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"【跨 Agent 协作消息】",
        f"- 发送者: {from_agent}",
        f"- 发送时间: {now_str}",
    ]
    if src_conv:
        lines.append(f"- 来源会话: {src_conv} (reply-to)")
    if intent:
        lines.append(f"- 协作主题/意图: {intent}")
    lines.append("")
    lines.append("【任务详情与上下文】")
    lines.append(message.strip())
    lines.append("")
    lines.append("（请基于当前会话的上下文与上述协作要求继续执行，完成相应任务并给出结论。）")
    return "\n".join(lines)


def inject_to_codebuddy(session_id, is_new, prompt, timeout_sec=120):
    cmd = ["/root/.local/bin/codebuddy", "-p", "--dangerously-skip-permissions"]
    if session_id:
        if is_new:
            cmd.extend(["--session-id", session_id])
        else:
            cmd.extend(["-r", session_id])
    cmd.append(prompt)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"CodeBuddy 执行超时 ({timeout_sec}s)"


def get_qoder_session_cwd(session_id):
    if not session_id:
        return os.getcwd()
    matches = glob.glob(os.path.join(QODER_PROJ_DIR, "*", f"{session_id}*.jsonl"))
    if not matches:
        matches = [p for p in glob.glob(os.path.join(QODER_PROJ_DIR, "*", "*.jsonl")) if session_id in os.path.basename(p)]
    if matches:
        path = matches[0]
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        if data.get("cwd") and os.path.isdir(data["cwd"]):
                            return data["cwd"]
                        dirs = data.get("directories")
                        if dirs and isinstance(dirs, list) and os.path.isdir(dirs[0]):
                            return dirs[0]
                    except Exception:
                        continue
        except Exception:
            pass
        folder = os.path.basename(os.path.dirname(path))
        if folder == "-root":
            return "/root"
        elif folder.startswith("-root-"):
            candidate = "/" + folder[1:].replace("-", "/")
            if os.path.isdir(candidate):
                return candidate
    return os.getcwd()


def inject_to_qoder(session_id, is_new, prompt, timeout_sec=120):
    cmd = ["qoder", "-p", "--dangerously-skip-permissions"]
    cwd = os.getcwd()
    if session_id:
        if is_new:
            cmd.extend(["--session-id", session_id])
        else:
            cwd = get_qoder_session_cwd(session_id)
            if cwd and os.path.isdir(cwd):
                cmd.extend(["--cwd", cwd])
            cmd.extend(["-r", session_id])
    cmd.append(prompt)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, cwd=cwd)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"qoder 执行超时 ({timeout_sec}s)"


def inject_to_agy(session_id, is_new, prompt, timeout_sec=120):
    cmd = ["/root/.local/bin/agy", "-p", "--dangerously-skip-permissions"]
    if session_id and not is_new:
        cmd.extend(["--conversation", session_id])
    # agy CLI 要求 prompt 以 -p='<prompt>' 形式附着在 flag 上，
    # 否则 -p 会把紧随其后的 --dangerously-skip-permissions 当作 prompt。
    cmd.append(f"-p={prompt}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"agy 执行超时 ({timeout_sec}s)"


def inject_to_cline(session_id, is_new, prompt, timeout_sec=120):
    cmd = ["cline", "--auto-approve", "true"]
    if session_id and not is_new:
        cmd.extend(["--id", session_id])
    cmd.append(prompt)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"cline 执行超时 ({timeout_sec}s)"


def main():
    p = argparse.ArgumentParser(description="跨 Agent 会话注入与协作工具")
    p.add_argument("--to", required=True, choices=["qoder", "codebuddy", "agy", "cline"],
                   help="目标 Agent (qoder / codebuddy / agy / cline)")
    p.add_argument("--from-agent", default="agy", help="发送方 Agent 名称 (默认 agy)")
    p.add_argument("--session", "-s", help="目标会话 ID（前缀即可）")
    p.add_argument("--latest", "-l", action="store_true", help="注入到目标 Agent 的最新活跃会话")
    p.add_argument("--new", "-n", action="store_true", help="创建新会话注入")
    p.add_argument("--intent", "-i", help="协作主题/意图简述")
    p.add_argument("--src-conv", help="来源会话 ID")
    p.add_argument("--msg", "-m", required=True, help="要注入的提示词/任务说明")
    p.add_argument("--timeout", "-t", type=int, default=120, help="执行超时时间(秒，默认120)")
    args = p.parse_args()

    # 确定会话 ID 与是否为新建
    target_sid = None
    is_new = False
    if args.new:
        is_new = True
        target_sid = str(uuid.uuid4())
    elif args.latest:
        target_sid = get_latest_session(args.to)
        if not target_sid:
            print(f"未找到 {args.to} 的历史会话，将创建新会话。")
            is_new = True
            target_sid = str(uuid.uuid4())
    elif args.session:
        target_sid = resolve_full_id(args.to, args.session)
    else:
        # 默认使用最新会话
        target_sid = get_latest_session(args.to)
        if not target_sid:
            is_new = True
            target_sid = str(uuid.uuid4())

    # 包装 Prompt
    full_prompt = build_injected_prompt(
        from_agent=args.from_agent,
        intent=args.intent,
        message=args.msg,
        src_conv=args.src_conv
    )

    print(f"[*] 正在向 [{args.to}] 发起会话注入...")
    if is_new:
        print(f"[*] 目标会话: [全新会话 ID: {target_sid}]")
    else:
        print(f"[*] 目标会话 ID: {target_sid}")

    t0 = time.time()
    if args.to == "codebuddy":
        code, out, err = inject_to_codebuddy(target_sid, is_new, full_prompt, timeout_sec=args.timeout)
    elif args.to == "qoder":
        code, out, err = inject_to_qoder(target_sid, is_new, full_prompt, timeout_sec=args.timeout)
    elif args.to == "agy":
        code, out, err = inject_to_agy(target_sid, is_new, full_prompt, timeout_sec=args.timeout)
    elif args.to == "cline":
        code, out, err = inject_to_cline(target_sid, is_new, full_prompt, timeout_sec=args.timeout)

    elapsed = time.time() - t0
    print(f"[*] 注入完成，耗时 {elapsed:.1f}s (退出码: {code})")
    print("=" * 60)
    if out.strip():
        print("【目标 Agent 应答输出】:")
        print(out.strip())
    if err.strip() and code != 0:
        print(f"【错误信息】:\n{err.strip()}")
    print("=" * 60)

    # 提示用户如何接管
    if target_sid:
        if args.to == "qoder":
            resume_cmd = f"qoder -r {target_sid}"
            view_cmd = f"python3 /root/qoder-log-viewer.py {target_sid[:8]}"
        elif args.to == "codebuddy":
            resume_cmd = f"codebuddy -r {target_sid}"
            view_cmd = f"python3 /root/codebuddy-log-viewer.py {target_sid[:8]}"
        elif args.to == "agy":
            resume_cmd = f"agy --conversation {target_sid}"
            view_cmd = f"python3 /root/agy-log-viewer.py {target_sid[:8]}"
        elif args.to == "cline":
            resume_cmd = f"cline --id {target_sid} -i"
            view_cmd = f"python3 /root/cline-log-viewer.py {target_sid}"
        
        print(f"\n💡 后续操作提示：")
        print(f"  - 交互接管该会话: `{resume_cmd}`")
        print(f"  - 随时查看会话流: `{view_cmd}`")


if __name__ == "__main__":
    main()
