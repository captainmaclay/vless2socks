"""Где лежат файлы приложения — одинаково для скрипта и для собранного .exe.

PyInstaller в режиме onefile распаковывает сборку во временную папку и
подставляет её в ``__file__``. Поэтому по ``__file__`` рядом с настоящим .exe
ничего не найти: пользовательские файлы (config.json, instances.json,
settings.json, .env, bin/, runtime/) надо искать в каталоге самого .exe, а
зашитые в сборку шаблоны — в каталоге распаковки.

Один источник правды на весь проект: раньше каждый модуль считал корень сам
через ``Path(__file__).resolve().parent``, и в сборке все пять расчётов
указывали не туда.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

__all__ = ["APP_DIR", "BUNDLE_DIR", "FROZEN", "INSTANCES_DIR", "bundled", "instance_config_path",
           "migrate_instance_configs", "unpack_bundled_bin"]

#: Запущены из собранного PyInstaller'ом .exe?
FROZEN = bool(getattr(sys, "frozen", False))

#: Рабочий каталог: рядом с .exe, а в режиме скрипта — корень проекта.
#: Здесь живут config.json, instances.json, settings.json, .env, bin/, runtime/.
APP_DIR = (
    Path(sys.executable).resolve().parent
    if FROZEN
    else Path(__file__).resolve().parent.parent
)

#: Рабочие конфиги прокси (``.instance_<id>.json``), раньше лежали прямо в :data:`APP_DIR`.
INSTANCES_DIR = APP_DIR / "instances"

#: Каталог с зашитыми в сборку данными (*.example.json, bin/).
#: В режиме скрипта совпадает с :data:`APP_DIR`.
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))


def bundled(name: str) -> Path:
    """Путь к файлу, зашитому в сборку (шаблоны ``*.example.json``, ``bin/...``)."""
    return BUNDLE_DIR / name


def instance_config_path(global_id: int | str, base: Path | None = None) -> Path:
    """Путь к рабочему конфигу прокси ``instances/.instance_<id>.json`` (папка создаётся)."""
    folder = (base / "instances") if base is not None else INSTANCES_DIR
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return folder / f".instance_{global_id}.json"


def migrate_instance_configs(base: Path | None = None) -> int:
    """Перенести старые ``.instance_*.json`` из корня в ``instances/``. Возвращает число файлов."""
    root = base if base is not None else APP_DIR
    target = root / "instances"
    moved = 0
    for src in root.glob(".instance_*.json"):
        try:
            target.mkdir(parents=True, exist_ok=True)
            src.replace(target / src.name)
            moved += 1
        except OSError:
            pass
    return moved


def unpack_bundled_bin() -> None:
    """Распаковать зашитые файлы из папки bin/ (xray.exe, dat-базы) в рабочий каталог рядом с .exe.
    Делается быстро и только если их еще нет или они неполные.
    """
    target_bin = APP_DIR / "bin"
    bundled_bin = BUNDLE_DIR / "bin"
    if not bundled_bin.is_dir():
        return

    target_bin.mkdir(parents=True, exist_ok=True)
    for src in bundled_bin.iterdir():
        if not src.is_file() or src.name.startswith("."):
            continue
        dst = target_bin / src.name
        if not dst.exists() or dst.stat().st_size == 0:
            try:
                shutil.copy2(src, dst)
            except Exception:
                pass

