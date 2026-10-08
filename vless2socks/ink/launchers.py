"""Generators for batch launcher scripts (.bat) and third-party app configurations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

from .icon_engine import DEFAULT_CHROME_PATHS, DEFAULT_XSHELL_PATHS, find_binary


def build_chrome_bat(
    port: int,
    profile_name: Optional[str] = None,
    chrome_path: Optional[str] = None,
    out_bat_path: Optional[Union[str, Path]] = None,
) -> str:
    """Generates a standalone .bat launcher for Google Chrome with proxy and isolated user-data-dir."""
    profile = profile_name or f"Proxy{port}"
    chrome_exe = chrome_path or find_binary(DEFAULT_CHROME_PATHS) or r"C:\Program Files\Google\Chrome\Application\chrome.exe"

    bat_content = f"""@echo off
chcp 65001 >nul
:: Google Chrome через SOCKS5 сокет 127.0.0.1:{port}
start "" "{chrome_exe}" --proxy-server="socks5://127.0.0.1:{port}" --user-data-dir="%LOCALAPPDATA%\\Google\\Chrome\\{profile}" %*
"""
    if out_bat_path:
        out_p = Path(out_bat_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            f.write(bat_content)
        return str(out_p)

    return bat_content


def build_xshell_bat(
    port: int,
    http_port: Optional[int] = None,
    xshell_path: Optional[str] = None,
    out_bat_path: Optional[Union[str, Path]] = None,
) -> str:
    """Generates a standalone .bat launcher for NetSarang Xshell with proxy environment variables."""
    h_port = http_port or (port + 10000 if port < 50000 else port)
    xshell_exe = xshell_path or find_binary(DEFAULT_XSHELL_PATHS) or r"C:\Program Files (x86)\NetSarang\Xshell 8\Xshell.exe"

    bat_content = f"""@echo off
chcp 65001 >nul
:: Запуск NetSarang Xshell через SOCKS5 сокет 127.0.0.1:{port}
set ALL_PROXY=socks5://127.0.0.1:{port}
set SOCKS_SERVER=127.0.0.1:{port}
set SOCKS5_SERVER=127.0.0.1:{port}
set HTTP_PROXY=http://127.0.0.1:{h_port}
set HTTPS_PROXY=http://127.0.0.1:{h_port}

start "" "{xshell_exe}" %*
"""
    if out_bat_path:
        out_p = Path(out_bat_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            f.write(bat_content)
        return str(out_p)

    return bat_content


def configure_xshell_proxy(port: int, proxy_name: Optional[str] = None) -> bool:
    """Configures Xshell 8 Common/Proxy INI and sets default session to use the proxy without breaking local shell."""
    name = proxy_name or f"Socket{port}"
    documents = os.path.expanduser(r"~\Documents")
    proxy_common_dir = os.path.join(documents, r"NetSarang Computer\8\Common\Proxy")

    try:
        os.makedirs(proxy_common_dir, exist_ok=True)
        ini_file = os.path.join(proxy_common_dir, f"{name}.ini")

        # NetSarang reads UTF-16 with BOM for Proxy INIs
        ini_content = f"[SECTION]\r\nTYPE=2\r\nHOST=127.0.0.1\r\nPORT={port}\r\nUSERNAME=\r\nPASSWORD=\r\n"
        with open(ini_file, "w", encoding="utf-16", newline="") as f:
            f.write(ini_content)

        # Update default.xshf to reference this proxy if available, ensuring StartUp=0
        default_xshf = os.path.join(documents, r"NetSarang Computer\8\Xshell\Sessions\default.xshf")
        if os.path.isfile(default_xshf):
            with open(default_xshf, "r", encoding="utf-16", errors="ignore") as f:
                text = f.read()

            import re

            # Replace or ensure [CONNECTION:PROXY] with Proxy=<name> and StartUp=0
            if "[CONNECTION:PROXY]" in text:
                text = re.sub(
                    r"\[CONNECTION:PROXY\]\r?\nProxy=[^\r\n]*\r?\nStartUp=[^\r\n]*",
                    f"[CONNECTION:PROXY]\r\nProxy={name}\r\nStartUp=0",
                    text,
                )
                text = text.lstrip("\ufeff")
                with open(default_xshf, "w", encoding="utf-16", newline="") as f:
                    f.write(text)

        return True
    except Exception as e:
        return False
