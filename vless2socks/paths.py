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

import sys
from pathlib import Path

__all__ = ["APP_DIR", "BUNDLE_DIR", "FROZEN", "bundled"]

#: Запущены из собранного PyInstaller'ом .exe?
FROZEN = bool(getattr(sys, "frozen", False))

#: Рабочий каталог: рядом с .exe, а в режиме скрипта — корень проекта.
#: Здесь живут config.json, instances.json, settings.json, .env, bin/, runtime/.
APP_DIR = (
    Path(sys.executable).resolve().parent
    if FROZEN
    else Path(__file__).resolve().parent.parent
)

#: Каталог с зашитыми в сборку данными (*.example.json).
#: В режиме скрипта совпадает с :data:`APP_DIR`.
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))


def bundled(name: str) -> Path:
    """Путь к файлу, зашитому в сборку (шаблоны ``*.example.json``)."""
    return BUNDLE_DIR / name
