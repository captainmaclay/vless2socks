"""Пошаговая диагностика: где именно рвётся цепочка.

Проверки идут снизу вверх по стеку — параметры, DNS, TCP, TLS, рукопожатие
VLESS, сквозной HTTP, UDP. Каждая следующая пропускается, если предыдущая
упала: так в выводе видно первую настоящую причину, а не лавину следствий.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import socket
import ssl
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .config import AppConfig
from .protocol import ProtocolError
from .transport import TransportError, build_ssl_context
from .vless import VlessConnection

__all__ = ["Status", "CheckResult", "run_diagnostics", "format_report"]


class Status(Enum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


MARKS = {
    Status.OK: "[ OK ]",
    Status.WARN: "[WARN]",
    Status.FAIL: "[FAIL]",
    Status.SKIP: "[ -- ]",
}


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    hint: str = ""
    elapsed: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)
    #: Дополнительные строки, печатаются с отступом под основной.
    notes: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.status is Status.FAIL


# ------------------------------------------------------------------ проверки


def check_backend(config: AppConfig) -> tuple[CheckResult, str, str]:
    """Определить движок.

    Возвращает ``(результат, имя_движка|"", путь_к_xray|"")``.
    """
    from .backend import XRAY, resolve_backend
    from .url import ConfigError

    try:
        choice = resolve_backend(config)
    except ConfigError as exc:
        return (
            CheckResult(
                "Движок", Status.FAIL,
                "ни один движок не потянет этот профиль",
                hint=str(exc),
            ),
            "",
            "",
        )

    notes = []
    if choice.name == XRAY:
        notes.append(f"бинарник: {choice.xray_path}")
        notes.append(f"версия:   {choice.xray_version or 'не определилась'}")
    result = CheckResult("Движок", Status.OK, str(choice), notes=notes)
    return result, choice.name, choice.xray_path


def check_parameters(config: AppConfig, backend: str = "") -> CheckResult:
    """Убедиться, что все параметры ссылки выбранный движок умеет."""
    from .backend import XRAY
    from .url import SocksServer

    server = config.server
    if isinstance(server, SocksServer):
        notes = [
            f"сервер:    {server.address}:{server.port}",
            f"протокол:  socks5",
            f"логин:     {server.username or '-'}",
        ]
        if server.remark:
            notes.insert(0, f"профиль:   {server.remark}")
        return CheckResult("Параметры ссылки", Status.OK, "SOCKS5 сервер", notes=notes)

    notes = [
        f"сервер:    {server.address}:{server.port}",
        f"транспорт: type={server.network}, security={server.security}"
        + (f", sni={server.sni}" if server.sni else ""),
        # UUID — это пароль к серверу, а отчёт делается ради того, чтобы его
        # кому-то показать. Оставляем края: их хватает, чтобы убедиться, что
        # в конфиге та самая ссылка, и мало, чтобы подключиться.
        f"UUID:      {_mask_secret(server.user_id)}",
    ]
    if server.remark:
        notes.insert(0, f"профиль:   {server.remark}")
    if server.alpn:
        notes.append(f"ALPN:      {', '.join(server.alpn)}")

    if server.unsupported:
        params = ", ".join(f"{u.param}={u.value}" for u in server.unsupported)
        if backend == XRAY:
            return CheckResult(
                "Параметры ссылки", Status.OK,
                f"{params} — обслуживает xray",
                notes=notes,
            )
        return CheckResult(
            "Параметры ссылки",
            Status.FAIL,
            f"не поддерживается: {params}",
            hint="\n".join(f"{u.reason}\n{u.remedy}" for u in server.unsupported),
            notes=notes,
        )

    warnings = []
    if server.allow_insecure:
        warnings.append(
            "allowInsecure=true — сертификат не проверяется, возможен MITM"
        )
    if server.fingerprint and backend != XRAY:
        warnings.append(
            f"fp={server.fingerprint} игнорируется: подделка TLS-отпечатка (uTLS) "
            f"в стандартном ssl недоступна. На работу это не влияет, "
            f"но отпечаток будет питоновский"
        )
    if warnings:
        return CheckResult(
            "Параметры ссылки", Status.WARN, "; ".join(warnings), notes=notes
        )

    return CheckResult("Параметры ссылки", Status.OK, "всё поддерживается", notes=notes)


def check_listen_port(config: AppConfig) -> CheckResult:
    """Проверить, что локальный порт SOCKS5 свободен."""
    host, port = config.listen_host, config.listen_port
    if port == 0:
        return CheckResult("Локальный порт", Status.SKIP, "порт выбирается системой")

    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    try:
        # SO_REUSEADDR ставим только на POSIX. В Windows он означает совсем
        # другое — «разрешить привязку к УЖЕ занятому порту», и проверка
        # свободен ли порт превратилась бы в проверку «существует ли порт».
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(1)
        return CheckResult("Локальный порт", Status.OK, f"{host}:{port} свободен")
    except OSError as exc:
        return CheckResult(
            "Локальный порт",
            Status.FAIL,
            f"{host}:{port} занять не получается: {exc}",
            hint="Порт уже занят другим процессом (возможно, вторым экземпляром "
                 "этой же программы или другим VPN-клиентом). "
                 "Выберите другой порт через -l 127.0.0.1:1081.",
        )
    finally:
        sock.close()


async def check_dns(config: AppConfig) -> CheckResult:
    """Разрешить адрес сервера в IP."""
    host = config.server.address
    try:
        ipaddress.ip_address(host)
        return CheckResult("DNS", Status.SKIP, f"{host} — уже IP-адрес")
    except ValueError:
        pass

    loop = asyncio.get_running_loop()
    started = time.monotonic()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, config.server.port, proto=socket.IPPROTO_TCP),
            timeout=10,
        )
    except asyncio.TimeoutError:
        return CheckResult(
            "DNS", Status.FAIL, f"{host}: таймаут разрешения имени (10 c)",
            hint="DNS-сервер не отвечает. Проверьте сетевые настройки.",
            elapsed=time.monotonic() - started,
        )
    except socket.gaierror as exc:
        return CheckResult(
            "DNS", Status.FAIL, f"{host}: {exc}",
            hint="Имя не разрешается. Опечатка в адресе, либо домен заблокирован "
                 "на уровне DNS — попробуйте DNS-over-HTTPS или другой резолвер.",
            elapsed=time.monotonic() - started,
        )

    addrs = sorted({info[4][0] for info in infos})
    elapsed = time.monotonic() - started

    suspicious = [
        a for a in addrs
        if ipaddress.ip_address(a).is_private
        or ipaddress.ip_address(a).is_loopback
        or ipaddress.ip_address(a).is_unspecified
    ]
    if suspicious:
        return CheckResult(
            "DNS", Status.WARN,
            f"{host} -> {', '.join(addrs)}",
            hint=f"Адреса {', '.join(suspicious)} — локальные/приватные. "
                 f"Похоже на подмену DNS провайдером.",
            elapsed=elapsed,
            data={"addresses": addrs},
        )

    return CheckResult(
        "DNS", Status.OK, f"{host} -> {', '.join(addrs)}",
        elapsed=elapsed, data={"addresses": addrs},
    )


async def check_tcp(config: AppConfig) -> CheckResult:
    """Открыть голый TCP до сервера — без TLS и без VLESS."""
    server = config.server
    started = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(server.address, server.port),
            timeout=config.connect_timeout,
        )
    except asyncio.TimeoutError:
        return CheckResult(
            "TCP", Status.FAIL,
            f"{server.address}:{server.port} — таймаут "
            f"({config.connect_timeout:.0f} c)",
            hint="Пакеты уходят в никуда: порт фильтруется провайдером "
                 "или сервер лежит. Попробуйте другой порт/сервер.",
            elapsed=time.monotonic() - started,
        )
    except ConnectionRefusedError:
        return CheckResult(
            "TCP", Status.FAIL,
            f"{server.address}:{server.port} — соединение отвергнуто",
            hint="Порт закрыт: на нём никто не слушает. "
                 "Проверьте номер порта в ссылке.",
            elapsed=time.monotonic() - started,
        )
    except OSError as exc:
        return CheckResult(
            "TCP", Status.FAIL, f"{server.address}:{server.port} — {exc}",
            elapsed=time.monotonic() - started,
        )

    elapsed = time.monotonic() - started
    peer = writer.get_extra_info("peername")
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # noqa: BLE001
        pass

    return CheckResult(
        "TCP", Status.OK,
        f"{peer[0]}:{peer[1]} отвечает, RTT {elapsed * 1000:.0f} мс",
        elapsed=elapsed,
    )


async def check_tls(config: AppConfig) -> CheckResult:
    """Выполнить TLS-рукопожатие и разобрать сертификат."""
    result = await _check_tls(config)
    if config.server.security == "reality" and result.failed:
        # Для reality это справочная проверка: подключаться по ней мы всё
        # равно не будем, поэтому падением всей диагностики она быть не должна.
        result.status = Status.WARN
        result.notes.append(
            "проверка справочная: при security=reality обычный TLS-клиент "
            "и не должен проходить — сервер отдаёт «чужой» сертификат всем, "
            "кто не прошёл скрытую аутентификацию REALITY"
        )
    return result


async def _check_tls(config: AppConfig) -> CheckResult:
    server = config.server
    if not server.speaks_tls_on_the_wire:
        return CheckResult("TLS", Status.SKIP, "security=none, TLS не используется")

    hostname = server.sni or server.address
    started = time.monotonic()

    if server.security == "reality":
        # Подключиться по REALITY мы не можем, но наружу порт отвечает обычным
        # TLS — показать, что там за сертификат, всё равно полезно.
        secure_ctx = ssl.create_default_context()
        secure_ctx.check_hostname = True
    else:
        secure_ctx = build_ssl_context(server)
    result = await _tls_handshake(server, secure_ctx, hostname)
    elapsed = time.monotonic() - started

    if result.get("error") is None:
        cert = result["cert"] or {}
        notes = [
            f"версия:  {result['version']}, шифр: {result['cipher']}",
            f"subject: {_name_of(cert.get('subject'))}",
            f"issuer:  {_name_of(cert.get('issuer'))}",
        ]
        san = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
        if san:
            notes.append(f"SAN:     {', '.join(san[:6])}")
        if result.get("alpn"):
            notes.append(f"ALPN:    {result['alpn']}")
        notes.append(f"SHA-256: {result['fingerprint']}")

        status, hint = Status.OK, ""
        days = _days_left(cert.get("notAfter"))
        if days is not None:
            notes.append(f"годен до {cert.get('notAfter')} (осталось {days} дн.)")
            if days < 0:
                status, hint = Status.FAIL, "Сертификат просрочен."
            elif days < 7:
                status = Status.WARN
                hint = "Сертификат истекает в ближайшие дни."

        if server.allow_insecure:
            status = Status.WARN if status is Status.OK else status
            hint = (hint + " Проверка сертификата отключена (allowInsecure).").strip()

        notes.append(
            "issuer выше — тот, кто подписал сертификат. Если это не публичный "
            "УЦ (Let's Encrypt, DigiCert, Google Trust Services и т.п.), "
            "TLS расшифровывает посредник в вашей сети."
        )

        return CheckResult(
            "TLS", status, f"рукопожатие прошло за {elapsed * 1000:.0f} мс",
            hint=hint, elapsed=elapsed, notes=notes,
            data={"cert": cert, "fingerprint": result["fingerprint"]},
        )

    # Проверка не прошла — выясняем, дело в сертификате или в самом TLS.
    error = result["error"]
    insecure_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    insecure_ctx.check_hostname = False
    insecure_ctx.verify_mode = ssl.CERT_NONE
    fallback = await _tls_handshake(server, insecure_ctx, hostname)

    if fallback.get("error") is None:
        return CheckResult(
            "TLS", Status.FAIL,
            f"сертификат отклонён: {error}",
            hint=f"Сам TLS работает ({fallback['version']}, "
                 f"отпечаток {fallback['fingerprint']}), проблема в доверии. "
                 f"Обычные причины: неверный sni= в ссылке, самоподписанный "
                 f"сертификат, просроченный сертификат, либо в системе нет "
                 f"корневых сертификатов. Временно обойти: allowInsecure=true "
                 f"(но тогда трафик можно прочитать посредине — только для теста).",
            elapsed=elapsed,
            data={"fingerprint": fallback["fingerprint"]},
        )

    return CheckResult(
        "TLS", Status.FAIL, f"рукопожатие не состоялось: {error}",
        hint="Сервер не говорит на TLS на этом порту. Скорее всего "
             "security= в ссылке не соответствует настройкам сервера, "
             "либо трафик режет DPI.",
        elapsed=elapsed,
    )


async def _tls_handshake(server, ctx, hostname: str) -> dict[str, Any]:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                server.address, server.port, ssl=ctx, server_hostname=hostname
            ),
            timeout=15,
        )
    except asyncio.TimeoutError:
        return {"error": "таймаут TLS-рукопожатия (15 c)"}
    except ssl.SSLCertVerificationError as exc:
        return {"error": exc.verify_message or str(exc)}
    except ssl.SSLError as exc:
        return {"error": str(exc)}
    except OSError as exc:
        return {"error": str(exc)}

    ssl_object = writer.get_extra_info("ssl_object")
    der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
    info = {
        "error": None,
        "version": ssl_object.version() if ssl_object else "?",
        "cipher": ssl_object.cipher()[0] if ssl_object else "?",
        "cert": ssl_object.getpeercert() if ssl_object else None,
        "alpn": ssl_object.selected_alpn_protocol() if ssl_object else None,
        "fingerprint": hashlib.sha256(der).hexdigest()[:32] if der else "?",
    }
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # noqa: BLE001
        pass
    return info


async def check_vless_handshake(
    config: AppConfig, probe_host: str, probe_port: int
) -> CheckResult:
    """Открыть VLESS-поток и дождаться заголовка ответа сервера."""
    started = time.monotonic()
    conn = None
    try:
        conn = await VlessConnection.connect_tcp(
            config.server, probe_host, probe_port,
            connect_timeout=config.connect_timeout,
        )
        await conn.flush_header()
        # Ждём только заголовок ответа: он приходит, как только сервер
        # опознал UUID и открыл соединение к цели — молчание самой цели
        # (например, HTTP-сервера) на это не влияет.
        await asyncio.wait_for(conn.read_response_header(), timeout=15)
        elapsed = time.monotonic() - started
        return CheckResult(
            "Рукопожатие VLESS", Status.OK,
            f"сервер принял UUID, туннель открыт за {elapsed * 1000:.0f} мс",
            elapsed=elapsed,
        )
    except ProtocolError as exc:
        return CheckResult(
            "Рукопожатие VLESS", Status.FAIL, str(exc),
            hint="Транспорт до сервера работает, но VLESS-уровень не отвечает. "
                 "По убыванию вероятности: неверный UUID; на сервере включён "
                 "Reality или другой транспорт (ws/grpc), а в ссылке указан tcp; "
                 "порт отдан другому inbound.",
            elapsed=time.monotonic() - started,
        )
    except asyncio.TimeoutError:
        return CheckResult(
            "Рукопожатие VLESS", Status.FAIL,
            "сервер принял соединение, но за 15 с не ответил",
            hint="Типично для Reality: сервер молча проксирует «чужой» трафик "
                 "на маскировочный сайт, если клиент не прошёл аутентификацию.",
            elapsed=time.monotonic() - started,
        )
    except TransportError as exc:
        return CheckResult(
            "Рукопожатие VLESS", Status.FAIL, str(exc),
            elapsed=time.monotonic() - started,
        )
    except OSError as exc:
        return CheckResult(
            "Рукопожатие VLESS", Status.FAIL, f"{type(exc).__name__}: {exc}",
            elapsed=time.monotonic() - started,
        )
    finally:
        if conn is not None:
            conn.close()


async def check_http_through_tunnel(
    config: AppConfig, host: str, port: int, path: str
) -> CheckResult:
    """Сделать настоящий HTTP-запрос через туннель."""
    from .selftest import check_tunnel

    started = time.monotonic()
    ok, message = await check_tunnel(config, host, port, path)
    elapsed = time.monotonic() - started
    if ok:
        return CheckResult("HTTP через туннель", Status.OK, message, elapsed=elapsed)
    return CheckResult(
        "HTTP через туннель", Status.FAIL, message,
        hint=f"Туннель открылся, но {host}:{port} через него недоступен. "
             f"Возможно, сам сервер не выпускает трафик наружу.",
        elapsed=elapsed,
    )


async def check_udp_through_tunnel(
    config: AppConfig, resolver: str = "8.8.8.8", domain: str = "example.com"
) -> CheckResult:
    """Отправить DNS-запрос через VLESS UDP и дождаться ответа."""
    if not config.udp_enabled:
        return CheckResult("UDP через туннель", Status.SKIP, "UDP отключён в настройках")

    query = _dns_query(domain)
    started = time.monotonic()
    conn = None
    try:
        conn = await VlessConnection.connect_udp(
            config.server, resolver, 53, connect_timeout=config.connect_timeout
        )
        await conn.write_datagram(query)
        answer = await asyncio.wait_for(conn.read_datagram(), timeout=15)
        elapsed = time.monotonic() - started
        if not answer:
            return CheckResult(
                "UDP через туннель", Status.FAIL, "ответа от DNS не пришло",
                hint="Сервер может не выпускать UDP. На работу TCP это не влияет — "
                     "запустите прокси с --no-udp.",
                elapsed=elapsed,
            )
        if len(answer) >= 4 and answer[:2] == query[:2]:
            return CheckResult(
                "UDP через туннель", Status.OK,
                f"DNS {resolver}:53 ответил за {elapsed * 1000:.0f} мс "
                f"({len(answer)} байт)",
                elapsed=elapsed,
            )
        return CheckResult(
            "UDP через туннель", Status.WARN,
            f"пришёл ответ {len(answer)} байт, но ID DNS-запроса не совпал",
            elapsed=elapsed,
        )
    except asyncio.TimeoutError:
        return CheckResult(
            "UDP через туннель", Status.FAIL, "таймаут DNS-запроса (15 c)",
            hint="Сервер, скорее всего, не выпускает UDP. Запустите с --no-udp.",
            elapsed=time.monotonic() - started,
        )
    except (ProtocolError, TransportError, OSError) as exc:
        return CheckResult(
            "UDP через туннель", Status.FAIL, f"{type(exc).__name__}: {exc}",
            elapsed=time.monotonic() - started,
        )
    finally:
        if conn is not None:
            await conn.wait_closed()


def check_xray_config(config: AppConfig) -> CheckResult:
    """Собрать конфиг для xray и показать, что в нём получилось."""
    from .xray import build_xray_config, describe_config

    try:
        data = build_xray_config(config, legacy_vnext=config.xray_legacy_config)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "Конфиг xray", Status.FAIL, f"{type(exc).__name__}: {exc}",
            hint="Похоже, в ссылке параметры, которых генератор не ожидал. "
                 "Пришлите ссылку — поправлю.",
        )

    missing = []
    stream = data["outbounds"][0].get("streamSettings") or {}
    if stream.get("security") == "reality":
        reality = stream["realitySettings"]
        if not reality.get("publicKey"):
            missing.append("pbk (publicKey)")
    if missing:
        return CheckResult(
            "Конфиг xray", Status.FAIL,
            f"в ссылке не хватает: {', '.join(missing)}",
            hint="Для REALITY нужен публичный ключ сервера. Скопируйте ссылку "
                 "из панели целиком — обычно параметр называется pbk=.",
            notes=describe_config(data).splitlines(),
        )

    return CheckResult(
        "Конфиг xray", Status.OK, "собран",
        notes=describe_config(data).splitlines(),
    )


async def check_xray_run(
    config: AppConfig, xray_path: str, probe: tuple[str, int, str]
) -> list[CheckResult]:
    """Поднять xray и проверить туннель через его SOCKS5-вход."""
    from .selftest import check_through_socks5
    from .xray import XrayProcess, XrayStartupError

    if config.listen_port == 0:
        return [
            CheckResult(
                "Запуск xray", Status.SKIP,
                "для проверки нужен конкретный порт (-l 127.0.0.1:1081)",
            )
        ]

    started = time.monotonic()
    process = XrayProcess(
        config, xray_path=xray_path, restart=False,
        legacy_vnext=config.xray_legacy_config,
    )
    try:
        await process.start()
    except XrayStartupError as exc:
        return [
            CheckResult(
                "Запуск xray", Status.FAIL, "процесс не поднялся",
                hint=str(exc), elapsed=time.monotonic() - started,
            ),
            CheckResult("HTTP через туннель", Status.SKIP, "xray не запущен"),
        ]

    results = [
        CheckResult(
            "Запуск xray", Status.OK,
            f"pid {process.pid}, SOCKS5 на "
            f"{config.listen_host}:{config.listen_port}",
            elapsed=time.monotonic() - started,
        )
    ]
    try:
        host, port, path = probe
        probe_started = time.monotonic()
        ok, message = await check_through_socks5(config, host, port, path)
        results.append(
            CheckResult(
                "HTTP через туннель",
                Status.OK if ok else Status.FAIL,
                message,
                hint="" if ok else (
                    "xray запустился, но трафик не ходит. Последние строки "
                    "его лога:\n" + "\n".join(process.log_tail[-10:])
                ),
                elapsed=time.monotonic() - probe_started,
            )
        )
    finally:
        await process.stop()
    return results


# ------------------------------------------------------------------ сценарий


async def run_diagnostics(
    config: AppConfig,
    *,
    probe_host: str = "www.gstatic.com",
    probe_port: int = 80,
    probe_path: str = "/generate_204",
) -> list[CheckResult]:
    """Прогнать все проверки по порядку, пропуская зависимые после падения."""
    from .backend import XRAY

    results: list[CheckResult] = []

    backend_result, backend, xray_path = check_backend(config)
    results.append(backend_result)
    params_result = check_parameters(config, backend)
    results.append(params_result)
    results.append(check_listen_port(config))

    tail = (
        ("Запуск xray", "HTTP через туннель")
        if backend == XRAY
        else ("Рукопожатие VLESS", "HTTP через туннель", "UDP через туннель")
    )

    dns = await check_dns(config)
    results.append(dns)
    if dns.failed:
        _skip(results, ("TCP", "TLS", *tail), "нет IP-адреса сервера")
        return results

    tcp = await check_tcp(config)
    results.append(tcp)
    if tcp.failed:
        _skip(results, ("TLS", *tail), "нет TCP-соединения")
        return results

    tls = await check_tls(config)
    results.append(tls)
    if tls.failed:
        _skip(results, tail, "TLS не установлен")
        return results

    if backend == XRAY:
        cfg_result = check_xray_config(config)
        results.append(cfg_result)
        if cfg_result.failed:
            _skip(results, tail, "конфиг не собран")
            return results
        results.extend(
            await check_xray_run(
                config, xray_path, (probe_host, probe_port, probe_path)
            )
        )
        return results

    if backend_result.failed or params_result.failed:
        # Движка нет или параметры не поддержаны — дальше лезть бессмысленно,
        # но нижние уровни мы уже проверили и показали, что сеть до сервера живая.
        _skip(results, tail, "профиль не обслуживается ни одним движком")
        return results

    handshake = await check_vless_handshake(config, probe_host, probe_port)
    results.append(handshake)
    if handshake.failed:
        _skip(results, ("HTTP через туннель", "UDP через туннель"),
              "туннель не открылся")
        return results

    results.append(
        await check_http_through_tunnel(config, probe_host, probe_port, probe_path)
    )
    results.append(await check_udp_through_tunnel(config))
    return results


def _skip(results: list[CheckResult], names, reason: str) -> None:
    for name in names:
        results.append(CheckResult(name, Status.SKIP, reason))


# --------------------------------------------------------------------- вывод


def format_report(results: list[CheckResult]) -> str:
    """Собрать человекочитаемый отчёт."""
    lines = ["", "=" * 72, "ДИАГНОСТИКА vless2socks", "=" * 72]
    for res in results:
        timing = f"  ({res.elapsed * 1000:.0f} мс)" if res.elapsed else ""
        lines.append(f"{MARKS[res.status]} {res.name}: {res.detail}{timing}")
        for note in res.notes:
            lines.append(f"         {note}")
        if res.hint:
            for line in res.hint.splitlines():
                if line.strip():
                    lines.append(f"      -> {line.strip()}")
    lines.append("=" * 72)

    failures = [r for r in results if r.failed]
    warns = [r for r in results if r.status is Status.WARN]
    if failures:
        lines.append(f"ИТОГ: не работает. Первая причина: {failures[0].name}.")
    elif warns:
        lines.append("ИТОГ: туннель работает, но есть замечания (см. WARN выше).")
    else:
        lines.append("ИТОГ: всё в порядке, можно запускать прокси.")
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------- утилиты


def _mask_secret(value: str) -> str:
    """Показать только края секрета — отчёт предназначен для пересылки."""
    if not value:
        return "(пусто)"
    if len(value) <= 12:
        return "***"
    return f"{value[:8]}...{value[-4:]}"


def _name_of(rdns) -> str:
    """Развернуть структуру subject/issuer из getpeercert() в строку."""
    if not rdns:
        return "(нет данных)"
    parts = []
    for rdn in rdns:
        for key, value in rdn:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


def _days_left(not_after: str | None) -> int | None:
    if not not_after:
        return None
    for fmt in ("%b %d %H:%M:%S %Y %Z", "%b %d %H:%M:%S %Y"):
        try:
            expires = datetime.strptime(not_after, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return (expires - datetime.now(timezone.utc)).days
    return None


def _dns_query(domain: str, qtype: int = 1) -> bytes:
    """Собрать минимальный DNS-запрос типа A."""
    header = struct.pack("!HHHHHH", 0x4A4B, 0x0100, 1, 0, 0, 0)
    question = b"".join(
        bytes([len(part)]) + part.encode("ascii") for part in domain.split(".")
    ) + b"\x00" + struct.pack("!HH", qtype, 1)
    return header + question
