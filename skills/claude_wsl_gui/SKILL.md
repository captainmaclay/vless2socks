---
name: claude_wsl_gui
description: >-
  Спецификация запуска Linux GUI-клиента Claude Desktop (Electron) в WSL2/WSLg с маршрутизацией через
  локальные сокеты vless2socks: цепочка ярлык → VBS → pre-flight IP-чекер → WSL-лаунчер, изоляция профилей,
  математика HiDPI (Windows scaling × Weston × Chromium DIP), персистентность геометрии окна через xdotool/xprop,
  жизненный цикл процесса (close-to-hide, Singleton locks, lockfile, ретраи), WSLg COPY MODE и диагностика.
---

# Claude Desktop GUI в WSL2/WSLg через vless2socks

Скилл описывает, как устроен и как чинить запуск изолированных инстансов Claude Desktop для Linux внутри
WSL2 (WSLg), где весь трафик каждого инстанса идёт через свой сокет vless2socks (`1015`, `1016`, ...).

Связанные материалы:
- [`skills/wsl_gui_hidpi/SKILL.md`](../wsl_gui_hidpi/SKILL.md) — история устранения диалога `Unlock Login Keyring` и первичная настройка HiDPI.
- [`VENDOR_C_WSL_GUI_ENVIRONMENTS.md`](../../VENDOR_C_WSL_GUI_ENVIRONMENTS.md), [`new_claude/howToCreate.md`](../../new_claude/howToCreate.md) — пошаговое создание окружений.
- [`wsl-proxy-isolation/SKILL.md`](../../wsl-proxy-isolation/SKILL.md) — netfilter-изоляция WSL2 на уровне ядра.

> [!IMPORTANT]
> **Шаблоны — единственный источник истины.** С 2026-10-23 исправления раздела 7 живут в
> `new_claude/template_claude_gui.sh`, `new_claude/template_run_gui.vbs` и `new_claude/wslg_health.sh`;
> оба инстанса (1015, 1016) переразвёрнуты из них через `update_launchers.py`. Рендер шаблона для инстанса 1
> побайтно совпадает с лаунчером, отлаженным вручную. Правки вносятся в шаблон с последующим
> `python new_claude/update_launchers.py`, а не в `/usr/local/bin/claude-gui-*` напрямую: деплой перезапишет
> ручные изменения.

---

## 1. Архитектура и цепочка запуска

```text
Рабочий стол Windows
  Claude GUI.lnk / GUI_1015.lnk
    └─ wscript.exe C:\MyFiles\herdrCenter\run_claude_gui_<id>.vbs        (всё скрыто, окно 0)
         ├─ 1. pythonw verify_and_notify.py --port <SOCKS>               (ждём, exit 0 = разрешено)
         ├─ 2. wsl.exe ... bash /usr/local/bin/wslg-health                (до 4 попыток через 1 с)
         │      exit 10 → wsl.exe --shutdown                              (самолечение COPY MODE)
         └─ 3. wsl.exe -d Ubuntu-24.04 -u ubuntu bash -lc /usr/local/bin/claude-gui-<id>   (не ждём)
                 └─ claude-desktop --proxy-server=socks5://127.0.0.1:<SOCKS> --user-data-dir=... &
                       └─ X11-окно → XWayland → Weston (RDP RAIL) → msrdc.exe → окно Windows
```

