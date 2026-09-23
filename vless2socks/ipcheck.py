"""Сравнение внешнего IP напрямую и через туннель.

Отвечает ровно на один вопрос: подменяется ли адрес, и — не менее важно —
остаётся ли прежним адрес у остальной системы. Оба запроса делаются с этой
же машины к одному и тому же сервису, разница только в маршруте.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import time
from dataclasses import dataclass, field

from .logging_setup import get_logger
from .socks_client import Socks5ClientError, http_get_via_socks5

__all__ = ["IpReport", "Service", "DEFAULT_SERVICES", "fetch_ip_direct",
           "fetch_ip_via_socks5", "compare_ip", "format_ip_report",
           "check_ip_leak", "check_ip_leak_async"]

log = get_logger("vless2socks.ipcheck")

IP_RE = re.compile(r"\b((?:\d{1,3}\.){3}\d{1,3}|[0-9a-fA-F:]{6,})\b")


@dataclass(frozen=True)
class Service:
    """Сервис, отдающий IP клиента простым текстом."""

    host: str
    path: str = "/"
    port: int = 80

    def __str__(self) -> str:
        return f"{self.host}{self.path}"


#: По HTTP, а не HTTPS — нам нужно только эхо адреса, и так проще и быстрее.
#: Несколько штук на случай, если какой-то недоступен.
DEFAULT_SERVICES = (
    Service("api.ipify.org", "/"),
    Service("ifconfig.me", "/ip"),
    Service("icanhazip.com", "/"),
    Service("ipinfo.io", "/ip"),
)


@dataclass
class IpReport:
    direct: str = ""
    direct_error: str = ""
    tunnel: str = ""
    tunnel_error: str = ""
    service: str = ""
    listen: str = ""
    backend: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def substituted(self) -> bool | None:
        """``True`` — адреса разные, ``False`` — одинаковые, ``None`` — неизвестно."""
        if not self.direct or not self.tunnel:
            return None
        return self.direct != self.tunnel

    @property
    def ok(self) -> bool:
        return self.substituted is True


def _extract_ip(body: str) -> str:
    """Выдернуть адрес из ответа сервиса."""
    candidate = body.strip().splitlines()[0].strip() if body.strip() else ""
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        pass
    match = IP_RE.search(body)
    if not match:
        return ""
    try:
        return str(ipaddress.ip_address(match.group(1)))
    except ValueError:
        return ""


async def _http_get_direct(
    service: Service, timeout: float
) -> tuple[str, str]:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(service.host, service.port), timeout=timeout
    )
    try:
        writer.write(
            (
                f"GET {service.path} HTTP/1.1\r\n"
                f"Host: {service.host}\r\n"
                "User-Agent: vless2socks\r\n"
                "Accept: */*\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(65536), timeout=timeout)
        while raw and b"\r\n\r\n" not in raw:
            more = await asyncio.wait_for(reader.read(65536), timeout=timeout)
            if not more:
                break
            raw += more
        head, _, body = raw.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode("latin-1").strip()
        return status, body.decode("utf-8", "replace").strip()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


async def fetch_ip_direct(
    services=DEFAULT_SERVICES, timeout: float = 15.0
) -> tuple[str, str, str]:
    """Узнать IP напрямую, в обход туннеля.

    Возвращает ``(ip, использованный сервис, ошибка)``.
    """
    last_error = ""
    for service in services:
        try:
            status, body = await _http_get_direct(service, timeout)
        except asyncio.TimeoutError:
            last_error = f"{service}: таймаут ({timeout:.0f} c)"
            continue
        except OSError as exc:
            last_error = f"{service}: {exc}"
            continue
        ip = _extract_ip(body)
        if ip:
            return ip, str(service), ""
        last_error = f"{service}: {status}, адрес в ответе не найден"
    return "", "", last_error or "ни один сервис не ответил"


async def fetch_ip_via_socks5(
    socks_host: str,
    socks_port: int,
    *,
    username: str = "",
    password: str = "",
    services=DEFAULT_SERVICES,
    timeout: float = 25.0,
) -> tuple[str, str, str]:
    """Узнать IP через локальный SOCKS5 — тем же путём, каким пойдёт браузер."""
    last_error = ""
    for service in services:
        try:
            status, body = await http_get_via_socks5(
                socks_host, socks_port, service.host, service.port, service.path,
                username=username, password=password, timeout=timeout,
            )
        except Socks5ClientError as exc:
            last_error = str(exc)
            # Если не работает сам SOCKS5, другие сервисы не помогут.
            if "локальный SOCKS5" in last_error or "не SOCKS5" in last_error:
                break
            continue
        except (asyncio.TimeoutError, OSError) as exc:
            last_error = f"{service}: {exc}"
            continue
        ip = _extract_ip(body)
        if ip:
            return ip, str(service), ""
        last_error = f"{service}: {status}, адрес в ответе не найден"
    return "", "", last_error or "ни один сервис не ответил через туннель"


async def compare_ip(
    socks_host: str,
    socks_port: int,
    *,
    username: str = "",
    password: str = "",
    services=DEFAULT_SERVICES,
    backend: str = "",
) -> IpReport:
    """Сделать оба запроса и собрать отчёт."""
    report = IpReport(listen=f"{socks_host}:{socks_port}", backend=backend)

    started = time.monotonic()
    report.direct, direct_service, report.direct_error = await fetch_ip_direct(services)
    direct_ms = (time.monotonic() - started) * 1000

    started = time.monotonic()
    report.tunnel, tunnel_service, report.tunnel_error = await fetch_ip_via_socks5(
        socks_host, socks_port,
        username=username, password=password, services=services,
    )
    tunnel_ms = (time.monotonic() - started) * 1000

    report.service = tunnel_service or direct_service
    if report.direct:
        report.notes.append(f"прямой запрос: {direct_ms:.0f} мс")
    if report.tunnel:
        report.notes.append(f"через туннель: {tunnel_ms:.0f} мс")
    if direct_service and tunnel_service and direct_service != tunnel_service:
        report.notes.append(
            f"сервисы разные ({direct_service} и {tunnel_service}) — "
            f"сравнение всё равно корректно, адрес отдают одинаково"
        )
    return report


def format_ip_report(report: IpReport) -> str:
    """Человекочитаемый вывод для --ip."""
    lines = ["", "=" * 72, "ПРОВЕРКА ПОДМЕНЫ IP", "=" * 72]
    if report.backend:
        lines.append(f"движок:           {report.backend}")
    lines.append(f"локальный SOCKS5: {report.listen}")
    if report.service:
        lines.append(f"сервис:           {report.service}")
    lines.append("")

    lines.append(
        f"IP остальной системы (мимо прокси): {report.direct or '?'}"
        + (f"   [{report.direct_error}]" if report.direct_error else "")
    )
    lines.append(
        f"IP через SOCKS5-туннель:            {report.tunnel or '?'}"
        + (f"   [{report.tunnel_error}]" if report.tunnel_error else "")
    )
    for note in report.notes:
        lines.append(f"  {note}")
    lines.append("")
    lines.append("-" * 72)

    verdict = report.substituted
    if verdict is True:
        lines.append("ИТОГ: подмена работает.")
        lines.append(
            "Трафик через socks5://" + report.listen + " выходит с другого адреса,"
        )
        lines.append(
            "а всё остальное в системе продолжает ходить со своим. Это и есть"
        )
        lines.append("изолированное подключение: маршруты и настройки ОС не менялись.")
    elif verdict is False:
        lines.append("ИТОГ: подмены НЕТ — оба адреса одинаковые.")
        lines.append(
            "Туннель отвечает, но выпускает трафик с того же адреса. "
            "Проверьте, что вы"
        )
        lines.append(
            "подключаетесь к тому серверу, к которому собирались; "
            "запустите --doctor."
        )
    else:
        if not report.direct and report.tunnel:
            lines.append(
                "ИТОГ: туннель работает, но прямой запрос не прошёл — "
                "сравнить не с чем."
            )
        elif report.direct and not report.tunnel:
            lines.append("ИТОГ: туннель не отдал адрес. Запустите --doctor.")
        else:
            lines.append("ИТОГ: ни один запрос не прошёл. Проверьте сеть.")
    lines.append("=" * 72)
    lines.append("")
    return "\n".join(lines)


async def check_ip_leak_async(
    socks_host: str,
    socks_port: int,
    *,
    username: str = "",
    password: str = "",
    timeout: float = 6.0,
    services=DEFAULT_SERVICES,
) -> tuple[bool, str, str, str]:
    """Check whether local proxy leaks the real machine IP.

    Returns:
        (is_leak, direct_ip, tunnel_ip, message)
        is_leak is True if direct_ip and tunnel_ip both exist and are identical.
    """
    report = await compare_ip(
        socks_host,
        socks_port,
        username=username,
        password=password,
        services=services,
    )
    if report.substituted is False:
        return (
            True,
            report.direct,
            report.tunnel,
            f"LEAK DETECTED: Original IP ({report.direct}) matches proxy exit IP ({report.tunnel})!",
        )
    elif report.substituted is True:
        return (
            False,
            report.direct,
            report.tunnel,
            f"SAFE: Exit IP ({report.tunnel}) differs from real IP ({report.direct}).",
        )
    else:
        err = report.tunnel_error or report.direct_error or "Could not resolve both IPs"
        return False, report.direct, report.tunnel, f"Indeterminate: {err}"


def check_ip_leak(
    socks_host: str,
    socks_port: int,
    *,
    username: str = "",
    password: str = "",
    timeout: float = 6.0,
    services=DEFAULT_SERVICES,
) -> tuple[bool, str, str, str]:
    """Synchronous wrapper for check_ip_leak_async."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            check_ip_leak_async(
                socks_host,
                socks_port,
                username=username,
                password=password,
                timeout=timeout,
                services=services,
            )
        )
    finally:
        loop.close()

