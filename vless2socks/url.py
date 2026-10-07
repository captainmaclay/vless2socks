"""Разбор ссылок вида ``vless://uuid@host:port?params#remark``.

Поддерживается подмножество, реально нужное для транспорта TCP + TLS:
security=tls|none, type=tcp, sni, alpn, fp, allowInsecure, flow, encryption.
"""

from __future__ import annotations

import uuid as uuid_mod
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

__all__ = [
    "VlessServer",
    "SocksServer",
    "WireGuardServer",
    "parse_vless_url",
    "parse_socks_url",
    "parse_wireguard_url",
    "parse_wireguard_conf",
    "parse_proxy_url",
    "server_from_mapping",
    "ConfigError",
    "Unsupported",
]

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


@dataclass
class SocksServer:
    """Параметры удалённого SOCKS5-сервера."""

    address: str
    port: int
    username: str = ""
    password: str = ""
    version: int = 5
    remark: str = ""
    strict: bool = True
    unsupported: list[Any] = field(default_factory=list)
    security: str = "none"
    network: str = "tcp"
    alpn: tuple[str, ...] = ()
    sni: str = ""

    @property
    def speaks_tls_on_the_wire(self) -> bool:
        return False

    @property
    def allow_insecure(self) -> bool:
        return False

    def __post_init__(self) -> None:
        self.address = str(self.address).strip().strip("[]")
        if not self.address:
            raise ConfigError("не указан адрес сервера")

        try:
            self.port = int(self.port)
        except (TypeError, ValueError):
            raise ConfigError(f"некорректный порт: {self.port!r}") from None
        if not 1 <= self.port <= 65535:
            raise ConfigError(f"порт вне диапазона 1..65535: {self.port}")

        try:
            self.version = int(self.version)
        except (TypeError, ValueError):
            self.version = 5

        self.username = str(self.username or "").strip()
        self.password = str(self.password or "").strip()
        self.remark = str(self.remark or "").strip()

    def describe(self) -> str:
        tag = f" ({self.remark})" if self.remark else ""
        auth = f"{self.username}:***@" if self.username else ""
        return f"socks{self.version}://{auth}{self.address}:{self.port}{tag}"

    def to_url(self) -> str:
        """Собрать ссылку вида socks5://[user:pass@]host:port[#remark]."""
        auth = ""
        if self.username:
            from urllib.parse import quote
            u = quote(self.username, safe="")
            p = f":{quote(self.password, safe='')}" if self.password else ""
            auth = f"{u}{p}@"
        frag = f"#{self.remark}" if self.remark else ""
        return f"socks{self.version}://{auth}{self.address}:{self.port}{frag}"


def parse_socks_url(url: str, *, strict: bool = True) -> SocksServer:
    """Разбор ссылки вида socks5://[username:password@]host:port[#remark]."""
    url = str(url).strip()
    lower = url.lower()
    if not (lower.startswith("socks5://") or lower.startswith("socks://")):
        raise ConfigError("ссылка должна начинаться с socks5:// или socks://")

    # urlsplit корректно разбирает socks5://user:pass@host:port#remark
    parts = urlsplit(url)
    address = parts.hostname or ""
    port = parts.port or 1080
    username = unquote(parts.username or "")
    password = unquote(parts.password or "")
    remark = unquote(parts.fragment) if parts.fragment else ""

    return SocksServer(
        address=address,
        port=port,
        username=username,
        password=password,
        version=5,
        remark=remark,
        strict=strict,
    )


