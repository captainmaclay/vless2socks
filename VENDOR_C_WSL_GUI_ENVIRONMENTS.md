# Архитектура и руководство: Изолированные тестовые среды Vendor C Desktop GUI в WSL2

> [!NOTE]
> **Исследовательские цели:**
> Данный материал и архитектурные решения подготовлены исключительно в исследовательских и образовательных целях для изучения механизмов изоляции процессов в Linux (WSL2/WSLg), предотвращения утечек сетевого трафика, криптографических подсистем Chromium OSCrypt/GNOME Keyring и бесконфликтной интеграции с графической оболочкой Windows.

В данном документе описана архитектура и практическая реализация запуска нескольких одновременных изолированных экземпляров **Vendor C Desktop GUI (Electron / WSLg)** и **Vendor C Code CLI** с изолированными сессиями, раздельными профилями и привязкой к индивидуальным портам прокси (SOCKS5 / HTTP).

---

## 1. Проблема стандартной конфигурации Electron в WSL

При попытке запустить второй экземпляр Vendor C Desktop «из коробки» возникают четыре критических конфликта:

1. **Единый каталог профиля (`~/.config/Claude`) и SingletonLock:**
   Electron использует блокировочный файл `SingletonLock` и сокет IPC. При запуске второго экземпляра он находит блокировку, пытается передать фокус первому экземпляру и завершается (`exit 0`), не открывая новое окно.
2. **Тотальная очистка процессов (`killall -9`):**
   Ранние версии скрипта `/usr/local/bin/claude-gui` при закрытии окна вызывали `killall -9 claude-desktop`. В результате закрытие одного окна мгновенно убивало все остальные тестовые окна.
3. **Единый сессионный DBus и коллизия сокетов:**
   Вспомогательные демоны (`cowork-linux-helper`, crashpad) создают сокеты в `/run/user/<uid>/`. Несколько копий приложения перехватывали сокеты друг друга.
4. **Сетевой пайплайн WSL:**
   Глобальный профиль WSL (`/etc/profile.d/herdr_claude_env.sh`) направляет весь сетевой трафик в один базовый сокет (по умолчанию 1015 / HTTP 11015). Требовалось направить второй инстанс в сокет 1016, не нарушая работу всей остальной подсистемы WSL.

---

## 2. Архитектура полного разделения (Zero Shared State)

Каждый экземпляр Vendor C GUI `N` полностью изолируется по четырем измерениям:

### 2.1. Изоляция дискового пространства и профилей
- **Инстанс 1 (Основной):**
  - Каталог: `/home/79251/`
  - Профиль: `/home/79251/.config/Claude`
  - PID-файл: `/run/user/1000/claude-gui-primary.pid`
- **Инстанс N (Изолированный):**
  - Каталог: `HOME="/home/79251/.claude-N"`
  - Профиль: `USER_DATA_DIR="/home/79251/.claude-N/.config/Claude"`
  - PID-файл: `/run/user/1000/claude-N/claude-gui-N.pid`
  - Параметр Chromium: `--user-data-dir="$USER_DATA_DIR"`

### 2.2. Изоляция сокетов и интеграция с GNOME Keyring (Secret Service)
Для предотвращения коллизий сокетов и поддержки безопасного сохранения токенов:
1. **Файловые сокеты и PID-файлы:** Каждый инстанс получает изолированный `XDG_RUNTIME_DIR="/run/user/1000/claude-N"`, где создаются `claude-cowork-vm.sock` и локальные PID-файлы.
2. **GNOME Keyring & DBus Secret Service:**
   - Для сохранения учетных данных Chromium/Electron обращается к `safeStorage` (Secret Service `org.freedesktop.secrets`).
   - Лаунчер подключается к общесистемной сессионной шине пользователя `DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/1000/bus"`.
   - При старте автоматически инициализируется и разблокируется парольный брелок `login.keyring` (`gnome-keyring-daemon --daemonize --login --components=secrets`).
   - Флаг `--password-store=gnome-libsecret` обеспечивает доступность аппаратного/системного шифрования (`safeStorage.isEncryptionAvailable() == true`), предотвращая появление предупреждения о невозможности сохранить учетные данные.

