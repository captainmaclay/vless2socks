"""Разбор ссылок вида ``vless://uuid@host:port?params#remark``.

Поддерживается подмножество, реально нужное для транспорта TCP + TLS:
security=tls|none, type=tcp, sni, alpn, fp, allowInsecure, flow, encryption.
"""

from __future__ import annotations

import uuid as uuid_mod
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

__all__ = ["VlessServer", "parse_vless_url", "ConfigError", "Unsupported"]

SUPPORTED_NETWORKS = {"tcp"}
SUPPORTED_SECURITY = {"none", "", "tls"}
# xtls-rprx-vision и прочие flow требуют XTLS, которого в чистом Python нет.
SUPPORTED_FLOWS = {"", "none"}


class ConfigError(ValueError):
    """Некорректная ссылка или конфигурация."""


@dataclass(frozen=True)
class Unsupported:
    """Параметр ссылки, который этот клиент реализовать не может."""

    param: str
    value: str
    reason: str
    #: Что делать пользователю.
    remedy: str = ""

    @property
    def message(self) -> str:
        return f"{self.reason} {self.remedy}".strip()


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class VlessServer:
    """Параметры удалённого VLESS-сервера."""

    address: str
    port: int
    user_id: str
    #: tls | none
    security: str = "tls"
    network: str = "tcp"
    sni: str = ""
    alpn: tuple[str, ...] = ()
    fingerprint: str = ""
    allow_insecure: bool = False
    flow: str = ""
    encryption: str = "none"
    remark: str = ""
    #: Путь для ws/httpupgrade/splithttp.
    path: str = ""
    #: Заголовок Host для ws/httpupgrade (параметр ``host=`` в ссылке).
    host_header: str = ""
    #: serviceName для gRPC.
    service_name: str = ""
    #: headerType для tcp (``http`` — маскировка под обычный HTTP).
    header_type: str = ""
    #: Параметры REALITY: publicKey (pbk), shortId (sid), spiderX (spx).
    public_key: str = ""
    short_id: str = ""
    spider_x: str = ""
    extra: dict[str, str] = field(default_factory=dict)
    #: ``False`` — не падать на неподдерживаемых параметрах, а собрать их
    #: в :attr:`unsupported`. Нужно диагностике, чтобы дойти до сетевых проверок.
    strict: bool = True

    def __post_init__(self) -> None:
        self.address = self.address.strip().strip("[]")
        if not self.address:
            raise ConfigError("не указан адрес сервера")

        try:
            self.port = int(self.port)
        except (TypeError, ValueError):
            raise ConfigError(f"некорректный порт: {self.port!r}") from None
        if not 1 <= self.port <= 65535:
            raise ConfigError(f"порт вне диапазона 1..65535: {self.port}")

        try:
            self.uuid_bytes = uuid_mod.UUID(str(self.user_id)).bytes
        except (ValueError, AttributeError, TypeError):
            raise ConfigError(f"некорректный UUID: {self.user_id!r}") from None
        self.user_id = str(uuid_mod.UUID(str(self.user_id)))

        self.security = (self.security or "none").lower()
        self.network = (self.network or "tcp").lower()
        self.flow = (self.flow or "").lower()
        self.encryption = (self.encryption or "none").lower()

        self.unsupported: list[Unsupported] = self._collect_unsupported()
        if self.strict and self.unsupported:
            raise ConfigError(self.unsupported[0].message)

        if self.security == "":
            self.security = "none"

        if isinstance(self.alpn, str):
            self.alpn = tuple(x.strip() for x in self.alpn.split(",") if x.strip())
        else:
            self.alpn = tuple(self.alpn or ())

        if not self.sni and self.security == "tls":
            self.sni = self.address

    def _collect_unsupported(self) -> list["Unsupported"]:
        """Найти параметры, которые этот клиент реализовать не может."""
        found: list[Unsupported] = []

        if self.security == "reality":
            found.append(
                Unsupported(
                    "security",
                    "reality",
                    "security=reality не поддерживается: REALITY прячет ключ клиента "
                    "внутри TLS ClientHello и требует собственного TLS-стека. "
                    "Стандартный модуль ssl такого не умеет.",
                    "Попросите на сервере профиль с security=tls (обычный TLS-сертификат) "
                    "либо используйте xray-core.",
                )
            )
        elif self.security not in SUPPORTED_SECURITY:
            found.append(
                Unsupported(
                    "security",
                    self.security,
                    f"security={self.security!r} не поддерживается.",
                    "Доступно: tls, none.",
                )
            )

        if self.network not in SUPPORTED_NETWORKS:
            found.append(
                Unsupported(
                    "type",
                    self.network,
                    f"type={self.network!r} не поддерживается этой сборкой.",
                    f"Доступно: {', '.join(sorted(SUPPORTED_NETWORKS))}.",
                )
            )

        if self.flow not in SUPPORTED_FLOWS:
            found.append(
                Unsupported(
                    "flow",
                    self.flow,
                    f"flow={self.flow!r} требует XTLS, которого в чистом Python нет.",
                    "Уберите flow в настройках клиента на панели — обычный VLESS "
                    "поверх TLS работает и без него.",
                )
            )

        if self.encryption != "none":
            found.append(
                Unsupported(
                    "encryption",
                    self.encryption,
                    f"encryption={self.encryption!r}: VLESS определяет только 'none'.",
                    "Проверьте ссылку — скорее всего она не от VLESS.",
                )
            )

        return found

    @property
    def uses_tls(self) -> bool:
        return self.security == "tls"

    @property
    def speaks_tls_on_the_wire(self) -> bool:
        """Ожидается ли TLS-рукопожатие на порту сервера.

        Для reality — да: наружу это выглядит как обычный TLS, даже если
        подключиться по-настоящему мы не можем. Диагностике это нужно,
        чтобы всё равно показать, что там за сертификат.
        """
        return self.security in ("tls", "reality")

    @property
    def is_supported(self) -> bool:
        return not self.unsupported

    def describe(self) -> str:
        label = f"{self.address}:{self.port}"
        if self.security == "none":
            label += f" {self.network}/plain"
        else:
            label += f" {self.network}/{self.security}(sni={self.sni or '-'})"
        if self.flow:
            label += f" flow={self.flow}"
        if self.remark:
            label += f" [{self.remark}]"
        return label

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "port": self.port,
            "uuid": self.user_id,
            "security": self.security,
            "type": self.network,
            "sni": self.sni,
            "alpn": list(self.alpn),
            "fingerprint": self.fingerprint,
            "allowInsecure": self.allow_insecure,
            "flow": self.flow,
            "remark": self.remark,
        }


