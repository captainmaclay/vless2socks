"""SOCKS5-сервер (RFC 1928 / RFC 1929), проксирующий трафик через VLESS."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import struct
import time
from typing import Awaitable, Callable

from .config import AppConfig
from .logging_setup import get_logger
from .protocol import ProtocolError
from .relay import relay
from .transport import TransportError
from .vless import VlessConnection

__all__ = ["Socks5Server"]

log = get_logger("vless2socks.socks5")

SOCKS_VERSION = 0x05
AUTH_VERSION = 0x01

METHOD_NO_AUTH = 0x00
METHOD_USERPASS = 0x02
METHOD_NONE_ACCEPTABLE = 0xFF

CMD_CONNECT = 0x01
CMD_BIND = 0x02
CMD_UDP_ASSOCIATE = 0x03

ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04

REP_SUCCESS = 0x00
REP_GENERAL_FAILURE = 0x01
REP_NOT_ALLOWED = 0x02
REP_NETWORK_UNREACHABLE = 0x03
REP_HOST_UNREACHABLE = 0x04
REP_CONNECTION_REFUSED = 0x05
REP_TTL_EXPIRED = 0x06
REP_CMD_NOT_SUPPORTED = 0x07
REP_ATYP_NOT_SUPPORTED = 0x08

HANDSHAKE_TIMEOUT = 30.0


class Socks5Error(Exception):
    """Ошибка на этапе рукопожатия SOCKS5."""

    def __init__(self, message: str, reply: int = REP_GENERAL_FAILURE) -> None:
        super().__init__(message)
        self.reply = reply


# --------------------------------------------------------------------- utils


def encode_socks_addr(host: str, port: int) -> bytes:
    """Закодировать ``ATYP + ADDR + PORT`` в формате SOCKS5."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            raw = host.encode("ascii")
        except UnicodeEncodeError:
            try:
                raw = host.encode("idna")
            except UnicodeError:
                # Имя из битого запроса клиента — не роняем релей из-за него.
                raw = host.encode("ascii", "replace")
        raw = raw[:255] or b"?"
        return bytes([ATYP_DOMAIN, len(raw)]) + raw + struct.pack("!H", port)
    if ip.version == 4:
        return bytes([ATYP_IPV4]) + ip.packed + struct.pack("!H", port)
    return bytes([ATYP_IPV6]) + ip.packed + struct.pack("!H", port)


def decode_socks_addr(data: bytes, offset: int = 0) -> tuple[str, int, int]:
    """Раскодировать ``ATYP + ADDR + PORT``. Возвращает ``(host, port, offset)``."""
    if offset >= len(data):
        raise Socks5Error("нет типа адреса", REP_ATYP_NOT_SUPPORTED)
    atyp = data[offset]
    offset += 1

    if atyp == ATYP_IPV4:
        if len(data) < offset + 6:
            raise Socks5Error("обрезанный IPv4-адрес")
        host = str(ipaddress.IPv4Address(data[offset : offset + 4]))
        offset += 4
    elif atyp == ATYP_DOMAIN:
        if offset >= len(data):
            raise Socks5Error("нет длины домена")
        length = data[offset]
        offset += 1
        if len(data) < offset + length + 2:
            raise Socks5Error("обрезанный домен")
        host = data[offset : offset + length].decode("ascii", errors="replace")
        offset += length
    elif atyp == ATYP_IPV6:
        if len(data) < offset + 18:
            raise Socks5Error("обрезанный IPv6-адрес")
        host = str(ipaddress.IPv6Address(data[offset : offset + 16]))
        offset += 16
    else:
        raise Socks5Error(f"неизвестный ATYP 0x{atyp:02x}", REP_ATYP_NOT_SUPPORTED)

    (port,) = struct.unpack("!H", data[offset : offset + 2])
    offset += 2
    return host, port, offset


def _reply(code: int, host: str = "0.0.0.0", port: int = 0) -> bytes:
    return bytes([SOCKS_VERSION, code, 0x00]) + encode_socks_addr(host, port)


