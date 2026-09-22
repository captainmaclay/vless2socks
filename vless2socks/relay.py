"""Двунаправленная перекачка данных между SOCKS5-клиентом и VLESS-потоком."""

from __future__ import annotations

import asyncio

from .logging_setup import get_logger
from .protocol import ProtocolError
from .vless import VlessConnection

__all__ = ["relay", "RelayStats"]

log = get_logger("vless2socks.relay")

CHUNK = 64 * 1024

#: Сколько ждать первый байт от клиента, чтобы склеить его с заголовком VLESS
#: в одну запись. Если клиент молчит (например, ждёт баннер SMTP/SSH),
#: заголовок уходит сам по себе — иначе туннель никогда не откроется.
FIRST_PAYLOAD_WAIT = 0.05

#: Сколько ждать «хвост» встречного направления после закрытия одной стороны.
TAIL_GRACE_UP = 30.0    # клиент закрылся -> дожидаемся ответа сервера
TAIL_GRACE_DOWN = 0.2   # сервер закрылся -> отдавать больше нечего


class RelayStats:
    __slots__ = ("sent", "received")

    def __init__(self) -> None:
        self.sent = 0
        self.received = 0

    def __str__(self) -> str:
        return f"tx {_human(self.sent)}, rx {_human(self.received)}"


def _human(n: int) -> str:
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TiB"


async def _client_to_upstream(
    reader: asyncio.StreamReader, upstream: VlessConnection, stats: RelayStats
) -> None:
    try:
        try:
            first = await asyncio.wait_for(
                reader.read(CHUNK), timeout=FIRST_PAYLOAD_WAIT
            )
        except asyncio.TimeoutError:
            await upstream.flush_header()
            first = await reader.read(CHUNK)

        data = first
        while True:
            if not data:
                await upstream.flush_header()
                upstream.write_eof()
                return
            stats.sent += len(data)
            await upstream.write(data)
            data = await reader.read(CHUNK)
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        return


async def _upstream_to_client(
    upstream: VlessConnection, writer: asyncio.StreamWriter, stats: RelayStats
) -> None:
    try:
        while True:
            data = await upstream.read(CHUNK)
            if not data:
                try:
                    if writer.can_write_eof():
                        writer.write_eof()
                except Exception:  # noqa: BLE001
                    pass
                return
            stats.received += len(data)
            writer.write(data)
            await writer.drain()
    except ProtocolError as exc:
        log.warning("%s: %s", upstream.target, exc)
        try:
            if writer.can_write_eof():
                writer.write_eof()
        except Exception:  # noqa: BLE001
            pass
        return
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        return


async def relay(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    upstream: VlessConnection,
    *,
    idle_timeout: float | None = None,
) -> RelayStats:
    """Гонять данные в обе стороны, пока одна из сторон не закроется."""
    stats = RelayStats()
    up = asyncio.ensure_future(_client_to_upstream(reader, upstream, stats))
    down = asyncio.ensure_future(_upstream_to_client(upstream, writer, stats))
    tasks = [up, down]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED,
                           timeout=idle_timeout)
        if down.done():
            # Сервер закрыл поток (или его не было вовсе) — отдавать больше
            # нечего, ждать клиента смысла нет.
            grace = TAIL_GRACE_DOWN
        else:
            # Клиент сделал половинное закрытие (обычный HTTP-запрос) —
            # даём ответу дойти полностью.
            grace = TAIL_GRACE_UP
        pending = [t for t in tasks if not t.done()]
        if pending:
            _, still_pending = await asyncio.wait(pending, timeout=grace)
            for task in still_pending:
                task.cancel()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return stats