| Компонент | Путь | Кто генерирует |
| :--- | :--- | :--- |
| Ярлыки `.lnk` (Рабочий стол, `C:\MyFiles\Proxy\ink`, `proxys\ink`) | — | [`create_desktop_shortcut.ps1`](../../new_claude/create_desktop_shortcut.ps1) |
| VBS-раннер | `C:\MyFiles\herdrCenter\run_claude_gui_<id>.vbs` | [`template_run_gui.vbs`](../../new_claude/template_run_gui.vbs) через `str.format()` |
| BAT-обёртки | `C:\MyFiles\Proxy\{Vendor_C_GUI_<id>,Claude_GUI_<id>,Claude_GUI_Socket_<port>}.bat` | `add_vendor_c_gui_instance.py` |
| WSL-лаунчер | `/usr/local/bin/claude-gui-<id>` (инстанс 1 ещё копируется в `claude-gui`) | [`template_claude_gui.sh`](../../new_claude/template_claude_gui.sh) через `str.replace()` |
| Проверка WSLg (общая для всех инстансов) | `/usr/local/bin/wslg-health` | [`wslg_health.sh`](../../new_claude/wslg_health.sh), ставит `deploy_wsl_launcher` |
| Pre-flight чекер | `new_claude/verify_and_notify.py` | — |
| Деплой одного инстанса | — | [`add_vendor_c_gui_instance.py`](../../new_claude/add_vendor_c_gui_instance.py) `--id --port --color --name` |
| Переразвёртывание 1 и 2 | — | [`update_launchers.py`](../../new_claude/update_launchers.py) |

Плейсхолдеры шаблонов: `{INSTANCE_ID}`, `{SOCKS_PORT}`, `{HTTP_PORT}` (по умолчанию `SOCKS + 10000`), `{WSL_USER}`, `{DISTRO}`.
Деплоер также подставляет `{UID}`, но лаунчер намеренно берёт UID в рантайме (`USER_UID=$(id -u)`): так
`/run/user/<uid>` всегда совпадает с реальным пользователем, даже если деплоер ошибся с UID.

> [!WARNING]
> VBS-шаблон рендерится через Python `str.format()`. Любые фигурные скобки в VBS, кроме плейсхолдеров,
> сломают генерацию (`KeyError`/`ValueError`). Литеральные скобки нужно удваивать: `{{`, `}}`.

`C:\MyFiles\vless2socks` — NTFS junction на `C:\proj\vless2socks`; оба пути указывают на один репозиторий.

---

## 2. Привязка к сокетам vless2socks и pre-flight

### 2.1. Маршрутизация внутри инстанса
| Уровень | Механизм | Значение для инстанса 1 |
| :--- | :--- | :--- |
| Chromium (весь сетевой стек Electron) | `--proxy-server="socks5://127.0.0.1:<SOCKS>"` | `socks5://127.0.0.1:1015` |
| Дочерние процессы (node-хелперы, git, curl) | `ALL_PROXY`/`all_proxy='socks5h://...'` (`h` — DNS на стороне прокси) | `socks5h://127.0.0.1:1015` |
| То же для HTTP-клиентов | `HTTP(S)_PROXY`/`http(s)_proxy='http://127.0.0.1:<HTTP>'` | `http://127.0.0.1:11015` |

WSL2 работает в `networkingMode=mirrored` (`%USERPROFILE%\.wslconfig`), поэтому `127.0.0.1` внутри WSL —
это loopback Windows, где слушает xray из vless2socks.

> [!CAUTION]
> `/etc/profile.d/herdr_claude_env.sh` глобально экспортирует прокси **1015** для всех login-шеллов.
> Лаунчер вызывается через `bash -lc`, поэтому сначала подхватывает 1015 и только потом переопределяет
> переменные своими (шаг 2 шаблона). Новый инстанс, собранный без шага 2, молча уйдёт через 1015.

### 2.2. Pre-flight: `verify_and_notify.py --port <SOCKS> --app "Vendor C GUI"`
1. **Ожидаемый IP.** Ищет в `C:\MyFiles\vless2socks\instances.json` запись с `listen`, содержащим `:<port>`,
   офлайн извлекает страну (`geo_ip.extract_country_hint`) и резолвит хост VLESS-сервера.
   Если не вышло и порт слушается, спрашивает `geo_ip.fetch_geo_via_socks`.
2. **Фактический IP.** Использует сокет как HTTP-прокси (`http://127.0.0.1:<port>`) для `api.ipify.org` и
   `ip-api.com`, fallback — `fetch_geo_via_socks`. Пустой ответ означает, что сокет не отвечает.
3. **Вердикт.** Успех — `detected == expected`. Для порта ≠ 1015 совпадение с IP базового 1015 считается утечкой
   и блокирует запуск. Если ожидаемый IP неизвестен, допускается любой непустой IP, кроме IP 1015.
