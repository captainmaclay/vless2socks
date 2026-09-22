"""Create a clean release copy of the project without personal data.

Copies all source files to a 'release/' directory, replacing config files
with clean templates (no VLESS URLs, passwords, or personal paths).

Usage:
    python prepare_release.py
"""

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RELEASE_DIR = ROOT / "release" / "vless2socks"

# Files to copy as-is (source code, docs, batch scripts)
COPY_FILES = [
    "main.py",
    "gui.py",
    "tray_widget.py",
    "i18n.py",
    "settings_manager.py",
    "backup_manager.py",
    "geo_ip.py",
    "requirements.txt",
    "README.md",
    "MODULES.md",
    "LICENSE",
    ".gitignore",
    "install.bat",
    "start.bat",
    "build.bat",
    "gui.bat",
    "run.bat",
    "tray.bat",
    "check.bat",
    "checkip.bat",
    "doctor.bat",
    "get_xray.bat",
    "selftest.bat",
    "test.bat",
    "vless2socks.spec",
]

# Directories to copy recursively
COPY_DIRS = [
    "vless2socks",
    "tools",
    "tests",
]

# Clean template data
CLEAN_CONFIG = {
    "url": "vless://00000000-0000-0000-0000-000000000000@example.com:443?security=tls&type=tcp&sni=example.com#my-server",
    "listen": "127.0.0.1:1080",
    "username": "",
    "password": "",
    "udp": True,
    "connectTimeout": 10,
    "udpIdleTimeout": 60,
    "logLevel": "info",
    "backend": "auto",
    "xrayPath": "",
    "xrayLegacyConfig": False,
}

CLEAN_INSTANCES = [CLEAN_CONFIG.copy()]

CLEAN_SETTINGS = {
    "language": "en",
    "force_port_takeover": True,
    "autostart_proxies": True,
    "start_minimized_tray": False,
    "auto_reconnect": True,
    "reconnect_intervals": "10, 15, 30, 60, 120, 180, 30",
    "auto_backup_enabled": False,
    "backup_interval_hours": 24,
    "backup_dir": "",
    "last_backup_time": "-",
    "backup_password": "",
}


def main():
    # Clean previous release
    if RELEASE_DIR.exists():
        shutil.rmtree(RELEASE_DIR)
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)

    # Copy source files
    copied = 0
    for fname in COPY_FILES:
        src = ROOT / fname
        if src.exists():
            shutil.copy2(src, RELEASE_DIR / fname)
            copied += 1
            print(f"  ✓ {fname}")
        else:
            print(f"  ⚠ {fname} (not found, skipped)")

    # Copy directories (excluding __pycache__)
    for dname in COPY_DIRS:
        src_dir = ROOT / dname
        dst_dir = RELEASE_DIR / dname
        if src_dir.exists():
            shutil.copytree(
                src_dir, dst_dir,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
            print(f"  ✓ {dname}/")
        else:
            print(f"  ⚠ {dname}/ (not found, skipped)")

    # Write clean config templates
    def write_json(path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    write_json(RELEASE_DIR / "config.example.json", CLEAN_CONFIG)
    write_json(RELEASE_DIR / "instances.example.json", CLEAN_INSTANCES)
    write_json(RELEASE_DIR / "settings.example.json", CLEAN_SETTINGS)
    print("  ✓ config.example.json (clean template)")
    print("  ✓ instances.example.json (clean template)")
    print("  ✓ settings.example.json (clean template)")

    # Create empty .env template
    (RELEASE_DIR / ".env.example").write_text('BACKUP_PASSWORD=""\n', encoding="utf-8")
    print("  ✓ .env.example (empty)")

    # Create empty bin/ and runtime/ dirs
    (RELEASE_DIR / "bin").mkdir(exist_ok=True)
    (RELEASE_DIR / "bin" / ".gitkeep").touch()
    print("  ✓ bin/ (empty, for xray-core)")

    print(f"\n{'='*50}")
    print(f"Release created at: {RELEASE_DIR}")
    print(f"Files copied: {copied}")
    print(f"\nThis directory is safe to push to GitHub.")
    print(f"No personal data, VLESS URLs, or passwords included.")


if __name__ == "__main__":
    main()
