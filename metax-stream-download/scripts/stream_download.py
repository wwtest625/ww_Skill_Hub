"""
流式下载：沐曦 TOS → 本机网络(不落盘) → SSH → 远程服务器
"""
import paramiko, sys, os, io
from urllib.request import urlopen

# 服务器配置
host = "192.2.0.82"
port = 22
user = "root"

# 配置文件路径
import json, re

def get_ssh_config(alias):
    """从 SSH config 解析配置"""
    config_path = os.path.expanduser("~/.ssh/config")
    with open(config_path) as f:
        text = f.read()
    blocks = text.split("Host ")
    for block in blocks:
        if block.strip().startswith(alias):
            hostname = re.search(r"HostName\s+(\S+)", block)
            user_m = re.search(r"User\s+(\S+)", block)
            port_m = re.search(r"Port\s+(\d+)", block)
            # 找密码注释
            pwd_m = re.search(r"#\s*password:\s*(\S+)", block)
            return {
                "hostname": hostname.group(1) if hostname else None,
                "user": user_m.group(1) if user_m else "root",
                "port": int(port_m.group(1)) if port_m else 22,
                "password": pwd_m.group(1) if pwd_m else None
            }
    return None

if len(sys.argv) < 3:
    print("用法: python stream_download.py <下载URL> <远程路径>")
    print("示例: python stream_download.py https://... /opt/maca-sdk-3.7.2.0.tar.xz")
    sys.exit(1)

url = sys.argv[1]
remote_path = sys.argv[2]

# 获取配置
config = get_ssh_config("tmp-192.2.0.82")
if not config or not config["hostname"]:
    print("错误: 找不到服务器配置")
    sys.exit(1)

print(f"连接 {config['hostname']}:{config['port']} ...")

# 打开远程文件（SFTP 方式，不支持流式追加）
# 改用 paramiko 的 exec_command 执行 cat > file，然后写 stdin
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

if config["password"]:
    ssh.connect(config["hostname"], port=config["port"], 
                username=config["user"], password=config["password"],
                timeout=30)
else:
    ssh.connect(config["hostname"], port=config["port"],
                username=config["user"], timeout=30)

transport = ssh.get_transport()
channel = transport.open_session()
channel.exec_command(f"cat > {remote_path}")

# 流式下载
print(f"下载中... {url[:80]}...")
resp = urlopen(url)
total = int(resp.headers.get('Content-Length', 0))
downloaded = 0
chunk_size = 1024 * 1024  # 1MB chunks

while True:
    chunk = resp.read(chunk_size)
    if not chunk:
        break
    channel.sendall(chunk)
    downloaded += len(chunk)
    if total:
        pct = downloaded * 100 / total
        print(f"\r  {downloaded/1024/1024:.1f}MB / {total/1024/1024:.1f}MB ({pct:.0f}%)", end="", flush=True)
    else:
        print(f"\r  {downloaded/1024/1024:.1f}MB", end="", flush=True)

channel.shutdown_write()
exit_code = channel.recv_exit_status()
print(f"\n完成! 退出码: {exit_code}")
ssh.close()
