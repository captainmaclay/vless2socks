"""Encrypted Backup & Security Manager for vlesstosocks5.

Security:
- Encryption standard: AES-256-GCM (Authenticated Encryption with Associated Data)
- Key Derivation: PBKDF2-HMAC-SHA256 (16-byte salt, 600,000 iterations)
- Password Fingerprint: SHA-256 displayed in UI (e.g. A1B2 C3D4 E5F6 7890)
- Master password saved in .env (BACKUP_PASSWORD=...)
- Danger Zone / Factory Reset to wipe all instances, configs, and credentials
- Custom backup directory with automatic scanning of available .hbak snapshots
- Periodic background auto-backup with configurable hour interval
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

import settings_manager
from vless2socks.paths import APP_DIR

ROOT_DIR = APP_DIR
ENV_FILE = ROOT_DIR / ".env"
SETTINGS_FILE = ROOT_DIR / "settings.json"
INSTANCES_FILE = ROOT_DIR / "instances.json"
CONFIG_FILE = ROOT_DIR / "config.json"

DEFAULT_BACKUP_DIR = Path.home() / "Documents" / "VlessBackups"

MAGIC_HEADER = b"HBAK\x01"  # Herdr / Vless Backup Version 1
PBKDF2_ITERATIONS = 600_000
SALT_SIZE = 16
NONCE_SIZE = 12
KEY_SIZE = 32  # 256 bits


def get_backup_dir() -> Path:
    """Get currently configured backup directory."""
    raw = settings_manager.get_setting("backup_dir", str(DEFAULT_BACKUP_DIR))
    p = Path(raw)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def set_backup_dir(path_str: str | Path) -> None:
    """Set and persist backup directory in settings."""
    settings_manager.set_setting("backup_dir", str(path_str))


def list_backups_in_dir(target_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """Scan and return list of valid .hbak backup snapshots sorted newest first."""
    if target_dir is None:
        target_dir = get_backup_dir()
    else:
        target_dir = Path(target_dir)

    if not target_dir.exists():
        return []

    results = []
    try:
        for f in target_dir.glob("*.hbak"):
            st = f.stat()
            size_kb = round(st.st_size / 1024, 1)
            mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
            results.append({
                "filename": f.name,
                "filepath": str(f),
                "size_kb": size_kb,
                "modified": mtime,
            })
    except Exception:
        pass

    results.sort(key=lambda x: x["modified"], reverse=True)
    return results


def compute_password_fingerprint(password: str) -> str:
    """Compute compact SHA-256 fingerprint for display."""
    if not password:
        return ""
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return f"{digest[:4]} {digest[4:8]} {digest[8:12]} {digest[12:16]}".upper()


def load_backup_password() -> str:
    """Read saved master password from .env or settings.json."""
    if ENV_FILE.exists():
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("BACKUP_PASSWORD="):
                        val = line.split("=", 1)[1].strip()
                        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                            val = val[1:-1]
                        return val
        except Exception:
            pass
    val = settings_manager.get_setting("backup_password", "")
    return str(val) if val else ""


def save_backup_password(password: str) -> None:
    """Save master password to .env and settings.json."""
    lines = []
    found = False
    if ENV_FILE.exists():
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            new_lines = []
            for line in lines:
                if line.strip().startswith("BACKUP_PASSWORD="):
                    new_lines.append(f'BACKUP_PASSWORD="{password}"\n')
                    found = True
                else:
                    new_lines.append(line)
            lines = new_lines
        except Exception:
            lines = []
    if not found:
        lines.append(f'BACKUP_PASSWORD="{password}"\n')

    try:
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception:
        pass

    settings_manager.set_setting("backup_password", password)


def derive_key(password: str, salt: bytes) -> bytes:
    """Derive 256-bit encryption key using PBKDF2-HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def create_backup_archive() -> bytes:
    """Pack instances.json, config.json, settings.json, .env into in-memory zip bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if INSTANCES_FILE.exists():
            zf.write(INSTANCES_FILE, arcname="instances.json")
        if CONFIG_FILE.exists():
            zf.write(CONFIG_FILE, arcname="config.json")
        if SETTINGS_FILE.exists():
            zf.write(SETTINGS_FILE, arcname="settings.json")
        if ENV_FILE.exists():
            zf.write(ENV_FILE, arcname=".env")

        manifest = {
            "version": "1.0",
            "timestamp": time.time(),
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "app": "vless2socks",
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    return buf.getvalue()


def export_encrypted_backup(password: str, dest_dir: Optional[Path] = None) -> Path:
    """Export encrypted .hbak file using AES-256-GCM. Returns Path to generated file."""
    if not password:
        raise ValueError("Master password cannot be empty")

    if dest_dir is None:
        dest_dir = get_backup_dir()
    else:
        dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    key = derive_key(password, salt)
    aesgcm = AESGCM(key)

    plaintext = create_backup_archive()
    ciphertext = aesgcm.encrypt(nonce, plaintext, MAGIC_HEADER)

    timestamp_str = time.strftime("%Y%m%d_%H%M%S")
    out_file = dest_dir / f"vless2socks_backup_{timestamp_str}.hbak"

    with open(out_file, "wb") as f:
        f.write(MAGIC_HEADER)
        f.write(salt)
        f.write(nonce)
        f.write(ciphertext)

    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    settings_manager.set_setting("last_backup_time", now_str)

    return out_file


def restore_encrypted_backup(hbak_file: Path | str, password: str) -> int:
    """Decrypt .hbak file and restore configuration files. Returns number of instances restored."""
    hbak_path = Path(hbak_file)
    if not hbak_path.exists():
        raise FileNotFoundError(f"Backup file not found: {hbak_path}")
    if not password:
        raise ValueError("Master password is required")

    with open(hbak_path, "rb") as f:
        data = f.read()

    header_len = len(MAGIC_HEADER)
    if len(data) < header_len + SALT_SIZE + NONCE_SIZE + 16:
        raise ValueError("Corrupted or invalid backup file format")

    if not data.startswith(MAGIC_HEADER):
        raise ValueError("Invalid magic header: not a valid .hbak snapshot")

    offset = header_len
    salt = data[offset : offset + SALT_SIZE]
    offset += SALT_SIZE
    nonce = data[offset : offset + NONCE_SIZE]
    offset += NONCE_SIZE
    ciphertext = data[offset:]

    key = derive_key(password, salt)
    aesgcm = AESGCM(key)

    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext, MAGIC_HEADER)
    except Exception as e:
        raise ValueError("Decryption failed. Incorrect password or modified archive.") from e

    # Unpack zip
    restored_count = 0
    buf = io.BytesIO(plaintext)
    with zipfile.ZipFile(buf, "r") as zf:
        namelist = zf.namelist()
        if "instances.json" in namelist:
            zf.extract("instances.json", path=ROOT_DIR)
            try:
                with open(INSTANCES_FILE, "r", encoding="utf-8") as f:
                    inst = json.load(f)
                    if isinstance(inst, list):
                        restored_count = len(inst)
            except Exception:
                restored_count = 1
        if "config.json" in namelist:
            zf.extract("config.json", path=ROOT_DIR)
        if "settings.json" in namelist:
            zf.extract("settings.json", path=ROOT_DIR)

    return restored_count


def check_and_run_auto_backup() -> tuple[bool, str, str | None]:
    """Check if scheduled auto-backup is due, and create backup if so."""
    if not settings_manager.get_setting("auto_backup_enabled", False):
        return False, "Auto-backup disabled", None

    pwd = load_backup_password()
    if not pwd:
        return False, "No master password saved", None

    interval_hours = settings_manager.get_setting("backup_interval_hours", 24)
    try:
        interval_hours = float(interval_hours)
    except Exception:
        interval_hours = 24.0

    interval_sec = max(60.0, interval_hours * 3600.0)

    last_str = settings_manager.get_setting("last_backup_time", "-")
    if last_str and last_str != "-":
        try:
            t_last = time.mktime(time.strptime(last_str, "%Y-%m-%d %H:%M:%S"))
            if time.time() - t_last < interval_sec:
                return False, "Interval not elapsed", None
        except Exception:
            pass

    target_dir = get_backup_dir()
    try:
        out_file = export_encrypted_backup(pwd, dest_dir=target_dir)
        msg = f"Auto-backup created: {out_file.name}"
        return True, msg, str(out_file)
    except Exception as e:
        return False, f"Auto-backup failed: {e}", None


def wipe_all_sensitive_data(stop_all_callback: Optional[Callable[[], None]] = None) -> None:
    """Terminate all proxies, purge instances.json, temp files, and clear password."""
    if stop_all_callback:
        try:
            stop_all_callback()
        except Exception:
            pass

    for p in ROOT_DIR.glob(".instance_*.json"):
        try:
            p.unlink()
        except Exception:
            pass

    default_instance = [
        {
            "name": "System Proxy",
            "order": 0,
            "url": "",
            "listen": "127.0.0.1:1015",
            "username": "",
            "password": "",
            "udp": True,
            "connectTimeout": 10,
            "udpIdleTimeout": 60,
            "logLevel": "info",
            "backend": "auto",
            "xrayPath": "",
            "xrayLegacyConfig": False,
            "killswitch": True,
        }
    ]
    with open(INSTANCES_FILE, "w", encoding="utf-8") as f:
        json.dump(default_instance, f, indent=2, ensure_ascii=False)
        f.write("\n")

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(default_instance[0], f, indent=2, ensure_ascii=False)
        f.write("\n")

    save_backup_password("")
