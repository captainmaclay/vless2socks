# Архитектура и руководство: Изолированные тестовые среды Vendor C Code CLI

> [!NOTE]
> **Исследовательские цели:**
> Данный материал и архитектурные решения подготовлены строго в исследовательских и образовательных целях для изучения механизмов изоляции процессов на платформе Windows, предотвращения утечек сетевого трафика, анализа разделения хранилища Windows Credential Manager и независимой маршрутизации CLI-приложений через локальные сокеты прокси.

В данном документе описана архитектура, механизм изоляции тестовых профилей и практическая реализация запуска нескольких одновременных изолированных экземпляров **Vendor C Code CLI** на Windows хосте с раздельным состоянием, независимой авторизацией и привязкой к индивидуальным портам прокси (SOCKS5 / HTTP).

---

## 1. Проблема стандартной конфигурации CLI

По умолчанию исполняемый файл CLI хранит все данные в едином домашнем каталоге пользователя:
- Конфигурация: `C:\Users\<user>\.claude.json`
- Сессии, история и проекты: `C:\Users\<user>\.claude\`
  - `history.jsonl` — история пользовательских команд.
  - `projects/` — локальные транскрипты и файлы сессий проектов.
  - `.credentials.json` — локальные сервисные токены.
- Хранилище учетных данных Windows (Credential Manager):
  - Ключ `Claude Code-credentials` — хранит активный OAuth2 refresh/access token.

При попытке использовать второй профиль в соседней консоли `cmd.exe`:
1. Команда авторизации перезаписывает глобальный ключ в Windows Credential Manager.
2. Первая консоль теряет авторизацию и переключается на второй профиль при следующем обновлении токена.
3. История команд и контексты проектов перемешиваются в одном каталоге проектов.

---

## 2. Архитектура полной изоляции (Zero Shared State)

### 2.1. Каталог конфигурации `CLAUDE_CONFIG_DIR`
CLI-утилита поддерживает переменную окружения `CLAUDE_CONFIG_DIR`:
```bat
set CLAUDE_CONFIG_DIR=C:\Users\79251\.claude-2
```
При ее установке:
1. Файл настроек размещается в `%CLAUDE_CONFIG_DIR%\.claude.json`.
2. Вся история, проекты и бэкапы изолируются в `%CLAUDE_CONFIG_DIR%\`:
   - `%CLAUDE_CONFIG_DIR%\projects\`
   - `%CLAUDE_CONFIG_DIR%\sessions\`
   - `%CLAUDE_CONFIG_DIR%\history.jsonl`

### 2.2. Изоляция в Windows Credential Manager
В архитектуре CLI предусмотрено разделение ключей хранилища учетных данных:
- Если `CLAUDE_CONFIG_DIR` **не задан** (основной инстанс): токен сохраняется под системным ключом `Claude Code-credentials`.
- Если `CLAUDE_CONFIG_DIR` **задан** (например, `.claude-2`): вычисляется 8-символьный SHA-256 хеш от пути каталога, и токен сохраняется под уникальным ключом `Claude Code-credentials-xxxxxxxx`.

**Результат:** Токены первого и второго профилей хранятся в Windows в разных ячейках и никогда не перезаписывают друг друга.

### 2.3. Сетевая изоляция через локальные сокеты
Каждый инстанс настраивается на свой SOCKS5 / HTTP прокси:
```bat
set HTTP_PROXY=http://127.0.0.1:1016
set HTTPS_PROXY=http://127.0.0.1:1016
set ALL_PROXY=http://127.0.0.1:1016
set http_proxy=http://127.0.0.1:1016
set https_proxy=http://127.0.0.1:1016
set all_proxy=http://127.0.0.1:1016
set NO_PROXY=
```
- **CLI 1 (`Vendor_C_Socket_1015.bat`):** SOCKS 1015 ──► Выходной IP: `<Primary_Node_IP>`.
- **CLI 2 (`Vendor_C_Socket_1016.bat`):** SOCKS 1016 ──► Выходной IP: `<Secondary_Node_IP>`.

### 2.4. Визуальная идентификация консолей
Каждый лаунчер задает собственный заголовок окна через команду `title`:
```bat
title Vendor C Code (Secondary - Socket 1016)
```
В панели задач Windows и заголовке окна сразу видно, в каком профиле и на каком порту выполняется сессия.

---

## 3. Эталонный файл лаунчера с защитой от утечек

Файл `C:\MyFiles\Proxy\Vendor_C_Socket_1016.bat`:
```bat
@echo off
chcp 65001 >nul
title Vendor C Code (Secondary - Socket 1016)

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
set http_proxy=http://127.0.0.1:1016
set https_proxy=http://127.0.0.1:1016
set all_proxy=http://127.0.0.1:1016
set NO_PROXY=

REM Isolated configuration directory for profile 2
set CLAUDE_CONFIG_DIR=C:\Users\79251\.claude-2
set CLAUDE_SECURESTORAGE_CONFIG_DIR=C:\Users\79251\.claude-2
if not exist "%CLAUDE_CONFIG_DIR%" mkdir "%CLAUDE_CONFIG_DIR%"

REM 3. Synchronize profile metadata with vless2socks
python "C:\MyFiles\vless2socks\vendor_c_sync.py" --port 1016 --quiet

cd /d "C:\Users\79251\Downloads\Claude_maclaren"
"C:\Users\79251\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe" --dangerously-skip-permissions %*

REM 4. Update status on exit in case session state changed
python "C:\MyFiles\vless2socks\vendor_c_sync.py" --port 1016 --quiet
```

---

## 4. Быстрое развертывание новых CLI инстансов

Для создания нового независимого профиля CLI используйте утилиту `add_vendor_c_cli_instance.py`:

```cmd
python C:\MyFiles\vless2socks\new_claude\add_vendor_c_cli_instance.py --id 3 --port 1020 --color purple
```

Параметры:
- `--id`: идентификатор инстанса (3, 4, tertiary, 1020).
- `--port`: порт локального SOCKS5 сокета (1020, 1030, 1081).
- `--color`: цветовая гамма иконки (`gold`, `purple`, `cyan`, `blue`, `green`, `red`, `orange`, `pink`, `black`).
- `--name`: опциональное отображаемое имя ярлыка.

Подробное пошаговое руководство: [`new_claude/howToCreate.md`](new_claude/howToCreate.md).
