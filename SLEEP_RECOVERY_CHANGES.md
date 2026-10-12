# vless2socks: Sleep Recovery & Single-Instance IPC Architecture Changelog

> **Target Audience:** AI Models, Developers, DevOps  
> **Environment:** Windows 10/11 x64, Python 3.12, Tkinter, Pystray, Xray-core  
> **Repository Path:** `C:\MyFiles\vless2socks`  
> **Date:** 2026-10-12  

---

## 1. Executive Summary & Problem Statement

В приложении `vless2socks` (графический мульти-прокси менеджер на базе Tkinter и Xray-core под Windows) были исследованы и устранены две критические системные проблемы, возникающие после перехода компьютера в режим сна (Sleep / Modern Standby / Hibernate):

1. **Мертвые прокси-туннели при ложном статусе "Healthy":**  
   После выхода Windows из сна исходящий трафик через все локальные прокси переставал работать (`schannel: failed to receive handshake` в curl, ERR_CONNECTION_CLOSED в браузерах). При этом локальный индикатор в GUI оставался зеленым (`Healthy`).
2. **Зависание GUI / невозможность развернуть окно из системного трея:**  
   Клик/двойной клик по иконке в системном трее Windows не разворачивал окно. Попытка запустить `gui.bat` показывала уведомление *"vless2socks is already running"* на 1.2 секунды и завершалась, не открывая окно и оставляя пользователя без доступа к интерфейсу.
