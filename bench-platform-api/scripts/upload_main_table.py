#!/usr/bin/env python3
"""上传 tar 到平台主表 (log-import-direct)"""
import json, subprocess, sys, argparse, os

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


def transfer_to_platform(src_alias, src_path, dst_path):
    """下载到本地 → 上传到平台 (避免 ssh_server_transfer 别名解析问题)"""
    import tempfile
    tmp_local = os.path.join(tempfile.gettempdir(), os.path.basename(dst_path))
    # 下载到本地
    r = subprocess.run(
        ["python", f"{SCRIPTS}/ssh_download.py", src_alias, src_path, tmp_local],
        capture_output=True, text=True, timeout=300)
    dl = json.loads(r.stdout)
    if not dl.get("success"):
        print(f"Download failed: {dl}")
        return False
    # 上传到平台
    r2 = subprocess.run(
        ["python", f"{SCRIPTS}/ssh_upload.py", PLATFORM, tmp_local, dst_path],
        capture_output=True, text=True, timeout=300)
    ul = json.loads(r2.stdout)
    os.remove(tmp_local)
    if ul.get("success"):
        return True
    print(f"Upload failed: {ul}")
    return False


def main():
    p = argparse.ArgumentParser(description="上传 tar 到平台主表")
    p.add_argument("--src-alias", required=True, help="源服务器 SSH 别名")
    p.add_argument("--tar-path", required=True, help="源服务器上的 tar 路径")
    p.add_argument("--task-id", type=int, required=True, help="测试任务 ID")
    p.add_argument("--gpu-device-id", type=int, required=True, help="GPU 设备 ID")
    p.add_argument("--no-overwrite", action="store_true", help="不覆盖已有数据")
    p.add_argument("--dry-run", action="store_true", help="仅打印不执行")
    args = p.parse_args()

    tar_name = os.path.basename(args.tar_path)
    dst = f"/tmp/{tar_name}"
    overwrite = "false" if args.no_overwrite else "true"

    print(f"Source: {args.src_alias}:{args.tar_path}")
    print(f"Target: task_id={args.task_id}, gpu_device_id={args.gpu_device_id}")
    print(f"Overwrite: {overwrite}")

    if args.dry_run:
        print("Dry run, exiting.")
        return

    # 1. 传输到平台服务器
    print(f"Transferring to {PLATFORM}...")
    if not transfer_to_platform(args.src_alias, args.tar_path, dst):
        print("Transfer failed!")
        sys.exit(1)
    print("Transfer OK")

    # 2. 导入
    print("Importing...")
    result = ssh(PLATFORM,
        f"curl -s -X POST '{API}/log-import-direct/' "
        f"-H 'X-Token: 1' "
        f"-F 'task_id={args.task_id}' "
        f"-F 'gpu_device_id={args.gpu_device_id}' "
        f"-F 'overwrite={overwrite}' "
        f"-F 'file=@{dst}'")

    # 清理
    ssh(PLATFORM, f"rm -f {dst}")

    try:
        r = json.loads(result)
        print(json.dumps(r, indent=2, ensure_ascii=False))
    except:
        print(f"Response: {result[:500]}")

    # 3. 验证
    v = ssh(PLATFORM,
        f"curl -s '{API}/performance-test-data/?test_task_id={args.task_id}&limit=50' -H 'X-Token: 1'")
    try:
        vd = json.loads(v)
        count = len(vd.get("data", []))
        print(f"\nVerified: {count} records in task {args.task_id}")
    except:
        pass

    print("Done!")


if __name__ == "__main__":
    main()
