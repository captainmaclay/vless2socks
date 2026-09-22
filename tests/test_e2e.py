"""Сквозные тесты: SOCKS5-клиент -> vless2socks -> тестовый VLESS-сервер -> цель."""

from __future__ import annotations

import asyncio
import socket
import unittest
import uuid as uuid_mod

from tests.fake_vless_server import FakeVlessServer
from tests.helpers import (
    TinyHttpServer,
    pack_socks_udp,
    socks5_connect,
    socks5_udp_associate,
    start_udp_echo,
    unpack_socks_udp,
)
from vless2socks.config import AppConfig
from vless2socks.socks5 import REP_SUCCESS, Socks5Server
from vless2socks.url import parse_vless_url

USER_ID = str(uuid_mod.uuid4())


def make_config(vless_port: int, **overrides) -> AppConfig:
    server = parse_vless_url(f"vless://{USER_ID}@127.0.0.1:{vless_port}?type=tcp")
    cfg = AppConfig(server=server, listen_host="127.0.0.1", listen_port=0)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


class E2ETestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.http = await TinyHttpServer().start()
        self.vless = await FakeVlessServer(USER_ID).start()
        self.proxy: Socks5Server | None = None

    async def asyncTearDown(self) -> None:
        if self.proxy:
            self.proxy.close()
        await self.vless.stop()
        await self.http.stop()

    async def start_proxy(self, **overrides) -> Socks5Server:
        self.proxy = Socks5Server(make_config(self.vless.port, **overrides))
        await self.proxy.start()
        return self.proxy

    # ------------------------------------------------------------- CONNECT

    async def test_connect_http_roundtrip(self):
        proxy = await self.start_proxy()
        reader, writer, rep = await socks5_connect(
            "127.0.0.1", proxy.port, "127.0.0.1", self.http.port
        )
        self.assertEqual(rep, REP_SUCCESS)

        writer.write(
            b"GET /hello HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n"
        )
        await writer.drain()
        body = await asyncio.wait_for(reader.read(-1), timeout=10)
        writer.close()

        self.assertIn(b"200 OK", body)
        self.assertIn(b"echo:/hello", body)
        self.assertEqual(self.http.hits, ["/hello"])
        self.assertEqual(self.vless.sessions[0][1:], ("127.0.0.1", self.http.port))

    async def test_connect_passes_large_payload_both_ways(self):
        """Проверяем, что релей не теряет данные на объёме больше одного чанка."""
        blob = bytes(range(256)) * 2048  # 512 KiB

        async def echo_handler(reader, writer):
            try:
                data = await reader.readexactly(len(blob))
                writer.write(data)
                await writer.drain()
                writer.write_eof()
            finally:
                writer.close()

        echo = await asyncio.start_server(echo_handler, "127.0.0.1", 0)
        echo_port = echo.sockets[0].getsockname()[1]

        proxy = await self.start_proxy()
        reader, writer, rep = await socks5_connect(
            "127.0.0.1", proxy.port, "127.0.0.1", echo_port
        )
        self.assertEqual(rep, REP_SUCCESS)

        writer.write(blob)
        await writer.drain()
        got = await asyncio.wait_for(reader.readexactly(len(blob)), timeout=30)
        writer.close()
        echo.close()
        await echo.wait_closed()

        self.assertEqual(got, blob)

    async def test_connect_to_dead_port_closes_stream(self):
        """VLESS сообщает об ошибке цели только обрывом потока.

        Как и xray, мы отвечаем клиенту SOCKS5 «успех» сразу (иначе туннель
        не открыть: заголовок ответа сервер шлёт вместе с первыми данными).
        Проверяем, что недоступная цель приводит к быстрому EOF, а не зависанию.
        """
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        dead_port = sock.getsockname()[1]
        sock.close()

        proxy = await self.start_proxy()
        reader, writer, rep = await socks5_connect(
            "127.0.0.1", proxy.port, "127.0.0.1", dead_port
        )
        self.assertEqual(rep, REP_SUCCESS)
        writer.write(b"GET / HTTP/1.1\r\nHost: t\r\n\r\n")
        await writer.drain()
        data = await asyncio.wait_for(reader.read(100), timeout=10)
        writer.close()
        self.assertEqual(data, b"")

    async def test_wrong_uuid_is_reported_as_failure(self):
        bad = parse_vless_url(
            f"vless://{uuid_mod.uuid4()}@127.0.0.1:{self.vless.port}?type=tcp"
        )
        cfg = AppConfig(server=bad, listen_host="127.0.0.1", listen_port=0)
        self.proxy = Socks5Server(cfg)
        await self.proxy.start()

        reader, writer, rep = await socks5_connect(
            "127.0.0.1", self.proxy.port, "127.0.0.1", self.http.port
        )
        # Сервер молча рвёт соединение — клиент должен получить пустой поток,
        # а не зависнуть.
        data = await asyncio.wait_for(reader.read(100), timeout=10)
        writer.close()
        self.assertEqual(data, b"")
        self.assertEqual(self.http.hits, [])

    # ---------------------------------------------------------------- auth

    async def test_auth_accepts_correct_credentials(self):
        proxy = await self.start_proxy(username="alice", password="s3cret")
        reader, writer, rep = await socks5_connect(
            "127.0.0.1", proxy.port, "127.0.0.1", self.http.port,
            username="alice", password="s3cret",
        )
        self.assertEqual(rep, REP_SUCCESS)
        writer.close()

    async def test_auth_rejects_wrong_password(self):
        proxy = await self.start_proxy(username="alice", password="s3cret")
        with self.assertRaises(PermissionError):
            await socks5_connect(
                "127.0.0.1", proxy.port, "127.0.0.1", self.http.port,
                username="alice", password="wrong",
            )

    async def test_auth_rejects_anonymous_client(self):
        proxy = await self.start_proxy(username="alice", password="s3cret")
        with self.assertRaises(PermissionError):
            await socks5_connect(
                "127.0.0.1", proxy.port, "127.0.0.1", self.http.port
            )

    # ----------------------------------------------------------------- UDP

    async def test_udp_associate_roundtrip(self):
        echo, echo_transport = await start_udp_echo()
        proxy = await self.start_proxy()

        ctrl_r, ctrl_w, relay_addr = await socks5_udp_associate(
            "127.0.0.1", proxy.port
        )

        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        sock.bind(("127.0.0.1", 0))
        try:
            packet = pack_socks_udp("127.0.0.1", echo.port, b"ping")
            await loop.sock_sendto(sock, packet, relay_addr)

            answer = await asyncio.wait_for(loop.sock_recv(sock, 65535), timeout=10)
            host, port, payload = unpack_socks_udp(answer)
            self.assertEqual(payload, b"echo:ping")
            self.assertEqual(port, echo.port)
            self.assertEqual(echo.received, [b"ping"])
        finally:
            sock.close()
            ctrl_w.close()
            echo_transport.close()

    async def test_udp_disabled_is_rejected(self):
        proxy = await self.start_proxy(udp_enabled=False)
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        await reader.readexactly(2)
        writer.write(b"\x05\x03\x00\x01" + socket.inet_aton("0.0.0.0") + b"\x00\x00")
        await writer.drain()
        head = await reader.readexactly(4)
        writer.close()
        self.assertEqual(head[1], 0x07)  # command not supported

    # ------------------------------------------------------------ протокол

    async def test_bind_command_is_rejected(self):
        proxy = await self.start_proxy()
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        await reader.readexactly(2)
        writer.write(b"\x05\x02\x00\x01" + socket.inet_aton("127.0.0.1") + b"\x00\x50")
        await writer.drain()
        head = await reader.readexactly(4)
        writer.close()
        self.assertEqual(head[1], 0x07)

    async def test_non_socks5_greeting_is_dropped(self):
        proxy = await self.start_proxy()
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(b"GET / HTTP/1.1\r\n\r\n")
        await writer.drain()
        data = await asyncio.wait_for(reader.read(100), timeout=10)
        writer.close()
        # Либо пусто, либо отказ SOCKS5 — но не зависание и не краш сервера.
        self.assertTrue(data == b"" or data[0] == 0x05)

    async def test_concurrent_connections(self):
        proxy = await self.start_proxy()

        async def one(i: int) -> bytes:
            reader, writer, rep = await socks5_connect(
                "127.0.0.1", proxy.port, "127.0.0.1", self.http.port
            )
            assert rep == REP_SUCCESS
            writer.write(
                f"GET /n{i} HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
            body = await asyncio.wait_for(reader.read(-1), timeout=15)
            writer.close()
            return body

        results = await asyncio.gather(*(one(i) for i in range(12)))
        for i, body in enumerate(results):
            self.assertIn(f"echo:/n{i}".encode(), body)
        self.assertEqual(len(self.http.hits), 12)


if __name__ == "__main__":
    unittest.main()