@dataclass
class WireGuardServer:
    """Параметры удалённого WireGuard-сервера."""

    address: str
    port: int
    secret_key: str
    peer_public_key: str
    local_address: list[str] = field(default_factory=lambda: ["10.0.0.2/32"])
    allowed_ips: list[str] = field(default_factory=list)
    preshared_key: str = ""
    mtu: int = 1420
    reserved: list[int] = field(default_factory=list)
    keep_alive: int = 0
    dns: list[str] = field(default_factory=list)
    remark: str = ""
    strict: bool = True
    unsupported: list[Any] = field(default_factory=list)
    security: str = "none"
    network: str = "udp"
    alpn: tuple[str, ...] = ()
    sni: str = ""

    @property
    def speaks_tls_on_the_wire(self) -> bool:
        return False

    @property
    def allow_insecure(self) -> bool:
        return False

    def __post_init__(self) -> None:
        self.address = str(self.address).strip().strip("[]")
        if not self.address:
            raise ConfigError("не указан адрес сервера")

        try:
            self.port = int(self.port)
        except (TypeError, ValueError):
            raise ConfigError(f"некорректный порт: {self.port!r}") from None
        if not 1 <= self.port <= 65535:
            raise ConfigError(f"порт вне диапазона 1..65535: {self.port}")

        self.secret_key = str(self.secret_key or "").strip()
        if not self.secret_key:
            raise ConfigError("не указан закрытый ключ (secretKey / PrivateKey)")

        self.peer_public_key = str(self.peer_public_key or "").strip()
        if not self.peer_public_key:
            raise ConfigError("не указан публичный ключ сервера (publicKey / PublicKey)")

        if isinstance(self.local_address, str):
            addrs = [a.strip() for a in self.local_address.split(",") if a.strip()]
            self.local_address = addrs or ["10.0.0.2/32"]
        elif not self.local_address:
            self.local_address = ["10.0.0.2/32"]

        if isinstance(self.allowed_ips, str):
            raw_ips = [ip.strip() for ip in self.allowed_ips.split(",") if ip.strip()]
        elif self.allowed_ips:
            raw_ips = [str(ip).strip() for ip in self.allowed_ips if str(ip).strip()]
        else:
            raw_ips = []
        seen_ips = set()
        dedup_ips = []
        for ip in raw_ips:
            if ip not in seen_ips:
                seen_ips.add(ip)
                dedup_ips.append(ip)
        self.allowed_ips = dedup_ips

        try:
            self.mtu = int(self.mtu or 1420)
        except (TypeError, ValueError):
            self.mtu = 1420

        try:
            self.keep_alive = int(self.keep_alive or 0)
        except (TypeError, ValueError):
            self.keep_alive = 0

        if isinstance(self.dns, str):
            self.dns = [d.strip() for d in self.dns.split(",") if d.strip()]
        elif self.dns:
            self.dns = [str(d).strip() for d in self.dns if str(d).strip()]
        else:
            self.dns = []

        self.preshared_key = str(self.preshared_key or "").strip()
        self.remark = str(self.remark or "").strip()

    def describe(self) -> str:
        tag = f" ({self.remark})" if self.remark else ""
        return f"wireguard://{self.address}:{self.port}{tag}"

    def to_url(self) -> str:
        """Собрать ссылку вида wireguard://[address:port]?pk=...&peer_pk=..."""
        from urllib.parse import quote
        qparams = [
            f"pk={quote(self.secret_key, safe='')}",
            f"peer_pk={quote(self.peer_public_key, safe='')}",
        ]
        if self.local_address:
            if isinstance(self.local_address, str):
                loc_list = [a.strip() for a in self.local_address.split(",") if a.strip()]
            else:
                loc_list = [str(a).strip() for a in self.local_address if str(a).strip()]
            if loc_list:
                qparams.append(f"local_address={quote(','.join(loc_list), safe='')}")
        if self.allowed_ips:
            if isinstance(self.allowed_ips, str):
                al_list = [a.strip() for a in self.allowed_ips.split(",") if a.strip()]
            else:
                al_list = [str(a).strip() for a in self.allowed_ips if str(a).strip()]
            if al_list:
                qparams.append(f"allowed_ips={quote(','.join(al_list), safe='')}")
        if self.preshared_key:
            qparams.append(f"psk={quote(self.preshared_key, safe='')}")
        if self.mtu and self.mtu != 1420:
            qparams.append(f"mtu={self.mtu}")
        if self.keep_alive:
            qparams.append(f"keepalive={self.keep_alive}")
        if self.dns:
            if isinstance(self.dns, str):
                dns_list = [d.strip() for d in self.dns.split(",") if d.strip()]
            else:
                dns_list = [str(d).strip() for d in self.dns if str(d).strip()]
            if dns_list:
                qparams.append(f"dns={quote(','.join(dns_list), safe='')}")
        if self.reserved:
            qparams.append(f"reserved={','.join(map(str, self.reserved))}")

        query_str = "&".join(qparams)
        frag = f"#{quote(self.remark, safe='')}" if self.remark else ""
        return f"wireguard://{self.address}:{self.port}/?{query_str}{frag}"

    def to_conf(self) -> str:
        """Сгенерировать стандартный текст конфигурационного файла WireGuard (.conf)."""
        lines = [
            "[Interface]",
            f"PrivateKey = {self.secret_key}",
            f"Address = {', '.join(self.local_address)}",
        ]
        if self.dns:
            lines.append(f"DNS = {', '.join(self.dns)}")
        if self.mtu and self.mtu != 1420:
            lines.append(f"MTU = {self.mtu}")
        lines.append("")
        lines.append("[Peer]")
        lines.append(f"PublicKey = {self.peer_public_key}")
        if self.preshared_key:
            lines.append(f"PresharedKey = {self.preshared_key}")
        if self.allowed_ips:
            lines.append(f"AllowedIPs = {', '.join(self.allowed_ips)}")
        lines.append(f"Endpoint = {self.address}:{self.port}")
        if self.keep_alive:
            lines.append(f"PersistentKeepalive = {self.keep_alive}")
        return "\n".join(lines) + "\n"


