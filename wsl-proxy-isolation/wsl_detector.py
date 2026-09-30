"""Модуль обнаружения и проверки статуса WSL (Windows Subsystem for Linux)."""

from __future__ import annotations

import subprocess
import shutil
import re
from typing import Dict, List, Optional, Any

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def is_wsl_installed() -> bool:
    """Быстро проверяет, установлен ли исполняемый файл wsl.exe в системе."""
    return shutil.which("wsl.exe") is not None or shutil.which("wsl") is not None


def get_wsl_distributions() -> List[Dict[str, Any]]:
    """Возвращает список установленных дистрибутивов WSL, их статус и версию WSL (1 или 2)."""
    if not is_wsl_installed():
        return []

    try:
        proc = subprocess.run(
            ["wsl.exe", "-l", "-v"],
            capture_output=True,
            text=True,
            encoding="utf-16-le",  # wsl -l -v часто выдает utf-16le на Windows
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=5.0,
        )
        output = proc.stdout
        if not output or "NAME" not in output:
            # Fallback to default encoding if utf-16-le wasn't matched
            proc_fallback = subprocess.run(
                ["wsl.exe", "-l", "-v"],
                capture_output=True,
                text=True,
                creationflags=CREATE_NO_WINDOW,
                timeout=5.0,
            )
            output = proc_fallback.stdout

        lines = [line.strip() for line in output.splitlines() if line.strip()]
        distros = []
        for line in lines[1:]:  # пропускаем заголовок NAME STATE VERSION
            is_default = line.startswith("*")
            cleaned = line.lstrip("*").strip()
            parts = re.split(r"\s{2,}", cleaned)
            if len(parts) >= 3:
                name, state, version = parts[0], parts[1], parts[2]
                distros.append({
                    "name": name,
                    "state": state,
                    "version": version,
                    "is_default": is_default,
                })
            elif len(parts) == 2:
                name, state = parts[0], parts[1]
                distros.append({
                    "name": name,
                    "state": state,
                    "version": "unknown",
                    "is_default": is_default,
                })
        return distros
    except Exception:
        return []


def get_active_distro(preferred: Optional[str] = None) -> Optional[str]:
    """Возвращает имя предпочтительного или запущенного/дефолтного дистрибутива WSL."""
    distros = get_wsl_distributions()
    if not distros:
        return None

    if preferred:
        for d in distros:
            if d["name"].lower() == preferred.lower():
                return d["name"]

    # Ищем дефолтный
    for d in distros:
        if d.get("is_default"):
            return d["name"]

    # Ищем запущенный
    for d in distros:
        if "running" in d.get("state", "").lower():
            return d["name"]

    return distros[0]["name"]
