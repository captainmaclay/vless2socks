#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vendor C Environment and Profile Management for vless2socks (Research & Testing).

Maps proxy ports to Vendor C profiles by default:
  - Profile 1 -> Port 1015 (~/.claude.json)
  - Profile 2 -> Port 1016 (~/.claude-2/.claude.json; old name ~/.claude-secondary)
  - Profile N -> Port 1015 + (N - 1) (~/.claude-N/.claude.json, ~/.claude-PORT/.claude.json)

Parses active OAuth profile details (email, display name, organization) and synchronizes
with vless2socks instances.json, state cache, and live Overview GUI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Optional

VLESS2SOCKS_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(VLESS2SOCKS_DIR, "vendor_c_accounts.json")
LEGACY_STATE_FILE = os.path.join(VLESS2SOCKS_DIR, "claude_accounts.json")
INSTANCES_FILE = os.path.join(VLESS2SOCKS_DIR, "instances.json")
USER_HOME = os.environ.get("USERPROFILE") or os.path.expanduser("~")


DEFAULT_PORT_PROFILE_MAP = {
    1015: 1,
    1016: 2,
    1017: 3,
    1020: 3,
    1021: 4,
    1030: 5,
}


def get_default_account_num(port: int, cfg: Optional[dict] = None) -> int:
    """Calculates default 1-based profile number from port or config override."""
    if cfg:
        val = cfg.get("vendor_c_account_num") or cfg.get("claude_account_num")
        if val is not None:
            try:
                num = int(val)
                if num > 0:
                    return num
            except (ValueError, TypeError):
                pass
    try:
        port = int(port)
    except (ValueError, TypeError):
        return 1

    return DEFAULT_PORT_PROFILE_MAP.get(port, 1)


def get_vendor_c_config_candidates(port: int, account_num: Optional[int] = None) -> list[str]:
    """Returns candidate paths to profile configuration files for the given port/profile."""
    if account_num is None:
        account_num = get_default_account_num(port)

    candidates = []
    if account_num == 1 or port == 1015:
        candidates.append(os.path.join(USER_HOME, ".claude.json"))
        candidates.append(os.path.join(USER_HOME, ".claude", "config.json"))
    elif account_num == 2 or port == 1016:
        candidates.append(os.path.join(USER_HOME, ".claude-2", ".claude.json"))
        candidates.append(os.path.join(USER_HOME, ".claude-secondary", ".claude.json"))   # old name of profile 2
        candidates.append(os.path.join(USER_HOME, ".claude-1016", ".claude.json"))
    else:
        candidates.append(os.path.join(USER_HOME, f".claude-{account_num}", ".claude.json"))
        if port in DEFAULT_PORT_PROFILE_MAP:
            candidates.append(os.path.join(USER_HOME, f".claude-{port}", ".claude.json"))

    if port in DEFAULT_PORT_PROFILE_MAP:
        candidates.append(os.path.join(USER_HOME, f".claude-{port}", ".claude.json"))
    candidates.append(os.path.join(USER_HOME, f".claude-{account_num}", ".claude.json"))

    seen = set()
    unique = []
    for c in candidates:
        norm = os.path.normpath(c)
        if norm not in seen:
            seen.add(norm)
            unique.append(norm)
    return unique


# Backward-compatible alias
get_claude_config_candidates = get_vendor_c_config_candidates


def parse_vendor_c_config(config_path: str) -> dict[str, Any]:
    """Parses oauthAccount block from a profile configuration file."""
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}

        oauth = data.get("oauthAccount") or {}
        email = (oauth.get("emailAddress") or "").strip()
        display_name = (oauth.get("displayName") or oauth.get("fullName") or "").strip()
        org_type = (oauth.get("organizationType") or "").strip()
        account_uuid = (oauth.get("accountUuid") or "").strip()

        return {
            "email": email,
            "display_name": display_name,
            "org_type": org_type,
            "account_uuid": account_uuid,
            "logged_in": bool(email),
            "config_path": config_path,
        }
    except Exception:
        return {}


# Backward-compatible alias
parse_claude_config = parse_vendor_c_config


def load_cached_accounts() -> dict[str, dict[str, Any]]:
    """Loads cache from vendor_c_accounts.json or legacy claude_accounts.json."""
    for p in (STATE_FILE, LEGACY_STATE_FILE):
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}


def save_cached_accounts(cache: dict[str, dict[str, Any]]) -> None:
    """Saves cache to vendor_c_accounts.json and legacy file for compatibility."""
    for p in (STATE_FILE, LEGACY_STATE_FILE):
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