4. **Результат.** `exit 0` + неблокирующий тост (отдельный detached `pythonw`, 4 с) либо `exit 1` +
   блокирующий тост (5 с). VBS запускает лаунчер только при `0`.

Ручная проверка (2026-10-23: `exit=0`, IP выходного узла совпал с ожидаемым, около 1 с):
```powershell
Test-NetConnection 127.0.0.1 -Port 1015          # TcpTestSucceeded : True (процесс xray)
python C:\MyFiles\vless2socks\new_claude\verify_and_notify.py --port 1015; $LASTEXITCODE
```

---

## 3. Изоляция инстанса

| Что | Значение (инстанс `<id>`) |
| :--- | :--- |
| `HOME` | `/home/ubuntu/.claude-<id>` |
| `XDG_CONFIG_HOME` | `$HOME/.config` |
| Профиль Electron (`--user-data-dir`) | `$HOME/.config/Claude` |
| `XDG_RUNTIME_DIR` | `/run/user/1000/claude-<id>` (tmpfs, `700`) |
| PID-файл | `$XDG_RUNTIME_DIR/claude-gui-<id>.pid` |
| Keyring | `$HOME/.local/share/keyrings/` |
| Геометрия | `$USER_DATA_DIR/window-geometry.conf`, `window-state.json`, `scale-factor.conf` |
| Лог лаунчера | `$HOME/.local/state/claude-gui-<id>/launcher.log` |
| Lockfile / флаг reopen | `$XDG_RUNTIME_DIR/claude-gui-<id>.lock`, `claude-gui-<id>.reopen` |

> [!NOTE]
> **DBus не изолирован.** Несмотря на docstring `add_vendor_c_gui_instance.py`, все инстансы используют общую
> сессионную шину `unix:path=/run/user/1000/bus` (шаг 5 шаблона). При systemd-сессии её поднимает
> `dbus-daemon --session` от systemd; лаунчер стартует свою шину, только если сокета нет.
> Отдельный `XDG_RUNTIME_DIR` даёт изоляцию PID-файлов и lockfile, а не DBus.

---

## 4. HiDPI: математика масштабирования

### 4.1. Три слоя
```text
Физический экран Windows      3072 × 1920 px, масштаб Windows 200% (192 DPI)
        │  RDP monitor: desktopScaleFactor=200 → Weston output scale = 2
        ▼
Логическая сетка Weston/XWayland   1536 × 960   (это и видит `xdotool getdisplaygeometry`)
        │  Chromium: 1 DIP = force-device-scale-factor × X-пиксель
        ▼
Интерфейс Electron (DIP)
```

Итоговый размер элемента на физическом экране:

```text
physical_px = DIP × force_device_scale_factor × weston_output_scale
```

| `--force-device-scale-factor` | Weston scale | Итог | Результат |
| :---: | :---: | :---: | :--- |
| **1.0** | 2 | **200%** | Совпадает с масштабом Windows — корректно |
| 2 | 2 | 400% | Гигантские шрифты и кнопки; окно 1280 DIP = 2560 X-px не влезает в 1536 |

Подтверждение в `/mnt/wslg/weston.log`:
```text
rdpMonitor[0]: weston x:0, y:0, width:1536, height:960
rdpMonitor[0]: desktopScaleFactor:200, deviceScaleFactor:180
Head mode change:rdp-0 NEW width:3072, height:1920, scale:2, clientScale:2.000000
```

### 4.2. Текущая конфигурация
- Лаунчер: `SCALE_FACTOR=1.0` по умолчанию. Приоритет переопределений: env `CLAUDE_SCALE_FACTOR` →
  файл `$USER_DATA_DIR/scale-factor.conf` (одно число) → `1.0`.
- `/etc/profile.d/wsl_hidpi.sh` в развёрнутой системе:
  ```bash
  export ELECTRON_OZONE_PLATFORM_HINT=auto
  export GDK_SCALE=1
  export GDK_DPI_SCALE=1
  export QT_AUTO_SCREEN_SCALE_FACTOR=1
  export QT_SCALE_FACTOR=1
  export XCURSOR_SIZE=24
  ```
