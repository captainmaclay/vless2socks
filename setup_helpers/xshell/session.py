"""
Xshell 8 Session Manager & Cryptography Helper
Handles generation and modification of .xsh session files, including
NetSarang v7/v8 credential encryption (RC4 + SHA256 with reversed SID+Username KDF).
"""

import base64
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import subprocess
from typing import Optional, Tuple


def rc4(key: bytes, data: bytes) -> bytes:
    """Pure-Python ARC4 (RC4) stream cipher implementation."""
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) % 256
        S[i], S[j] = S[j], S[i]
    i = j = 0
    out = bytearray()
    for byte in data:
        i = (i + 1) % 256
        j = (j + S[i]) % 256
        S[i], S[j] = S[j], S[i]
        out.append(byte ^ S[(S[i] + S[j]) % 256])
    return bytes(out)


def get_windows_identity() -> Tuple[str, str]:
    """
    Retrieve current Windows username (with exact token casing) and user SID string.
    """
    # 1. Exact username from token
    buf = ctypes.create_unicode_buffer(256)
    size = wintypes.DWORD(256)
    if ctypes.windll.advapi32.GetUserNameW(buf, ctypes.byref(size)):
        username = buf.value
    else:
        username = os.environ.get("USERNAME", "Admin")

    # 2. SID from whoami
    sid = ""
    try:
        out = subprocess.check_output("whoami /user", shell=True, text=True)
        for line in out.splitlines():
            parts = line.strip().split()
            if parts and parts[-1].startswith("S-1-5-"):
                sid = parts[-1]
                break
    except Exception:
        pass

    if not sid:
        sid = "S-1-5-21-1216202834-3237055983-2975759330-1000"

    return username, sid


def encrypt_password(password: str, username: Optional[str] = None, sid: Optional[str] = None) -> str:
    """
    Encrypt a password using NetSarang v7/v8 encryption scheme:
    KDF context: (Username[::-1] + SID)[::-1] == SID[::-1] + Username
    Cipher: RC4(SHA256(KDF))
    Payload: RC4_encrypt(pwd) + SHA256(pwd)
    Output: Base64 string
    """
    if not username or not sid:
        u, s = get_windows_identity()
        username = username or u
        sid = sid or s

    # NetSarang v7/v8 reverse KDF string formula
    tmp = username[::-1] + sid
    sid_string = tmp[::-1]

    key = hashlib.sha256(sid_string.encode("ascii")).digest()
    pwd_bytes = password.encode("utf-8")
    checksum = hashlib.sha256(pwd_bytes).digest()

    encrypted = rc4(key, pwd_bytes)
    payload = encrypted + checksum
    return base64.b64encode(payload).decode("ascii")


def decrypt_password(b64_str: str, username: Optional[str] = None, sid: Optional[str] = None) -> Optional[str]:
    """Decrypt a NetSarang v7/v8 session password."""
    if not username or not sid:
        u, s = get_windows_identity()
        username = username or u
        sid = sid or s

    tmp = username[::-1] + sid
    sid_string = tmp[::-1]
    key = hashlib.sha256(sid_string.encode("ascii")).digest()

    raw = base64.b64decode(b64_str)
    if len(raw) < 32:
        return None

    ciphertext = raw[:-32]
    expected_checksum = raw[-32:]

    plaintext = rc4(key, ciphertext)
    if hashlib.sha256(plaintext).digest() == expected_checksum:
        return plaintext.decode("utf-8")
    return None


class XshellSessionManager:
    """Manages Xshell 8 session files in the user documents directory."""

    def __init__(self, base_dir: Optional[Path] = None):
        if base_dir is None:
            self.base_dir = Path.home() / "Documents" / "NetSarang Computer" / "8" / "Xshell" / "Sessions"
        else:
            self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.default_xshf = self.base_dir / "default.xshf"

    def create_session(
        self,
        name: str,
        host: str,
        user: str,
        password: str,
        port: int = 22,
        proxy: str = "SOCKS5_1030",
        overwrite: bool = True
    ) -> Path:
        """
        Create a new Xshell .xsh session file pre-configured with host, credentials,
        and designated proxy tunnel.
        """
        if not name.endswith(".xsh"):
            filename = f"{name}.xsh"
        else:
            filename = name

        target_file = self.base_dir / filename
        if target_file.exists() and not overwrite:
            raise FileExistsError(f"Session file '{target_file}' already exists.")

        encrypted_pwd = encrypt_password(password)

        # Base content from default template if available
        if self.default_xshf.exists():
            content = self.default_xshf.read_text(encoding="utf-16")
        else:
            content = "[SessionInfo]\nVersion=8.1\nDescription=Xshell session file\n[CONNECTION]\nPort=22\nHost=\nProtocol=SSH\n[CONNECTION:AUTHENTICATION]\nUserName=\nAuthMethodList=01\n[CONNECTION:PROXY]\nProxy=\nStartUp=0\n"

        # Apply Host and Port
        content = content.replace("Host=\r\n", f"Host={host}\r\n")
        content = content.replace("Host=\n", f"Host={host}\n")
        content = content.replace("Port=22\r\n", f"Port={port}\r\n")
        content = content.replace("Port=22\n", f"Port={port}\n")

        # Apply User and Encrypted Password
        auth_block = f"UserName={user}\r\nPassword={encrypted_pwd}\r\n"
        if "UserName=\r\n" in content:
            content = content.replace("UserName=\r\n", auth_block)
        elif "UserName=\n" in content:
            content = content.replace("UserName=\n", f"UserName={user}\nPassword={encrypted_pwd}\n")

        # Apply Proxy
        if f"Proxy={proxy}" not in content:
            content = content.replace("Proxy=\r\n", f"Proxy={proxy}\r\n")
            content = content.replace("Proxy=\n", f"Proxy={proxy}\n")

        target_file.write_text(content, encoding="utf-16")
        return target_file

    def list_sessions(self):
        """List all .xsh session files."""
        return list(self.base_dir.glob("*.xsh"))

    def delete_session(self, name: str) -> bool:
        """Remove a session file if it exists."""
        if not name.endswith(".xsh"):
            name = f"{name}.xsh"
        f = self.base_dir / name
        if f.exists():
            f.unlink()
            return True
        return False