3. **Chrome Socket Launcher (Сокет #3):**  
   Создан изолированный батник и multi-res ярлык запуска Google Chrome через сокет #3 (`Remote Proxy`, SOCKS5 `127.0.0.1:1083`, HTTP `127.0.0.1:11083`).

---

## 2. Root Cause Analysis (Анализ первопричин)

### Проблема А: Почему прокси умирали, но считались "живыми"
* **Сетевой уровень (L4 / L7):** При засыпании Windows сетевой адаптер разрывает внешние TCP/TLS сессии к VLESS-серверам. Процессы `xray.exe` остаются в памяти. Их локальные порты (`127.0.0.1:108x`) продолжают принимать входящий `TCP SYN` и отвечать `SYN-ACK` (так как локальный стек Windows loopback активен).
* **Логический баг проверки здоровья:** Метод `is_port_alive(host, port)` делал лишь `socket.connect((127.0.0.1, port))`. Локальный коннект проходил мгновенно, и функция возвращала ложный `healthy = True`. Реальное рукопожатие с удаленным апстримом не проверялось.
* **Баг в машине состояний (`apply_health_status`):** Если порт отваливался, код проверял `has_live_process = bool(self.process and self.process.poll() is None)`. Так как `xray.exe` технически оставался запущенным, статус менялся на `"starting"`, но **`self.restart()` не вызывался**. Прокси зависал в мертвом состоянии навсегда.

### Проблема Б: Почему GUI не разворачивался из трея
* **Блокировка DWM (`LockSetForegroundWindow`):** В Windows 10/11 вызов `self.deiconify()` из Tkinter блокируется операционной системой, если вызывающий процесс не обладает фокусом ввода пользователя. Окно восстанавливалось в фоновом/невидимом слое.
* **Инвалидация дескрипторов Shell:** При выходе из сна Windows Shell (Explorer) часто инвалидирует значки в области уведомлений (`Shell_NotifyIconW`), из-за чего поток `pystray` перестает получать события клика мыши.
* **Падение очереди GUI-коллбэков:** Метод `_drain_ui_queue()` ранее не оборачивал вызовы в `finally: super().after(...)`. При единичном исключении очередь обработки событий из фонового трея навсегда умирала.
* **Отсутствие IPC в Single-Instance:** Функция `_acquire_instance_lock()` блокировала файл `.vless2socks.lock`. При повторном запуске приложения она видела замок и закрывалась, не имея механизма отправить сигнал «Покажи окно» работающему процессу.

---

## 3. Архитектура решения: 3 эшелона защиты

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Windows Operating System                        │
└──────────────────┬─────────────────────────────────┬───────────────────┘
                   │                                 │
         WM_POWERBROADCAST                     User runs gui.bat
       (Resume from Sleep)                  (Secondary Instance)
                   │                                 │
                   ▼                                 ▼
    ┌─────────────────────────────┐   ┌─────────────────────────────┐
    │ 1. PowerBroadcastWatcher    │   │ 2. SingleInstanceIpcServer  │
    │ (Win32 Hidden Message Pump) │   │ (Local Loopback 127.0.0.1:  │
    │  [power_watcher.py]         │   │  Port 10199 [ipc_server.py] │
    └──────────────┬──────────────┘   └──────────────┬──────────────┘
                   │                                 │
          Delay 2.5s (DHCP ready)             Sends "SHOW"
                   │                                 │
                   ▼                                 ▼
    ┌─────────────────────────────┐   ┌─────────────────────────────┐
    │ Trigger Auto-Recovery       │   │ Windows Win32 API Unhide    │
    │ • Restart active proxies    │   │ • ShowWindow(hwnd, 9)       │
    │ • Re-initialize tray icon   │   │ • SetForegroundWindow(hwnd) │
    │ • Refresh Overview UI       │   │ • Topmost pulse toggle      │
    └─────────────────────────────┘   └─────────────────────────────┘
                   ▲
                   │
                   │ Periodic check (every 12s)
                   │
    ┌─────────────────────────────┐
    │ 3. Active SOCKS5 Probe      │
    │ (tunnel_probe.py)           │
    │ 10-byte binary CONNECT to   │
    │ 1.1.1.1:53 / 8.8.8.8:53     │
    └─────────────────────────────┘
```

---

## 4. Новые модули

### 4.1. `vless2socks/power_watcher.py`
Слушает события Win32 `WM_POWERBROADCAST` (0x0218) через скрытое окно сообщений.
* **События:** `PBT_APMSUSPEND` (0x0004), `PBT_APMRESUMEAUTOMATIC` (0x0012), `PBT_APMRESUMESUSPEND` (0x0007).
* **Архитектура:** Полноценная 64-битная структура `WNDCLASS` и строго типизированный `DefWindowProcW` с поддержкой `argtypes`/`restype`.
* **Поведение:** При получении resume ставит таймер на 2.5 секунды (ожидание инициализации Wi-Fi/DHCP стека Windows), после чего запускает горячий перезапуск всех запущенных прокси и пересоздание значка трея.

### 4.2. `vless2socks/ipc_server.py`
Локальный TCP-сервер межпроцессного взаимодействия на `127.0.0.1:10199`.
* **Команды:**
  * `SHOW` — переводит окно работающего GUI на передний план.
  * `RESTART` — инициирует горячий рестарт всех прокси.
  * `PING` — проверка доступности (возвращает `PONG`).
* **Клиент:** Функция `request_show_running_instance()`. При повторном запуске `gui.bat` обращается к порту 10199, запрашивает развертывание окна и моментально выходит с кодом 0.

### 4.3. `vless2socks/tunnel_probe.py`
Сверхбыстрый активный зонд сквозного туннеля SOCKS5:
* Отправляет бинарный SOCKS5 handshake (`\x05\x01\x00` -> `\x05\x00`).
* Отправляет бинарный `CONNECT` запрос к быстрому DNS IP (`1.1.1.1:53` или `8.8.8.8:53`).
* Читает 10-байтный ответ (`reply[1] == 0` означает успешный сквозной туннель).
* **Время выполнения:** ~12–25 мс.
* **Трафик:** 0 байт полезной нагрузки.
* При 2 повторных сбоях туннеля метод `check_health_sync()` возвращает `False`, активируя самовосстановление.

---

## 5. Изменения в `gui.py`

### 5.1. Активная проверка туннеля и автоперезапуск зависших процессов
```python
    def check_health_sync(self, timeout: float = 0.6) -> bool:
        h, p_str = self.get_listen()
        try:
            p = int(p_str)
        except ValueError:
            return False

        # 1. Быстрая проверка локального порта
        if not is_port_alive(h, p, timeout=timeout):
            return False

        # 2. Периодическая проверка реального туннеля (каждые 12 сек)
        if self.running and not self.is_transitioning:
            now = time.time()
            if now - getattr(self, "_last_tunnel_probe", 0.0) >= 12.0:
                self._last_tunnel_probe = now
                try:
                    ok, _ = probe_socks5_tunnel(h, p, timeout=1.0)
                    if not ok:
                        self._upstream_failures = getattr(self, "_upstream_failures", 0) + 1
                        if self._upstream_failures >= 2:
                            return False
                    else:
                        self._upstream_failures = 0
                except Exception:
                    pass

        return True

    def apply_health_status(self, alive: bool) -> bool:
        ...
        if self.running:
            if alive:
                self._upstream_failures = 0
                ...
            elif not alive:
                has_live_process = bool(self.process and self.process.poll() is None)
                if has_live_process:
                    if self.healthy:
                        self.healthy = False
                        self._set_state("error")
                        self.app.refresh_overview()
                        changed = True
                    # ВАЖНО: Если процесс висит, но туннель мертв — вызываем горячий рестарт!
                    if not self._manual_stop and self.is_auto_reconnect_enabled():
                        self._log("🔄 [SelfRecovery] Dead upstream tunnel detected with live process. Restarting instance...")
                        self.restart(blocking=False)
```

### 5.2. Надежное развертывание окна в Windows Win32 API
```python
    def _do_show(self):
        try:
            self.deiconify()
        except Exception:
            pass
        self._hidden = False

        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = self.winfo_id()
                parent_hwnd = ctypes.windll.user32.GetAncestor(hwnd, 2)  # GA_ROOT
                target_hwnd = parent_hwnd if parent_hwnd else hwnd
                ctypes.windll.user32.ShowWindow(target_hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(target_hwnd)
            except Exception:
                pass

        try:
            self.lift()
            self.attributes("-topmost", True)
            self.attributes("-topmost", False)
            self.focus_force()
        except Exception:
            pass
```

### 5.3. Интеграция Single-Instance IPC в точку входа `main()`
```python
    # Проверка на множественный запуск GUI
    if not _acquire_instance_lock():
        try:
            if request_show_running_instance():
                return 0  # Экземпляр найден и развернут на экране!
        except Exception:
            pass
        _show_already_running_notice(1200)
        return 0
```

### 5.4. Защита очереди GUI `_drain_ui_queue`
```python
    def _drain_ui_queue(self):
        try:
            while True:
                ms, func, args = self._ui_queue.get_nowait()
                try:
                    super().after(ms, func, *args)
                except Exception:
                    pass
        except queue.Empty:
            pass
        except Exception:
            pass
        finally:
            super().after(UI_QUEUE_POLL_MS, self._drain_ui_queue)
```

---

## 6. Chrome Desktop Launcher (Сокет #3)

* **Файлы:**
  * `C:\Users\79251\Desktop\Chrome_Socket_3_FRANCE_1083.bat`
  * `C:\Users\79251\Desktop\Chrome Socket 3 - France 1083.lnk`
  * `C:\MyFiles\Proxy\Chrome_Socket_1083.bat`
  * `C:\MyFiles\Proxy\ico\chrome_red.ico`
* **Конфигурация:**
  * Профиль: `--user-data-dir="%LOCALAPPDATA%\Google\Chrome\Proxy1083"`
  * Прокси: `--proxy-server="socks5://127.0.0.1:1083"`
  * Маршрутизация: VLESS TCP удаленный сервер (`203.0.113.42`).

---

## 7. Протокол валидации и тестов

| Тест | Команда / Метод | Ожидаемый результат | Фактический результат |
|---|---|---|---|
| IPC Ping | `send_ipc_command('PING')` | `"PONG"` | `"PONG"` (Успешно) |
| IPC Show | `send_ipc_command('SHOW')` | `"OK"` | `"OK"` (Окно развернуто) |
| Вторичный запуск | Запуск `gui.bat` при открытом GUI | Фокус существующего окна | Завершение с кодом 0, окно на экране |
| Зонд туннеля (живой) | `probe_socks5_tunnel('127.0.0.1', 1083)` | `(True, 'Tunnel active')` | `(True, 'Tunnel active')` |
| Зонд туннеля (мертвый)| `probe_socks5_tunnel('127.0.0.1', 19999)`| `(False, 'Upstream tunnel...')` | `(False, 'Upstream tunnel...')` |
| SOCKS5 Эндпоинт | `curl.exe -s --socks5 127.0.0.1:1083 https://api.ipify.org` | `<Remote_IP>` | `203.0.113.42` |
| HTTP Эндпоинт | `curl.exe -s -x http://127.0.0.1:11083 https://api.ipify.org` | `<Remote_IP>` | `203.0.113.42` |
| Синтаксис Python | `python -m py_compile gui.py` | Код 0 | Код 0 (Ошибок нет) |