- `%USERPROFILE%\.wslgconfig`: `[system-distro-env] WESTON_RDP_DISABLE_FRACTIONAL_HI_DPI_SCALING=false`, без
  `WESTON_RDP_DEBUG_DESKTOP_SCALING_FACTOR`: Weston сам берёт масштаб монитора Windows (`desktopScaleFactor`).
- Провижининг: [`setup_helpers/09_configure_wsl_hidpi_keyring.ps1`](../../setup_helpers/09_configure_wsl_hidpi_keyring.ps1)
  (`-ScaleFactor 1 -Dpi 96` по умолчанию) → [`configure_wsl_hidpi_keyring.sh`](../../new_claude/configure_wsl_hidpi_keyring.sh)
  (значения выше + `Xft.dpi: 96`).

> [!CAUTION]
> До 2026-10-23 шаг 09 писал `WESTON_RDP_DEBUG_DESKTOP_SCALING_FACTOR=200`, а `configure_wsl_hidpi_keyring.sh` —
> `GDK_SCALE=2`, `QT_SCALE_FACTOR=2`, `GDK_DPI_SCALE=0.5`, `XCURSOR_SIZE=32`, `Xft.dpi: 192`. Если на машине
> остался старый `.wslgconfig` или `/etc/profile.d/wsl_hidpi.sh`, перезапустите шаг 09 и выполните `wsl --shutdown`.

### 4.3. Фактический бэкенд: X11, а не Wayland
`--ozone-platform-hint=auto` + `--enable-features=WaylandWindowDecorations` задуманы для нативного Wayland, но
дочерние процессы Claude Desktop запускаются с `--ozone-platform=x11` (видно в `ps`). Приложение работает через
XWayland. На этом держится вся персистентность геометрии: `xdotool`/`xprop` видят только X11-окна.
Переход Electron на нативный Wayland сделает окно невидимым для лаунчера (раздел 5) и сломает фокус, сохранение
геометрии и автозакрытие.

`MESA: error: ZINK: failed to choose pdev` и `glx: failed to create drisw screen` в логе безвредны:
это фолбэк на программный рендер.

---

## 5. Персистентность геометрии окна

### 5.1. Двухуровневое хранилище
| Файл | Формат | Назначение |
| :--- | :--- | :--- |
| `window-geometry.conf` | `WIDTH= HEIGHT= X= Y= IS_MAXIMIZED=` | Источник истины для bash |
| `window-state.json` | `{x,y,width,height,isMaximized,isFullScreen,displayBounds}` | Файл, который читает Electron при старте |

### 5.2. Алгоритм перед стартом (шаг 11 шаблона)
1. `DISP_W×DISP_H` = `xdotool getdisplaygeometry`; при ошибке или ширине < 800 используется `1536×960`.
2. Значения по умолчанию: `W = 75% DISP_W` (минимум 900), `H = 80% DISP_H` (минимум 650), окно по центру.
3. Если есть `window-geometry.conf`: принимаются `400 ≤ W ≤ DISP_W`, `300 ≤ H ≤ DISP_H`, `0 ≤ X < DISP_W`, `0 ≤ Y < DISP_H`.
4. Иначе, если есть `window-state.json`: пороги строже (`W ≥ 600`, `H ≥ 450`), чтобы отбросить «микроскопические»
   411×312, оставшиеся от эпохи scale=2.
5. Оба файла перезаписываются атомарно (`*.tmp` + `mv -f`). В `displayBounds` пишется текущий
   `0,0,DISP_W,DISP_H`, чтобы Electron не счёл сохранённую позицию лежащей на отключённом мониторе.

### 5.3. После старта (шаг 13)
- **Поиск окна:** до 40 × 0,5 с: `xdotool search --onlyvisible --pid <MAIN_PID>`, затем по прямым детям (`pgrep -P`).
- **Коррекция:** если Electron открылся с отклонением больше 60 px по W/H, выполняются `xdotool windowsize` + `windowmove`.
  При `IS_MAXIMIZED=true` и отсутствии `_NET_WM_STATE_MAXIMIZED_*` окно растягивается на `DISP_W×DISP_H`.
