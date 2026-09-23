"""Сборка конфигурации xray-core из разобранной ``vless://`` ссылки.

Структура конфига описана в документации Xray:
inbounds[socks] -> outbounds[vless] + streamSettings.
Всё, что здесь генерируется, проверяется тестами побайтово — исправить
опечатку в имени поля иначе можно только по невнятной ошибке от xray.
"""

from __future__ import annotations

import copy
from typing import Any

from ..config import AppConfig
from ..url import SocksServer, VlessServer

__all__ = ["build_xray_config", "describe_config", "redact_config"]

#: Имя поля streamSettings для каждого транспорта.
_TRANSPORT_SETTINGS_KEY = {
    "tcp": "tcpSettings",
    "ws": "wsSettings",
    "websocket": "wsSettings",
    "grpc": "grpcSettings",
    "http": "httpSettings",
    "h2": "httpSettings",
    "httpupgrade": "httpupgradeSettings",
    "splithttp": "splithttpSettings",
    "xhttp": "xhttpSettings",
    "quic": "quicSettings",
    "kcp": "kcpSettings",
    "mkcp": "kcpSettings",
}

#: xray ожидает "ws", а не "websocket"; "h2" он называет "http".
_NETWORK_ALIASES = {"websocket": "ws", "h2": "http", "mkcp": "kcp"}

_LOG_LEVELS = {
    "debug": "debug",
    "info": "info",
    "warning": "warning",
    "warn": "warning",
    "error": "error",
    "none": "none",
}


def get_physical_gateway_ip() -> str | None:
    """Определить локальный IP физического шлюза в Windows, минуя виртуальные адаптеры (TUN)."""
    import subprocess
    import sys
    if sys.platform == "win32":
        try:
            out = subprocess.check_output("route print 0.0.0.0", shell=True, text=True)
            for line in out.splitlines():
                parts = line.strip().split()
                if len(parts) >= 5 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
                    gateway, iface = parts[2], parts[3]
                    if not iface.startswith(("172.19.", "127.", "169.254.")):
                        return iface
        except Exception:
            pass
    return None


def build_xray_config(config: AppConfig, *, legacy_vnext: bool = False) -> dict[str, Any]:
    """Собрать полный конфиг xray для текущих настроек.

    :param legacy_vnext: старая форма outbound с вложенностью ``vnext``/``users``.
        Xray убрал её в PR #5101 (ветка 26.x), но она нужна тем, у кого уже
        лежит бинарник постарше. Ошибиться форматом нельзя — xray просто
        откажется читать конфиг, поэтому :class:`XrayProcess` при отказе
        пробует вторую форму автоматически.
    """
    if isinstance(config.server, SocksServer):
        primary_outbound = _socks_outbound(config.server)
    else:
        primary_outbound = _vless_outbound(config.server, legacy_vnext=legacy_vnext)

    send_through = getattr(config, "send_through", "")
    if send_through == "none":
        pass
    elif send_through and send_through != "auto":
        primary_outbound["sendThrough"] = send_through
    else:
        # Автоматически на Windows: направлять исходящий сокет Xray через реальный
        # физический адаптер, чтобы туннель не перехватывался локальными TUN (Throne, Sing-box).
        phys_ip = get_physical_gateway_ip()
        if phys_ip:
            primary_outbound["sendThrough"] = phys_ip

    outbounds = [primary_outbound]
    routing = None

    if getattr(config, "killswitch", False):
        outbounds.append({
            "tag": "block",
            "protocol": "blackhole",
            "settings": {"response": {"type": "none"}},
        })
        outbounds.append(_direct_outbound())
        routing = {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["socks-in", "http-in"],
                    "outboundTag": "proxy",
                },
                {
                    "type": "field",
                    "inboundTag": ["socks-in", "http-in"],
                    "outboundTag": "block",
                },
            ],
        }
    else:
        outbounds.append(_direct_outbound())

    res = {
        "log": {"loglevel": _LOG_LEVELS.get(config.log_level.lower(), "warning")},
        "inbounds": [_socks_inbound(config), _http_inbound(config)],
        "outbounds": outbounds,
    }
    if routing:
        res["routing"] = routing
    return res


# ------------------------------------------------------------------- inbound


def _http_inbound(config: AppConfig) -> dict[str, Any]:
    return {
        "tag": "http-in",
        "listen": config.listen_host,
        "port": config.listen_port + 10000,
        "protocol": "http",
        "settings": {},
        "sniffing": {
            "enabled": True,
            "destOverride": ["http", "tls"],
            "routeOnly": False,
        },
    }