### 2.3. Сетевая изоляция (Индивидуальные сокеты прокси)
Базовый системный файл `/etc/profile.d/herdr_claude_env.sh` остаётся на порту **1015** (трафик WSL по умолчанию).
Лаунчер инстанса `N` локально переопределяет переменные среды и передаёт флаг Chromium:
```bash
export HTTPS_PROXY='http://127.0.0.1:11016'
export HTTP_PROXY='http://127.0.0.1:11016'
export ALL_PROXY='socks5h://127.0.0.1:1016'
export https_proxy='http://127.0.0.1:11016'
export http_proxy='http://127.0.0.1:11016'
export all_proxy='socks5h://127.0.0.1:1016'

claude-desktop --no-sandbox --ozone-platform=x11 --password-store=gnome-libsecret \
  --proxy-server="socks5://127.0.0.1:1016" \
  --user-data-dir="$USER_DATA_DIR" "$@" &
```
Chromium сетевой стек (`NetworkService`) направляет все TCP-соединения строго в указанный сокет.

### 2.4. Безопасное управление процессами (PID-scoped process management)
Скрипт лаунчера оперирует исключительно своим деревом процессов:
- **Поиск активного окна:**
  ```bash
  xdotool search --onlyvisible --pid "$MAIN_PID"
  ```
  Если окно уже открыто — вызывается `xdotool windowactivate` и скрипт завершается.
- **Очистка при закрытии окна:**
  Фоновый цикл каждые 2 секунды опрашивает наличие окна. Когда пользователь закрывает окно крестиком `[X]`:
  ```bash
  kill -TERM "$MAIN_PID" 2>/dev/null
  sleep 1
  pkill -9 -P "$MAIN_PID" 2>/dev/null
  kill -9 "$MAIN_PID" 2>/dev/null
  pkill -9 -f "claude-desktop.*--user-data-dir=$USER_DATA_DIR" 2>/dev/null
  pkill -9 -f "chrome_crashpad_handler.*--database=$USER_DATA_DIR" 2>/dev/null
  rm -f "$USER_DATA_DIR/Singleton"* 2>/dev/null
  rm -f "$PID_FILE" 2>/dev/null
  ```
  Другие экземпляры Vendor C Desktop не затрагиваются.

---

## 3. Интеграция с Windows, Защита от Утечек и Оповещения Mac UI

1. **Бесшумный VBS-скрипт с верификацией IP (`C:\MyFiles\herdrCenter\run_claude_gui_N.vbs`):**
   ```vbscript
   Set WshShell = CreateObject("WScript.Shell")
   ' 1. Pre-launch проверка IP с выводом всплывающего уведомления в стиле macOS UI
   ret = WshShell.Run("pythonw ""C:\MyFiles\vless2socks\new_claude\verify_and_notify.py"" --port {SOCKS_PORT} --app ""Vendor C GUI""", 0, True)

   ' 2. Запуск приложения разрешается только при успешном прохождении проверки (ret = 0)
   If ret = 0 Then
       WshShell.Run "wsl.exe -d Ubuntu bash -lc ""/usr/local/bin/claude-gui-N""", 0, False
   End If
   ```
   Флаг `0, True` скрытно запускает проверку, ожидая её результат. Если проверка возвращает `1` (ошибка, несовпадение IP или утечка в 1015), приложение **не запускается**.