def parse_wireguard_url(url: str, *, strict: bool = True) -> WireGuardServer:
    """Разбор ссылки wireguard:// или wg://."""
    url = str(url).strip()
    lower = url.lower()
    if not (lower.startswith("wireguard://") or lower.startswith("wg://")):
        raise ConfigError("ссылка должна начинаться с wireguard:// или wg://")

    # urlsplit
    parts = urlsplit(url)
    address = parts.hostname or ""
    port = parts.port or 51820

    params = {k.lower(): v for k, v in parse_qsl(parts.query, keep_blank_values=True)}
    secret_key = (
        params.get("pk")
        or params.get("secretkey")
        or params.get("privatekey")
        or params.get("private_key")
        or (unquote(parts.username) if parts.username else "")
    )
    peer_public_key = (
        params.get("peer_pk")
        or params.get("publickey")
        or params.get("public_key")
        or params.get("peer_public_key")
        or (unquote(parts.password) if parts.password else "")
    )
    local_addr_str = (
        params.get("local_address")
        or params.get("address")
        or params.get("ip")
        or "10.0.0.2/32"
    )
    local_addrs = [a.strip() for a in local_addr_str.split(",") if a.strip()]

    allowed_ips_str = (
        params.get("allowed_ips")
        or params.get("allowedips")
        or params.get("allowed_ip")
        or params.get("allowedip")
        or ""
    )
    allowed_ips = [a.strip() for a in allowed_ips_str.split(",") if a.strip()]

    psk = params.get("psk") or params.get("preshared_key") or params.get("presharedkey") or ""
    mtu = int(params.get("mtu", 1420)) if params.get("mtu", "").isdigit() else 1420
    keepalive_val = params.get("keepalive") or params.get("persistentkeepalive") or params.get("keep_alive") or 0
    keep_alive = int(keepalive_val) if str(keepalive_val).isdigit() else 0
    dns_str = params.get("dns") or ""
    dns = [d.strip() for d in dns_str.split(",") if d.strip()]
    reserved = []
    if params.get("reserved"):
        try:
            reserved = [int(x.strip()) for x in params["reserved"].split(",") if x.strip()]
        except Exception:
            reserved = []

    remark = unquote(parts.fragment) if parts.fragment else ""

    return WireGuardServer(
        address=address,
        port=port,
        secret_key=secret_key,
        peer_public_key=peer_public_key,
        local_address=local_addrs,
        allowed_ips=allowed_ips,
        preshared_key=psk,
        mtu=mtu,
        keep_alive=keep_alive,
        dns=dns,
        reserved=reserved,
        remark=remark,
        strict=strict,
    )


