"""Запуск дочерних процессов без всплывающего окна консоли.

На Windows консольная программа, запущенная из процесса без консоли (а GUI и
собранный windowed-exe именно такие), получает свежевыделенную консоль — и
пользователь видит мигающее окно или новую вкладку терминала. Для xray это
особенно заметно: его опрашивают на версию при каждом старте и держат запущенным
всё время работы прокси.

``CREATE_NO_WINDOW`` убирает консоль, а ``STARTUPINFO`` с ``SW_HIDE`` страхует
случай, когда флаг проигнорирован (например, если процесс стартует через
оболочку). На не-Windows возвращается пустой словарь, так что вызов безопасно
раскрывать в любые ``subprocess``-функции и в ``asyncio.create_subprocess_exec``.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

__all__ = ["no_window_kwargs"]


def no_window_kwargs() -> dict[str, Any]:
    """Аргументы для subprocess, прячущие окно консоли дочернего процесса."""
    if sys.platform != "win32":
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }
