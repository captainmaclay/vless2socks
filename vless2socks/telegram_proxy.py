"""
Модуль внутреннего Telegram Proxy и диспетчера ротации для vless2socks.

Функционал:
1. Создание ссылок для Telegram:
   - tg://proxy?server=127.0.0.1&port=9910&secret=dd648b061090960667e2620c7949495503 (MTProto)
   - tg://socks?server=127.0.0.1&port=9910 (SOCKS5)
2. Внутренний диспетчер Telegram Proxy на порту 127.0.0.1:9910:
   - Обслуживает подключения Telegram (SOCKS5 и MTProto)
   - Автоматическая ротация: направляет трафик на активный прокси с флагом TelegramProxy
   - При сбое текущего прокси автоматически переключается (ротируется) на следующий рабочий прокси из пула.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import socket
import struct
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .socks_client import Socks5ClientError, open_via_socks5

logger = logging.getLogger("vless2socks.telegram_proxy")

DEFAULT_TG_HOST = "127.0.0.1"
DEFAULT_TG_PORT = 9910
DEFAULT_TG_SECRET = "dd648b061090960667e2620c7949495503"

# Датацентры Telegram (IP и порт)
TELEGRAM_DCS: Dict[int, Tuple[str, int]] = {
    1: ("149.154.175.50", 443),
    2: ("149.154.167.51", 443),
    3: ("149.154.175.100", 443),
    4: ("149.154.167.91", 443),
    5: ("91.108.56.100", 443),
}


def get_current_tg_port() -> int:
    """Получить текущий порт виртуального сокета из настроек (по умолчанию 9910)."""
    try:
        import settings_manager
        return settings_manager.get_telegram_proxy_port()
    except Exception:
        return DEFAULT_TG_PORT


def build_tg_proxy_url(
    server: str = DEFAULT_TG_HOST,
    port: Optional[int] = None,
    secret: str = DEFAULT_TG_SECRET,
) -> str:
    """Генерация ссылки MTProto прокси для интеграции с Telegram."""
    actual_port = get_current_tg_port() if port is None else int(port)
    return f"tg://proxy?server={server}&port={actual_port}&secret={secret}"


def build_tg_socks_url(
    server: str = DEFAULT_TG_HOST,
    port: Optional[int] = None,
    username: str = "",
    password: str = "",
) -> str:
    """Генерация ссылки SOCKS5 прокси для интеграции с Telegram."""
    actual_port = get_current_tg_port() if port is None else int(port)
    url = f"tg://socks?server={server}&port={actual_port}"
    if username:
        url += f"&user={username}"
    if password:
        url += f"&pass={password}"
    return url


def is_endpoint_reachable(host: str, port: int, timeout: float = 0.6) -> bool:
    """Быстрая проверка доступности сокета TCP/SOCKS5 без вызова ошибок EOF на сервере."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as s:
            try:
                s.settimeout(min(timeout, 0.3))
                s.sendall(b"\x05\x01\x00")
                _ = s.recv(2)
            except Exception:
                pass
            return True
    except Exception:
        return False


class TelegramProxyDispatcher:
    """
    Диспетчер внутреннего Telegram Proxy с пулом прокси и автоматической ротацией.
    Слушает порт get_current_tg_port() и проксирует трафик через здоровые прокси vless2socks.

    A proxy is skipped when its local port is closed, when the GUI's tunnel probe found it
    unhealthy, or after FAIL_THRESHOLD relays in a row that got nothing back from upstream.
    The last case matters with xray: it answers a SOCKS CONNECT with "success" at once even
    when its server is down, so a dead server is only visible as a relay that stays silent.
    """

    FAIL_THRESHOLD = 2
    FAIL_COOLDOWN_S = 60.0

    def __init__(
        self,
        listen_host: str = DEFAULT_TG_HOST,
        listen_port: int = DEFAULT_TG_PORT,
        secret: str = DEFAULT_TG_SECRET,
    ):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.secret = secret

        self._pool: List[Dict[str, Any]] = []
        self._current_index: int = 0
        self._lock = threading.Lock()

        self._server: Optional[asyncio.Server] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._rotation_count: int = 0
        self._last_rotated_name: str = ""
        # Health the GUI measured end to end (tunnel probe), per (host, port)
        self._health: Dict[Tuple[str, int], bool] = {}
        # Consecutive relays through a proxy that got nothing back: (count, last time)
        self._failures: Dict[Tuple[str, int], Tuple[int, float]] = {}

    def is_running(self) -> bool:
        return self._running and is_endpoint_reachable(self.listen_host, self.listen_port, timeout=0.3)

    def set_pool(self, instances_or_configs: List[Any]):
        """Обновить пул прокси с флагом TelegramProxy."""
        new_pool = []
        for item in instances_or_configs:
            if hasattr(item, "cfg"):
                cfg = item.cfg
                h, p = item.get_listen()
                # У ProxyInstance имя отдаёт get_display_name(); get_name там нет.
                name_getter = getattr(item, "get_display_name", None) or getattr(item, "get_name", None)
                name = name_getter() if callable(name_getter) else cfg.get("name", "")
                name = name or f"Proxy-{p}"
                is_tp = item.is_telegram_proxy()
                if is_tp and getattr(item, "running", None) is not None:
                    # a running proxy whose tunnel probe has not passed yet is not counted as down
                    healthy = bool(item.running) and getattr(item, "healthy", True) is not False
                    self._health[(h, int(p))] = healthy if item.running else False
            elif isinstance(item, dict):
                cfg = item
                listen = cfg.get("listen", "127.0.0.1:1081")
                h, _, p_str = listen.rpartition(":")
                h = h or "127.0.0.1"
                try:
                    p = int(p_str or "1081")
                except ValueError:
                    p = 1081
                name = cfg.get("name", f"Proxy-{p}")
                is_tp = bool(cfg.get("telegram_proxy", cfg.get("TelegramProxy", False)))
            else:
                continue

            if is_tp:
                new_pool.append({
                    "host": h,
                    "port": int(p),
                    "name": name,
                    "username": cfg.get("username", ""),
                    "password": cfg.get("password", ""),
                })

        with self._lock:
            self._pool = new_pool
            if self._current_index >= len(self._pool):
                self._current_index = 0
        logger.info("TelegramProxy pool updated: %d proxies available", len(new_pool))

    def get_pool(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._pool)

    @staticmethod
    def _key(proxy: Dict[str, Any]) -> Tuple[str, int]:
        return (proxy["host"], int(proxy["port"]))

    def set_health(self, host: str, port: int, healthy: bool) -> None:
        """End-to-end health from the GUI (tunnel probe / process state)."""
        with self._lock:
            self._health[(host, int(port))] = bool(healthy)
            if healthy:
                self._failures.pop((host, int(port)), None)

    def _usable(self, proxy: Dict[str, Any]) -> bool:
        key = self._key(proxy)
        if self._health.get(key) is False:
            return False
        count, when = self._failures.get(key, (0, 0.0))
        return not (count >= self.FAIL_THRESHOLD and time.monotonic() - when < self.FAIL_COOLDOWN_S)

    def _switch_to(self, idx: int, reason: str) -> Dict[str, Any]:
        cand = self._pool[idx]
        if idx != self._current_index:
            self._current_index = idx
            self._rotation_count += 1
            self._last_rotated_name = cand.get("name", "")
            logger.info("Telegram Proxy: %s -> '%s' (%s:%d)", reason, cand.get("name"), cand["host"], cand["port"])
        return cand

    def get_active_proxy(self) -> Optional[Dict[str, Any]]:
        """Текущий рабочий прокси; при сбое — ротация на следующий отмеченный TelegramProxy.

        First choice: healthy (by the GUI and by recent relays) and reachable, starting from
        the current one in pool order. If none is, any reachable one, so Telegram still gets
        a chance through a proxy the probe may have misjudged."""
        with self._lock:
            if not self._pool:
                return None
            total = len(self._pool)
            order = [(self._current_index + step) % total for step in range(total)]
            reachable: Dict[int, bool] = {}

            def is_up(idx: int) -> bool:
                if idx not in reachable:
                    cand = self._pool[idx]
                    reachable[idx] = is_endpoint_reachable(cand["host"], cand["port"])
                return reachable[idx]

            for idx in order:
                if self._usable(self._pool[idx]) and is_up(idx):
                    return self._switch_to(idx, "РОТАЦИЯ (текущий недоступен)")
            for idx in order:
                if is_up(idx):
                    return self._switch_to(idx, "РОТАЦИЯ (нет здоровых, берём доступный)")
            return self._pool[self._current_index]

    def report_result(self, proxy: Optional[Dict[str, Any]], ok: bool) -> None:
        """Outcome of one relay. After FAIL_THRESHOLD silent relays in a row the proxy
        is skipped for FAIL_COOLDOWN_S and the dispatcher moves on to the next one."""
        if not proxy:
            return
        key = self._key(proxy)
        with self._lock:
            if ok:
                self._failures.pop(key, None)
                return
            count = self._failures.get(key, (0, 0.0))[0] + 1
            self._failures[key] = (count, time.monotonic())
            if count >= self.FAIL_THRESHOLD:
                logger.warning("Telegram Proxy: '%s' %d раз(а) подряд без ответа от сервера — исключён на %ds",
                               proxy.get("name"), count, int(self.FAIL_COOLDOWN_S))

    def rotate_to_next(self) -> Optional[Dict[str, Any]]:
        """Принудительно переключиться на следующий рабочий прокси."""
        with self._lock:
            if not self._pool:
                return None
            total = len(self._pool)
            if total <= 1:
                return self._pool[0]
            order = [(self._current_index + step) % total for step in range(1, total + 1)]
            for usable_only in (True, False):
                for idx in order:
                    cand = self._pool[idx]
                    if (not usable_only or self._usable(cand)) and is_endpoint_reachable(cand["host"], cand["port"]):
                        return self._switch_to(idx, "ручная ротация")
            return self._pool[self._current_index]

    async def _in_thread(self, fn, *args):
        """Run a blocking pool operation (it connects to local ports) off the event loop."""
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    async def _open_upstream(self, dst_host: str, dst_port: int):
        """Connect through the active proxy; on failure go through the rest of the pool.
        Returns (proxy, reader, writer) or (None, None, None)."""
        tried = set()
        for _ in range(max(1, len(self.get_pool()))):
            active = await self._in_thread(self.get_active_proxy)
            if not active or self._key(active) in tried:
                break
            tried.add(self._key(active))
            try:
                up_reader, up_writer = await open_via_socks5(
                    active["host"], active["port"], dst_host, dst_port,
                    username=active.get("username", ""), password=active.get("password", ""),
                    timeout=12.0,
                )
                return active, up_reader, up_writer
            except Exception as e:
                logger.warning("Сбой подключения через '%s': %s. Ротация...", active.get("name"), e)
                self.report_result(active, False)
                self.report_result(active, False)    # a refused CONNECT is a clear failure
                await self._in_thread(self.rotate_to_next)
        return None, None, None

    def start(self):
        """Запустить сервер диспетчера в фоновом потоке."""
        # start() может быть вызван из нескольких потоков сразу: второй слушатель на том же порту недопустим
        with self._lock:
            if self._running or (self._thread is not None and self._thread.is_alive()):
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_server_thread,
                name="TelegramProxyDispatcher-Thread",
                daemon=True,
            )
            self._thread.start()

        # Дожидаемся старта порта
        for _ in range(25):
            time.sleep(0.1)
            if self.is_running():
                break

    def stop(self):
        """Остановить сервер диспетчера."""
        self._running = False
        self._stop_event.set()
        if self._loop and self._server:
            try:
                self._loop.call_soon_threadsafe(self._server.close)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None
        self._loop = None
        self._server = None

    def set_listen_port(self, port: int):
        """Динамически обновить порт виртуального сокета (с перезапуском, если сервер запущен)."""
        new_p = int(port)
        if self.listen_port == new_p:
            return
        was_running = self._running
        if was_running:
            self.stop()
        self.listen_port = new_p
        if was_running:
            self.start()

    def _run_server_thread(self):
        """Поток выполнения цикла событий asyncio."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _main():
            try:
                self._server = await asyncio.start_server(
                    self._handle_client,
                    self.listen_host,
                    self.listen_port,
                    reuse_address=True,
                )
                self._running = True
                logger.info(
                    "Telegram Proxy Dispatcher запущен на %s:%d",
                    self.listen_host,
                    self.listen_port,
                )
                async with self._server:
                    while not self._stop_event.is_set():
                        await asyncio.sleep(0.5)
            except Exception as e:
                logger.error("Ошибка Telegram Proxy Dispatcher: %s", e)
            finally:
                self._running = False

        loop = self._loop
        try:
            loop.run_until_complete(_main())
        except Exception:
            pass
        finally:
            self._running = False
            # every port change restarts the listener: without this each restart left
            # an unclosed event loop behind
            try:
                loop.close()
            except Exception:
                pass

    async def _handle_client(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ):
        """Обработка входящего соединения Telegram (определение SOCKS5 или MTProto)."""
        try:
            first_byte = await asyncio.wait_for(client_reader.read(1), timeout=10.0)
            if not first_byte:
                client_writer.close()
                return

            if first_byte == b"\x05":
                # SOCKS5 соединение от Telegram Desktop
                await self._handle_socks5(first_byte, client_reader, client_writer)
            else:
                # MTProto соединение от Telegram
                await self._handle_mtproto(first_byte, client_reader, client_writer)
        except Exception as e:
            logger.debug("Клиент отключился: %s", e)
            try:
                client_writer.close()
            except Exception:
                pass

    async def _handle_socks5(
        self,
        first_byte: bytes,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ):
        """Обработка SOCKS5 handshake и проксирование через активный апстрим."""
        # 1. Читаем оставшуюся часть приветствия SOCKS5
        nmethods_b = await client_reader.readexactly(1)
        nmethods = nmethods_b[0]
        methods = await client_reader.readexactly(nmethods)

        # Отвечаем: метод без аутентификации (0x00)
        client_writer.write(b"\x05\x00")
        await client_writer.drain()

        # 2. Читаем запрос на подключение (CONNECT)
        req_header = await client_reader.readexactly(4)
        ver, cmd, _, atyp = req_header
        if ver != 5 or cmd != 1:  # 1 = CONNECT
            client_writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
            await client_writer.drain()
            client_writer.close()
            return

        # Разбор целевого адреса
        if atyp == 0x01:  # IPv4
            addr_bytes = await client_reader.readexactly(4)
            dst_host = socket.inet_ntoa(addr_bytes)
        elif atyp == 0x03:  # Domain
            dlen = (await client_reader.readexactly(1))[0]
            dst_host = (await client_reader.readexactly(dlen)).decode("utf-8", errors="replace")
        elif atyp == 0x04:  # IPv6
            addr_bytes = await client_reader.readexactly(16)
            dst_host = socket.inet_ntop(socket.AF_INET6, addr_bytes)
        else:
            client_writer.close()
            return

        port_bytes = await client_reader.readexactly(2)
        dst_port = struct.unpack(">H", port_bytes)[0]

        # 3. Подключаемся через активный прокси пула (с ротацией по всему пулу)
        active, up_reader, up_writer = await self._open_upstream(dst_host, dst_port)
        if not active:
            client_writer.write(b"\x05\x04\x00\x01\x00\x00\x00\x00\x00\x00")
            await client_writer.drain()
            client_writer.close()
            return

        # Отправляем подтверждение успеха клиенту
        client_writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        await client_writer.drain()

        # Двунаправленный обмен; молчащий апстрим = мёртвый сервер за живым локальным портом
        sent, received = await self._pipe_streams(client_reader, client_writer, up_reader, up_writer)
        if sent:
            self.report_result(active, received > 0)

    async def _handle_mtproto(
        self,
        first_byte: bytes,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ):
        """Обработка MTProto приветствия и маршрутизация на Telegram DC с корректной перешифровкой."""
        rest_greeting = await client_reader.readexactly(63)
        greeting = first_byte + rest_greeting

        # Извлекаем секрет без dd префикса
        sec_hex = self.secret
        if sec_hex.startswith("dd"):
            sec_hex = sec_hex[2:]
        try:
            sec_bytes = bytes.fromhex(sec_hex)
        except Exception:
            sec_bytes = b"\x00" * 16

        # 1. Расшифровка заголовка клиента:
        # Client->Server key строится из прямого greeting[8:40] + sec_bytes
        # Server->Client key строится из реверсивного greeting[8:40][::-1] + sec_bytes
        try:
            client_key = hashlib.sha256(greeting[8:40] + sec_bytes).digest()
            client_iv = greeting[40:56]
            server_key = hashlib.sha256(greeting[8:40][::-1] + sec_bytes).digest()
            server_iv = greeting[40:56][::-1]

            client_dec = Cipher(algorithms.AES(client_key), modes.CTR(client_iv)).decryptor()
            client_enc = Cipher(algorithms.AES(server_key), modes.CTR(server_iv)).encryptor()

            # Смещаем счетчик CTR на первые 56 байт
            _ = client_dec.update(greeting[:56])
            decrypted_tail = client_dec.update(greeting[56:64])

            raw_dc = struct.unpack("<h", decrypted_tail[4:6])[0]
            dc_id = abs(raw_dc)
        except Exception as e:
            logger.debug("Ошибка разбора MTProto заголовка клиента: %s", e)
            client_writer.close()
            return

        if dc_id not in TELEGRAM_DCS:
            dc_id = 2
        dc_host, dc_port = TELEGRAM_DCS[dc_id]

        # 2. Подключение к датацентру через рабочий upstream SOCKS5 (с ротацией по всему пулу)
        active, up_reader, up_writer = await self._open_upstream(dc_host, dc_port)
        if not active:
            client_writer.close()
            return

        # 3. Формируем легитимный заголовок MTProto для датацентра Telegram (БЕЗ секрета!)
        dc_header = bytearray(os.urandom(64))
        while dc_header[0] in (0xef, 0x48) or bytes(dc_header[:4]) in (b"POST", b"GET ", b"HEAD"):
            dc_header = bytearray(os.urandom(64))

        dc_key = hashlib.sha256(dc_header[8:40]).digest()
        dc_iv = bytes(dc_header[40:56])
        dc_server_key = hashlib.sha256(dc_header[8:40][::-1]).digest()
        dc_server_iv = bytes(dc_header[40:56][::-1])

        dc_enc = Cipher(algorithms.AES(dc_key), modes.CTR(dc_iv)).encryptor()
        dc_dec = Cipher(algorithms.AES(dc_server_key), modes.CTR(dc_server_iv)).decryptor()

        _ = dc_enc.update(bytes(dc_header[:56]))
        dc_header[56:64] = dc_enc.update(decrypted_tail)

        # Отправляем новый заголовок в DC
        up_writer.write(bytes(dc_header))
        await up_writer.drain()

        # 4. Двунаправленный обмен с непрерывной перешифровкой (re-encryption):
        _sent, received = await self._pipe_encrypted_streams(
            client_reader, client_writer, client_dec, client_enc,
            up_reader, up_writer, dc_dec, dc_enc
        )
        # the DC header was sent, so a working path always brings something back
        self.report_result(active, received > 0)

    async def _pipe_encrypted_streams(
        self,
        client_r: asyncio.StreamReader,
        client_w: asyncio.StreamWriter,
        client_dec: Any,
        client_enc: Any,
        dc_r: asyncio.StreamReader,
        dc_w: asyncio.StreamWriter,
        dc_dec: Any,
        dc_enc: Any,
    ):
        """Двунаправленная перекачка с AES-CTR перешифровкой между клиентом и датацентром.
        Returns (bytes sent to the DC, bytes received from it)."""
        counts = [0, 0]

        async def _client_to_dc():
            try:
                while True:
                    data = await client_r.read(32768)
                    if not data:
                        break
                    counts[0] += len(data)
                    plain = client_dec.update(data)
                    for_dc = dc_enc.update(plain)
                    dc_w.write(for_dc)
                    await dc_w.drain()
            except Exception:
                pass
            finally:
                try:
                    dc_w.close()
                except Exception:
                    pass

        async def _dc_to_client():
            try:
                while True:
                    data = await dc_r.read(32768)
                    if not data:
                        break
                    counts[1] += len(data)
                    plain = dc_dec.update(data)
                    for_client = client_enc.update(plain)
                    client_w.write(for_client)
                    await client_w.drain()
            except Exception:
                pass
            finally:
                try:
                    client_w.close()
                except Exception:
                    pass

        t1 = asyncio.create_task(_client_to_dc())
        t2 = asyncio.create_task(_dc_to_client())
        await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
        return counts[0], counts[1]

    async def _pipe_streams(
        self,
        r1: asyncio.StreamReader,
        w1: asyncio.StreamWriter,
        r2: asyncio.StreamReader,
        w2: asyncio.StreamWriter,
    ):
        """Двунаправленная перекачка байт между клиентом и апстримом.
        Returns (bytes sent upstream, bytes received from upstream)."""
        counts = [0, 0]

        async def _forward(src, dst, slot):
            try:
                while True:
                    data = await src.read(32768)
                    if not data:
                        break
                    counts[slot] += len(data)
                    dst.write(data)
                    await dst.drain()
            except Exception:
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        t1 = asyncio.create_task(_forward(r1, w2, 0))
        t2 = asyncio.create_task(_forward(r2, w1, 1))
        await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
        return counts[0], counts[1]


# ── Глобальный экземпляр диспетчера ─────────────────────────────────

_DISPATCHER_LOCK = threading.Lock()
_GLOBAL_DISPATCHER: Optional[TelegramProxyDispatcher] = None


def get_tg_dispatcher() -> TelegramProxyDispatcher:
    global _GLOBAL_DISPATCHER
    with _DISPATCHER_LOCK:
        if _GLOBAL_DISPATCHER is None:
            p = get_current_tg_port()
            _GLOBAL_DISPATCHER = TelegramProxyDispatcher(listen_port=p)
        return _GLOBAL_DISPATCHER


def start_tg_dispatcher(pool: Optional[List[Any]] = None) -> bool:
    disp = get_tg_dispatcher()
    if pool is not None:
        disp.set_pool(pool)
    disp.start()
    return disp.is_running()


def stop_tg_dispatcher() -> bool:
    disp = get_tg_dispatcher()
    disp.stop()
    return not disp.is_running()


def is_tg_dispatcher_running() -> bool:
    disp = get_tg_dispatcher()
    return disp.is_running()