- **Цикл опроса каждые 1,5 с:** `xprop -id <win> _NET_WM_STATE` (ищется `MAXIMIZED`) и
  `xdotool getwindowgeometry --shell`. При изменении вызывается `save_geometry` (оба файла).
  Пока окно развёрнуто, сохраняется только флаг, а последние «нормальные» размеры остаются для восстановления.

---

## 6. Жизненный цикл процесса (базовый шаблон)

| Шаг | Действие |
| :--- | :--- |
| 9 | Если PID-файл жив и у него есть видимое окно — `xdotool windowactivate`, выход |
| 10 | Иначе — `kill -9` старого PID и его детей, `pkill` по `--user-data-dir=<профиль>` и `chrome_crashpad_handler --database=<профиль>`, `rm Singleton*` |
| 12 | `claude-desktop ... &`, PID в PID-файл |
| 13 | Когда окно исчезло из `--onlyvisible` — `kill -TERM`, через 1 с `kill -9` и та же очистка |

Зачем убивать: Claude Desktop по крестику **не выходит, а прячет окно** (в `main.log` пишется
`main window close requested (the app may hide it instead)`). Трея в WSLg нет, поэтому спрятанное приложение
недостижимо и висит в памяти (около 1,3 ГБ RSS на инстанс). Это и есть «зомби-процессы» Electron в WSLg.

---

## 7. Отказоустойчивость запуска (исправления от 2026-10-23, в шаблонах для всех инстансов)

### 7.1. Найденные баги
| # | Симптом | Причина |
| :--- | :--- | :--- |
| B1 | Окно открывается «не с первого раза» | Шаг 13 убивал приложение при **первой** пропаже окна. Клик по ярлыку в это время упирался в живой PID без видимого окна (ветка 9 не срабатывала), и шаг 10 убивал уже запускающийся экземпляр |
| B2 | После закрытия крестиком клик ничего не делает | Ветка «lock busy» / шаг 9 искали только видимое окно; спрятанное окно не восстанавливалось |
| B3 | Окно в панели задач с пустой белой миниатюрой, на экране ничего | WSLg COPY MODE (раздел 8) |
| B4 | `windowactivate` не поднимает окно в Windows | WSLg RAIL не пробрасывает X11-активацию на передний план |
| B5 | Нет следов неудачных запусков | Лаунчер запускается скрыто (`WshShell.Run ..., 0`), вывод терялся |

### 7.2. Лаунчер (`template_claude_gui.sh` → `/usr/local/bin/claude-gui-<id>`)
- **Лог (B5).** `exec >>$HOME/.local/state/claude-gui-<id>/launcher.log 2>&1`, ротация в `.1` при размере > 1 МБ,
  функция `log()` с меткой времени и PID лаунчера.
- **Lockfile.** `exec 9<>$XDG_RUNTIME_DIR/claude-gui-<id>.lock; flock -n 9`. Режим `<>` не обрезает файл, поэтому
  проигравший лаунчер не стирает записанный в lock PID держателя. Лок держится всё время жизни лаунчера.
  Electron запускается с `9>&-`, чтобы осиротевшие дочерние процессы не удерживали лок.
- **Второй лаунчер при занятом локе (B1, B2):**
  1. Окно видимо → `focus_existing_instance` (выход).
  2. Окна нет → `touch claude-gui-<id>.reopen`, затем до 60 × 0,5 с: либо окно появилось (фокус, выход),
     либо лок освободился (свежий запуск).
  3. Держатель в цикле опроса видит `reopen` при спрятанном окне и завершается сразу, не дожидаясь 9 с.
     Проверено: от клика до нового окна около 2 с.
- **Толерантность к пропаже окна (B1).** Завершение только после `MAX_MISSES=6` проверок подряд (около 9 с);
  если окно вернулось, счётчик сбрасывается.