def _socks_inbound(config: AppConfig) -> dict[str, Any]:
    settings: dict[str, Any] = {"udp": bool(config.udp_enabled)}
    if config.auth_required:
        settings["auth"] = "password"
        settings["accounts"] = [
            {"user": config.username, "pass": config.password}
        ]
    else:
        settings["auth"] = "noauth"

    return {
        "tag": "socks-in",
        "listen": config.listen_host,
        "port": config.listen_port,
        "protocol": "socks",
        "settings": settings,
        # Без sniffing xray отправит на сервер IP, уже разрешённый локально,
        # и удалённый DNS (socks5h) потеряет смысл.
        "sniffing": {
            "enabled": True,
            "destOverride": ["http", "tls", "quic"],
            "routeOnly": False,
        },
    }


# ------------------------------------------------------------------ outbound


def _vless_outbound(server: VlessServer, *, legacy_vnext: bool = False) -> dict[str, Any]:
    if legacy_vnext:
        user: dict[str, Any] = {
            "id": server.user_id,
            "encryption": server.encryption or "none",
            "level": 0,
        }
        if server.flow:
            user["flow"] = server.flow
        settings: dict[str, Any] = {
            "vnext": [
                {"address": server.address, "port": server.port, "users": [user]}
            ]
        }
    else:
        # Современная форма: адрес и пользователь лежат прямо в settings.
        # Ровно так выглядит официальный пример XTLS для VLESS+Vision+REALITY.
        settings = {
            "address": server.address,
            "port": server.port,
            "id": server.user_id,
            "encryption": server.encryption or "none",
        }
        if server.flow:
            settings["flow"] = server.flow

    return {
        "tag": "proxy",
        "protocol": "vless",
        "settings": settings,
        "streamSettings": _stream_settings(server),
    }


def _socks_outbound(server: SocksServer) -> dict[str, Any]:
    srv: dict[str, Any] = {
        "address": server.address,
        "port": int(server.port),
    }
    if server.username:
        srv["users"] = [
            {
                "user": server.username,
                "pass": server.password or "",
                "level": 0,
            }
        ]
    return {
        "tag": "proxy",
        "protocol": "socks",
        "settings": {
            "servers": [srv]
        },
    }


def _direct_outbound() -> dict[str, Any]:
    # Правил маршрутизации нет, поэтому весь трафик уходит в первый outbound.
    # Этот нужен только чтобы xray было куда отправить служебные соединения.
    return {"tag": "direct", "protocol": "freedom", "settings": {}}


def _stream_settings(server: VlessServer) -> dict[str, Any]:
    network = _NETWORK_ALIASES.get(server.network, server.network)
    stream: dict[str, Any] = {"network": network}

    transport = _transport_settings(server, network)
    if transport:
        stream[_TRANSPORT_SETTINGS_KEY.get(network, f"{network}Settings")] = transport

    if server.security == "reality":
        stream["security"] = "reality"
        stream["realitySettings"] = _reality_settings(server)
    elif server.security == "tls":
        stream["security"] = "tls"
        stream["tlsSettings"] = _tls_settings(server)
    else:
        stream["security"] = "none"

    return stream


def _transport_settings(server: VlessServer, network: str) -> dict[str, Any]:
    if network == "ws":
        settings: dict[str, Any] = {"path": server.path or "/"}
        host = server.host_header or server.sni
        if host:
            settings["headers"] = {"Host": host}
        return settings

    if network == "httpupgrade":
        settings = {"path": server.path or "/"}
        if server.host_header or server.sni:
            settings["host"] = server.host_header or server.sni
        return settings

    if network == "grpc":
        settings = {"serviceName": server.service_name}
        if server.extra.get("mode") == "multi":
            settings["multiMode"] = True
        return settings

    if network == "http":
        settings = {"path": server.path or "/"}
        host = server.host_header or server.sni
        if host:
            settings["host"] = [h.strip() for h in host.split(",") if h.strip()]
        return settings

    if network == "tcp" and server.header_type == "http":
        host = server.host_header or server.sni
        return {
            "header": {
                "type": "http",
                "request": {
                    "path": [server.path or "/"],
                    "headers": {"Host": [h.strip() for h in host.split(",") if h.strip()]},
                },
            }
        }

    return {}


