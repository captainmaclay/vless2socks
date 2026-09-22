<p align="center">
  <img src="https://img.shields.io/badge/OS-Windows_11-0078D6?logo=windows&logoColor=white" alt="OS" />
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/SOCKS5-Multi--Proxy-4CAF50?logo=openvpn&logoColor=white" alt="SOCKS5" />
  <img src="https://img.shields.io/badge/VLESS-Xray--Core-FF6F00?logo=v&logoColor=white" alt="VLESS" />
  <img src="https://img.shields.io/badge/Encryption-AES--256--GCM-2E7D32?logo=gnuprivacyguard&logoColor=white" alt="AES-256-GCM" />
  <img src="https://img.shields.io/badge/License-MIT-blue" alt="License" />
</p>

<h1 align="center">vless2socks</h1>

<p align="center">
  <b>Multi-Proxy SOCKS5 Manager & VLESS Tunnel Hub for Windows</b><br>
  <sub>Professional GUI for managing unlimited SOCKS5 proxies over VLESS (TCP/TLS/REALITY) with Xray-core backend</sub>
</p>

![screenshot](https://i.imgur.com/LReRDxM.png)

---

<!-- ENGLISH SECTION -->
# 🇬🇧 English

## What is vless2socks?

**vless2socks** is a Windows desktop application that converts VLESS proxy links into local SOCKS5 endpoints. It provides a professional Tkinter-based GUI for managing multiple proxy instances simultaneously — with real-time health monitoring, geo-IP country detection, AES-256-GCM encrypted backups, and full bilingual (EN/RU) localization.

### Key Capabilities

| Feature | Description |
|---------|-------------|
| **Multi-Proxy Management** | Unlimited SOCKS5 proxy instances with 10-per-page pagination |
| **Dual Backend Engine** | Pure Python SOCKS5 server or Xray-core (auto-detected) |
| **Geo-IP Detection** | Country detection from URL fragments + live exit IP lookup via SOCKS5 |
| **Hidden VLESS URLs** | Eye toggle (👁️ / 🙈) to show/hide sensitive VLESS links |
| **Encrypted Backups** | AES-256-GCM with PBKDF2 key derivation, `.hbak` format |
| **Auto-Reconnect** | Configurable retry intervals with exponential backoff |
| **Force Port Takeover** | Automatically kill processes occupying required ports |
| **System Tray** | Minimize to tray with status indicator icons |
| **Bilingual UI** | English (default) and Russian with instant hot-switch |
| **Factory Reset** | One-click wipe of all proxies, configs, and credentials |

## Installation

### Prerequisites

- **Windows 10/11** (x64)
- **Python 3.10+** with pip
- **Xray-core** binary (auto-downloaded on first run, or place manually in `bin/`)

### Quick Start

```bash
git clone https://github.com/captainmaclay/vless2socks.git
cd vless2socks
install.bat     :: .venv + dependencies + Xray-core + config templates
start.bat       :: launch the GUI
```

`install.bat` is idempotent — re-running it only fills in what is missing.
`start.bat` calls it by itself when `.venv` is absent, so a fresh clone needs
nothing but `start.bat`.

<details>
<summary>Manual setup, step by step</summary>

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m tools.get_xray
gui.bat
```
</details>

### Alternative: CLI Mode

```bash
# Single proxy from config file
python main.py -c config.json

# Single proxy from URL
python main.py --url "vless://UUID@host:443?security=tls&type=tcp&sni=host" -l 127.0.0.1:1080

# Run diagnostics
python main.py -c config.json --doctor

# Check tunnel
python main.py -c config.json --test

# Compare direct vs. tunnel IP
python main.py -c config.json --ip
```

## Building a Windows .exe

```bash
build.bat
```

Runs PyInstaller against `vless2socks.spec` and produces **two** executables plus
the xray-core files next to them:

```
dist/
├── vless2socks.exe        # GUI (windowed)
├── vless2socks-cli.exe    # console worker — one process per running proxy
└── bin/
    ├── xray.exe
    ├── geoip.dat
    └── geosite.dat
```

Both executables are required: the GUI runs every proxy as a separate process,
and a frozen build has no `python.exe` or `main.py` on disk to call. `xray.exe`
and its databases deliberately stay **outside** the exe — baking 65 MB into a
onefile build would re-extract them to a temp folder on every launch.

`config.json`, `instances.json` and `settings.json` are created automatically on
first run from templates bundled inside the exe, so `dist/` ships as-is.

## GUI Overview

The application has **5 main tabs**:

### 🏠 Overview
Real-time dashboard showing all configured SOCKS5 proxies with their status, ports, country flags, and quick actions. Start/Stop All buttons and Refresh Geo for batch operations.

### 🛡️ Proxies
Paginated proxy management (10 per page). Each proxy card includes:
- VLESS URL field (hidden by default with 👁️ toggle)
- Host / Port configuration with auto-free-port finder
- Start / Stop toggle with real-time health indicator (●)
- Live log viewer with auto-scroll
- Geo-IP country card with refresh button
- SOCKS5 authentication (username/password)
- Backend engine selection (Auto / Python / Xray)

### ⚙️ Options
Persistent settings saved to `settings.json`:
- **Force Port Takeover** — kill processes occupying required ports (default: ON)
- **Auto-Start Proxies on Launch** — start all proxies when app opens (default: ON)
- **Start Minimized to Tray** — launch directly to system tray (default: OFF)
- **Auto-Reconnect on Failure** — automatic retry with configurable intervals (default: ON)
  - Default cycle: `10s → 15s → 30s → 1m → 2m → 3m → 30s (repeat)`

### 💾 Backup & Security
- **Master Password** with SHA-256 fingerprint display
- **Create Encrypted Backup** — exports `.hbak` file (AES-256-GCM)
- **Restore from Backup** — decrypt and restore all configurations
- **Backup Directory** — configurable folder with auto-scan of available snapshots
- **Auto-Backup** — periodic background backups with configurable interval
- **Factory Reset** — wipe all instances, configs, and credentials

### 🌐 Localization
Switch between English and Russian with one click. All labels, buttons, and messages update instantly without restart.

## Configuration Files

| File | Purpose |
|------|---------|
| `config.json` | Single-instance CLI config (created by `--init-config`) |
| `instances.json` | Multi-proxy GUI config (array of proxy objects) |
| `settings.json` | App preferences (language, options, backup settings) |
| `.env` | Master backup password storage |
| `config.example.json` | Template with placeholder values |

## Project Structure

```
vless2socks/
├── main.py                 # CLI entry point
├── gui.py                  # GUI application (VlessApp + ProxyInstance)
├── install.bat             # One-click setup: .venv, deps, Xray-core
├── start.bat               # One-click launch (runs install.bat if needed)
├── gui.bat                 # GUI launcher script
├── build.bat               # Release build: both .exe + xray-core
├── vless2socks.spec        # PyInstaller spec (GUI + CLI targets)
├── tray_widget.py          # Standalone system tray widget
├── i18n.py                 # Bilingual translations (EN/RU)
├── settings_manager.py     # Persistent settings (settings.json)
├── backup_manager.py       # AES-256-GCM encrypted backup system
├── geo_ip.py               # Country detection & live geo lookup
├── requirements.txt        # Python dependencies
├── config.example.json     # Config template
├── run.bat                 # CLI launcher
├── check.bat               # Tunnel check launcher
├── checkip.bat             # IP comparison launcher
├── doctor.bat              # Diagnostics launcher
├── get_xray.bat            # Xray-core downloader
├── tray.bat                # Tray widget launcher
│
├── vless2socks/            # Core library package
│   ├── __init__.py
│   ├── paths.py            # App directories (script / frozen .exe)
│   ├── config.py           # Configuration parser & AppConfig
│   ├── url.py              # VLESS URL parser
│   ├── backend.py          # Backend engine resolver (Python / Xray)
│   ├── socks5.py           # Pure Python SOCKS5 server
│   ├── socks_client.py     # SOCKS5 client for testing
│   ├── protocol.py         # VLESS protocol implementation
│   ├── vless.py            # VLESS connection handler
│   ├── transport.py        # TLS/TCP transport layer
│   ├── relay.py            # Bidirectional data relay
│   ├── doctor.py           # Step-by-step diagnostics
│   ├── selftest.py         # Tunnel connectivity test
│   ├── ipcheck.py          # Direct vs. tunnel IP comparison
│   ├── logging_setup.py    # Console logging configuration
│   └── xray/               # Xray-core integration
│       ├── __init__.py
│       ├── binary.py       # Xray binary discovery
│       ├── config_builder.py # Xray JSON config generator
│       └── runner.py       # Xray process manager
│
├── tools/                  # Utilities
│   └── get_xray.py         # Xray-core auto-downloader
│
├── bin/                    # Xray-core binary (auto-downloaded)
└── runtime/                # Runtime data
```

## Dependencies

```
cryptography>=41.0.0    # AES-256-GCM encryption for backups
pystray>=0.19.5         # System tray icon
Pillow>=10.0.0          # Tray icon image generation
```

Python standard library modules used: `tkinter`, `asyncio`, `subprocess`, `socket`, `json`, `threading`, `hashlib`, `zipfile`, `io`, `time`, `pathlib`, `argparse`, `signal`, `os`, `sys`, `re`, `urllib.parse`.

## Encryption Details

| Parameter | Value |
|-----------|-------|
| Algorithm | AES-256-GCM (Authenticated Encryption) |
| Key Derivation | PBKDF2-HMAC-SHA256 |
| Salt | 16 bytes (random) |
| Nonce | 12 bytes (random) |
| Key Length | 256 bits |
| PBKDF2 Iterations | 600,000 |
| File Format | `.hbak` with `HBAK\x01` magic header |
| Contents | ZIP archive (instances.json, config.json, settings.json, .env) |

## License

MIT License. See [LICENSE](LICENSE) for details.

---

<!-- RUSSIAN SECTION -->
# 🇷🇺 Русская документация

## Что такое vless2socks?

**vless2socks** — это десктопное приложение для Windows, которое преобразует VLESS-ссылки в локальные SOCKS5-прокси. Профессиональный GUI на Tkinter для одновременного управления множеством прокси-инстансов — с мониторингом состояния в реальном времени, определением страны по GeoIP, шифрованными бэкапами AES-256-GCM и двуязычным интерфейсом (EN/RU).

### Основные возможности

| Возможность | Описание |
|-------------|----------|
| **Мультипрокси** | Неограниченное количество SOCKS5-прокси с пагинацией по 10 на страницу |
| **Двойной движок** | Встроенный Python SOCKS5-сервер или Xray-core (автовыбор) |
| **Geo-IP** | Определение страны из URL-фрагмента + живой запрос внешнего IP через SOCKS5 |
| **Скрытие VLESS-ссылок** | Кнопка-глаз (👁️ / 🙈) для показа/скрытия конфиденциальных ссылок |
| **Шифрованные бэкапы** | AES-256-GCM с PBKDF2, формат `.hbak` |
| **Авто-переподключение** | Настраиваемые интервалы повтора с нарастающей задержкой |
| **Захват портов** | Автоматическое завершение процессов, занимающих нужные порты |
| **Системный трей** | Сворачивание в трей с иконкой-индикатором |
| **Локализация** | Английский (по умолчанию) и русский — мгновенное переключение |
| **Сброс настроек** | Полная очистка всех прокси, конфигов и учётных данных |

## Установка

### Требования

- **Windows 10/11** (x64)
- **Python 3.10+** с pip
- **Xray-core** (скачивается автоматически при первом запуске или вручную в `bin/`)

### Быстрый старт

```bash
git clone https://github.com/captainmaclay/vless2socks.git
cd vless2socks
install.bat     :: .venv + зависимости + Xray-core + шаблоны конфигов
start.bat       :: запуск GUI
```

`install.bat` можно запускать повторно — он доставляет только то, чего нет.
`start.bat` сам вызовет установку, если `.venv` отсутствует, так что свежему
клону достаточно одного `start.bat`.

<details>
<summary>Установка вручную, по шагам</summary>

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m tools.get_xray
gui.bat
```
</details>

### Режим командной строки (CLI)

```bash
# Один прокси из config.json
python main.py -c config.json

# Один прокси из VLESS-ссылки
python main.py --url "vless://UUID@host:443?security=tls&type=tcp&sni=host" -l 127.0.0.1:1080

# Диагностика
python main.py -c config.json --doctor

# Проверка туннеля
python main.py -c config.json --test

# Сравнение прямого и туннельного IP
python main.py -c config.json --ip
```

## Сборка .exe под Windows

```bash
build.bat
```

Запускает PyInstaller по `vless2socks.spec` и кладёт рядом **два** исполняемых
файла и xray-core:

```
dist/
├── vless2socks.exe        # GUI (без консоли)
├── vless2socks-cli.exe    # консольный рабочий процесс — по одному на прокси
└── bin/
    ├── xray.exe
    ├── geoip.dat
    └── geosite.dat
```

Нужны оба exe: GUI поднимает каждый прокси отдельным процессом, а внутри сборки
нет ни `python.exe`, ни `main.py` на диске. `xray.exe` и его базы намеренно лежат
**снаружи** exe — зашивать 65 МБ в onefile-сборку значит распаковывать их во
временную папку при каждом запуске.

`config.json`, `instances.json` и `settings.json` создаются сами при первом
запуске из шаблонов, зашитых в exe, — папку `dist/` можно отдавать как есть.

## Вкладки GUI

### 🏠 Обзор (Overview)
Панель мониторинга со всеми настроенными прокси: статус, порты, флаги стран, кнопки управления. Групповые операции «Запустить все» / «Остановить все».

### 🛡️ Прокси (Proxies)
Управление прокси с пагинацией (10 на страницу). Каждая карточка содержит:
- Поле VLESS URL (скрыто по умолчанию, кнопка 👁️)
- Хост / Порт с автоподбором свободного порта
- Кнопка Start/Stop с индикатором здоровья (●)
- Живой просмотр логов с автопрокруткой
- Карточка GeoIP с обновлением
- SOCKS5-аутентификация (логин/пароль)
- Выбор движка (Auto / Python / Xray)

### ⚙️ Опции (Options)
Постоянные настройки в `settings.json`:
- **Force Port Takeover** — принудительное освобождение портов (по умолчанию: ВКЛ)
- **Auto-Start Proxies on Launch** — запуск прокси при старте (по умолчанию: ВКЛ)
- **Start Minimized to Tray** — запуск в трее (по умолчанию: ВЫКЛ)
- **Auto-Reconnect on Failure** — автопереподключение (по умолчанию: ВКЛ)
  - Цикл по умолчанию: `10с → 15с → 30с → 1м → 2м → 3м → 30с (повтор)`

### 💾 Бэкап и Безопасность (Backup & Security)
- Мастер-пароль с отпечатком SHA-256
- Создание зашифрованного бэкапа `.hbak` (AES-256-GCM)
- Восстановление из бэкапа
- Выбор папки бэкапов с автосканированием доступных снимков
- Авто-бэкап с настраиваемым интервалом (в часах)
- Полный сброс (Factory Reset)

### 🌐 Локализация (Localization)
Переключение между английским и русским одним кликом. Все элементы обновляются мгновенно.

## Файлы конфигурации

| Файл | Назначение |
|------|-----------|
| `config.json` | Конфиг для одного прокси (CLI) |
| `instances.json` | Все прокси для GUI (массив объектов) |
| `settings.json` | Настройки приложения (язык, опции, бэкапы) |
| `.env` | Хранилище мастер-пароля бэкапов |
| `config.example.json` | Шаблон конфигурации |

## Шифрование

| Параметр | Значение |
|----------|----------|
| Алгоритм | AES-256-GCM (аутентифицированное шифрование) |
| Получение ключа | PBKDF2-HMAC-SHA256 |
| Соль | 16 байт (случайная) |
| Nonce | 12 байт (случайный) |
| Длина ключа | 256 бит |
| Итерации PBKDF2 | 600 000 |
| Формат файла | `.hbak` с магическим заголовком `HBAK\x01` |
| Содержимое | ZIP-архив (instances.json, config.json, settings.json, .env) |

## Лицензия

MIT License. Подробности в файле [LICENSE](LICENSE).
