"""Выбор движка: собственный Python-клиент или xray-core.

Правило простое: всё, что Python-клиент умеет, он и обслуживает; остальное —
reality, xtls-flow, ws/grpc — уходит в xray, если тот установлен.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig
from .url import ConfigError

__all__ = ["BackendChoice", "resolve_backend", "PYTHON", "XRAY"]

PYTHON = "python"
XRAY = "xray"
VALID = (PYTHON, XRAY, "auto")


@dataclass
class BackendChoice:
    name: str
    reason: str
    #: Путь к найденному xray (только для name == "xray").
    xray_path: str = ""
    xray_version: str = ""

    def __str__(self) -> str:
        if self.name == XRAY:
            version = f" {self.xray_version}" if self.xray_version else ""
            return f"xray-core{version} ({self.reason})"
        return f"встроенный Python-клиент ({self.reason})"


def resolve_backend(config: AppConfig) -> BackendChoice:
    """Решить, каким движком поднимать прокси.

    Бросает :class:`ConfigError`, если выбранный вручную движок невозможен, —
    молча подменять движок за спиной пользователя неправильно.
    """
    requested = (config.backend or "auto").lower()
    if requested not in VALID:
        raise ConfigError(
            f"backend={requested!r} — допустимо: {', '.join(VALID)}"
        )

    from .url import SocksServer

    if isinstance(config.server, SocksServer):
        if requested == PYTHON:
            raise ConfigError(
                "backend=python не поддерживает SOCKS5 upstream. "
                "Используйте backend=xray или auto."
            )
        path, version = _locate_xray(config, required=True)
        return BackendChoice(XRAY, "SOCKS5 upstream через xray", path, version)

    unsupported = config.server.unsupported
    params = ", ".join(f"{u.param}={u.value}" for u in unsupported)

    if requested == PYTHON:
        if unsupported:
            raise ConfigError(
                f"backend=python не потянет эту ссылку: {params}. "
                f"{unsupported[0].reason} "
                f"Уберите --backend python, чтобы задействовать xray."
            )
        return BackendChoice(PYTHON, "выбран вручную")

    if requested == XRAY:
        path, version = _locate_xray(config, required=True)
        return BackendChoice(XRAY, "выбран вручную", path, version)

    # auto
    if not unsupported:
        return BackendChoice(PYTHON, "профиль поддержан целиком")

    path, version = _locate_xray(config, required=False)
    if path:
        return BackendChoice(XRAY, f"профиль требует: {params}", path, version)

    raise ConfigError(
        f"этот профиль требует xray: {params}.\n"
        f"{unsupported[0].reason} {unsupported[0].remedy}\n\n"
        f"Установить xray одной командой:\n"
        f"  python tools/get_xray.py"
    )


def _locate_xray(config: AppConfig, *, required: bool) -> tuple[str, str]:
    from .xray import XrayNotFound, find_xray, xray_version

    try:
        path = find_xray(config.xray_path or None)
    except XrayNotFound as exc:
        if required:
            raise ConfigError(str(exc)) from None
        return "", ""
    return str(path), xray_version(path)
