"""Вспомогательные заглушки для тестов: SOCKS5-клиент, HTTP- и UDP-эхо-серверы."""

from __future__ import annotations

import asyncio
import socket
import struct


async def socks5_connect(
    proxy_host: str,
    proxy_port: int,
    dst_host: str,
    dst_port: int,
    username: str = "",
    password: str = "",
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, int]:
    """Пройти рукопожатие SOCKS5 CONNECT. Возвращает (reader, writer, REP)."""
    reader, writer = await asyncio.open_connection(proxy_host, proxy_port)

    if username:
        writer.write(b"\x05\x01\x02")
    else:
        writer.write(b"\x05\x01\x00")
    await writer.drain()

    ver, method = await reader.readexactly(2)
    assert ver == 5, ver

    if method == 0x02:
        u = username.encode()
        p = password.encode()
        writer.write(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
        await writer.drain()
        _, status = await reader.readexactly(2)
        if status != 0:
            writer.close()
            raise PermissionError("SOCKS5: аутентификация отклонена")
    elif method == 0xFF:
        writer.close()
        raise PermissionError("SOCKS5: сервер не принял метод аутентификации")

    host = dst_host.encode()
    writer.write(
        b"\x05\x01\x00\x03" + bytes([len(host)]) + host + struct.pack("!H", dst_port)
    )
    await writer.drain()

    head = await reader.readexactly(4)
    rep, atyp = head[1], head[3]
    if atyp == 0x01:
        await reader.readexactly(4 + 2)
    elif atyp == 0x04:
        await reader.readexactly(16 + 2)
    elif atyp == 0x03:
        n = (await reader.readexactly(1))[0]
        await reader.readexactly(n + 2)
    return reader, writer, rep


async def socks5_udp_associate(
    proxy_host: str, proxy_port: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, tuple[str, int]]:
    """Выполнить UDP ASSOCIATE. Возвращает (control reader/writer, relay addr)."""
    reader, writer = await asyncio.open_connection(proxy_host, proxy_port)
    writer.write(b"\x05\x01\x00")
    await writer.drain()
    await reader.readexactly(2)

    writer.write(b"\x05\x03\x00\x01" + socket.inet_aton("0.0.0.0") + b"\x00\x00")
    await writer.drain()

    head = await reader.readexactly(4)
    assert head[1] == 0x00, f"UDP ASSOCIATE отклонён, REP={head[1]}"
    atyp = head[3]
    if atyp == 0x01:
        body = await reader.readexactly(6)
        host = socket.inet_ntoa(body[:4])
    elif atyp == 0x04:
        body = await reader.readexactly(18)
        host = socket.inet_ntop(socket.AF_INET6, body[:16])
    else:
        n = (await reader.readexactly(1))[0]
        body = await reader.readexactly(n + 2)
        host = body[:n].decode()
    (port,) = struct.unpack("!H", body[-2:])
    return reader, writer, (host, port)


def pack_socks_udp(host: str, port: int, payload: bytes) -> bytes:
    raw = host.encode()
    return b"\x00\x00\x00\x03" + bytes([len(raw)]) + raw + struct.pack("!H", port) + payload


def unpack_socks_udp(packet: bytes) -> tuple[str, int, bytes]:
    atyp = packet[3]
    off = 4
    if atyp == 0x01:
        host = socket.inet_ntoa(packet[off : off + 4])
        off += 4
    elif atyp == 0x04:
        host = socket.inet_ntop(socket.AF_INET6, packet[off : off + 16])
        off += 16
    else:
        n = packet[off]
        off += 1
        host = packet[off : off + n].decode()
        off += n
    (port,) = struct.unpack("!H", packet[off : off + 2])
    return host, port, packet[off + 2 :]


class TinyHttpServer:
    """HTTP/1.1-сервер на пару строк: отвечает эхом на путь запроса."""

    def __init__(self, host: str = "127.0.0.1") -> None:
        self.host = host
        self._server: asyncio.AbstractServer | None = None
        self.hits: list[str] = []

    @property
    def port(self) -> int:
        assert self._server and self._server.sockets
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> "TinyHttpServer":
        self._server = await asyncio.start_server(self._handle, self.host, 0)
        return self

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5)
            path = request_line.decode("latin-1").split(" ")[1] if request_line else "/"
            self.hits.append(path)
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                if line in (b"\r\n", b"\n", b""):
                    break
            body = f"echo:{path}".encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                + f"Content-Length: {len(body)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionError, IndexError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass


class UdpEchoServer(asyncio.DatagramProtocol):
    """UDP-эхо: возвращает b'echo:' + payload."""

    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self.received: list[bytes] = []

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        self.received.append(data)
        if self.transport:
            self.transport.sendto(b"echo:" + data, addr)

    @property
    def port(self) -> int:
        assert self.transport
        return self.transport.get_extra_info("sockname")[1]


async def start_udp_echo(host: str = "127.0.0.1") -> tuple[UdpEchoServer, asyncio.DatagramTransport]:
    loop = asyncio.get_running_loop()
    proto = UdpEchoServer()
    transport, _ = await loop.create_datagram_endpoint(lambda: proto, local_addr=(host, 0))
    return proto, transport