- **Ретраи старта.** До `MAX_ATTEMPTS=4` попыток с паузой `RETRY_DELAY=1.5` с. Попытка считается неудачной,
  если Electron завершился до появления окна или окна нет 20 с. Между попытками выполняется `stop_instance`
  (kill дерева, crashpad, `Singleton*`, PID-файл).
- **Фокус (B4).** `xdotool windowminimize` → `sleep 0.3` → `windowactivate`.
- **COPY MODE (B3).** Если в `/mnt/wslg/weston.log` есть `rdp_allocate_shared_memory: Failed`, в лог пишется предупреждение.

### 7.3. `wslg_health.sh` → `/usr/local/bin/wslg-health` + `template_run_gui.vbs` → `run_claude_gui_<id>.vbs`
`wslg-health` ждёт до 8 с маркер `Output 'rdp-0' enabled` в `weston.log` и возвращает:
`0` — OK; `10` — COPY MODE и ни одного `claude-desktop`; `11` — COPY MODE, но GUI уже запущен.

VBS после pre-flight вызывает `wslg-health` до 4 раз с интервалом 1 с (пока `wsl.exe` не вернёт 0/10/11).
При `10` выполняется `wsl.exe --shutdown`, пауза 2 с, затем запуск. При `11` VBS не трогает WSL, чтобы не убить
работающие GUI.

### 7.4. `%USERPROFILE%\.wslconfig`
```ini
[general]
instanceIdleTimeout=-1
```
Дистрибутив не останавливается по простою (раздел 8). Ключ поддерживается WSL 2.7.1.0
(`general.instanceIdleTimeout` есть в `wslservice.exe`). Применяется после `wsl --shutdown`.
Цена: VM и дистрибутив постоянно держат память.

---

## 8. WSLg COPY MODE

**Симптом:** заголовок окна в Windows — `[WARN:COPY MODE] Claude (Ubuntu-24.04)`. Окно и иконка в панели задач есть,
миниатюра белая, на экране ничего. Так ведёт себя **любое** X11-приложение, например `xmessage`.

**Признак в `/mnt/wslg/weston.log`:**
```text
rdp_allocate_shared_memory: Failed to open "/mnt/shared_memory/{...}" with error: Input/output error
```
> [!WARNING]
> Строка `RDP backend: enable_copy_warning_title = 1` — это **настройка**, она есть и в здоровой сессии.
> По ней COPY MODE определять нельзя: первая версия `wslg-health` давала ложные срабатывания и лишний `wsl --shutdown`.

**Причина (воспроизведено):** перезапуск **дистрибутива внутри уже работающей VM** — остановка по простою после
закрытия последнего процесса или `wsl --terminate <distro>`. Weston поднимается заново, а общая память virtiofs
отвечает I/O error. Холодный старт VM после `wsl --shutdown` проходит чисто.

| Событие | Перезапуск | Итог |
| :--- | :--- | :--- |
| Дистрибутив перезапущен, VM жива | только distro | COPY MODE |
| `wsl --shutdown` → старт | VM целиком | норма |
| `wsl --terminate Ubuntu-24.04` → старт | только distro | COPY MODE |

**Лечение:** только `wsl --shutdown` (останавливает **всё** в WSL). Профилактика — `instanceIdleTimeout=-1`.

---

## 9. Подводные камни (Gotchas)

1. **Зомби Electron.** Крестик прячет окно, а не завершает процесс; без цикла шага 13 процессы копятся.
   Убивать нужно и `chrome_crashpad_handler --database=<профиль>`: он переживает основной процесс.
2. **Singleton locks.** `SingletonLock` → `<hostname>-<pid>`, `SingletonSocket` → `/tmp/scoped_dir*/SingletonSocket`,
   `SingletonCookie`. Живой экземпляр с тем же `--user-data-dir` перехватывает повторный запуск (`second-instance`,
   exit 0 примерно за 1,5 с), но **не показывает** спрятанное окно. Удалять `Singleton*` можно только после
   гарантированной смерти процесса и только под lockfile, иначе получится два Electron на одном профиле.
