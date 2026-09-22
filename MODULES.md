# vless2socks — Detailed Module Documentation

> Complete technical reference for every module, class, and function in the project.  
> Use this document to navigate the codebase quickly.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Entry Points](#entry-points)
3. [GUI Layer](#gui-layer)
4. [Support Modules](#support-modules)
5. [Core Library (vless2socks/)](#core-library-vless2socks)
6. [Xray Integration (vless2socks/xray/)](#xray-integration-vless2socksxray)
7. [Tools](#tools)
8. [Configuration Files](#configuration-files)
9. [Batch Launchers](#batch-launchers)
10. [Data Flow Diagrams](#data-flow-diagrams)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    User Interface                        │
│  ┌──────────┐   ┌──────────────┐   ┌──────────────┐    │
│  │  gui.py  │   │ tray_widget  │   │  main.py     │    │
│  │  (GUI)   │   │ (Tray Icon)  │   │  (CLI)       │    │
│  └────┬─────┘   └──────┬───────┘   └──────┬───────┘    │
│       │                │                   │            │
├───────┼────────────────┼───────────────────┼────────────┤
│       │         Support Modules            │            │
│  ┌────┴─────┐  ┌──────┴──────┐  ┌─────────┴────────┐  │
│  │settings  │  │backup_mgr   │  │  i18n.py         │  │
│  │_manager  │  │  (.hbak)    │  │  (EN/RU)         │  │
│  └──────────┘  └─────────────┘  └──────────────────┘  │
│                    │                                    │
│  ┌─────────────────┴────────────────────────────┐      │
│  │               geo_ip.py                       │      │
│  │  (Country detection & live lookup)            │      │
│  └───────────────────────────────────────────────┘      │
│                                                          │
├──────────────────────────────────────────────────────────┤
│                  Core Library (vless2socks/)              │
│  ┌────────┐ ┌───────┐ ┌──────────┐ ┌──────────────┐    │
│  │config  │ │ url   │ │ backend  │ │  socks5      │    │
│  │.py     │ │.py    │ │ .py      │ │  server      │    │
│  └────────┘ └───────┘ └──────────┘ └──────────────┘    │
│  ┌────────┐ ┌───────┐ ┌──────────┐ ┌──────────────┐    │
│  │protocol│ │vless  │ │transport │ │  relay       │    │
│  │.py     │ │.py    │ │.py       │ │  .py         │    │
│  └────────┘ └───────┘ └──────────┘ └──────────────┘    │
│  ┌────────┐ ┌───────┐ ┌──────────┐ ┌──────────────┐    │
│  │doctor  │ │self   │ │ipcheck   │ │socks_client  │    │
│  │.py     │ │test.py│ │.py       │ │.py           │    │
│  └────────┘ └───────┘ └──────────┘ └──────────────┘    │
│                                                          │
│  ┌──────────────── xray/ ──────────────────────────┐    │
│  │  binary.py  │ config_builder.py │  runner.py    │    │
│  └─────────────┴───────────────────┴───────────────┘    │
└──────────────────────────────────────────────────────────┘
```

---

## Entry Points

### `main.py` — CLI Entry Point

**Purpose**: Command-line interface for running a single SOCKS5 proxy over VLESS.

**Size**: ~420 lines

**Key Functions**:

| Function | Lines | Description |
|----------|-------|-------------|
| `build_parser()` | 33–101 | Builds `argparse.ArgumentParser` with all CLI flags |
| `main(argv)` | 357–416 | Main entry point: parses args, loads config, dispatches to handler |
| `run_server(config)` | 145–156 | Resolves backend (Python/Xray) and starts persistent SOCKS5 server |
| `_run_with_python(config)` | 159–189 | Starts pure Python SOCKS5 server with signal handling |
| `_run_with_xray(config, choice)` | 192–226 | Starts Xray-core subprocess with supervisor coroutine |
| `run_test(config, probe)` | 229–251 | Tests tunnel connectivity and exits |
| `run_ip_check(config, service)` | 305–345 | Compares direct IP vs. tunnel exit IP |
| `run_doctor(config, probe)` | 348–354 | Runs multi-step diagnostics (DNS, TCP, TLS, VLESS, HTTP, UDP) |
| `_parse_probe(value)` | 104–113 | Parses `HOST[:PORT][/PATH]` probe URL |
| `_write_template(path)` | 116–128 | Writes default `config.json` template |
| `running_proxy(config, choice)` | 276–302 | Async context manager: start proxy → yield → stop |

**CLI Flags**:
- `-c / --config FILE` — path to config.json
- `-u / --url VLESS_URL` — direct VLESS link
- `-l / --listen HOST:PORT` — SOCKS5 listen address
- `--username / --password` — SOCKS5 authentication
- `--backend auto|python|xray` — engine selection
- `--test` — test tunnel and exit
- `--doctor` — detailed step-by-step diagnostics
- `--ip` — direct vs. tunnel IP comparison
- `--init-config FILE` — write config template
- `--print-xray-config` — show generated Xray config (redacted)

---

## GUI Layer

### `gui.py` — Multi-Proxy GUI Application

**Purpose**: Full-featured Tkinter desktop application for managing unlimited SOCKS5 proxies.

**Size**: ~1954 lines

**Color Theme**: Catppuccin Mocha palette (dark theme)

#### Utility Functions (lines 78–217)

| Function | Description |
|----------|-------------|
| `is_port_free(port, host)` | Check if TCP port is available for binding |
| `is_port_alive(host, port, timeout)` | Check if something responds on a port |
| `find_free_port(start, host, exclude)` | Scan for first available port starting from `start` |
| `kill_processes_on_port(port, current_pid)` | Windows-specific: use `netstat` + `taskkill` to free a port |
| `extract_server_name(url)` | Extract human-readable name from VLESS URL (remark or hostname) |
| `load_base_config()` | Load `config.json` as template |
| `load_instances()` | Load array from `instances.json` |
| `save_instances(instances)` | Save array to `instances.json` |
| `make_tray_icon(color)` | Generate PIL icon image for system tray |
| `attach_clipboard_and_context_menu(widget)` | Enable Ctrl+C/V/X/A and right-click menu on Entry widgets (supports Russian keyboard layout) |

#### `ProxyInstance` Class (lines 332–880)

Each `ProxyInstance` manages one SOCKS5 tunnel subprocess.

**Constructor** (`__init__`):
- Receives: parent `VlessApp`, config dict, global instance ID
- Creates: per-instance config file (`.instance_N.json`)
- Initializes: geo info from URL, empty UI references, reconnect state

**Lifecycle Methods**:

| Method | Description |
|--------|-------------|
| `start()` | Write instance config → optionally force-free port → launch `main.py` subprocess → start reader and watcher threads |
| `stop()` | Set `_manual_stop`, cancel reconnect, terminate subprocess, update UI |
| `toggle()` | Start if stopped, stop if running |
| `destroy()` | Cancel reconnect, kill subprocess, delete temp config file |
| `close_instance()` | Confirm dialog → stop → remove from instances list → rebuild tabs |

**Health & Monitoring**:

| Method | Description |
|--------|-------------|
| `poll_health()` | Called every 3s from main poll loop; TCP-probe the port; on first healthy, trigger geo lookup; on unexpected death, schedule reconnect |
| `_reader(proc)` | Background thread reading subprocess stdout line by line into `log_lines` deque |
| `_watcher(proc)` | Background thread waiting for subprocess exit; if unexpected, schedule reconnect |

**Auto-Reconnect**:

| Method | Description |
|--------|-------------|
| `_schedule_reconnect()` | Calculate delay from `reconnect_intervals` list, schedule `_do_reconnect` via `after()` |
| `_do_reconnect()` | Called by timer; starts proxy if not manually stopped |
| `_cancel_reconnect()` | Cancel pending `after()` timer |

**Geo-IP**:

| Method | Description |
|--------|-------------|
| `_init_geo_from_url()` | Extract country hint offline from VLESS URL fragment |
| `check_geo_now()` | Launch background geo lookup through SOCKS5 proxy → update card |
| `_on_geo_result(data)` | Thread-safe callback to update geo labels in UI |

**UI Building**:

| Method | Description |
|--------|-------------|
| `build_tab_ui(parent)` | Create complete Tkinter frame: status bar, geo card, network fields, VLESS URL field with eye toggle, log viewer, advanced accordion |
| `_toggle_url_visibility()` | Show/hide VLESS URL characters |
| `_copy_url_to_clipboard()` | Copy VLESS URL to clipboard with feedback |
| `_apply_free_port()` | Find next free port and update port entry |

#### `VlessApp(tk.Tk)` Class (lines ~880–1954)

Main application window inheriting from `tk.Tk`.

**Initialization** (`__init__`):
1. Load language preference
2. Create window with title, size (1180×820), dark theme
3. Build header bar with app title, badge, and account count
4. Build 5 navigation tabs (Overview, Proxies, Options, Backup, Localization)
5. Load saved instances data
6. Create `ProxyInstance` objects
7. Build all tab UIs
8. Start polling loop (3-second interval)
9. Configure tray minimize behavior
10. Optionally auto-start proxies

**Tab Builders**:

| Method | Tab | Description |
|--------|-----|-------------|
| `_build_overview_tab()` | 🏠 Overview | Dashboard grid with proxy cards, Start All / Stop All / Refresh Geo buttons |
| `_build_proxies_tab()` | 🛡️ Proxies | Paginated ttk.Notebook with proxy tabs + "New Proxy" button + pagination controls |
| `_build_options_tab()` | ⚙️ Options | 4 toggle checkboxes + reconnect interval editor |
| `_build_backup_tab()` | 💾 Backup | Password field, fingerprint, create/restore buttons, backup dir selector, auto-backup toggle, factory reset |
| `_build_localization_tab()` | 🌐 Localization | Language radio buttons (EN/RU) |

**Core Methods**:

| Method | Description |
|--------|-------------|
| `_poll_loop()` | Every 3 seconds: poll all proxy health, update overview, check auto-backup |
| `start_all()` | Start every proxy instance sequentially |
| `stop_all()` | Stop every proxy instance |
| `refresh_overview()` | Rebuild overview dashboard grid |
| `refresh_current_page_tabs()` | Rebuild proxy tabs for current pagination page |
| `_update_header_stats()` | Update "Accounts: N | Active: M" counter |
| `_navigate_page(direction)` | Pagination: switch page and rebuild tabs |
| `_add_new_proxy()` | Add blank proxy instance with next free port |

**Backup Methods**:

| Method | Description |
|--------|-------------|
| `_do_create_backup()` | Encrypt and export `.hbak` file |
| `_do_restore_backup()` | File picker → decrypt → restore |
| `_restore_specific_backup(path)` | Decrypt specific `.hbak` → reload all data |
| `_render_available_backups()` | Scan backup dir, show snapshot rows with Restore buttons |
| `_choose_backup_dir()` | Folder picker → persist in settings → rescan |
| `check_and_run_auto_backup()` | Check if auto-backup interval elapsed → create backup |

**Tray Methods**:

| Method | Description |
|--------|-------------|
| `_setup_tray()` | Create pystray.Icon with menu (Show/Exit) |
| `_hide_to_tray()` | Withdraw window + show tray icon |
| `_show_from_tray()` | Destroy tray icon + deiconify window |

**Factory Reset**:

| Method | Description |
|--------|-------------|
| `_danger_wipe_all()` | Confirm dialog → wipe all data via `backup_manager.wipe_all_sensitive_data()` → reload UI |

---

### `tray_widget.py` — Standalone System Tray Widget

**Purpose**: Lightweight tray-only interface for running a single proxy from `config.json` without the full GUI.

**Size**: ~419 lines

**Classes**:

#### `LogWindow`
Tkinter popup for viewing proxy logs.
- `show()` — open log window in new thread
- `_refresh()` — reload text from `deque`
- `_clear()` — clear log buffer

#### `VlessTrayApp`
Main tray application.
- `run()` — create pystray menu and start health check thread
- `_start_proxy()` — launch `main.py` as subprocess
- `_stop_proxy()` — terminate subprocess (SIGTERM → SIGKILL after 5s)
- `_health_loop()` — TCP probe every 5 seconds
- `_read_output(proc)` — read subprocess stderr for log
- `_watch_process(proc)` — detect unexpected process exit

**Tray Icons**: 4 generated PIL images (green ✓, red ×, yellow …, red !)

---

## Support Modules

### `settings_manager.py` — Persistent Settings

**Purpose**: Single source of truth for all persistent application preferences.

**Size**: ~100 lines

**Storage**: `settings.json` (JSON file in project root)

**Default Settings**:

| Key | Default | Type | Description |
|-----|---------|------|-------------|
| `language` | `"en"` | str | UI language |
| `force_port_takeover` | `true` | bool | Kill processes on busy ports |
| `autostart_proxies` | `true` | bool | Start proxies on app launch |
| `start_minimized_tray` | `false` | bool | Launch to system tray |
| `auto_reconnect` | `true` | bool | Auto-retry failed proxies |
| `reconnect_intervals` | `"10, 15, 30, 60, 120, 180, 30"` | str | Retry delays in seconds |
| `auto_backup_enabled` | `false` | bool | Periodic auto-backup |
| `backup_interval_hours` | `24` | int | Hours between auto-backups |
| `backup_dir` | `~/Documents/VlessBackups` | str | Backup destination folder |
| `last_backup_time` | `"-"` | str | ISO timestamp of last backup |
| `backup_password` | `""` | str | Master encryption password |

**Functions**:

| Function | Description |
|----------|-------------|
| `load_settings()` | Read settings.json, merge with defaults |
| `save_settings(settings)` | Write full dict to settings.json |
| `get_setting(key, default)` | Get single value |
| `set_setting(key, value)` | Update and persist single value |
| `parse_reconnect_intervals(raw_val)` | Parse comma-separated string into list of positive ints |

---

### `i18n.py` — Internationalization

**Purpose**: Bilingual translation system (English default, Russian).

**Size**: ~353 lines

**Mechanism**: Dictionary-based lookup with `t(key, **kwargs)` function. Supports `str.format()` interpolation.

**Functions**:

| Function | Description |
|----------|-------------|
| `t(key, **kwargs)` | Translate key to current language, format with kwargs |
| `get_current_language()` | Return `"en"` or `"ru"` |
| `set_language(lang)` | Switch active language |
| `load_language_preference()` | Read language from settings_manager |
| `save_language_preference(lang)` | Persist language choice |

**Translation Coverage**: ~150 keys covering all UI labels, button texts, status messages, error messages, tooltips, and dialog prompts.

---

### `backup_manager.py` — Encrypted Backup System

**Purpose**: AES-256-GCM encrypted backup and restore of all configuration data.

**Size**: ~346 lines

**Encryption Flow**:
```
Password → PBKDF2(salt, 600K iters) → 256-bit Key
                                          ↓
instances.json + config.json + settings.json + .env
         ↓ ZIP compress
    plaintext bytes
         ↓ AES-256-GCM(key, nonce)
    HBAK\x01 | salt(16) | nonce(12) | ciphertext
         ↓
    vless2socks_backup_YYYYMMDD_HHMMSS.hbak
```

**Key Functions**:

| Function | Description |
|----------|-------------|
| `export_encrypted_backup(password, dest_dir)` | Create `.hbak` file: ZIP → encrypt → write |
| `restore_encrypted_backup(hbak_file, password)` | Read `.hbak` → decrypt → unzip → overwrite configs |
| `create_backup_archive()` | Pack config files into in-memory ZIP |
| `derive_key(password, salt)` | PBKDF2-HMAC-SHA256 key derivation |
| `compute_password_fingerprint(password)` | SHA-256 → first 16 hex chars formatted as `A1B2 C3D4 E5F6 7890` |
| `load_backup_password()` | Read from `.env` or settings.json |
| `save_backup_password(password)` | Write to `.env` and settings.json |
| `get_backup_dir()` | Get configured backup directory (create if missing) |
| `set_backup_dir(path_str)` | Persist backup directory choice |
| `list_backups_in_dir(target_dir)` | Scan folder for `.hbak` files → return sorted metadata list |
| `check_and_run_auto_backup()` | Check if interval elapsed → create backup if due |
| `wipe_all_sensitive_data(stop_all_callback)` | Factory reset: stop proxies, delete temp files, reset instances.json, clear password |

---

### `geo_ip.py` — Geo-IP & Country Detection

**Purpose**: Determine proxy exit country — offline from URL parsing and live through SOCKS5 tunnel.

**Size**: ~224 lines

**Two Detection Modes**:

1. **Offline** (`extract_country_hint(url)`) — parses VLESS URL:
   - Emoji flags in `#fragment` (e.g., `#🇫🇮 FINLAND 3 VLESS TCP`)
   - Country name words in fragment
   - Domain prefix matching (e.g., `fi.example.com` → Finland)

2. **Live** (`fetch_geo_via_socks(host, port)`) — connects through SOCKS5:
   - Primary API: `http://ip-api.com/json` → IP, country, city
   - Fallback API: `http://ipwho.is/`
   - 5-minute memory cache per `host:port`

**Functions**:

| Function | Description |
|----------|-------------|
| `extract_country_hint(url)` | Offline: parse country/flag/remark from VLESS URL |
| `fetch_geo_via_socks(host, port, timeout)` | Live: query geo API through SOCKS5 proxy |
| `fetch_geo_async(host, port, callback, fallback_url)` | Background thread wrapper with callback |
| `code_to_flag(code)` | Convert ISO 3166-1 alpha-2 → emoji flag |

**Domain Map**: Recognizes 21 country prefixes (fi, de, nl, us, uk, fr, se, ch, pl, at, it, es, tr, kz, ru, ua, sg, jp, etc.)

---

## Core Library (vless2socks/)

### `vless2socks/__init__.py`
Package marker with version string.

### `vless2socks/paths.py` — Application Directories

**Purpose**: Single source of truth for where the app's files live, identical for
`python gui.py` and for the frozen `.exe`.

**Size**: ~1,900 bytes

**Key Exports**:
- `APP_DIR` — writable working directory: next to the `.exe` when frozen, project
  root when run as a script. Holds `config.json`, `instances.json`,
  `settings.json`, `.env`, `bin/`, `runtime/`
- `BUNDLE_DIR` — read-only PyInstaller extraction dir (`sys._MEIPASS`); holds the
  bundled `*.example.json` templates
- `FROZEN` — whether we run from a PyInstaller build
- `bundled(name)` — path to a file baked into the build

**Why it exists**: PyInstaller's onefile mode unpacks the build into a temp
directory and points `__file__` there, so `Path(__file__).parent` does **not**
resolve next to the real `.exe`. Every module now imports `APP_DIR` from here
instead of computing its own root.

### `vless2socks/config.py` — Configuration Parser

**Purpose**: Parse CLI args and config.json into a unified `AppConfig` dataclass.

**Size**: ~5,500 bytes

**Key Exports**:
- `AppConfig` — dataclass holding all runtime configuration
- `load_config(path, **overrides)` — parse config file + CLI overrides
- `DEFAULT_CONFIG` — template dict for `--init-config`

### `vless2socks/url.py` — VLESS URL Parser

**Purpose**: Parse `vless://UUID@host:port?params#remark` URLs into structured config.

**Size**: ~12,500 bytes

**Handles**: `security` (tls/reality/none), `type` (tcp/ws/grpc), `flow` (xtls-rprx-vision), `sni`, `fp` (fingerprint), `pbk`/`sid` (reality), `path` (websocket), etc.

**Key Exports**:
- `parse_vless_url(url, strict=True)` — returns `ServerConfig`
- `ConfigError` — exception for invalid configs

### `vless2socks/backend.py` — Backend Engine Resolver

**Purpose**: Decide whether to use pure Python or Xray-core based on the VLESS profile.

**Size**: ~3,200 bytes

**Logic**:
- `auto` mode: use Python for basic TLS+TCP; use Xray for REALITY, XTLS, WebSocket, gRPC
- `python` mode: force pure Python (error if unsupported features)
- `xray` mode: force Xray-core (error if binary not found)

### `vless2socks/socks5.py` — Pure Python SOCKS5 Server

**Purpose**: Full SOCKS5 server implementation (RFC 1928) that tunnels through VLESS.

**Size**: ~19,400 bytes

**Supports**: CONNECT, BIND, UDP ASSOCIATE, username/password auth

### `vless2socks/socks_client.py` — SOCKS5 Client

**Purpose**: Client-side SOCKS5 connector for testing and geo-IP lookups.

**Size**: ~6,800 bytes

**Key Functions**:
- `http_get_via_socks5(host, port, ...)` — make HTTP GET through SOCKS5 proxy

### `vless2socks/protocol.py` — VLESS Protocol

**Purpose**: VLESS handshake and framing (command, UUID auth, address encoding).

**Size**: ~7,300 bytes

### `vless2socks/vless.py` — VLESS Connection Handler

**Purpose**: High-level VLESS connection: TLS handshake + VLESS protocol + data relay.

**Size**: ~6,700 bytes

### `vless2socks/transport.py` — Transport Layer

**Purpose**: TLS/TCP connection establishment with SNI, ALPN, and fingerprint support.

**Size**: ~3,600 bytes

### `vless2socks/relay.py` — Bidirectional Relay

**Purpose**: Bidirectional data copying between client and VLESS tunnel (asyncio streams).

**Size**: ~4,800 bytes

### `vless2socks/doctor.py` — Step-by-Step Diagnostics

**Purpose**: Comprehensive troubleshooting: parameters → DNS → TCP → TLS → VLESS handshake → HTTP → UDP.

**Size**: ~32,800 bytes

**Output**: Colorized step-by-step report with ✅/❌ per check.

### `vless2socks/selftest.py` — Tunnel Test

**Purpose**: Quick connectivity check through the tunnel.

**Size**: ~3,900 bytes

**Functions**:
- `check_tunnel(config, host, port, path)` — direct VLESS test
- `check_through_socks5(config, host, port, path)` — test via running SOCKS5 proxy

### `vless2socks/ipcheck.py` — IP Comparison

**Purpose**: Compare direct external IP vs. tunnel exit IP to verify tunneling works.

**Size**: ~10,100 bytes

### `vless2socks/logging_setup.py` — Logging Configuration

**Purpose**: Configure console output encoding (handle Windows cp866/cp1251 vs UTF-8).

**Size**: ~3,400 bytes

---

## Xray Integration (vless2socks/xray/)

### `xray/__init__.py`
Package exports: `XrayProcess`, `XrayStartupError`, `build_xray_config`, `XRAY`.

### `xray/binary.py` — Binary Discovery

**Purpose**: Locate xray-core executable in `bin/`, project root, or system PATH.

**Size**: ~2,900 bytes

### `xray/config_builder.py` — Config Generator

**Purpose**: Generate Xray JSON config from `AppConfig` with inbound SOCKS5 and outbound VLESS.

**Size**: ~11,400 bytes

**Supports**: Legacy vnext format (xray < v26) and modern format, REALITY settings, WebSocket, gRPC.

**Key Function**: `redact_config(cfg)` — remove UUIDs and passwords for display.

### `xray/runner.py` — Process Manager

**Purpose**: Start/stop/supervise Xray-core subprocess with automatic restart on crash.

**Size**: ~13,300 bytes

**Class `XrayProcess`**:
- `start()` — launch xray subprocess with generated config
- `stop()` — graceful terminate → force kill
- `supervise()` — coroutine that restarts xray on unexpected exit

---

## Tools

### `tools/get_xray.py` — Xray-Core Downloader

**Purpose**: Auto-download latest Xray-core release from GitHub to `bin/`.

**Size**: ~10,700 bytes

**Usage**: `python -m tools.get_xray`

---

## Configuration Files

### `config.json` — Single Proxy Config

```json
{
  "url": "vless://UUID@host:443?security=tls&type=tcp&sni=host#remark",
  "listen": "127.0.0.1:1080",
  "username": "",
  "password": "",
  "udp": true,
  "connectTimeout": 10,
  "udpIdleTimeout": 60,
  "logLevel": "info",
  "backend": "auto",
  "xrayPath": "",
  "xrayLegacyConfig": false
}
```

Used by CLI (`main.py -c config.json`) and standalone tray widget.

### `instances.json` — Multi-Proxy Config

Array of proxy config objects (same structure as config.json). Used by GUI to manage unlimited proxies.

```json
[
  { "url": "vless://...", "listen": "127.0.0.1:1080", ... },
  { "url": "vless://...", "listen": "127.0.0.1:1081", ... }
]
```

### `settings.json` — Application Preferences

```json
{
  "language": "en",
  "force_port_takeover": true,
  "autostart_proxies": true,
  "start_minimized_tray": false,
  "auto_reconnect": true,
  "reconnect_intervals": "10, 15, 30, 60, 120, 180, 30",
  "auto_backup_enabled": false,
  "backup_interval_hours": 24,
  "backup_dir": "C:\\Users\\...\\Documents\\VlessBackups",
  "last_backup_time": "-",
  "backup_password": ""
}
```

### `.env` — Password Storage

```env
BACKUP_PASSWORD="your_master_password_here"
```

### `config.example.json` — Template

Clean template with placeholder UUID for new users to fill in.

---

## Batch Launchers

| Script | Command | Description |
|--------|---------|-------------|
| `install.bat` | `.venv` + deps + Xray-core + config templates | First-time setup, idempotent |
| `start.bat` | `pythonw gui.py` (runs `install.bat` first if needed) | Recommended way to launch |
| `build.bat` | `pyinstaller vless2socks.spec` + copies `bin/` | Build both .exe into `dist/` |
| `gui.bat` | `pythonw gui.py` | Launch GUI (auto-installs pystray/Pillow if missing) |
| `run.bat` | `python main.py -c config.json` | Start CLI proxy (creates config template if missing) |
| `tray.bat` | `pythonw tray_widget.py` | Launch standalone tray widget |
| `check.bat` | `python main.py -c config.json --test` | Test tunnel connectivity |
| `checkip.bat` | `python main.py -c config.json --ip` | Direct vs. tunnel IP comparison |
| `doctor.bat` | `python main.py -c config.json --doctor` | Run full diagnostics |
| `get_xray.bat` | `python -m tools.get_xray` | Download Xray-core binary |

---

## Data Flow Diagrams

### Proxy Start Flow (GUI)

```
User clicks "▶ ON"
      │
      ▼
ProxyInstance.start()
      │
      ├── Write .instance_N.json (per-instance config)
      │
      ├── Force port takeover? ─yes─► kill_processes_on_port()
      │                               │
      │                               ▼
      │                          netstat -ano → taskkill /F /PID
      │
      ├── subprocess.Popen(proxy_command(...))
      │      script build : python main.py        -c .instance_N.json
      │      frozen  build: vless2socks-cli.exe   -c .instance_N.json
      │
      ├── Thread: _reader() → read stdout → append to log_lines deque
      │
      └── Thread: _watcher() → wait for process exit
              │
              ├── Normal exit → update UI (stopped)
              │
              └── Unexpected exit → _schedule_reconnect()
                      │
                      ▼
                  after(delay_ms) → _do_reconnect() → start()
```

### Backup Flow

```
User clicks "Create Backup"
      │
      ▼
export_encrypted_backup(password)
      │
      ├── create_backup_archive()
      │   └── ZIP(instances.json, config.json, settings.json, .env)
      │
      ├── os.urandom(16) → salt
      ├── os.urandom(12) → nonce
      ├── derive_key(password, salt) → PBKDF2 → 256-bit key
      │
      ├── AESGCM(key).encrypt(nonce, zip_bytes, MAGIC_HEADER)
      │
      └── Write: HBAK\x01 | salt | nonce | ciphertext
              → vless2socks_backup_YYYYMMDD_HHMMSS.hbak
```

### Geo-IP Detection Flow

```
Proxy becomes healthy (port responds)
      │
      ▼
check_geo_now() → fetch_geo_async(host, port, callback)
      │
      └── Background Thread:
              │
              ├── HTTP GET http://ip-api.com/json via SOCKS5
              │   └── Returns: { country, countryCode, query(IP), city }
              │
              ├── On failure: HTTP GET http://ipwho.is/ via SOCKS5
              │
              └── callback(result) → update geo_card_label in UI
```