def get_vendor_c_account_for_port(port: int, cfg: Optional[dict] = None) -> dict[str, Any]:
    """Resolves live or cached Vendor C profile information for a proxy port."""
    acc_num = get_default_account_num(port, cfg)
    candidates = get_vendor_c_config_candidates(port, acc_num)

    info: dict[str, Any] = {
        "port": port,
        "account_num": acc_num,
        "email": "",
        "name": "",
        "org_type": "",
        "logged_in": False,
        "config_path": candidates[0] if candidates else "",
        "is_vendor_c": (port in (1015, 1016, 1017) or bool(cfg and (cfg.get("system_proxy") or cfg.get("System_Proxy")))),
    }

    # 1. Try reading live candidate config files
    for path in candidates:
        parsed = parse_vendor_c_config(path)
        if parsed.get("email"):
            info["email"] = parsed["email"]
            info["name"] = parsed["display_name"]
            info["org_type"] = parsed["org_type"]
            info["logged_in"] = True
            info["config_path"] = path
            return info

    # 2. Check cfg fields if present
    if cfg:
        cfg_email = cfg.get("vendor_c_email") or cfg.get("claude_email") or cfg.get("claude_account")
        if cfg_email:
            info["email"] = str(cfg_email).strip()
            info["name"] = str(cfg.get("vendor_c_name") or cfg.get("claude_name") or "").strip()
            info["logged_in"] = True
            return info

    # 3. Check cached state file
    cache = load_cached_accounts()
    cached_entry = cache.get(str(port))
    if cached_entry and cached_entry.get("email"):
        info["email"] = cached_entry.get("email", "")
        info["name"] = cached_entry.get("name", "")
        info["org_type"] = cached_entry.get("org_type", "")
        info["logged_in"] = bool(info["email"])

    return info


# Backward-compatible alias
get_claude_account_for_port = get_vendor_c_account_for_port


def sync_vendor_c_account(port: int, notify_ipc: bool = True) -> dict[str, Any]:
    """Synchronizes Vendor C profile metadata with instances.json, cache, and IPC."""
    acc_info = get_vendor_c_account_for_port(port)
    port_str = str(port)

    # 1. Update cache files
    cache = load_cached_accounts()
    cache[port_str] = {
        "account_num": acc_info["account_num"],
        "email": acc_info["email"],
        "name": acc_info["name"],
        "org_type": acc_info["org_type"],
        "logged_in": acc_info["logged_in"],
        "updated_at": int(time.time()),
    }
    save_cached_accounts(cache)

    # 2. If GUI is running, notify via IPC (GUI will update in-memory instances and save cleanly)
    ipc_sent = False
    if notify_ipc:
        try:
            import socket
            with socket.create_connection(("127.0.0.1", 10199), timeout=0.4) as s:
                s.settimeout(0.4)
                s.sendall(b"UPDATE_VENDOR_C\n")
                s.recv(128)
                ipc_sent = True
        except Exception:
            pass

    # 3. Only update instances.json directly if GUI is NOT running (no race condition),
    # and NEVER tamper with system_proxy / System_Proxy flags!
    if not ipc_sent and os.path.exists(INSTANCES_FILE):
        try:
            with open(INSTANCES_FILE, "r", encoding="utf-8") as f:
                instances = json.load(f)
            changed = False
            for inst in instances:
                listen = str(inst.get("listen", "")).strip()
                if f":{port}" in listen:
                    inst["vendor_c_account_num"] = acc_info["account_num"]
                    inst["vendor_c_email"] = acc_info["email"]
                    inst["vendor_c_name"] = acc_info["name"]
                    inst["vendor_c_updated_at"] = int(time.time())
                    # Keep legacy keys for full compatibility
                    inst["claude_account_num"] = acc_info["account_num"]
                    inst["claude_email"] = acc_info["email"]
                    inst["claude_name"] = acc_info["name"]
                    inst["claude_updated_at"] = int(time.time())
                    changed = True
                    break
            if changed:
                with open(INSTANCES_FILE, "w", encoding="utf-8") as f:
                    json.dump(instances, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    return acc_info


# Backward-compatible and profile aliases
get_default_profile_num = get_default_account_num
sync_claude_account = sync_vendor_c_account


def main():
    parser = argparse.ArgumentParser(description="Synchronize Vendor C profile metadata for vless2socks.")
    parser.add_argument("--port", type=int, default=1015, help="SOCKS5 port (default: 1015)")
    parser.add_argument("--quiet", action="store_true", help="Suppress console output")
    args = parser.parse_args()

    info = sync_vendor_c_account(args.port)
    if not args.quiet:
        email_str = info["email"] if info["logged_in"] else "Не авторизован"
        name_str = f" ({info['name']})" if info["name"] else ""
        print(f"[Vendor C Sync] Порт {info['port']} -> {info['account_num']} профиль: {email_str}{name_str}")


if __name__ == "__main__":
    main()