3. **`pkill -f` по шаблону.** Шаблон `claude-desktop.*--user-data-dir=<полный путь профиля>` не задевает соседние
   инстансы: путь заканчивается на `/.config/Claude`, поэтому `.claude-1` не совпадёт с `.claude-10`. Шаблон —
   регулярное выражение, точки в пути совпадают с любым символом; для текущих путей это безвредно. Сокращать
   шаблон до `claude-desktop` нельзя: это убьёт все инстансы сразу.
4. **`/run/user/1000` — tmpfs.** Он исчезает, когда у пользователя не остаётся сессий (logind), вместе с PID и lock.
   Лаунчер пересоздаёт каталоги; устаревшая ветка шага 9 этим и спасается.
5. **Подмена скрипта на лету.** Bash читает скрипт по мере исполнения. Обновлять лаунчер нужно через `install`
   (новый inode; так делает `deploy_wsl_launcher` с 2026-10-23), а не через `sudo tee`/редактирование на месте:
   они переписывают тот же inode, и работающий лаунчер выполнит обрезанный или смешанный файл.
6. **Скрытый запуск.** VBS запускает всё с окном `0`, поэтому stdout/stderr нужно явно писать в файл (раздел 7.2).
7. **Ярлыки-дубли.** `Claude GUI.lnk` и `GUI_1015.lnk` указывают на один VBS — это нормально, lockfile не даст два экземпляра.
8. **Secret Service и `safeStorage`.** Лаунчер запускает Electron с `--password-store=basic`. В `main.log`:
   `[safeStorage] isEncryptionAvailable=false ... (backend=basic_text) — session will not persist; app secrets fall back to plaintext`.
   Keyring (`login.keyring` без пароля + `gnome-keyring-daemon --login --components=secrets`) поднимается шагами 7–8,
   но Electron при `basic` его **не использует**. Он нужен только для того, чтобы не всплывал `Unlock Login Keyring`
   у других потребителей. Утверждение «keyring обеспечивает safeStorage» для текущей конфигурации неверно:
   это компромисс между отсутствием модального окна и сохранением сессии (см. `wsl_gui_hidpi`).
9. **Логин через браузер.** В дистрибутиве нет браузера: `xdg-open: no method available for opening 'https://claude.com/cai/login/...'`.
   OAuth-вход из GUI не откроет страницу автоматически.
10. **Нативный Wayland.** См. 4.3: при переходе Electron на Wayland `xdotool` перестанет видеть окно.

---

## 10. Диагностика (runbook)

```powershell
# Windows: сокет, pre-flight, окно WSLg
Test-NetConnection 127.0.0.1 -Port 1015
python C:\MyFiles\vless2socks\new_claude\verify_and_notify.py --port 1015; $LASTEXITCODE
(Get-Process msrdc -ErrorAction SilentlyContinue).MainWindowTitle   # нет "[WARN:COPY MODE]"?
wsl.exe -l -v                                                        # Ubuntu-24.04 Running
```
```bash
# WSL (через файл-скрипт: PowerShell 5.1 ломает кавычки и пайпы в `wsl.exe bash -c "..."`)
tail -30 ~/.claude-1/.local/state/claude-gui-1/launcher.log            # инстанс 2: .claude-2 / claude-gui-2
grep -c 'rdp_allocate_shared_memory: Failed' /mnt/wslg/weston.log     # 0 = здоров
cat /run/user/1000/claude-1/claude-gui-1.{pid,lock}
xdotool search --onlyvisible --pid "$(cat /run/user/1000/claude-1/claude-gui-1.pid)"
grep -E 'Starting app|close requested|beforeQuit|safeStorage' ~/.claude-1/.config/Claude/logs/main.log | tail
bash -x /usr/local/bin/claude-gui-1                                   # пошаговая трассировка
```