def parse_wireguard_conf(
    conf_or_path: str | Any,
    *,
    remark: str = "",
    strict: bool = True,
) -> WireGuardServer:
    """Парсинг настроек WireGuard из файла .conf или сырого текста (raw INI)."""
    import configparser
    from pathlib import Path

    text = ""
    suggested_remark = remark
    p = None
    if isinstance(conf_or_path, Path):
        p = conf_or_path
    elif isinstance(conf_or_path, str) and ("\n" not in conf_or_path and "\r" not in conf_or_path):
        candidate = Path(conf_or_path.strip().strip('"').strip("'"))
        if candidate.exists() and candidate.is_file():
            p = candidate

    if p is not None:
        if not suggested_remark:
            suggested_remark = p.stem
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = p.read_text(encoding="cp1251", errors="replace")
    else:
        text = str(conf_or_path)

    # Очищаем комментарии (# и ;) для надежного разбора ConfigParser
    cleaned_lines = []
    for line in text.splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith(("#", ";")):
            continue
        cleaned_lines.append(line)

    cleaned_text = "\n".join(cleaned_lines)
    cp = configparser.ConfigParser(strict=False)
    try:
        cp.read_string(cleaned_text)
    except Exception as exc:
        raise ConfigError(f"некорректный формат конфигурации WireGuard: {exc}") from None

    if "Interface" not in cp:
        raise ConfigError("в конфигурации WireGuard отсутствует секция [Interface]")
    if "Peer" not in cp:
        raise ConfigError("в конфигурации WireGuard отсутствует секция [Peer]")

    iface = cp["Interface"]
    peer = cp["Peer"]

    secret_key = iface.get("privatekey", "").strip()
    if not secret_key:
        raise ConfigError("не указан PrivateKey в секции [Interface]")

    peer_public_key = peer.get("publickey", "").strip()
    if not peer_public_key:
        raise ConfigError("не указан PublicKey в секции [Peer]")

    endpoint = peer.get("endpoint", "").strip()
    if not endpoint:
        raise ConfigError("не указан Endpoint в секции [Peer]")

    if ":" in endpoint:
        host_part, _, port_part = endpoint.rpartition(":")
        address = host_part.strip().strip("[]")
        try:
            port = int(port_part.strip())
        except ValueError:
            port = 51820
    else:
        address = endpoint.strip().strip("[]")
        port = 51820

    raw_addrs = iface.get("address", "").strip()
    local_addrs = [a.strip() for a in raw_addrs.split(",") if a.strip()] or ["10.0.0.2/32"]

    raw_allowed = peer.get("allowedips", "").strip()
    allowed_ips = [a.strip() for a in raw_allowed.split(",") if a.strip()]

    raw_dns = iface.get("dns", "").strip()
    dns = [d.strip() for d in raw_dns.split(",") if d.strip()]

    preshared_key = peer.get("presharedkey", "").strip()

    try:
        mtu = int(iface.get("mtu", 1420))
    except (ValueError, TypeError):
        mtu = 1420

    try:
        keepalive = int(peer.get("persistentkeepalive", peer.get("keepalive", 0)))
    except (ValueError, TypeError):
        keepalive = 0

    return WireGuardServer(
        address=address,
        port=port,
        secret_key=secret_key,
        peer_public_key=peer_public_key,
        local_address=local_addrs,
        allowed_ips=allowed_ips,
        preshared_key=preshared_key,
        mtu=mtu,
        keep_alive=keepalive,
        dns=dns,
        remark=suggested_remark,
        strict=strict,
    )