def _error_to_reply(exc: BaseException) -> int:
    if isinstance(exc, Socks5Error):
        return exc.reply
    if isinstance(exc, ConnectionRefusedError):
        return REP_CONNECTION_REFUSED
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return REP_TTL_EXPIRED
    if isinstance(exc, socket.gaierror):
        return REP_HOST_UNREACHABLE
    if isinstance(exc, TransportError):
        return REP_NETWORK_UNREACHABLE
    return REP_GENERAL_FAILURE


# -------------------------------------------------------------------- server


class Socks5Server:
    """SOCKS5-сервер, который каждую сессию отдаёт в отдельное VLESS-соединение."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._server: asyncio.AbstractServer | None = None
        self._conn_id = 0
        self.active = 0

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> asyncio.AbstractServer:
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.config.listen_host,
            port=self.config.listen_port,
            # На Windows SO_REUSEADDR разрешает привязаться к УЖЕ занятому
            # порту: второй экземпляр молча «украл» бы часть соединений.
            # На POSIX asyncio и так ставит его сам, так что просто не мешаем.
            reuse_address=None if os.name == "nt" else True,
        )
        for sock in self._server.sockets or ():
            log.info("SOCKS5 слушает %s", _fmt_sockname(sock.getsockname()))
        return self._server

    async def serve_forever(self) -> None:
        server = self._server or await self.start()
        async with server:
            await server.serve_forever()

    def close(self) -> None:
        if self._server is not None:
            self._server.close()

    @property
    def port(self) -> int:
        """Фактический порт (полезно при listen_port=0 в тестах)."""
        if not self._server or not self._server.sockets:
            return self.config.listen_port
        return self._server.sockets[0].getsockname()[1]

    # ------------------------------------------------------------- handling

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._conn_id += 1
        cid = self._conn_id
        self.active += 1
        peer = writer.get_extra_info("peername")
        try:
            await asyncio.wait_for(
                self._session(cid, reader, writer), timeout=None
            )
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.debug("[%d] %s: непредвиденная ошибка: %r", cid, peer, exc)
        finally:
            self.active -= 1
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def _session(
        self, cid: int, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await asyncio.wait_for(
                self._negotiate(reader, writer), timeout=HANDSHAKE_TIMEOUT
            )
            cmd, host, port = await asyncio.wait_for(
                self._read_request(reader), timeout=HANDSHAKE_TIMEOUT
            )
        except asyncio.TimeoutError:
            log.debug("[%d] таймаут рукопожатия SOCKS5", cid)
            return
        except Socks5Error as exc:
            log.debug("[%d] отказ в рукопожатии: %s", cid, exc)
            try:
                writer.write(_reply(exc.reply))
                await writer.drain()
            except Exception:  # noqa: BLE001
                pass
            return

        if cmd == CMD_CONNECT:
            await self._do_connect(cid, reader, writer, host, port)
        elif cmd == CMD_UDP_ASSOCIATE and self.config.udp_enabled:
            await self._do_udp_associate(cid, reader, writer)
        else:
            reason = "BIND не поддержан" if cmd == CMD_BIND else "UDP выключен"
            log.info("[%d] отклонено: %s (cmd=0x%02x)", cid, reason, cmd)
            writer.write(_reply(REP_CMD_NOT_SUPPORTED))
            await writer.drain()

    async def _negotiate(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        head = await reader.readexactly(2)
        if head[0] != SOCKS_VERSION:
            raise Socks5Error(
                f"версия {head[0]} не SOCKS5 (клиент, возможно, шлёт HTTP-прокси)"
            )
        methods = set(await reader.readexactly(head[1]))

        wanted = METHOD_USERPASS if self.config.auth_required else METHOD_NO_AUTH
        if wanted not in methods:
            writer.write(bytes([SOCKS_VERSION, METHOD_NONE_ACCEPTABLE]))
            await writer.drain()
            raise Socks5Error("клиент не предложил подходящий метод аутентификации")

        writer.write(bytes([SOCKS_VERSION, wanted]))
        await writer.drain()

        if wanted == METHOD_USERPASS:
            await self._auth_userpass(reader, writer)

    async def _auth_userpass(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        ver = (await reader.readexactly(1))[0]
        if ver != AUTH_VERSION:
            raise Socks5Error(f"неизвестная версия суб-протокола аутентификации: {ver}")
        ulen = (await reader.readexactly(1))[0]
        username = (await reader.readexactly(ulen)).decode("utf-8", "replace")
        plen = (await reader.readexactly(1))[0]
        password = (await reader.readexactly(plen)).decode("utf-8", "replace")

        ok = (username == self.config.username) and (password == self.config.password)
        writer.write(bytes([AUTH_VERSION, 0x00 if ok else 0x01]))
        await writer.drain()
        if not ok:
            raise Socks5Error(f"неверные логин/пароль (user={username!r})")

    async def _read_request(
        self, reader: asyncio.StreamReader
    ) -> tuple[int, str, int]:
        head = await reader.readexactly(4)
        if head[0] != SOCKS_VERSION:
            raise Socks5Error(f"версия {head[0]} не SOCKS5 в запросе")
        cmd = head[1]
        rest = bytearray(head[3:4])

        atyp = head[3]
        if atyp == ATYP_IPV4:
            rest += await reader.readexactly(4 + 2)
        elif atyp == ATYP_IPV6:
            rest += await reader.readexactly(16 + 2)
        elif atyp == ATYP_DOMAIN:
            length_byte = await reader.readexactly(1)
            rest += length_byte
            rest += await reader.readexactly(length_byte[0] + 2)
        else:
            raise Socks5Error(f"неизвестный ATYP 0x{atyp:02x}", REP_ATYP_NOT_SUPPORTED)

        host, port, _ = decode_socks_addr(bytes(rest), 0)
        return cmd, host, port

    # --------------------------------------------------------------- CONNECT

    async def _do_connect(
        self,
        cid: int,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        host: str,
        port: int,
    ) -> None:
        started = time.monotonic()
        try:
            upstream = await VlessConnection.connect_tcp(
                self.config.server,
                host,
                port,
                connect_timeout=self.config.connect_timeout,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("[%d] CONNECT %s:%d — %s", cid, host, port, exc)
            writer.write(_reply(_error_to_reply(exc)))
            await writer.drain()
            return

        bind_host, bind_port = _local_addr(writer)
        writer.write(_reply(REP_SUCCESS, bind_host, bind_port))
        await writer.drain()
        log.info("[%d] CONNECT %s:%d открыт", cid, host, port)

        try:
            stats = await relay(reader, writer, upstream)
        finally:
            await upstream.wait_closed()
        log.info(
            "[%d] CONNECT %s:%d закрыт (%s, %.1f c)",
            cid, host, port, stats, time.monotonic() - started,
        )

    # --------------------------------------------------------- UDP ASSOCIATE

    async def _do_udp_associate(
        self, cid: int, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        loop = asyncio.get_running_loop()
        bind_host, _ = _local_addr(writer)
        if ":" in bind_host:
            bind_host = "::1"

        relay_proto = UdpRelay(self.config, cid)
        transport, _ = await loop.create_datagram_endpoint(
            lambda: relay_proto, local_addr=(bind_host, 0)
        )
        local_host, local_port = transport.get_extra_info("sockname")[:2]

        writer.write(_reply(REP_SUCCESS, local_host, local_port))
        await writer.drain()
        log.info("[%d] UDP ASSOCIATE на %s:%d", cid, local_host, local_port)

        try:
            # Ассоциация живёт, пока жив управляющий TCP-коннект.
            while await reader.read(4096):
                pass
        except (ConnectionError, OSError):
            pass
        finally:
            await relay_proto.shutdown()
            transport.close()
            log.info("[%d] UDP ASSOCIATE закрыт", cid)


class UdpRelay(asyncio.DatagramProtocol):
    """Релей SOCKS5-UDP: на каждую цель поднимается отдельный VLESS-поток."""

    def __init__(self, config: AppConfig, cid: int) -> None:
        self.config = config
        self.cid = cid
        self.transport: asyncio.DatagramTransport | None = None
        self._streams: dict[tuple[str, int], "UdpStream"] = {}
        self._client_addr: tuple[str, int] | None = None
        self._closing = False

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        if self._closing:
            return
        # Первый увиденный отправитель считается клиентом этой ассоциации.
        if self._client_addr is None:
            self._client_addr = addr[:2]
        elif addr[:2] != self._client_addr:
            log.debug("[%d] UDP от чужого адреса %s — отброшено", self.cid, addr)
            return

        if len(data) < 4:
            return
        frag = data[2]
        if frag != 0:
            log.debug("[%d] UDP-фрагменты не поддержаны (FRAG=%d)", self.cid, frag)
            return
        try:
            host, port, offset = decode_socks_addr(data, 3)
        except Socks5Error as exc:
            log.debug("[%d] некорректный UDP-заголовок: %s", self.cid, exc)
            return
        payload = data[offset:]

        key = (host, port)
        stream = self._streams.get(key)
        if stream is None:
            stream = UdpStream(self, host, port)
            self._streams[key] = stream
            stream.start()
        stream.send(payload)

    def deliver(self, host: str, port: int, payload: bytes) -> None:
        """Отправить ответ обратно SOCKS5-клиенту."""
        if self.transport is None or self._client_addr is None:
            return
        packet = b"\x00\x00\x00" + encode_socks_addr(host, port) + payload
        try:
            self.transport.sendto(packet, self._client_addr)
        except OSError:
            pass

    def drop(self, key: tuple[str, int]) -> None:
        self._streams.pop(key, None)

    async def shutdown(self) -> None:
        self._closing = True
        streams = list(self._streams.values())
        self._streams.clear()
        for stream in streams:
            await stream.close()


class UdpStream:
    """Один VLESS-поток под конкретную пару (host, port) для UDP."""

    def __init__(self, relay_proto: UdpRelay, host: str, port: int) -> None:
        self.relay = relay_proto
        self.host = host
        self.port = port
        self.key = (host, port)
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=256)
        self._task: asyncio.Task | None = None
        self.last_seen = time.monotonic()

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._run())

    def send(self, payload: bytes) -> None:
        self.last_seen = time.monotonic()
        try:
            self.queue.put_nowait(payload)
        except asyncio.QueueFull:
            log.debug("UDP-очередь к %s:%d переполнена", self.host, self.port)

    async def _run(self) -> None:
        conn: VlessConnection | None = None
        try:
            conn = await VlessConnection.connect_udp(
                self.relay.config.server,
                self.host,
                self.port,
                connect_timeout=self.relay.config.connect_timeout,
            )
            reader_task = asyncio.ensure_future(self._pump_down(conn))
            try:
                await self._pump_up(conn)
            finally:
                reader_task.cancel()
                await asyncio.gather(reader_task, return_exceptions=True)
        except (TransportError, ProtocolError, OSError) as exc:
            log.debug("UDP %s:%d — %s", self.host, self.port, exc)
        finally:
            if conn is not None:
                await conn.wait_closed()
            self.relay.drop(self.key)

    async def _pump_up(self, conn: VlessConnection) -> None:
        idle = self.relay.config.udp_idle_timeout
        while True:
            try:
                payload = await asyncio.wait_for(self.queue.get(), timeout=idle)
            except asyncio.TimeoutError:
                return
            if payload is None:
                return
            await conn.write_datagram(payload)

    async def _pump_down(self, conn: VlessConnection) -> None:
        while True:
            payload = await conn.read_datagram()
            if payload is None:
                return
            self.relay.deliver(self.host, self.port, payload)

    async def close(self) -> None:
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


# --------------------------------------------------------------------- misc


def _local_addr(writer: asyncio.StreamWriter) -> tuple[str, int]:
    sockname = writer.get_extra_info("sockname")
    if isinstance(sockname, tuple) and len(sockname) >= 2:
        return str(sockname[0]), int(sockname[1])
    return "0.0.0.0", 0


def _fmt_sockname(sockname) -> str:
    if isinstance(sockname, tuple) and len(sockname) >= 2:
        host, port = sockname[0], sockname[1]
        return f"[{host}]:{port}" if ":" in str(host) else f"{host}:{port}"
    return str(sockname)


ClientHandler = Callable[
    [asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]
]
