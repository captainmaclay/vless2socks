"""Бэкенд на xray-core: для профилей, которые чистый Python реализовать не может.

Python здесь — оболочка: разбирает ссылку, собирает конфиг, запускает и стережёт
процесс. По VLESS говорит сам xray.
"""

from .binary import XrayNotFound, find_xray, xray_version
from .config_builder import build_xray_config, describe_config
from .runner import XrayProcess, XrayStartupError

__all__ = [
    "build_xray_config",
    "describe_config",
    "find_xray",
    "xray_version",
    "XrayNotFound",
    "XrayProcess",
    "XrayStartupError",
]