def parse_proxy_url(url: str, *, strict: bool = True) -> VlessServer | SocksServer | WireGuardServer:
    """Универсальный парсер: определяет vless://, socks5://, wireguard://, сырой raw .conf текст или путь к .conf файлу."""
    url = str(url).strip()
    lower = url.lower()
    if "[interface]" in lower:
        return parse_wireguard_conf(url, strict=strict)
    if (lower.endswith(".conf") or ".conf" in lower) and ("\n" not in url):
        from pathlib import Path
        p = Path(url.strip('"').strip("'"))
        if p.exists() and p.is_file():
            return parse_wireguard_conf(p, strict=strict)
    if lower.startswith("vless://"):
        return parse_vless_url(url, strict=strict)
    if lower.startswith(("socks5://", "socks://")):
        return parse_socks_url(url, strict=strict)
    if lower.startswith(("wireguard://", "wg://")):
        return parse_wireguard_url(url, strict=strict)
    raise ConfigError("неподдерживаемый протокол: ожидается vless://, socks5://, wireguard:// или .conf")


def server_from_mapping(data: dict[str, Any], *, strict: bool = True) -> VlessServer | SocksServer | WireGuardServer:
    """Собрать :class:`VlessServer`, :class:`SocksServer` или :class:`WireGuardServer` из словаря config.json."""
    if "url" in data and data["url"]:
        url_str = str(data["url"]).strip()
        if url_str.lower().startswith(("socks5://", "socks://")):
            server = parse_socks_url(url_str, strict=strict)
            if data.get("username") is not None and not server.username:
                server.username = str(data["username"])
            if data.get("password") is not None and not server.password:
                server.password = str(data["password"])
            if data.get("remark") is not None and not server.remark:
                server.remark = str(data["remark"])
            return server

        if url_str.lower().startswith(("wireguard://", "wg://")):
            server = parse_wireguard_url(url_str, strict=strict)
            if data.get("allowed_ips") or data.get("allowedIPs"):
                raw_al = data.get("allowed_ips") or data.get("allowedIPs")
                if isinstance(raw_al, str):
                    server.allowed_ips = [a.strip() for a in raw_al.split(",") if a.strip()]
                elif isinstance(raw_al, (list, tuple)):
                    server.allowed_ips = [str(a).strip() for a in raw_al if str(a).strip()]
            return server

        server = parse_vless_url(url_str, strict=strict)
        # Явные поля конфига перекрывают то, что пришло из ссылки.
        if data.get("allowInsecure") is not None:
            server.allow_insecure = _as_bool(data["allowInsecure"])
        if data.get("sni"):
            server.sni = str(data["sni"])
        return server

    proto = str(data.get("protocol", "")).lower()
    if proto in ("wireguard", "wg"):
        raw_al = data.get("allowed_ips") or data.get("allowedIPs") or []
        if isinstance(raw_al, str):
            allowed_ips = [a.strip() for a in raw_al.split(",") if a.strip()]
        else:
            allowed_ips = [str(a).strip() for a in raw_al if str(a).strip()]
        return WireGuardServer(
            address=str(data.get("address", "")),
            port=int(data.get("port", 51820)),
            secret_key=str(data.get("secretKey") or data.get("privateKey") or ""),
            peer_public_key=str(data.get("publicKey") or data.get("peerPublicKey") or ""),
            local_address=data.get("localAddress") or data.get("addressList") or ["10.0.0.2/32"],
            allowed_ips=allowed_ips,
            preshared_key=str(data.get("presharedKey") or ""),
            mtu=int(data.get("mtu", 1420)),
            reserved=data.get("reserved") or [],
            remark=str(data.get("remark") or data.get("name") or ""),
            strict=strict,
        )

    if proto in ("socks", "socks5") or ("uuid" not in data and "address" in data and "secretKey" not in data and "privateKey" not in data):
        return SocksServer(
            address=str(data.get("address", "")),
            port=data.get("port", 1080),
            username=str(data.get("username", "")),
            password=str(data.get("password", "")),
            version=int(data.get("version", 5)),
            remark=str(data.get("remark") or data.get("name") or ""),
            strict=strict,
        )

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

