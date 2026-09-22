"""Минимальный VLESS-сервер на asyncio — только для тестов.

Умеет ровно то, что нужно для сквозной проверки клиента: разобрать заголовок
запроса, сверить UUID, подключиться к цели (TCP) или пробросить UDP-датаграммы,
ответить заголовком VLESS и качать данные в обе стороны.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
import struct
import uuid as uuid_mod
from typing import Optional

from vless2socks.protocol import (
    CMD_TCP,
    CMD_UDP,
    ProtocolError,
    build_response_header,
    parse_request_header,
)


class FakeVlessServer:
    def __init__(
        self,
        user_id: str,
        host: str = "127.0.0.1",
        port: int = 0,
        ssl_context: Optional[ssl.SSLContext] = None,
        source_address: Optional[tuple[str, int]] = None,
    ) -> None:
        self.user_id = str(uuid_mod.UUID(user_id))
        self.host = host
        self.requested_port = port
        self.ssl_context = ssl_context
        #: С какого адреса сервер ходит к цели. Нужно тестам, чтобы показать
        #: настоящую подмену адреса: цель видит его, а не адрес клиента.
        self.source_address = source_address
        self._server: asyncio.AbstractServer | None = None
        self.sessions: list[tuple[int, str, int]] = []

    @property
    def port(self) -> int:
        assert self._server and self._server.sockets
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> "FakeVlessServer":
        self._server = await asyncio.start_server(
            self._handle, self.host, self.requested_port, ssl=self.ssl_context
        )
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> "FakeVlessServer":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    # ------------------------------------------------------------- handling

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            buf = bytearray()
            request = None
            while request is None:
                chunk = await reader.read(4096)
                if not chunk:
                    return
                buf += chunk
                try:
                    request = parse_request_header(bytes(buf))
                except ProtocolError:
                    if len(buf) > 8192:
                        raise
                    continue

            if request.user_id != self.user_id:
                writer.close()
                return

            payload = bytes(buf[request.header_len:])
            self.sessions.append((request.command, request.host, request.port))

            if request.command == CMD_TCP:
                await self._do_tcp(request, payload, reader, writer)
            elif request.command == CMD_UDP:
                await self._do_udp(request, payload, reader, writer)
        except (ConnectionError, ProtocolError, OSError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def _do_tcp(self, request, payload, reader, writer) -> None:
        try:
            t_reader, t_writer = await asyncio.wait_for(
                asyncio.open_connection(
                    request.host, request.port, local_addr=self.source_address
                ),
                timeout=10,
            )
        except (OSError, asyncio.TimeoutError):
            return

        writer.write(build_response_header())
        if payload:
            t_writer.write(payload)
            await t_writer.drain()
        await writer.drain()

        async def up() -> None:
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        if t_writer.can_write_eof():
                            t_writer.write_eof()
                        return
                    t_writer.write(data)
                    await t_writer.drain()
            except (ConnectionError, OSError):
                return

        async def down() -> None:
            try:
                while True:
                    data = await t_reader.read(65536)
                    if not data:
                        if writer.can_write_eof():
                            writer.write_eof()
                        return
                    writer.write(data)
                    await writer.drain()
            except (ConnectionError, OSError):
                return

        tasks = [asyncio.ensure_future(up()), asyncio.ensure_future(down())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            await asyncio.wait(tasks, timeout=2.0)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            t_writer.close()

    async def _do_udp(self, request, payload, reader, writer) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        target = (request.host, request.port)

        writer.write(build_response_header())
        await writer.drain()
        header_sent = True

        async def send_frame(data: bytes) -> None:
            writer.write(struct.pack("!H", len(data)) + data)
            await writer.drain()

        async def down() -> None:
            try:
                while True:
                    data = await loop.sock_recv(sock, 65535)
                    await send_frame(data)
            except (ConnectionError, OSError, asyncio.CancelledError):
                return

        down_task = asyncio.ensure_future(down())
        try:
            buf = bytearray(payload)
            while True:
                while len(buf) >= 2:
                    (length,) = struct.unpack("!H", bytes(buf[:2]))
                    if len(buf) < 2 + length:
                        break
                    datagram = bytes(buf[2 : 2 + length])
                    del buf[: 2 + length]
                    await loop.sock_sendto(sock, datagram, target)
                chunk = await reader.read(65536)
                if not chunk:
                    return
                buf += chunk
        finally:
            down_task.cancel()
            await asyncio.gather(down_task, return_exceptions=True)
            sock.close()
            assert header_sent
