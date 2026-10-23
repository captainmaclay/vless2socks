"""Publish GitHub release v1.2.4 with binaries and assets."""
import os
import sys
import json
import urllib.request
import urllib.error

TOKEN = os.environ.get("GITHUB_PUSH_TOKEN") or sys.argv[1]

headers = {
    "Authorization": f"token {TOKEN}",
    "User-Agent": "vless2socks-releaser",
    "Accept": "application/vnd.github.v3+json",
}

body = """- **Chrome Isolation Profiles:** Automated deployment of dedicated Chrome instances bound to local SOCKS5 proxies in `proxys/` with isolated cookies and user data.
- **Claude & Vendor C Isolation:** Environment isolation (`CLAUDE_CONFIG_DIR`) and socket redirection for Windows Claude CLI (ports 1015/1016).
- **NetSarang Xshell 8 Setup & Automation:** Antigravity skill, automated installer (`setup_helpers.xshell`), desktop shortcut recovery, and UTF-16 LE SOCKS5 INI generation.
- **Antigravity Orchestration & Workflows:** Added `initial_start` skill, orchestrator sequence (`workflow_sequence.json`), `0_HowToInstall.txt`, and full technical references.
- **Security & Git Protocol:** Added `github_safe_push` skill and automated credential sanitization.

Unpack the zip and run vless2socks.exe; xray is in bin/ next to it. From this version on, later releases install from the Updates tab.
SHA-256 zip: 780B2E9A9FE0148FC0FC5F2D391FDDF0F50DBCFA5B5946464EFC31744D9147CD
SHA-256 exe: 5A36B57E641BEC820D983EADC89BC03F8C1BE6668F17630DF50BDFBD065A9BE2"""

payload = {
    "tag_name": "v1.2.4",
    "target_commitish": "main",
    "name": "vless2socks 1.2.4",
    "body": body,
    "draft": False,
    "prerelease": False,
}

print("Creating Release v1.2.4 on GitHub...")
req = urllib.request.Request(
    "https://api.github.com/repos/captainmaclay/vless2socks/releases",
    data=json.dumps(payload).encode("utf-8"),
    headers={**headers, "Content-Type": "application/json"},
    method="POST",
)

try:
    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        release_id = res["id"]
        upload_url_template = res["upload_url"]
        print(f"Created Release {res['name']} (id={release_id})")
except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8", errors="ignore")
    print(f"Failed to create release: {e.code} {err_body}")
    # If release already created, fetch it
    req_existing = urllib.request.Request(
        "https://api.github.com/repos/captainmaclay/vless2socks/releases/tags/v1.2.4",
        headers=headers,
    )
    with urllib.request.urlopen(req_existing) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        release_id = res["id"]
        upload_url_template = res["upload_url"]
        print(f"Using existing release {res['name']} (id={release_id})")

upload_base = upload_url_template.split("{")[0]

def upload_asset(file_path, asset_name, content_type):
    url = f"{upload_base}?name={asset_name}"
    size = os.path.getsize(file_path)
    print(f"Uploading {asset_name} ({size} bytes)...")
    with open(file_path, "rb") as f:
        data = f.read()
    ureq = urllib.request.Request(
        url,
        data=data,
        headers={**headers, "Content-Type": content_type, "Content-Length": str(size)},
        method="POST",
    )
    with urllib.request.urlopen(ureq) as uresp:
        ures = json.loads(uresp.read().decode("utf-8"))
        print(f"Successfully uploaded {ures['name']} (id={ures['id']})")

upload_asset("dist/vless2socks-v1.2.4-win64.zip", "vless2socks-v1.2.4-win64.zip", "application/zip")
upload_asset("dist/vless2socks.exe", "vless2socks.exe", "application/octet-stream")

print("All assets uploaded successfully!")
