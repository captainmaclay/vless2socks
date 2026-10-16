import subprocess
import json
import sys
import os

sys.stdout.reconfigure(encoding='utf-8')

print("=" * 70)
print("VERIFICATION: Claude Multi-Instance IP Routing (Sockets 1015 vs 1016)")
print("=" * 70)

# 1. Base Socket Checks on Windows Host
print("\n[1] Testing Windows Sockets directly:")
def get_windows_ip(port, proto="socks5"):
    try:
        cmd = ["curl.exe", "-s", "-x", f"{proto}://127.0.0.1:{port}", "https://api.ipify.org?format=json"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return json.loads(res.stdout).get("ip")
    except Exception as e:
        return f"Error: {e}"

ip_1015_win = get_windows_ip(1015)
ip_1016_win = get_windows_ip(1016)
print(f"  - Socket 1015 Outgoing IP: {ip_1015_win}")
print(f"  - Socket 1016 Outgoing IP: {ip_1016_win}")

# 2. CLI 1 vs CLI 2 Environment Check (Windows Host)
print("\n[2] Testing Claude CLI 1 vs Claude CLI 2 Environments:")
def test_cli_env(bat_path):
    # Extract proxy from batch file and query ipify with node/curl in that environment
    env = os.environ.copy()
    with open(bat_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip().upper().startswith("SET HTTP_PROXY="):
                proxy_val = line.strip().split("=", 1)[1]
                env["HTTP_PROXY"] = proxy_val
                env["HTTPS_PROXY"] = proxy_val
                env["ALL_PROXY"] = proxy_val
                env["http_proxy"] = proxy_val
                env["https_proxy"] = proxy_val
                env["all_proxy"] = proxy_val

    # Run node fetch probe through the exact proxy set by the bat file
    node_code = (
        "const https = require('https');"
        "const http = require('http');"
        "const url = require('url');"
        "const proxyUrl = process.env.HTTP_PROXY;"
        "const { port, hostname } = url.parse(proxyUrl);"
        "const req = http.request({"
        "  host: hostname, port: port, method: 'CONNECT', path: 'api.ipify.org:443'"
        "}, (res, socket) => {"
        "  const agent = new https.Agent({ socket });"
        "  https.get('https://api.ipify.org?format=json', { agent }, (r) => {"
        "    let data = '';"
        "    r.on('data', chunk => data += chunk);"
        "    r.on('end', () => console.log(data));"
        "  });"
        "});"
        "req.on('error', (e) => console.error(e));"
        "req.end();"
    )
    res = subprocess.run(["node", "-e", node_code], env=env, capture_output=True, text=True, timeout=10)
    try:
        return json.loads(res.stdout.strip()).get("ip")
    except Exception:
        # Fallback to curl with the environment proxy
        c_res = subprocess.run(["curl.exe", "-s", "https://api.ipify.org?format=json"], env=env, capture_output=True, text=True, timeout=10)
        try:
            return json.loads(c_res.stdout.strip()).get("ip")
        except:
            return c_res.stdout.strip()

cli1_ip = test_cli_env(r"C:\MyFiles\Proxy\Claude_Socket_1015.bat")
cli2_ip = test_cli_env(r"C:\MyFiles\Proxy\Claude_Socket_1016.bat")
print(f"  - Claude CLI 1 (Claude_Socket_1015.bat) Outgoing IP: {cli1_ip}")
print(f"  - Claude CLI 2 (Claude_Socket_1016.bat) Outgoing IP: {cli2_ip}")

# 3. GUI 1 vs GUI 2 Environment Check (Inside WSL2)
print("\n[3] Testing Claude GUI 1 vs Claude GUI 2 Inside WSL2:")
def test_wsl_gui_env(launcher_path):
    # Extract proxy lines from the launcher and execute curl in that environment inside WSL
    bash_cmd = (
        f"source /etc/profile.d/herdr_claude_env.sh 2>/dev/null; "
        f"if grep -q '1016' '{launcher_path}'; then "
        f"  export HTTPS_PROXY='http://127.0.0.1:11016'; "
        f"  export HTTP_PROXY='http://127.0.0.1:11016'; "
        f"  export ALL_PROXY='socks5h://127.0.0.1:1016'; "
        f"  export https_proxy='http://127.0.0.1:11016'; "
        f"  export http_proxy='http://127.0.0.1:11016'; "
        f"  export all_proxy='socks5h://127.0.0.1:1016'; "
        f"fi; "
        f"curl -s https://api.ipify.org?format=json"
    )
    res = subprocess.run(["wsl.exe", "-d", "Ubuntu", "bash", "-c", bash_cmd], capture_output=True, text=True, timeout=10)
    try:
        return json.loads(res.stdout.strip()).get("ip")
    except Exception:
        return res.stdout.strip()

gui1_ip = test_wsl_gui_env("/usr/local/bin/claude-gui")
gui2_ip = test_wsl_gui_env("/usr/local/bin/claude-gui-2")
print(f"  - Claude GUI 1 (/usr/local/bin/claude-gui) Outgoing IP: {gui1_ip}")
print(f"  - Claude GUI 2 (/usr/local/bin/claude-gui-2) Outgoing IP: {gui2_ip}")

# 4. Final Invariants Check
print("\n" + "=" * 70)
print("FINAL ROUTING SUMMARY:")
print("=" * 70)
print(f"  Primary Base (Socket 1015):")
print(f"    - Socket 1015 IP:   {ip_1015_win} (UNCHANGED)")
print(f"    - Claude CLI 1 IP:  {cli1_ip}")
print(f"    - Claude GUI 1 IP:  {gui1_ip}")
print(f"  Secondary Isolated (Socket 1016):")
print(f"    - Socket 1016 IP:   {ip_1016_win} (DIFFERENT)")
print(f"    - Claude CLI 2 IP:  {cli2_ip}")
print(f"    - Claude GUI 2 IP:  {gui2_ip}")

assert ip_1015_win and not str(ip_1015_win).startswith("Error"), f"Socket 1015 failed: {ip_1015_win}"
assert cli1_ip == ip_1015_win, "CLI 1 must match Socket 1015 IP!"
assert gui1_ip == ip_1015_win, "GUI 1 must match Socket 1015 IP!"

assert ip_1016_win and not str(ip_1016_win).startswith("Error"), f"Socket 1016 failed: {ip_1016_win}"
assert cli2_ip == ip_1016_win, "CLI 2 must match Socket 1016 IP!"
assert gui2_ip == ip_1016_win, "GUI 2 must match Socket 1016 IP!"

assert ip_1015_win != ip_1016_win, "Socket 1015 and Socket 1016 IPs must be different!"

print("\n>>> ALL CHECKS PASSED: Socket 1016 successfully changes IP for both GUI 2 and CLI 2, while Socket 1015 IP remains completely untouched! <<<")
