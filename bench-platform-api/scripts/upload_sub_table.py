#!/usr/bin/env python3
"""解析远程日志 → 批量上传到副表 (TaskDataSheetRecord)"""
import os, re, json, subprocess, sys, argparse

SCRIPTS = os.path.expanduser("~/.workbuddy/skills/ssh-skill/scripts")
PLATFORM = "192.2.29.9"
API = "http://127.0.0.1:9001/api/model-storage"


def ssh(host, cmd, timeout=120):
    r = subprocess.run(
        ["python", f"{SCRIPTS}/ssh_execute.py", host, cmd],
        capture_output=True, text=True, timeout=timeout)
    data = json.loads(r.stdout)
    if data.get("success"):
        return data["stdout"]
    print(f"SSH failed: {data.get('stderr', '')}")
    sys.exit(1)


def parse_log(content, fname, args):
    def xt(p):
        m = re.search(p, content)
        return float(m.group(1)) if m else 0

    m = re.match(r'il(\d+)_ol(\d+)_np(\d+)_mc(\d+)', fname)
    if not m:
        return None

    il, ol, np_val, mc = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    success_req = 0
    m2 = re.search(r'Successful requests:\s+(\d+)', content)
    if m2:
        success_req = int(m2.group(1))

    return {
        "filename": fname,
        "model_name": args.model_name,
        "success_requests": success_req,
        "concurrency": mc,
        "requests_count": np_val,
        "benchmark_duration": xt(r'Benchmark duration \(s\):\s+([\d.]+)'),
        "input_tokens": il * success_req,
        "output_tokens": ol * success_req,
        "request_throughput": xt(r'Request throughput \(req/s\):\s+([\d.]+)'),
        "output_token_throughput": xt(r'Output token throughput \(tok/s\):\s+([\d.]+)'),
        "total_token_throughput": xt(r'Total token throughput \(tok/s\):\s+([\d.]+)'),
        "avg_ttft": xt(r'Mean TTFT \(ms\):\s+([\d.]+)'),
        "median_ttft": xt(r'Median TTFT \(ms\):\s+([\d.]+)'),
        "p99_ttft": xt(r'P99 TTFT \(ms\):\s+([\d.]+)'),
        "avg_tpot": xt(r'Mean TPOT \(ms\):\s+([\d.]+)'),
        "median_tpot": xt(r'Median TPOT \(ms\):\s+([\d.]+)'),
        "p99_tpot": xt(r'P99 TPOT \(ms\):\s+([\d.]+)'),
        "input_length": il,
        "output_length": ol,
        "gpu_device_id": args.gpu_device_id,
        "gpu_count": args.gpu_count,
        "machine_model": args.machine_model,
        "dataset": args.dataset,
        "data_type": args.data_type,
        "framework": args.framework,
        "framework_version": args.framework_version,
    }


def main():
    p = argparse.ArgumentParser(description="上传日志到平台副表")
    p.add_argument("--remote", required=True, help="SSH 别名（如 remote-146）")
    p.add_argument("--log-dir", required=True, help="远程日志目录路径")
    p.add_argument("--pattern", default="il*_ol*_np*_mc*.log", help="文件匹配模式")
    p.add_argument("--sheet-id", type=int, required=True, help="副表 ID")
    p.add_argument("--gpu-device-id", type=int, required=True, help="GPU 设备 ID")
    p.add_argument("--gpu-count", type=int, default=16, help="GPU 数量")
    p.add_argument("--machine-model", default="5330 G7 utrla")
    p.add_argument("--model-name", required=True, help="模型简称")
    p.add_argument("--data-type", default="w8a8", help="数据类型")
    p.add_argument("--framework", default="vllm", help="推理框架")
    p.add_argument("--framework-version", default="0.20.0")
    p.add_argument("--dataset", default="random")
    p.add_argument("--dry-run", action="store_true", help="仅解析不实际上传")
    args = p.parse_args()

    # 1. 获取文件列表
    print(f"Scanning {args.remote}:{args.log_dir}")
    files_out = ssh(args.remote, f"ls {args.log_dir}/{args.pattern} 2>/dev/null")
    file_list = sorted([f.strip() for f in files_out.strip().split('\n') if f.strip()])
    print(f"Found {len(file_list)} log files")

    if not file_list:
        print("No files found!")
        sys.exit(1)

    # 2. 解析
    records = []
    for i, fpath in enumerate(file_list, 1):
        fname = os.path.basename(fpath)
        print(f"[{i}/{len(file_list)}] {fname}...", end=" ")

        content = ssh(args.remote, f"cat {fpath}", timeout=60)
        data = parse_log(content, fname, args)
        if not data:
            print("SKIP (can't parse)")
            continue

        records.append(data)
        print(f"OK (concurrency={data['concurrency']}, output_tps={data['output_token_throughput']:.1f})")

    print(f"\nParsed {len(records)} records")

    if args.dry_run:
        print("Dry run, not uploading.")
        return

    # 3. 上传
    payload = json.dumps({"sheet_id": args.sheet_id, "data": records}, ensure_ascii=False)
    tmp = "/tmp/upload_sub_table_payload.json"

    print(f"Uploading to sheet_id={args.sheet_id}...")
    ssh(PLATFORM, f"cat > {tmp} << 'EOF'\n{payload}\nEOF")
    result = ssh(PLATFORM,
        f"curl -s -X POST '{API}/task-data-sheet-records/batch/' "
        f"-H 'X-Token: 1' -H 'Content-Type: application/json' -d @{tmp}",
        timeout=30)
    ssh(PLATFORM, f"rm -f {tmp}")

    try:
        r = json.loads(result)
        msg = r.get("message", r.get("error", str(r)))
        print(f"\nResult: {msg}")
    except:
        print(f"\nResponse: {result[:200]}")

    # 4. 验证
    v = ssh(PLATFORM,
        f"curl -s '{API}/task-data-sheet-records/?sheet_id={args.sheet_id}&limit=50' -H 'X-Token: 1'")
    try:
        vd = json.loads(v)
        count = len(vd.get("data", []))
        print(f"Verified: {count} records in sheet {args.sheet_id}")
    except:
        pass

    print("Done!")


if __name__ == "__main__":
    main()
