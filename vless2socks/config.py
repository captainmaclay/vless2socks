"""Конфигурация приложения: config.json + аргументы командной строки."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .url import (
    ConfigError,
    SocksServer,
    VlessServer,
    parse_proxy_url,
    parse_socks_url,
    parse_vless_url,
    server_from_mapping,
)

__all__ = ["AppConfig", "load_config", "ConfigError"]


@dataclass
class AppConfig:
    server: VlessServer | SocksServer
    listen_host: str = "127.0.0.1"
    listen_port: int = 1081
    username: str = ""
    password: str = ""
    udp_enabled: bool = True
    connect_timeout: float = 10.0
    udp_idle_timeout: float = 60.0
    log_level: str = "info"
    #: auto | python | xray
    backend: str = "auto"
    #: Явный путь к бинарнику xray (иначе ищется в bin/, рядом и в PATH).
    xray_path: str = ""
    #: Писать outbound в старой форме (vnext) — для xray до версии 26.
    #: Обычно не нужно: при отказе формат подбирается автоматически.
    xray_legacy_config: bool = False
    killswitch: bool = False
    name: str = ""
    order: float = 0.0
    send_through: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def auth_required(self) -> bool:
        return bool(self.username)

    def describe(self) -> str:
        auth = "user/pass" if self.auth_required else "no-auth"
        udp = "UDP on" if self.udp_enabled else "UDP off"
        return (
            f"socks5://{self.listen_host}:{self.listen_port} ({auth}, {udp}) "
            f"-> {self.server.describe()}"
        )


DEFAULT_CONFIG = {
    "url": "vless://00000000-0000-0000-0000-000000000000@example.com:443"
           "?security=tls&type=tcp&sni=example.com#my-server",
    "listen": "127.0.0.1:1081",
    "username": "",
    "password": "",
    "udp": True,
    "connectTimeout": 10,
    "udpIdleTimeout": 60,
    "logLevel": "info",
    "backend": "auto",
    "xrayPath": "",
    "xrayLegacyConfig": False,
}


def _split_listen(value: str, default_host: str, default_port: int) -> tuple[str, int]:
    value = str(value).strip()
    if not value:
        return default_host, default_port
    if value.startswith("["):  # [::1]:1080
        host, _, rest = value[1:].partition("]")
        port = rest.lstrip(":")
        return host, int(port) if port else default_port
    if value.count(":") > 1:
        # Голый IPv6 без скобок: "::1" — это адрес целиком, а не host:port.
        # "::1:1080" неотличимо от адреса, поэтому требуем скобки для порта.
        return value, default_port
    if ":" in value:
        host, _, port = value.rpartition(":")
        return (host or default_host), int(port)
    if value.isdigit():
        return default_host, int(value)
    return value, default_port


def load_config(
    path: str | Path | None = None,
    *,
    url: str | None = None,
    listen: str | None = None,
    username: str | None = None,
    password: str | None = None,
    udp: bool | None = None,
    log_level: str | None = None,
    backend: str | None = None,
    xray_path: str | None = None,
    xray_legacy_config: bool | None = None,
    strict: bool = True,
) -> AppConfig:
    """Собрать конфигурацию: файл (если есть) + переопределения из CLI.

    :param strict: ``False`` — не падать на неподдерживаемых параметрах ссылки
        (нужно режиму ``--doctor``, который должен дойти до сетевых проверок).
    """
    data: dict[str, Any] = {}

    if path is not None:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"файл конфигурации не найден: {p}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{p}: некорректный JSON — {exc}") from None
        if not isinstance(data, dict):
            raise ConfigError(f"{p}: ожидался объект JSON на верхнем уровне")

    if url:
        server = parse_proxy_url(url, strict=strict)
    elif data:
        server = server_from_mapping(data, strict=strict)
    else:
        raise ConfigError(
            "не задан сервер: укажите --url 'vless://...' или 'socks5://...' или -c config.json"
        )

    host, port = _split_listen(
        listen if listen is not None else data.get("listen", ""),
        "127.0.0.1",
        1081,
    )

    default_ks = True if port == 1015 else False
    default_name = "System Proxy" if port == 1015 else ""
    try:
        raw_order = data.get("order")
        order_val = float(raw_order) if raw_order is not None else (0.0 if port == 1015 else 1.0)
    except (ValueError, TypeError):
        order_val = 0.0 if port == 1015 else 1.0

    return AppConfig(
        server=server,
        listen_host=host,
        listen_port=port,
        username=str(username if username is not None else data.get("username", "")),
        password=str(password if password is not None else data.get("password", "")),
        udp_enabled=bool(udp if udp is not None else data.get("udp", True)),
        connect_timeout=float(data.get("connectTimeout", 10) or 10),
        udp_idle_timeout=float(data.get("udpIdleTimeout", 60) or 60),
        log_level=str(log_level or data.get("logLevel", "info")),
        backend=str(backend or data.get("backend", "auto")).lower(),
        xray_path=str(xray_path if xray_path is not None else data.get("xrayPath", "")),
        xray_legacy_config=bool(
            xray_legacy_config
            if xray_legacy_config is not None
            else data.get("xrayLegacyConfig", False)
        ),
        killswitch=bool(data.get("killswitch", default_ks)),
        name=str(data.get("name", default_name)),
        order=order_val,
        send_through=str(data.get("sendThrough") or data.get("send_through") or "").strip(),
    )
