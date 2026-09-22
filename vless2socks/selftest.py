"""Проверка живости VLESS-туннеля без внешних зависимостей."""

from __future__ import annotations

import asyncio
import time

from .config import AppConfig
from .logging_setup import get_logger
from .vless import VlessConnection

__all__ = ["check_tunnel", "check_through_socks5"]

log = get_logger("vless2socks.selftest")

DEFAULT_PROBE_HOST = "www.gstatic.com"
DEFAULT_PROBE_PORT = 80
DEFAULT_PROBE_PATH = "/generate_204"


async def check_tunnel(
    config: AppConfig,
    host: str = DEFAULT_PROBE_HOST,
    port: int = DEFAULT_PROBE_PORT,
    path: str = DEFAULT_PROBE_PATH,
    timeout: float = 15.0,
) -> tuple[bool, str]:
    """Открыть туннель, сделать HTTP-запрос и вернуть ``(успех, сообщение)``."""
    started = time.monotonic()
    conn: VlessConnection | None = None
    try:
        conn = await asyncio.wait_for(
            VlessConnection.connect_tcp(
                config.server, host, port,
                connect_timeout=config.connect_timeout,
            ),
            timeout=timeout,
        )
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "User-Agent: vless2socks/selftest\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        await asyncio.wait_for(conn.write(request), timeout=timeout)

        chunks: list[bytes] = []
        deadline = time.monotonic() + timeout
        while b"\r\n" not in b"".join(chunks):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            data = await asyncio.wait_for(conn.read(4096), timeout=remaining)
            if not data:
                break
            chunks.append(data)

        response = b"".join(chunks)
        if not response:
            return False, "сервер принял соединение, но ничего не вернул"

        status_line = response.split(b"\r\n", 1)[0].decode("latin-1")
        elapsed = time.monotonic() - started
        if status_line.startswith("HTTP/"):
            return True, f"{status_line} через {host}:{port} за {elapsed:.2f} c"
        return False, f"неожиданный ответ: {status_line!r}"

    except asyncio.TimeoutError:
        return False, f"таймаут проверки ({timeout:.0f} c)"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if conn is not None:
            await conn.wait_closed()


async def check_through_socks5(
    config: AppConfig,
    host: str = DEFAULT_PROBE_HOST,
    port: int = DEFAULT_PROBE_PORT,
    path: str = DEFAULT_PROBE_PATH,
    timeout: float = 20.0,
) -> tuple[bool, str]:
    """То же, но через уже поднятый локальный SOCKS5-порт.

    Нужно для движка xray: там VLESS говорит он, и единственная точка, за
    которую мы можем подёргать, — его SOCKS5-вход.
    """
    from .socks_client import Socks5ClientError, http_get_via_socks5

    started = time.monotonic()
    try:
        status, _ = await http_get_via_socks5(
            config.listen_host, config.listen_port, host, port, path,
            username=config.username, password=config.password, timeout=timeout,
        )
    except Socks5ClientError as exc:
        return False, str(exc)
    except asyncio.TimeoutError:
        return False, f"таймаут проверки ({timeout:.0f} c)"
    except OSError as exc:
        return False, f"локальный SOCKS5-порт недоступен: {exc}"

    elapsed = time.monotonic() - started
    if status.startswith("HTTP/"):
        return True, f"{status} через {host}:{port} за {elapsed:.2f} c"
    return False, f"неожиданный ответ: {status!r}"
