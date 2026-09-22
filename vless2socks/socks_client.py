"""Минимальный клиент SOCKS5 — им пользуются самопроверка и проверка IP.

Ходить через собственный локальный порт важно принципиально: так проверка
идёт ровно тем же путём, что и браузер пользователя, а не в обход него.
"""

from __future__ import annotations

import asyncio
import struct

__all__ = ["Socks5ClientError", "open_via_socks5", "http_get_via_socks5"]


class Socks5ClientError(Exception):
    """Не удалось пройти через локальный SOCKS5."""


REPLY_MESSAGES = {
    0x01: "общая ошибка SOCKS-сервера",
    0x02: "соединение запрещено правилами",
    0x03: "сеть недоступна",
    0x04: "узел недоступен",
    0x05: "соединение отвергнуто",
    0x06: "истёк TTL",
    0x07: "команда не поддерживается",
    0x08: "тип адреса не поддерживается",
}


async def open_via_socks5(
    socks_host: str,
    socks_port: int,
    target_host: str,
    target_port: int,
    *,
    username: str = "",
    password: str = "",
    timeout: float = 20.0,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Открыть TCP-соединение до цели через локальный SOCKS5."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(socks_host, socks_port), timeout=timeout
        )
    except asyncio.TimeoutError:
        raise Socks5ClientError(
            f"таймаут подключения к {socks_host}:{socks_port}"
        ) from None
    except OSError as exc:
        raise Socks5ClientError(
            f"локальный SOCKS5 {socks_host}:{socks_port} недоступен: {exc}"
        ) from None

    try:
        await _negotiate(reader, writer, username, password, timeout)
        await _connect(reader, writer, target_host, target_port, timeout)
    except BaseException:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        raise
    return reader, writer


async def _negotiate(reader, writer, username: str, password: str, timeout: float) -> None:
    writer.write(b"\x05\x01\x02" if username else b"\x05\x01\x00")
    await writer.drain()

    try:
        version, method = await asyncio.wait_for(
            reader.readexactly(2), timeout=timeout
        )
    except (asyncio.TimeoutError, asyncio.IncompleteReadError):
        raise Socks5ClientError("сервер не ответил на приветствие SOCKS5") from None

    if version != 5:
        raise Socks5ClientError(
            f"на порту отвечает не SOCKS5 (версия {version}). "
            f"Возможно, там HTTP-прокси или вообще другая программа"
        )
    if method == 0xFF:
        raise Socks5ClientError("сервер не принял ни один метод аутентификации")

    if method == 0x02:
        user = username.encode("utf-8")[:255]
        secret = password.encode("utf-8")[:255]
        writer.write(
            b"\x01" + bytes([len(user)]) + user + bytes([len(secret)]) + secret
        )
        await writer.drain()
        try:
            _, status = await asyncio.wait_for(
                reader.readexactly(2), timeout=timeout
            )
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            raise Socks5ClientError("сервер не ответил на логин/пароль") from None
        if status != 0:
            raise Socks5ClientError("логин или пароль отвергнуты")


async def _connect(reader, writer, host: str, port: int, timeout: float) -> None:
    try:
        raw = host.encode("ascii")
    except UnicodeEncodeError:
        raw = host.encode("idna")
    writer.write(
        b"\x05\x01\x00\x03" + bytes([len(raw)]) + raw + struct.pack("!H", port)
    )
    await writer.drain()

    try:
        head = await asyncio.wait_for(reader.readexactly(4), timeout=timeout)
        atyp = head[3]
        if atyp == 0x01:
            await reader.readexactly(6)
        elif atyp == 0x04:
            await reader.readexactly(18)
        elif atyp == 0x03:
            length = (await reader.readexactly(1))[0]
            await reader.readexactly(length + 2)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError):
        raise Socks5ClientError("соединение оборвалось на ответе SOCKS5") from None

    reply = head[1]
    if reply != 0x00:
        raise Socks5ClientError(
            f"SOCKS5 отказал в CONNECT к {host}:{port}: "
            f"{REPLY_MESSAGES.get(reply, f'код {reply}')}"
        )


async def http_get_via_socks5(
    socks_host: str,
    socks_port: int,
    host: str,
    port: int,
    path: str,
    *,
    username: str = "",
    password: str = "",
    timeout: float = 20.0,
) -> tuple[str, str]:
    """GET через SOCKS5. Возвращает ``(строка статуса, тело)``."""
    reader, writer = await open_via_socks5(
        socks_host, socks_port, host, port,
        username=username, password=password, timeout=timeout,
    )
    try:
        return await _http_exchange(reader, writer, host, path, timeout)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


async def _http_exchange(reader, writer, host: str, path: str, timeout: float):
    writer.write(
        (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "User-Agent: vless2socks\r\n"
            "Accept: */*\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
    )
    await writer.drain()

    try:
        raw = await asyncio.wait_for(reader.read(65536), timeout=timeout)
    except asyncio.TimeoutError:
        raise Socks5ClientError("цель не ответила на HTTP-запрос") from None
    while raw and b"\r\n\r\n" not in raw:
        try:
            more = await asyncio.wait_for(reader.read(65536), timeout=timeout)
        except asyncio.TimeoutError:
            break
        if not more:
            break
        raw += more

    if not raw:
        raise Socks5ClientError("туннель открылся, но ответа не пришло")

    head, _, body = raw.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].decode("latin-1").strip()
    return status, body.decode("utf-8", "replace").strip()