2. **Всплывающее окно в стиле macOS UI (`verify_and_notify.py`):**
   - **Позиционирование:** Нижний правый угол экрана над панелью задач.
   - **Дизайн:** Тёмная тема macOS Dark Mode (`#1e1e24`, закругленная граница `#3a3a46`, бейдж `GUI vless2socks`, системные шрифты Apple/Segoe UI Variable).
   - **Неблокирующий запуск при успехе:** Проверка IP занимает ~0.3–0.5 сек. При успехе всплывашка отображается в фоновом независимом процессе `pythonw.exe` на **4.0 секунды**, а скрипт мгновенно возвращает код `0`, запуская Vendor C GUI **без задержек**.
   - **Неуспех/Утечка:** Показывает полученный IP и ожидаемый IP красным цветом на **5.0 секунд** с плавным затуханием, код возврата `1` (запуск заблокирован).

3. **Multi-resolution иконки (`C:\MyFiles\Proxy\ico\`):**
   - Инстанс 1 (Основной 1015): оригинальный терракотовый `claude.ico`.
   - Инстанс 2 (Сокет 1016): золотой `claude_gold.ico`.
   - Инстанс 3 (Сокет 1020): фиолетовый `claude_purple.ico`.
   - Поддерживаются любые цвета через генератор `new_claude/add_vendor_c_gui_instance.py`.

4. **Нейминг ярлыков Windows:**
   - На Рабочем столе: `GUI_{port}.lnk` (например, `GUI_1015.lnk`, `GUI_1016.lnk`, `GUI_1020.lnk`).
   - В каталоге прокси: `C:\MyFiles\Proxy\ink\GUI_{port}.lnk`.
   - Объект: `C:\Windows\System32\wscript.exe "C:\MyFiles\herdrCenter\run_claude_gui_N.vbs"`.

---

## 4. Консольный эквивалент (Vendor C Code CLI)

Для консольного клиента Vendor C Code CLI изоляция достигается через переменную окружения `CLAUDE_CONFIG_DIR`:
```bat
@echo off
chcp 65001 >nul
title Vendor C Code (Instance 2 - Socket 1016)

REM 1. Pre-launch IP Verification with Mac UI floating notification
python "C:\MyFiles\vless2socks\new_claude\verify_and_notify.py" --port 1016 --app "Vendor C CLI"
if errorlevel 1 (
    echo [BLOCKED] IP verification failed. Launch aborted to prevent leak!
    timeout /t 3 >nul
    exit /b 1
)

REM 2. CLI strictly via socket 127.0.0.1:1016
set HTTP_PROXY=http://127.0.0.1:1016
set HTTPS_PROXY=http://127.0.0.1:1016
set ALL_PROXY=http://127.0.0.1:1016
set NO_PROXY=

set CLAUDE_CONFIG_DIR=C:\Users\79251\.claude-2
if not exist "%CLAUDE_CONFIG_DIR%" mkdir "%CLAUDE_CONFIG_DIR%"

python "C:\MyFiles\vless2socks\vendor_c_sync.py" --port 1016 --quiet

cd /d "C:\Users\79251\Downloads\Claude_maclaren"
"C:\Users\79251\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe" --dangerously-skip-permissions %*
```
Ярлыки CLI именуются по формату `CLI_{port}.lnk` (например, `CLI_1015.lnk`, `CLI_1016.lnk`).

---

## 5. Быстрое добавление новых экземпляров

Для развёртывания нового экземпляра (GUI + VBS + Bat + Иконка + Ярлык `GUI_{port}.lnk` на Рабочий стол) используйте утилиту в папке `new_claude`:

```cmd
python C:\MyFiles\vless2socks\new_claude\add_vendor_c_gui_instance.py --id 3 --port 1020 --color purple
```
Параметры:
- `--id`: номер или идентификатор инстанса (3, 4, 1020).
- `--port`: SOCKS5 порт локального прокси (1020, 1030, 1081).
- `--color`: цвет иконки (`gold`, `purple`, `cyan`, `blue`, `green`, `red`, `orange`, `pink`, `black`).
- `--name`: опциональное имя ярлыка (по умолчанию создается `GUI_{port}`).

Подробный путеводитель по созданию новых тестовых сред и карта скиллов: [`new_claude/howToCreate.md`](new_claude/howToCreate.md).
