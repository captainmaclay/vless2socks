"""Запуск и надзор за процессом xray-core.

Главная задача — превратить «xray молча упал» в внятное сообщение: мы копим
его вывод, и если процесс умирает на старте, показываем последние строки
вместо пустого кода возврата.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..logging_setup import get_logger
from ..noconsole import no_window_kwargs
from ..paths import APP_DIR
from .binary import find_xray
from .config_builder import build_xray_config, redact_config

__all__ = ["XrayProcess", "XrayStartupError", "write_config"]

log = get_logger("vless2socks.xray")

#: Корень приложения — туда кладутся runtime/ и bin/, независимо от текущей папки.
#: В собранном .exe это каталог рядом с .exe, а не папка распаковки.
PROJECT_ROOT = APP_DIR

#: Сколько ждём, пока xray откроет SOCKS5-порт.
READY_TIMEOUT = 15.0
#: Сколько строк вывода храним для отчёта об ошибке.
LOG_TAIL = 40
#: Паузы перед перезапусками (последняя повторяется).
RESTART_BACKOFF = (1.0, 2.0, 5.0, 10.0, 30.0)


class XrayStartupError(RuntimeError):
    """xray не поднялся. В сообщении — его собственный вывод."""


def write_config(
    config: AppConfig,
    directory: str | os.PathLike[str],
    *,
    legacy_vnext: bool = False,
) -> Path:
    """Записать конфиг xray в файл и вернуть путь.

    Файл содержит UUID, поэтому на POSIX права режутся до 0600.
    """
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "xray-config.json"

    data = build_xray_config(config, legacy_vnext=legacy_vnext)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            path.chmod(0o600)
    return path


class XrayProcess:
    """Процесс xray под присмотром: старт, логи, ожидание готовности, рестарт."""

    def __init__(
        self,
        config: AppConfig,
        *,
        xray_path: str | os.PathLike[str] | None = None,
        runtime_dir: str | os.PathLike[str] | None = None,
        restart: bool = True,
        legacy_vnext: bool = False,
    ) -> None:
        self.config = config
        self.xray_path = find_xray(xray_path)
        # Не Path.cwd(): запуск из другой папки не должен разбрасывать
        # конфиги с UUID где попало. Держим их рядом с проектом.
        self.runtime_dir = Path(runtime_dir or PROJECT_ROOT / "runtime")
        self.restart = restart
        #: Какую форму outbound писать. Переключается сама, если xray откажется.
        self.legacy_vnext = legacy_vnext
        self.config_path: Path | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._log_tail: deque[str] = deque(maxlen=LOG_TAIL)
        self._reader_task: asyncio.Task | None = None
        self._stopping = False
        self.restarts = 0

    # ------------------------------------------------------------------ old

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def log_tail(self) -> list[str]:
        return list(self._log_tail)

    # ---------------------------------------------------------------- старт

    async def start(self) -> None:
        """Запустить xray и дождаться, пока он откроет SOCKS5-порт."""
        # Если порт уже кем-то занят, ожидание готовности увидит чужой сокет
        # и радостно отрапортует об успехе. Проверяем заранее, чтобы не
        # получить «работает», а на деле трафик в чужой прокси.
        host, port = self.config.listen_host, self.config.listen_port
        if port and await _port_accepts(host, port):
            raise XrayStartupError(
                f"порт {host}:{port} уже занят другим процессом.\n"
                f"Это может быть второй экземпляр программы или другой "
                f"VPN-клиент. Освободите порт или выберите другой: "
                f"-l 127.0.0.1:1081"
            )

        try:
            await self._launch(legacy_vnext=self.legacy_vnext)
        except XrayStartupError as exc:
            if self.legacy_vnext or not _looks_like_config_rejection(exc):
                raise
            # Форма outbound у xray менялась: PR #5101 убрал вложенность
            # vnext/users. Какой бинарник у пользователя — заранее не узнать,
            # поэтому при отказе читать конфиг молча пробуем вторую форму.
            log.warning(
                "xray не принял конфиг в современной форме — пробую старую "
                "(vnext). Похоже, у вас xray до версии 26."
            )
            self.legacy_vnext = True
            await self._launch(legacy_vnext=True)

    async def _launch(self, *, legacy_vnext: bool) -> None:
        self.config_path = write_config(
            self.config, self.runtime_dir, legacy_vnext=legacy_vnext
        )
        log.debug("конфиг xray: %s", self.config_path)
        log.debug(
            "конфиг (без секретов): %s",
            json.dumps(
                redact_config(
                    build_xray_config(self.config, legacy_vnext=legacy_vnext)
                ),
                ensure_ascii=False,
            ),
        )
        self._log_tail.clear()
        await self._spawn()
        await self._wait_ready()

    async def _spawn(self) -> None:
        assert self.config_path is not None
        try:
            self._proc = await asyncio.create_subprocess_exec(
                str(self.xray_path),
                "run",
                "-c",
                str(self.config_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=str(self.xray_path.parent),
                # Без этого xray на Windows поднимает собственное окно консоли
                # и держит его всё время работы прокси.
                **no_window_kwargs(),
            )
        except OSError as exc:
            raise XrayStartupError(
                f"не удалось запустить {self.xray_path}: {exc}"
            ) from exc

        self._reader_task = asyncio.ensure_future(self._pump_logs())

    async def _pump_logs(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    return
                line = raw.decode("utf-8", "replace").rstrip()
                if not line:
                    continue
                self._log_tail.append(line)
                log.info("xray: %s", line)
        except (asyncio.CancelledError, ConnectionError):
            return

    async def _wait_ready(self) -> None:
        """Ждать, пока порт начнёт принимать соединения, или пока xray умрёт."""
        deadline = time.monotonic() + READY_TIMEOUT
        host, port = self.config.listen_host, self.config.listen_port

        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.returncode is not None:
                raise XrayStartupError(self._death_report(self._proc.returncode))
            if await _port_accepts(host, port):
                log.info(
                    "xray запущен (pid %s), SOCKS5 слушает %s:%d",
                    self.pid, host, port,
                )
                return
            await asyncio.sleep(0.2)

        # Важно: НЕ self.stop() — он выставляет _stopping, и надзор после
        # неудачного перезапуска замолчал бы навсегда. Убиваем только процесс.
        await self._kill_process()
        raise XrayStartupError(
            f"xray запустился, но за {READY_TIMEOUT:.0f} с не открыл "
            f"{host}:{port}.\n" + self._log_report()
        )

    def _death_report(self, code: int) -> str:
        return (
            f"xray завершился с кодом {code} сразу после запуска.\n"
            + self._log_report()
            + f"\nКонфиг лежит здесь: {self.config_path}"
        )

    def _log_report(self) -> str:
        if not self._log_tail:
            return "Он ничего не написал в лог — проверьте, что файл не повреждён."
        lines = "\n".join(f"  {line}" for line in self._log_tail)
        return f"Последние строки его лога:\n{lines}"

    # -------------------------------------------------------------- надзор

    async def supervise(self) -> None:
        """Держать xray живым, пока не позовут :meth:`stop`."""
        while not self._stopping:
            assert self._proc is not None
            code = await self._proc.wait()
            if self._stopping:
                return

            if not self.restart:
                log.error("xray завершился с кодом %s, перезапуск отключён", code)
                return

            delay = RESTART_BACKOFF[min(self.restarts, len(RESTART_BACKOFF) - 1)]
            self.restarts += 1
            log.error(
                "xray завершился с кодом %s. %s Перезапуск через %.1f с (попытка %d)",
                code, self._log_report(), delay, self.restarts,
            )
            await asyncio.sleep(delay)
            if self._stopping:
                return
            try:
                await self._spawn()
                await self._wait_ready()
                log.info("xray перезапущен")
            except XrayStartupError as exc:
                log.error("перезапуск не удался: %s", exc)

    # --------------------------------------------------------------- стоп

    async def _kill_process(self) -> None:
        """Прибить процесс, не объявляя всю работу законченной."""
        proc = self._proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()
        if self._reader_task is not None:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
            self._reader_task = None

    async def stop(self) -> None:
        self._stopping = True
        await self._kill_process()

    async def __aenter__(self) -> "XrayProcess":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()


#: Слова, по которым видно, что xray споткнулся именно о ФОРМУ outbound.
#: Намеренно узкий список: при «invalid user id» переписывать форму незачем —
#: пользователь должен увидеть настоящую причину, а не вторую ошибку подряд.
_CONFIG_REJECTION_MARKERS = (
    "vnext",
    "unknown field",
    "unmarshal",
    "json: ",
    "not allowed",
    "no valid outbound",
)


def _looks_like_config_rejection(exc: Exception) -> bool:
    text = str(exc).lower()
    if "уже занят" in text or "не открыл" in text:
        return False
    return any(marker in text for marker in _CONFIG_REJECTION_MARKERS)


async def _port_accepts(host: str, port: int) -> bool:
    """Проверить, принимает ли кто-то соединения на host:port."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=1.0
        )
    except (OSError, asyncio.TimeoutError, socket.gaierror):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True
