"""
Движок фонового мониторинга (Watcher) для Zapret 2.
Выполняет периодические проверки и retry восстановления при падении сервисов.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

from . import core

logger = logging.getLogger("Zapret2Watcher")


class Zapret2Watcher:
    """
    Фоновый наблюдатель за состоянием модулей Zapret 2 (winws2 и Telegram MTProxy).
    Реализует адаптивный retry backoff (1с, 5с, 10с, 15с...) при сбоях.
    """

    RETRY_SCHEDULE = [1.0, 5.0, 8.0]
    NORMAL_POLL_INTERVAL = 8.0

    def __init__(
        self,
        enabled: bool = True,
        watch_main: bool = True,
        watch_tg: bool = True,
        status_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        tg_proxy_provider: Optional[Callable[[], List[Any]]] = None,
        tg_restart_handler: Optional[Callable[[List[Any]], bool]] = None,
    ):
        self.enabled = bool(enabled)
        self.watch_main = bool(watch_main)
        self.watch_tg = bool(watch_tg)
        self.status_callback = status_callback
        self.tg_proxy_provider = tg_proxy_provider
        self.tg_restart_handler = tg_restart_handler

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._lock = threading.Lock()

        self._main_ok = False
        self._tg_ok = False
        self._has_tg_target = False
        self._tg_target_display = "не выбрано"
        self._relay_error = False
        self._relay_error_msg = ""
        self._last_tg_reconnect_time = 0.0
        self._recovering = False
        self._retry_count = 0
        self._last_check_time = 0.0

    def start(self):
        """Запустить фоновый поток мониторинга."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._thread = threading.Thread(target=self._run_loop, name="Zapret2Watcher-Thread", daemon=True)
            self._thread.start()

    def stop(self):
        """Остановить фоновый поток."""
        self._stop_event.set()
        self._wake_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None

    def trigger_immediate_check(self):
        """Разбудить поток для немедленной проверки."""
        self._wake_event.set()

    def set_enabled(self, enabled: bool):
        """Включить/выключить мониторинг (switcher on/off)."""
        with self._lock:
            self.enabled = bool(enabled)
            if not self.enabled:
                self._recovering = False
                self._retry_count = 0
                self._relay_error = False
                self._relay_error_msg = ""
        self.trigger_immediate_check()

    def set_watch_targets(self, watch_main: bool, watch_tg: bool):
        """Настроить цели мониторинга (чекбоксы MainZapret2 и Telegram Proxy)."""
        with self._lock:
            self.watch_main = bool(watch_main)
            self.watch_tg = bool(watch_tg)
        self.trigger_immediate_check()

    def get_state(self) -> Dict[str, Any]:
        """Текущее состояние наблюдателя для UI."""
        with self._lock:
            return {
                "enabled": self.enabled,
                "watch_main": self.watch_main,
                "watch_tg": self.watch_tg,
                "main_ok": self._main_ok,
                "tg_ok": self._tg_ok,
                "has_tg_target": self._has_tg_target,
                "tg_target_display": self._tg_target_display,
                "relay_error": self._relay_error,
                "relay_error_msg": self._relay_error_msg,
                "recovering": self._recovering,
                "retry_count": self._retry_count,
            }

    def _notify(self):
        """Уведомить UI о смене состояния."""
        if self.status_callback:
            try:
                self.status_callback(self.get_state())
            except Exception:
                pass

    def _run_loop(self):
        """Основной цикл фоновой проверки."""
        while not self._stop_event.is_set():
            delay = self.NORMAL_POLL_INTERVAL

            if self.enabled:
                main_needed = self.watch_main
                tg_needed = self.watch_tg

                # 1. Проверка MainZapret2 (winws2.exe)
                main_active = core.is_winws2_running()

                # 2. Проверка Telegram Proxy (ТОЛЬКО свои прокси, зафлаганные во vless2socks)
                tg_proxies = []
                if self.tg_proxy_provider:
                    try:
                        tg_proxies = self.tg_proxy_provider()
                    except Exception:
                        tg_proxies = []
                else:
                    tg_proxies = core.get_configured_telegram_proxies()

                has_tg_target = bool(tg_proxies)
                tg_active = False

                lang = "ru"
                try:
                    from i18n import get_current_language
                    lang = get_current_language()
                except Exception:
                    pass

                if has_tg_target:
                    # Проверяем живость выбранных прокси
                    upstream_alive = False
                    for p in tg_proxies:
                        if hasattr(p, "running"):
                            if p.running:
                                upstream_alive = True
                                break
                        elif isinstance(p, dict):
                            if p.get("running"):
                                upstream_alive = True
                                break
                            h = p.get("host", "127.0.0.1")
                            pt = p.get("port", 1081)
                            if core.is_endpoint_reachable(h, pt):
                                upstream_alive = True
                                break

                    tg_port = core.DEFAULT_TG_PORT
                    try:
                        from vless2socks import telegram_proxy
                        tg_port = telegram_proxy.get_current_tg_port()
                    except Exception:
                        pass
                    disp_alive = core.is_endpoint_reachable("127.0.0.1", tg_port)
                    tg_active = upstream_alive and disp_alive

                    # Строка отображения выбранного порта/адреса
                    if hasattr(tg_proxies[0], "get_listen"):
                        h, pt = tg_proxies[0].get_listen()
                        tg_target_display = f"{h}:{pt}"
                    elif isinstance(tg_proxies[0], dict):
                        tg_target_display = f"{tg_proxies[0].get('host', '127.0.0.1')}:{tg_proxies[0].get('port', 1081)}"
                    else:
                        tg_target_display = "127.0.0.1:1081"
                    if len(tg_proxies) > 1:
                        tg_target_display += f" (+{len(tg_proxies)-1})"
                else:
                    # Ни один tg прокси не выбран: "не выбрано" / "undefined"
                    tg_active = False
                    tg_target_display = "undefined" if lang.lower().startswith("en") else "не выбрано"

                with self._lock:
                    self._main_ok = main_active
                    self._tg_ok = tg_active
                    self._has_tg_target = has_tg_target
                    self._tg_target_display = tg_target_display
                    self._relay_error = False
                    self._relay_error_msg = ""

                # Восстановление требуется ТОЛЬКО если целевой сервис действительно сконфигурирован!
                # Если ни один TG прокси не выбран - не пытаемся перезапускать вхолостую
                needs_recovery = (main_needed and not main_active) or (tg_needed and has_tg_target and not tg_active)

                if needs_recovery:
                    with self._lock:
                        self._recovering = True
                        retry_idx = min(self._retry_count, len(self.RETRY_SCHEDULE) - 1)
                        delay = self.RETRY_SCHEDULE[retry_idx]
                        self._retry_count += 1

                    self._notify()

                    # Выполнение восстановления
                    if main_needed and not main_active:
                        core.start_winws2()

                    if tg_needed and has_tg_target and not tg_active:
                        if self.tg_restart_handler:
                            try:
                                self.tg_restart_handler(tg_proxies)
                            except Exception:
                                pass
                        else:
                            core.restart_tg_proxy()

                    # Проверка после попытки восстановления
                    main_now = core.is_winws2_running()
                    tg_now = False
                    if has_tg_target:
                        up_now = False
                        for p in tg_proxies:
                            if hasattr(p, "running"):
                                if p.running:
                                    up_now = True
                                    break
                            elif isinstance(p, dict):
                                if p.get("running"):
                                    up_now = True
                                    break
                                h = p.get("host", "127.0.0.1")
                                pt = p.get("port", 1081)
                                if core.is_endpoint_reachable(h, pt):
                                    up_now = True
                                    break
                        disp_now = core.is_endpoint_reachable("127.0.0.1", tg_port)
                        tg_now = up_now and disp_now

                    recovered = (not main_needed or main_now) and (not tg_needed or not has_tg_target or tg_now)

                    with self._lock:
                        self._main_ok = main_now
                        self._tg_ok = tg_now
                        if recovered:
                            self._recovering = False
                            self._retry_count = 0
                            delay = self.NORMAL_POLL_INTERVAL

                    self._notify()
                else:
                    with self._lock:
                        self._recovering = False
                        self._retry_count = 0
                        delay = self.NORMAL_POLL_INTERVAL
                    self._notify()
            else:
                with self._lock:
                    self._recovering = False
                    self._retry_count = 0
                self._notify()
                delay = self.NORMAL_POLL_INTERVAL

            # Ожидание следующего тика (или пробуждение по событию)
            self._wake_event.wait(timeout=delay)
            self._wake_event.clear()