def _tls_settings(server: VlessServer) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "serverName": server.sni or server.address,
        "allowInsecure": bool(server.allow_insecure),
    }
    if server.alpn:
        settings["alpn"] = list(server.alpn)
    if server.fingerprint:
        # Здесь fp работает по-настоящему: у xray есть uTLS.
        settings["fingerprint"] = server.fingerprint
    return settings


def _reality_settings(server: VlessServer) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "serverName": server.sni or server.address,
        "publicKey": server.public_key,
        # REALITY требует отпечатка; chrome — то, что подставляют панели по умолчанию.
        "fingerprint": server.fingerprint or "chrome",
        "show": False,
    }
    if server.short_id:
        settings["shortId"] = server.short_id
    if server.spider_x:
        settings["spiderX"] = server.spider_x
    return settings


# --------------------------------------------------------------------- вывод


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    """Копия конфига без UUID и паролей — для показа в логах и в --doctor."""
    safe = copy.deepcopy(config)
    for outbound in safe.get("outbounds", []):
        settings = outbound.get("settings", {})
        if settings.get("id"):  # современная плоская форма
            settings["id"] = _mask(settings["id"])
        for vnext in settings.get("vnext", []):  # старая форма
            for user in vnext.get("users", []):
                if user.get("id"):
                    user["id"] = _mask(user["id"])
        for server in settings.get("servers", []):  # SOCKS outbound
            for user in server.get("users", []):
                if user.get("pass"):
                    user["pass"] = "***"
    for inbound in safe.get("inbounds", []):
        for account in inbound.get("settings", {}).get("accounts", []):
            if account.get("pass"):
                account["pass"] = "***"
    for outbound in safe.get("outbounds", []):
        reality = outbound.get("streamSettings", {}).get("realitySettings")
        if reality and reality.get("publicKey"):
            reality["publicKey"] = _mask(reality["publicKey"])
    return safe


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def describe_config(config: dict[str, Any]) -> str:
    """Короткая сводка конфига одной строкой на каждый значимый параметр."""
    inbound = config["inbounds"][0]
    outbound = config["outbounds"][0]
    proto = outbound.get("protocol", "vless")

    if proto == "socks":
        servers = outbound.get("settings", {}).get("servers", [{}])
        srv = servers[0] if servers else {}
        auth = "user/pass" if srv.get("users") else "no-auth"
        lines = [
            f"inbound:  socks {inbound['listen']}:{inbound['port']} "
            f"({inbound['settings']['auth']}, udp={inbound['settings']['udp']})",
            f"outbound: socks5 {srv.get('address')}:{srv.get('port')} [{auth}]",
        ]
        if any(o.get("tag") == "block" and o.get("protocol") == "blackhole" for o in config.get("outbounds", [])):
            lines.append("killswitch: активен (blackhole при утечке)")
        return "\n".join(lines)

    stream = outbound.get("streamSettings", {})
    settings = outbound.get("settings", {})
    if "vnext" in settings:
        target = settings["vnext"][0]
        peer, flow = target, target["users"][0].get("flow")
        shape = "vnext (старая форма)"
    else:
        peer, flow = settings, settings.get("flow")
        shape = "плоская форма"

    lines = [
        f"inbound:  socks {inbound['listen']}:{inbound['port']} "
        f"({inbound['settings']['auth']}, udp={inbound['settings']['udp']})",
        f"outbound: vless {peer.get('address')}:{peer.get('port')}  [{shape}]",
        f"поток:    network={stream.get('network')}, security={stream.get('security')}",
    ]
    if flow:
        lines.append(f"flow:     {flow}")
    if "realitySettings" in stream:
        r = stream["realitySettings"]
        lines.append(
            f"reality:  serverName={r['serverName']}, fingerprint={r['fingerprint']}"
            + (f", shortId={r['shortId']}" if r.get("shortId") else "")
        )
    if "tlsSettings" in stream:
        t = stream["tlsSettings"]
        lines.append(
            f"tls:      serverName={t['serverName']}, "
            f"allowInsecure={t['allowInsecure']}"
            + (f", fingerprint={t['fingerprint']}" if t.get("fingerprint") else "")
        )
    if any(o.get("tag") == "block" and o.get("protocol") == "blackhole" for o in config.get("outbounds", [])):
        lines.append("killswitch: активен (blackhole при утечке)")
    return "\n".join(lines)