def parse_vless_url(url: str, *, strict: bool = True) -> VlessServer:
    """Разобрать ``vless://`` ссылку в :class:`VlessServer`.

    :param strict: ``True`` — бросать :class:`ConfigError` на неподдерживаемых
        параметрах. ``False`` — собрать их в ``server.unsupported`` и вернуть
        объект; так диагностика может дойти до сетевых проверок.
    """
    if not isinstance(url, str):
        raise ConfigError("ссылка должна быть строкой")

    raw = url.strip()
    if not raw:
        raise ConfigError("пустая ссылка")
    if not raw.lower().startswith("vless://"):
        raise ConfigError("ссылка должна начинаться с vless://")

    parts = urlsplit(raw)

    if not parts.username:
        raise ConfigError("в ссылке нет UUID (ожидается vless://UUID@host:port)")
    if not parts.hostname:
        raise ConfigError("в ссылке нет адреса сервера")
    if parts.port is None:
        raise ConfigError("в ссылке не указан порт")

    params = {k.lower(): v for k, v in parse_qsl(parts.query, keep_blank_values=True)}
    known = {
        "security", "type", "sni", "alpn", "fp", "allowinsecure", "flow",
        "encryption", "host", "headertype", "path", "servicename",
        "pbk", "sid", "spx",
    }
    extra = {k: v for k, v in params.items() if k not in known}

    return VlessServer(
        address=parts.hostname,
        port=parts.port,
        user_id=unquote(parts.username),
        security=params.get("security", "none"),
        network=params.get("type", "tcp"),
        sni=params.get("sni", "") or params.get("host", ""),
        alpn=params.get("alpn", ""),
        fingerprint=params.get("fp", ""),
        allow_insecure=_as_bool(params.get("allowinsecure"), False),
        flow=params.get("flow", ""),
        encryption=params.get("encryption", "none"),
        remark=unquote(parts.fragment) if parts.fragment else "",
        path=unquote(params.get("path", "")),
        host_header=params.get("host", ""),
        service_name=unquote(params.get("servicename", "")),
        header_type=params.get("headertype", ""),
        public_key=params.get("pbk", ""),
        short_id=params.get("sid", ""),
        spider_x=unquote(params.get("spx", "")),
        extra=extra,
        strict=strict,
    )


def server_from_mapping(data: dict[str, Any], *, strict: bool = True) -> VlessServer:
    """Собрать :class:`VlessServer` из словаря config.json."""
    if "url" in data and data["url"]:
        server = parse_vless_url(str(data["url"]), strict=strict)
        # Явные поля конфига перекрывают то, что пришло из ссылки.
        if data.get("allowInsecure") is not None:
            server.allow_insecure = _as_bool(data["allowInsecure"])
        if data.get("sni"):
            server.sni = str(data["sni"])
        return server

    return VlessServer(
        address=str(data.get("address", "")),
        port=data.get("port", 0),
        user_id=str(data.get("uuid", "")),
        security=str(data.get("security", "tls")),
        network=str(data.get("type", "tcp")),
        sni=str(data.get("sni", "")),
        alpn=data.get("alpn", ()),
        fingerprint=str(data.get("fingerprint", "")),
        allow_insecure=_as_bool(data.get("allowInsecure"), False),
        flow=str(data.get("flow", "")),
        encryption=str(data.get("encryption", "none")),
        remark=str(data.get("remark", "")),
        path=str(data.get("path", "")),
        host_header=str(data.get("host", "")),
        service_name=str(data.get("serviceName", "")),
        header_type=str(data.get("headerType", "")),
        public_key=str(data.get("publicKey", "")),
        short_id=str(data.get("shortId", "")),
        spider_x=str(data.get("spiderX", "")),
        strict=strict,
    )
