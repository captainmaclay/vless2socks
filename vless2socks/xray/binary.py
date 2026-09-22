"""Поиск исполняемого файла xray и опрос его версии."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..noconsole import no_window_kwargs
from ..paths import APP_DIR

__all__ = ["XrayNotFound", "find_xray", "xray_version", "search_paths"]

EXE = "xray.exe" if sys.platform == "win32" else "xray"

#: Куда get_xray.py кладёт скачанный бинарник. В собранном виде — bin/ рядом с .exe.
BUNDLED_DIR = APP_DIR / "bin"


class XrayNotFound(FileNotFoundError):
    """xray не найден ни в одном из мест поиска."""


def search_paths() -> list[Path]:
    """Места, где ищется xray — в порядке приоритета."""
    candidates = [
        BUNDLED_DIR / EXE,
        APP_DIR / EXE,
        Path.cwd() / "bin" / EXE,
        Path.cwd() / EXE,
    ]
    env = os.environ.get("XRAY_PATH")
    if env:
        candidates.insert(0, Path(env))
    return candidates


def find_xray(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Найти xray. Бросает :class:`XrayNotFound` с понятным объяснением."""
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
        raise XrayNotFound(
            f"указанный путь к xray не годится: {path}\n"
            f"Файла нет или он не исполняемый."
        )

    for candidate in search_paths():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()

    from_path = shutil.which("xray")
    if from_path:
        return Path(from_path).resolve()

    looked = "\n".join(f"  {p}" for p in search_paths())
    raise XrayNotFound(
        "не найден исполняемый файл xray. Искал здесь:\n"
        f"{looked}\n  (а также в PATH)\n\n"
        "Скачать одной командой:\n"
        "  python tools/get_xray.py\n\n"
        "Либо положите xray вручную в папку bin/ рядом с проектом, "
        "либо укажите путь: --xray-path C:\\путь\\к\\xray.exe"
    )


def xray_version(path: str | os.PathLike[str]) -> str:
    """Спросить у бинарника версию. Пустая строка, если не ответил."""
    try:
        proc = subprocess.run(
            [str(path), "version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            # Иначе на Windows каждый опрос версии мигает окном консоли.
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""

    output = (proc.stdout or "") + (proc.stderr or "")
    match = re.search(r"Xray[ ]+([0-9][^ (]*)", output)
    if match:
        return match.group(1)
    first = output.strip().splitlines()
    return first[0].strip() if first else ""