| Наблюдение | Вывод |
| :--- | :--- |
| `exit 1` у `verify_and_notify.py` | Сокет не отвечает или IP не совпал; запуск блокируется намеренно |
| Видимое X-окно есть, но в Windows пусто | COPY MODE → `wsl --shutdown` |
| `close requested` в `main.log`, затем `window gone ... shutting down` в launcher.log | Окно закрыли крестиком, лаунчер штатно завершил экземпляр |
| `attempt N: claude-desktop exited before showing a window` | Ранний крах Electron; см. `main.log` и Crashpad |
| `lock still busy after 30s` | Держатель лока завис; смотреть `cat claude-gui-1.lock` и процессы |

---

## 11. Реестр скриптов и открытые задачи

| Скрипт | Назначение |
| :--- | :--- |
| [`new_claude/template_claude_gui.sh`](../../new_claude/template_claude_gui.sh) | Шаблон WSL-лаунчера (разделы 3–7) |
| [`new_claude/template_run_gui.vbs`](../../new_claude/template_run_gui.vbs) | Шаблон VBS-раннера: pre-flight → `wslg-health` (ретраи) → самолечение → запуск |
| [`new_claude/wslg_health.sh`](../../new_claude/wslg_health.sh) | Детект WSLg COPY MODE (exit 0/10/11) |
| [`new_claude/add_vendor_c_gui_instance.py`](../../new_claude/add_vendor_c_gui_instance.py) | Иконка (HSV), лаунчер + `wslg-health` через `install`, VBS, BAT, ярлыки |
| [`new_claude/update_launchers.py`](../../new_claude/update_launchers.py) | Переразвёртывание инстансов 1 (1015) и 2 (1016) из шаблонов |
| [`new_claude/create_desktop_shortcut.ps1`](../../new_claude/create_desktop_shortcut.ps1) | `.lnk` в трёх каталогах + сброс кэша иконок |
| [`new_claude/verify_and_notify.py`](../../new_claude/verify_and_notify.py) | Pre-flight IP + тост |
| [`new_claude/init_wsl_keyring.sh`](../../new_claude/init_wsl_keyring.sh) | Keyring + self-test `secret-tool` (его вывод «safeStorage guaranteed TRUE» неверен при `--password-store=basic`, см. Gotcha 8) |
| [`new_claude/configure_wsl_hidpi_keyring.sh`](../../new_claude/configure_wsl_hidpi_keyring.sh), [`setup_helpers/09_*.ps1`](../../setup_helpers/09_configure_wsl_hidpi_keyring.ps1) | Keyring + HiDPI env (scale 1, `Xft.dpi` 96) |

Проверка шаблона после правок (рендер для инстанса 1 и побайтное сравнение с установленным лаунчером):
```bash
sed -e 's/{INSTANCE_ID}/1/g; s/{SOCKS_PORT}/1015/g; s/{HTTP_PORT}/11015/g; s/{WSL_USER}/ubuntu/g' \
    new_claude/template_claude_gui.sh | tr -d '\r' | cmp - /usr/local/bin/claude-gui-1
```

Открытые задачи:
1. Добавить `instanceIdleTimeout=-1` в провижининг `.wslconfig` (сейчас выставлен вручную).
2. Исправить вывод `init_wsl_keyring.sh` про `safeStorage` или перейти на `--password-store=gnome-libsecret` после проверки, что окно разблокировки не возвращается.

---

## 12. Верификация

1. `verify_and_notify.py --port 1015` возвращает `0`; тост зелёный.
2. Клик по ярлыку: окно появляется, заголовок в Windows без `[WARN:COPY MODE]`.
3. Два быстрых клика: в `launcher.log` строка `launcher already running (lock busy, holder N)`, `claude-desktop` один
   (проверено на 1015 и 1016).
4. Крестик, затем клик в течение 9 с: `reopen requested, shutting down instance for relaunch` → `lock acquired after waiting` → `window found`.
5. Закрыть окно и подождать больше 1 минуты: `wsl -l -v` показывает Running, в `weston.log` нет `rdp_allocate_shared_memory: Failed`.
6. `wsl --terminate Ubuntu-24.04` → клик по ярлыку: VBS выполняет `wsl --shutdown`, окно рисуется нормально.
7. Размер и позицию окна сохранить → закрыть → открыть: окно на прежнем месте (`window-geometry.conf`).
