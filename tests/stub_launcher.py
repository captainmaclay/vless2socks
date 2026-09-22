"""Сделать из stub_xray.py «исполняемый файл», который можно запустить как xray.

На POSIX это shell-скрипт с shebang, на Windows — .cmd. Нужно, чтобы тесты
жизненного цикла работали на обеих платформах: XrayProcess запускает путь
напрямую, а не через интерпретатор.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

STUB = Path(__file__).resolve().parent / "stub_xray.py"


def make_stub_xray(directory: str | os.PathLike[str], name: str = "xray") -> Path:
    """Создать launcher в ``directory`` и вернуть путь к нему."""
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        path = target_dir / f"{name}.cmd"
        path.write_text(
            "@echo off\r\n"
            f'"{sys.executable}" "{STUB}" %*\r\n',
            encoding="ascii",
        )
        return path

    path = target_dir / name
    path.write_text(
        "#!/bin/sh\n"
        f'exec "{sys.executable}" "{STUB}" "$@"\n',
        encoding="ascii",
    )
    path.chmod(0o755)
    return path
