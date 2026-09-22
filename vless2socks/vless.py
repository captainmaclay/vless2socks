"""Клиентское VLESS-соединение поверх транспорта."""

from __future__ import annotations

import asyncio
import struct

from .logging_setup import get_logger
from .protocol import (
    CMD_TCP,
    CMD_UDP,
    ProtocolError,
    build_request_header,
)
from .transport import TransportError, open_upstream
from .url import VlessServer

__all__ = ["VlessConnection"]

log = get_logger("vless2socks.vless")


class VlessConnection:
    """Одно соединение до VLESS-сервера, уже адресованное конкретной цели.

    Заголовок запроса отправляется при первой записи (вместе с первыми данными,
    чтобы не плодить лишний TCP-сегмент). Заголовок ответа сервера вычитывается
    лениво, при первом чтении.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        header: bytes,
        *,
        target: str,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._pending_header: bytes | None = header
        self._response_read = False
        self._closed = False
        self.target = target

    # ------------------------------------------------------------------ init

    @classmethod
    async def connect(
        cls,
        server: VlessServer,
        host: str,
        port: int,
        *,
        command: int = CMD_TCP,
        connect_timeout: float = 10.0,
    ) -> "VlessConnection":
        reader, writer = await open_upstream(server, connect_timeout=connect_timeout)
        header = build_request_header(server.uuid_bytes, command, host, port)
        return cls(reader, writer, header, target=f"{host}:{port}")

    @classmethod
    async def connect_tcp(
        cls, server: VlessServer, host: str, port: int, **kw
    ) -> "VlessConnection":
        return await cls.connect(server, host, port, command=CMD_TCP, **kw)

    @classmethod
    async def connect_udp(
        cls, server: VlessServer, host: str, port: int, **kw
    ) -> "VlessConnection":
        return await cls.connect(server, host, port, command=CMD_UDP, **kw)

    # ------------------------------------------------------------------- io

    async def write(self, data: bytes) -> None:
        """Отправить полезную нагрузку (при первом вызове — вместе с заголовком)."""
        if self._closed:
            raise ConnectionResetError("VLESS-соединение уже закрыто")
        if self._pending_header is not None:
            data = self._pending_header + data
            self._pending_header = None
        if not data:
            return
        self._writer.write(data)
        await self._writer.drain()

    async def flush_header(self) -> None:
        """Отправить заголовок, даже если полезных данных ещё нет."""
        if self._pending_header is not None:
            await self.write(b"")

    async def _read_response_header(self) -> None:
        if self._response_read:
            return
        self._response_read = True
        try:
            head = await self._reader.readexactly(2)
        except asyncio.IncompleteReadError as exc:
            raise ProtocolError(
                "сервер закрыл соединение, не прислав заголовок ответа VLESS "
                f"(получено {len(exc.partial)} байт). Обычно это неверный UUID, "
                f"неверный порт/SNI или сервер ждёт другой транспорт"
            ) from None
        version, addons_len = head[0], head[1]
        if version != 0:
            raise ProtocolError(
                f"сервер ответил версией VLESS {version}, ожидалась 0 "
                f"(похоже, на том конце не VLESS)"
            )
        if addons_len:
            await self._reader.readexactly(addons_len)

    async def read_response_header(self) -> None:
        """Дождаться только заголовка ответа сервера, не трогая нагрузку.

        Нужно диагностике: подтверждает, что сервер принял UUID и открыл
        соединение к цели, даже если сама цель ещё молчит.
        """
        await self._read_response_header()

    async def read(self, n: int = 65536) -> bytes:
        """Прочитать полезную нагрузку. Пустой ``bytes`` означает EOF."""
        await self._read_response_header()
        return await self._reader.read(n)

    # ------------------------------------------------------------- udp frames

    async def write_datagram(self, payload: bytes) -> None:
        """Отправить UDP-датаграмму (кадр с 2-байтовым префиксом длины)."""
        await self.write(struct.pack("!H", len(payload)) + payload)

    async def read_datagram(self) -> bytes | None:
        """Прочитать одну UDP-датаграмму. ``None`` — поток закончился."""
        await self._read_response_header()
        try:
            head = await self._reader.readexactly(2)
            (length,) = struct.unpack("!H", head)
            if length == 0:
                return b""
            return await self._reader.readexactly(length)
        except asyncio.IncompleteReadError:
            return None

    # ---------------------------------------------------------------- close

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._writer.close()
        except Exception:  # noqa: BLE001 - закрытие не должно ронять релей
            pass

    async def wait_closed(self) -> None:
        self.close()
        try:
            await self._writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass

    def write_eof(self) -> None:
        """Послать EOF в сторону сервера, не закрывая чтение."""
        try:
            if self._writer.can_write_eof():
                self._writer.write_eof()
        except Exception:  # noqa: BLE001
            pass

    async def __aenter__(self) -> "VlessConnection":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.wait_closed()


__all__.append("TransportError")
