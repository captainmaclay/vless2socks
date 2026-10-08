"""Internationalization (i18n) module for vlesstosocks5.
Supports English ('en') as default and Russian ('ru').
"""

from __future__ import annotations

from typing import Any
import settings_manager

# Active language (default 'en')
_CURRENT_LANGUAGE: str = "en"

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        # App Window
        "app_title": "vless2socks — Multi Proxy Manager & SOCKS5 Tunnel Hub",
        "app_badge": "MULTI-SOCKS5",
        "count_summary": "Accounts: {total} | Active: {running}",

        # Top Navigation Tabs
        "nav_overview": "🏠 Overview",
        "nav_proxies": "🛡️ Proxies",
        "nav_options": "⚙️ Options",
        "nav_backup": "💾 Backup & Security",
        "nav_localization": "🌐 Localization",

        # Restart Services Split Button & Menu
        "btn_restart_services": "RestartServices",
        "restart_menu_title": "RestartServices Settings",
        "lbl_restart_wsl": "Restart WSL",
        "lbl_restart_work_proxy": "Restart WorkProxy",
        "lbl_restart_system_proxy": "Restart SystemProxy",
        "lbl_restart_telegram_proxy": "Restart TelegramProxy",
        "restart_menu_hint": "Checked services restart together; click a name to restart only it",
        "msg_restart_no_selection": "Please select at least one service to restart in the menu.",
        "msg_restart_in_progress": "Restarting...",
        "restart_hud_title": "RestartServices Monitor",
        "restart_hud_pinned": "Pinned",

        # Overview Tab
        "overview_title": "Active SOCKS5 Tunnels & Geo Overview",
        "overview_subtitle": "Real-time monitoring of all configured SOCKS5 proxies, ports, and external IP countries",
        "btn_start_all": "▶  Start All",
        "btn_stop_all": "⏹  Stop All",
        "btn_refresh_all_geo": "🔄 Refresh All Geo",
        "col_index": "#",
        "col_name": "Server / Remark",
        "col_socks5": "SOCKS5 Proxy",
        "col_http": "HTTP Bridge",
        "col_country": "Geo Zone / Country",
        "col_status": "Status",
        "col_actions": "Actions",
        "btn_view_tab": "✏️ Configure",
        "empty_overview": "No SOCKS5 proxies configured. Click '➕ Add Proxy' to create one.",
        "lbl_proxy_name": "Name:",
        "lbl_proxy_order": "Order:",
        "rename_proxy_title": "Rename Proxy",
        "rename_proxy_prompt": "Enter new name for proxy (port {port}):",
        "reorder_proxy_title": "Change Proxy Order",
        "reorder_proxy_prompt": "Enter numeric order for '{name}' (>= 0, e.g. 0, 1, 1.5):",
        "err_negative_order": "Order value cannot be negative (minimum value is 0).",
        "err_invalid_number": "Please enter a valid number (e.g. 0, 1, 1.5).",

        # Proxies Tab & Pagination
        "btn_new_tab": "  ＋  New Proxy  ",
        "pagination_prev": "◀ Previous",
        "pagination_next": "Next ▶",
        "pagination_info": "Page {page} of {pages} (Accounts {start}–{end} of {total})",
        "tab_default_name": "Proxy {index}",
        "status_stopped": "SOCKS5 — stopped",
        "status_starting": "SOCKS5 — starting...",
        "status_running": "Running: SOCKS5 :{port} | HTTP :{http_port}",
        "status_error": "SOCKS5 — error",
        "btn_on": "▶  Start",
        "btn_off": "⏹  Stop",
        "btn_free_port": "🔓 Free port",
        "lbl_host": "Host:",
        "lbl_port": "Port:",
        "lbl_killswitch": "Killswitch (IP Leak Protection)",
        "tip_killswitch": "Block all traffic if original IP leaks and prevent direct fallback",
        "ks_active": "ACTIVE",
        "ks_off": "OFF",
        "killswitch_leak_detected": "⚠️ KILLSWITCH ALERT: Original IP leak detected! Real IP ({real_ip}) matches proxy exit IP ({exit_ip}). Proxy was stopped immediately to prevent leaks.",
        "killswitch_verified_safe": "🛡️ Killswitch verified safe: Exit IP ({exit_ip}) differs from real IP ({real_ip}).",
        "btn_verify_leak": "🛡️ Verify Killswitch",
        "lbl_proxy_flags": "Flags / Roles:",
        "lbl_system_proxy": "System_Proxy",
        "lbl_work_proxy": "Work_Proxy",
        "lbl_telegram_proxy": "TelegramProxy",
        "tip_system_proxy": "Mark this proxy as System_Proxy (default port 1015)",
        "tip_work_proxy": "Mark this proxy as Work_Proxy (default port 1030 / corporate network)",
        "tip_telegram_proxy": "Mark this proxy for Telegram use (auto-rotation & TG link generation)",
        "badge_system_proxy": "⚙️ System_Proxy",
        "badge_work_proxy": "💼 Work_Proxy",
        "badge_telegram_proxy": "✈️ TelegramProxy",
        "btn_copy_tg_socks": "Copy TG_socks",
        "msg_tg_link_copied": "Telegram proxy link copied to clipboard:\n{url}",
        "tg_proxy_undefined": "undefined",
        "badge_tg_none": "TG: UNDEFINED",
        "lbl_force_restart": "Force-Restart",
        "tip_force_restart": "Automatically reconnect fallen or failed SOCKS5 and VLESS proxies",
        "lbl_protocol": "Protocol:",
        "proto_vless": "VLESS",
        "proto_socks5": "SOCKS5",
        "proto_wireguard": "WireGuard",
        "btn_edit_socks": "⚙️ Edit SOCKS5 Settings",
        "lbl_socks_summary": "SOCKS5 Upstream:",
        "socks_not_configured": "Not configured. Click 'Edit SOCKS5 Settings' to set up credentials.",
        "btn_edit_wireguard": "⚙️ Edit WireGuard Settings",
        "lbl_wireguard_summary": "WireGuard Upstream:",
        "wireguard_not_configured": "Not configured. Click 'Edit WireGuard Settings' or paste .conf / link.",
        "dlg_wireguard_title": "WireGuard Settings",
        "grp_wireguard_interface": "Interface (Local Client)",
        "grp_wireguard_peer": "Peer (Server)",
        "lbl_private_key": "Private Key",
        "lbl_local_ip": "Client IP",
        "lbl_peer_public_key": "Peer Public Key",
        "lbl_endpoint_address": "Endpoint IP/Host",
        "lbl_endpoint_port": "Endpoint Port",
        "lbl_preshared_key": "Pre-shared Key (Optional)",
        "lbl_mtu": "MTU",
        "lbl_dns": "DNS Servers (comma separated)",
        "lbl_allowed_ips": "Allowed IPs (Subnets)",
        "lbl_persistent_keepalive": "Persistent Keepalive (sec)",
        "btn_import_conf": "📁 Import .conf File",
        "btn_paste_conf": "📋 Paste raw .conf / link",
        "btn_export_conf": "📄 Copy as .conf",
        "msg_private_key_required": "Private key is required",
        "msg_public_key_required": "Peer public key is required",
        "msg_invalid_conf": "Invalid WireGuard configuration file",
        "dlg_socks_title": "Edit",
        "grp_common": "Common",
        "grp_socks": "Socks",
        "lbl_name": "Name",
        "lbl_address": "Address",
        "lbl_port_field": "Port",
        "lbl_version": "Version",
        "lbl_username": "Username",
        "lbl_password": "Password",
        "btn_advanced_settings": "Advanced Settings",
        "btn_ok": "OK",
        "btn_cancel": "Cancel",
        "msg_addr_required": "Address is required",
        "msg_port_required": "Valid Port is required (1-65535)",
        "msg_url_prefix_any": "URL must start with vless://, socks5:// or wireguard://",
        "lbl_vless_url": "VLESS URL:",
        "tip_toggle_mask": "Show / Hide VLESS URL",
        "tip_copy_url": "Copy VLESS URL to clipboard",
        "copied_toast": "Copied!",
        "lbl_geo_zone": "Geo Zone / Country:",
        "btn_check_geo": "🔍 Check IP & Geo",
        "geo_checking": "Detecting IP & Country...",
        "geo_not_checked": "Not checked yet (Start proxy or click 'Check')",
        "geo_verified": "{flag} {country} • IP: {ip}",
        "geo_offline_hint": "{flag} {country} (from URL tag) • Start proxy to verify exit IP",
        "btn_save_apply": "💾  Save & Restart",
        "lbl_log": "Connection Log:",

        # Options Tab
        "options_title": "Application Options & Startup Behavior",
        "options_subtitle": "Configure automated port takeover, launch behavior, and system tray integration",
        "opt_force_port_takeover_title": "Force Port Takeover (Reclaim Busy Ports)",
        "opt_force_port_takeover_desc": "If local SOCKS5 or HTTP ports are occupied, automatically terminate conflicting processes and reclaim ports before launch.",
        "opt_autostart_proxies_title": "Auto-Start Proxies on Launch",
        "opt_autostart_proxies_desc": "Automatically launch all configured SOCKS5 proxy tunnels when the application starts.",
        "opt_start_minimized_tray_title": "Start Minimized to System Tray",
        "opt_start_minimized_tray_desc": "Launch the application quietly into the system tray without opening the main window.",
        "opt_auto_reconnect_title": "Auto-Reconnect on Failure",
        "opt_auto_reconnect_desc": "If a proxy tunnel crashes or disconnects unexpectedly, automatically attempt to restart and reconnect.",
        "opt_reconnect_intervals_lbl": "Retry Intervals (seconds):",
        "opt_reconnect_intervals_desc": "Sequence of retry delays in seconds (default: 10, 15, 30, 60, 120, 180, 30). The last delay repeats indefinitely.",
        "btn_reset_intervals": "↺ Reset to Default",
        "opt_tg_proxy_port_title": "TelegramProxy Virtual Socket Port",
        "opt_tg_proxy_port_desc": "Local port listened by the TelegramProxy virtual hub. Telegram connects to this port, and traffic is dynamically balanced across active proxies marked with the TelegramProxy flag.",
        "opt_tg_proxy_port_lbl": "Listen Port:",
        "btn_reset_tg_port": "↺ Reset to 1373",
        "opt_saved_hint": "✓ Changes are saved automatically to settings.json and persist across restarts",

        # Port Management
        "port_invalid": "Invalid port number specified",
        "port_freeing": "Freeing ports {port} (SOCKS5) and {http_port} (HTTP)...",
        "port_freed": "Port {port} successfully freed (terminated processes: {count}).",
        "port_already_free": "Port {port} is already free.",
        "port_busy_wait": "Process on port {port} not found, but port is in TIME_WAIT state.",
        "port_fail_free": "Failed to free port {port}: {error}",

        # Validation & Notifications
        "msg_url_required": "Please enter a VLESS URL",
        "msg_url_prefix": "URL must start with vless://",
        "msg_cannot_close_last": "Cannot close the last proxy account",
        "msg_confirm_delete": "Are you sure you want to delete this proxy account?",
        "msg_confirm_delete_title": "Confirm Deletion",

        # Backup & Security Tab
        "backup_title": "Encrypted Backup & Security (AES-256-GCM)",
        "backup_subtitle": "Export encrypted snapshots, protect credentials with master password, or factory reset sensitive data",
        "master_pwd_title": "🔑 Master Encryption Password",
        "master_pwd_desc": "Used to derive AES-256-GCM key with PBKDF2-HMAC-SHA256 (600,000 rounds). Required to export/import backups.",
        "lbl_master_pwd": "Password:",
        "lbl_pwd_fingerprint": "SHA-256 Fingerprint:",
        "btn_save_pwd": "💾 Save Password",
        "pwd_saved_msg": "Master password saved successfully!",
        "pwd_empty_warning": "Please enter a password before saving",

        "lbl_backup_folder": "Backup Directory:",
        "btn_browse_folder": "📁 Browse...",
        "lbl_auto_backup": "Automatic Periodic Backup",
        "lbl_auto_backup_desc": "Automatically create encrypted snapshots every N hours in the background (requires saved master password).",
        "lbl_backup_interval": "Interval (hours):",
        "lbl_last_backup_time": "Last backup: {time}",
        "lbl_available_backups": "Available Snapshots in Folder:",
        "empty_backups_list": "No .hbak snapshots found in this folder. Click 'Export .hbak Snapshot' to create one.",
        "btn_restore_this": "⚡ Restore",
        "btn_refresh_backups": "🔄 Refresh List",
        "col_filename": "Snapshot File",
        "col_size": "Size",
        "col_modified": "Created / Modified",

        "export_title": "📦 Export Encrypted Snapshot (.hbak)",
        "export_desc": "Creates a single AES-256-GCM encrypted snapshot containing all instances, ports, URLs, and settings.",
        "btn_export_hbak": "Export .hbak Snapshot",
        "export_success": "Encrypted snapshot exported successfully:\n{path}",

        "restore_title": "📥 Restore from Encrypted Snapshot",
        "restore_desc": "Select a .hbak file and enter the master password to restore all proxies and configurations.",
        "btn_restore_hbak": "Select & Restore .hbak",
        "restore_success": "Restoration completed successfully! Loaded {count} proxy accounts.",
        "restore_bad_pwd": "Decryption failed. Invalid master password or corrupted file.",

        "danger_title": "⚠️ Danger Zone: Factory Reset",
        "danger_desc": "Terminates all running SOCKS5 processes and wipes all VLESS URLs, accounts, cached IPs, and saved passwords.",
        "btn_wipe_all": "🗑️ Wipe All Sensitive Data / Factory Reset",
        "danger_confirm_1": "Are you sure you want to reset all proxy configurations and delete all accounts?",
        "danger_confirm_2": "FINAL CONFIRMATION: Type 'DELETE' to confirm permanent wipe of all accounts and configurations:",
        "danger_wiped_msg": "All accounts and sensitive data have been wiped. Application reset to default state.",

        # Localization Tab
        "loc_title": "🌐 Language / Локализация",
        "loc_desc": "Select your preferred language. Changes apply immediately and persist across sessions.",
        "lang_en": "🇬🇧 English (Default)",
        "lang_ru": "🇷🇺 Русский (Исходный)",
        "btn_apply_lang": "Apply Language / Применить",

        # Single Instance Lock
        "already_running": "vless2socks is already running.",

        # WSL Isolation Guard
        "nav_wsl_isolation": "🛡️ WSL Isolation",
        "wsl_title": "WSL2 Network Isolation Guard (Socket :1015 Strict Restriction)",
        "wsl_subtitle": "Full Linux kernel network jail (nftables) locking WSL2 egress to SOCKS5/HTTP proxy with zero DNS leaks",
        "wsl_lbl_status": "WSL Status:",
        "wsl_lbl_kernel": "Kernel Firewall Jail:",
        "wsl_lbl_port": "Proxy SOCKS5 Port:",
        "wsl_lbl_port_status": "Port Reachability:",
        "wsl_lbl_dns": "DNS Delegation:",
        "wsl_lbl_leak": "Direct IP Leak:",
        "btn_wsl_apply": "🚀 Apply Isolation",
        "btn_wsl_test": "🧪 Run Leak & Audit Diagnostic",
        "btn_wsl_remove": "🌐 Remove Isolation (Direct IP)",
        "btn_wsl_refresh": "🔄 Refresh Status",
        "btn_wsl_clear_log": "Clear Log",
        "wsl_msg_applied": "WSL2 network isolation rules successfully applied!",
        "wsl_msg_removed": "WSL2 network isolation removed. Direct internet restored.",
        "wsl_msg_confirm_remove": "Are you sure you want to disable WSL2 network isolation? WSL2 will gain unrestricted direct internet access.",

        # Launchers & Shortcuts Modal & Settings
        "btn_launchers_guide": "🛠️ How to Create .bat & Shortcuts",
        "lbl_launchers_guide_title": "Launchers & Shortcuts Automation (.bat / .lnk / .ico)",
        "lbl_launchers_guide_desc": "Folder structure (C:\\MyFiles\\Proxy), isolated Chrome profiles, custom multi-res icons, Antigravity prompts and 1-click generator.",
        "dlg_launchers_title": "How to Create .bat Launchers & Shortcuts",
        "btn_quick_generate_launchers": "🚀 Generate / Update Workspace Now",
        "btn_copy_prompt": "📋 Copy",
        "btn_prompt_copied": "✅ Copied!",
        "btn_open_proxy_dir": "📁 Open C:\\MyFiles\\Proxy",
        "launchers_generated_success": "Successfully generated launchers, icons, and shortcuts in {path}!",
        "launchers_generating": "Generating launchers and multi-res icons...",

        # System Tray
        "tray_show": "Show Window",
        "tray_exit": "Exit",
        "tray_running": "vless2socks — {count} active",
        "tray_starting": "vless2socks — starting...",
        "tray_stopped": "vless2socks — stopped",
    },
    "ru": {
        # App Window
        "app_title": "vless2socks — Multi Proxy Manager & SOCKS5 Tunnel Hub",
        "app_badge": "МУЛЬТИ-SOCKS5",
        "count_summary": "Вкладок: {total} | Активных: {running}",

        # Top Navigation Tabs
        "nav_overview": "🏠 Обзор",
        "nav_proxies": "🛡️ Прокси",
        "nav_options": "⚙️ Опции",
        "nav_backup": "💾 Бэкап и безопасность",
        "nav_localization": "🌐 Локализация",

        # Restart Services Split Button & Menu
        "btn_restart_services": "RestartServices",
        "restart_menu_title": "Настройки RestartServices",
        "lbl_restart_wsl": "Restart WSL",
        "lbl_restart_work_proxy": "Restart WorkProxy",
        "lbl_restart_system_proxy": "Restart SystemProxy",
        "lbl_restart_telegram_proxy": "Restart TelegramProxy",
        "restart_menu_hint": "Галочки — общий перезапуск; клик по названию — только этот сервис",
        "msg_restart_no_selection": "Выберите хотя бы один сервис для перезапуска.",
        "msg_restart_in_progress": "Перезапуск...",
        "restart_hud_title": "Монитор RestartServices",
        "restart_hud_pinned": "Закреплено",

        # Overview Tab
        "overview_title": "Активные SOCKS5 туннели и гео-обзор",
        "overview_subtitle": "Мониторинг всех настроенных SOCKS5 прокси, портов и стран внешнего IP",
        "btn_start_all": "▶  Запустить все",
        "btn_stop_all": "⏹  Остановить все",
        "btn_refresh_all_geo": "🔄 Обновить все гео",
        "col_index": "№",
        "col_name": "Сервер / Описание",
        "col_socks5": "SOCKS5 прокси",
        "col_http": "HTTP мост",
        "col_country": "Гео-зона / Страна",
        "col_status": "Статус",
        "col_actions": "Действия",
        "btn_view_tab": "✏️ Настроить",
        "empty_overview": "Нет настроенных SOCKS5 прокси. Нажмите «＋ Новая вкладка» для добавления.",
        "lbl_proxy_name": "Имя:",
        "lbl_proxy_order": "Номер:",
        "rename_proxy_title": "Переименование прокси",
        "rename_proxy_prompt": "Введите новое название для прокси (порт {port}):",
        "reorder_proxy_title": "Изменение номера порядка",
        "reorder_proxy_prompt": "Введите цифровой номер для «{name}» (>= 0, например 0, 1, 1.5):",
        "err_negative_order": "Номер порядка не может быть отрицательным (минимум 0).",
        "err_invalid_number": "Пожалуйста, введите корректное число (например: 0, 1, 1.5).",

        # Proxies Tab & Pagination
        "btn_new_tab": "  ＋  Новая вкладка  ",
        "pagination_prev": "◀ Назад",
        "pagination_next": "Вперёд ▶",
        "pagination_info": "Страница {page} из {pages} (Аккаунты {start}–{end} из {total})",
        "tab_default_name": "Прокси {index}",
        "status_stopped": "SOCKS5 — остановлен",
        "status_starting": "SOCKS5 — запускается...",
        "status_running": "Работает: SOCKS5 :{port} | HTTP :{http_port}",
        "status_error": "SOCKS5 — ошибка",
        "btn_on": "▶  Старт",
        "btn_off": "⏹  Стоп",
        "btn_free_port": "🔓 Освободить порт",
        "lbl_host": "Host:",
        "lbl_port": "Port:",
        "lbl_killswitch": "Killswitch (Защита от утечки IP)",
        "tip_killswitch": "Блокировать соединения при утечке оригинального IP и запретить прямой доступ в обход туннеля",
        "ks_active": "АКТИВЕН",
        "ks_off": "ВЫКЛ",
        "killswitch_leak_detected": "⚠️ ТРЕВОГА KILLSWITCH: Обнаружена утечка оригинального IP! Реальный IP ({real_ip}) совпадает с IP прокси ({exit_ip}). Прокси немедленно остановлен.",
        "killswitch_verified_safe": "🛡️ Killswitch проверен: утечек нет (Внешний IP: {exit_ip} != Реальный: {real_ip}).",
        "btn_verify_leak": "🛡️ Проверить Killswitch",
        "lbl_proxy_flags": "Флаги / Роли:",
        "lbl_system_proxy": "System_Proxy",
        "lbl_work_proxy": "Work_Proxy",
        "lbl_telegram_proxy": "TelegramProxy",
        "tip_system_proxy": "Назначить этот прокси системным (System_Proxy, порт 1015)",
        "tip_work_proxy": "Назначить этот прокси рабочим (Work_Proxy, порт 1030 / корп. сеть)",
        "tip_telegram_proxy": "Использовать этот прокси для Telegram (авто-ротация и создание TG-ссылок)",
        "badge_system_proxy": "⚙️ System_Proxy",
        "badge_work_proxy": "💼 Work_Proxy",
        "badge_telegram_proxy": "✈️ TelegramProxy",
        "btn_copy_tg_socks": "Copy TG_socks",
        "msg_tg_link_copied": "Ссылка для Telegram скопирована в буфер обмена:\n{url}",
        "tg_proxy_undefined": "не выбрано",
        "badge_tg_none": "TG: НЕ ВЫБРАН",
        "lbl_force_restart": "Force-Restart",
        "tip_force_restart": "Автоматически перезапускать упавшие SOCKS5 и VLESS прокси",
        "lbl_protocol": "Протокол:",
        "proto_vless": "VLESS",
        "proto_socks5": "SOCKS5",
        "proto_wireguard": "WireGuard",
        "btn_edit_socks": "⚙️ Настроить SOCKS5",
        "lbl_socks_summary": "Исходящий SOCKS5:",
        "socks_not_configured": "Не настроен. Нажмите «Настроить SOCKS5» для ввода реквизитов.",
        "btn_edit_wireguard": "⚙️ Настроить WireGuard",
        "lbl_wireguard_summary": "Исходящий WireGuard:",
        "wireguard_not_configured": "Не настроен. Нажмите «Настроить WireGuard» для ввода реквизитов или импорта .conf.",
        "dlg_wireguard_title": "Параметры WireGuard",
        "grp_wireguard_interface": "Интерфейс (Клиент)",
        "grp_wireguard_peer": "Пир (Сервер WireGuard)",
        "lbl_private_key": "Приватный ключ (Private Key):",
        "lbl_local_ip": "Внутренний IP (Address):",
        "lbl_peer_public_key": "Публичный ключ сервера (PublicKey):",
        "lbl_endpoint_address": "Адрес сервера (Endpoint IP/Host):",
        "lbl_endpoint_port": "Порт сервера (Endpoint Port):",
        "lbl_preshared_key": "Pre-shared Key (Опционально):",
        "lbl_mtu": "MTU:",
        "lbl_dns": "DNS серверы (через запятую):",
        "lbl_allowed_ips": "Разрешённые подсети (AllowedIPs):",
        "lbl_persistent_keepalive": "Keepalive интервал (сек):",
        "btn_import_conf": "📁 Импорт .conf файла",
        "btn_paste_conf": "📋 Вставить raw .conf / ссылку",
        "btn_export_conf": "📄 Копировать как .conf",
        "msg_private_key_required": "Укажите приватный ключ (PrivateKey)",
        "msg_public_key_required": "Укажите публичный ключ сервера (PublicKey)",
        "msg_invalid_conf": "Некорректный файл конфигурации WireGuard",
        "dlg_socks_title": "Edit",
        "grp_common": "Common",
        "grp_socks": "Socks",
        "lbl_name": "Name",
        "lbl_address": "Address",
        "lbl_port_field": "Port",
        "lbl_version": "Version",
        "lbl_username": "Username",
        "lbl_password": "Password",
        "btn_advanced_settings": "Advanced Settings",
        "btn_ok": "OK",
        "btn_cancel": "Отмена",
        "msg_addr_required": "Введите адрес сервера (Address)",
        "msg_port_required": "Укажите корректный порт (1-65535)",
        "msg_url_prefix_any": "URL должен начинаться с vless://, socks5:// или wireguard://",
        "lbl_vless_url": "VLESS URL:",
        "tip_toggle_mask": "Показать / скрыть VLESS URL",
        "tip_copy_url": "Скопировать VLESS URL в буфер обмена",
        "copied_toast": "Скопировано!",
        "lbl_geo_zone": "Гео-зона / Страна:",
        "btn_check_geo": "🔍 Проверить IP и гео",
        "geo_checking": "Определение IP и страны...",
        "geo_not_checked": "Не проверено (Запустите прокси или нажмите «Проверить»)",
        "geo_verified": "{flag} {country} • IP: {ip}",
        "geo_offline_hint": "{flag} {country} (из тега URL) • Запустите прокси для проверки внешнего IP",
        "btn_save_apply": "💾  Сохранить и перезапустить",
        "lbl_log": "Лог подключения:",

        # Options Tab
        "options_title": "Параметры и поведение приложения",
        "options_subtitle": "Настройка автоматического перехвата портов, автозапуска и системного трея",
        "opt_force_port_takeover_title": "Принудительное подключение",
        "opt_force_port_takeover_desc": "Отнимаем порты, если они заняты. Принудительно очищаем порт, затем запускаем прокси.",
        "opt_autostart_proxies_title": "Запускать proxy при старте",
        "opt_autostart_proxies_desc": "Автоматически запускает все введенные прокси-туннели при запуске программы.",
        "opt_start_minimized_tray_title": "Запускать в Трее",
        "opt_start_minimized_tray_desc": "Запускать программу сразу свёрнутой в системный трей без открытия главного окна.",
        "opt_auto_reconnect_title": "Принудительное переподключение",
        "opt_auto_reconnect_desc": "Если сервер упал, автоматически пытается переподнять SOCKS5 туннель по циклу задержек.",
        "opt_reconnect_intervals_lbl": "Интервалы переподключения (секунды):",
        "opt_reconnect_intervals_desc": "Последовательность задержек в секундах (по умолчанию: 10, 15, 30, 60, 120, 180, 30). Последнее значение повторяется для всех следующих попыток.",
        "btn_reset_intervals": "↺ Сбросить по умолчанию",
        "opt_tg_proxy_port_title": "Порт виртуального сокета TelegramProxy",
        "opt_tg_proxy_port_desc": "Локальный порт, на который настраивается Telegram. Трафик бесшовно перенаправляется на рабочие прокси с флагом TelegramProxy с автоматической ротацией при сбоях.",
        "opt_tg_proxy_port_lbl": "Порт прослушивания:",
        "btn_reset_tg_port": "↺ Сбросить на 1373",
        "opt_saved_hint": "✓ Изменения сохраняются автоматически в settings.json и действуют после перезапуска",

        # Port Management
        "port_invalid": "Указан некорректный номер порта",
        "port_freeing": "Освобождение портов {port} (SOCKS5) и {http_port} (HTTP)...",
        "port_freed": "Порт {port} успешно освобождён (завершено процессов: {count}).",
        "port_already_free": "Порт {port} уже свободен.",
        "port_busy_wait": "Процесс на порту {port} не найден, но порт временно занят (TIME_WAIT).",
        "port_fail_free": "Не удалось освободить порт {port}: {error}",

        # Validation & Notifications
        "msg_url_required": "Введите VLESS URL",
        "msg_url_prefix": "URL должен начинаться с vless://",
        "msg_cannot_close_last": "Нельзя закрыть последнюю вкладку",
        "msg_confirm_delete": "Вы уверены, что хотите удалить эту вкладку прокси?",
        "msg_confirm_delete_title": "Подтверждение удаления",

        # Backup & Security Tab
        "backup_title": "Зашифрованный бэкап и безопасность (AES-256-GCM)",
        "backup_subtitle": "Экспорт зашифрованных снимков, защита мастер-паролем или заводской сброс чувствительных данных",
        "master_pwd_title": "🔑 Мастер-пароль шифрования",
        "master_pwd_desc": "Используется для генерации AES-256-GCM ключа через PBKDF2-HMAC-SHA256 (600 000 итераций). Нужен для бэкапов.",
        "lbl_master_pwd": "Пароль:",
        "lbl_pwd_fingerprint": "SHA-256 Отпечаток:",
        "btn_save_pwd": "💾 Сохранить пароль",
        "pwd_saved_msg": "Мастер-пароль успешно сохранён!",
        "pwd_empty_warning": "Введите пароль перед сохранением",

        "lbl_backup_folder": "Папка для бэкапов:",
        "btn_browse_folder": "📁 Обзор...",
        "lbl_auto_backup": "Автоматический бэкап",
        "lbl_auto_backup_desc": "Автоматически создавать зашифрованные снимки каждые N часов в фоновом режиме (требуется сохранённый пароль).",
        "lbl_backup_interval": "Интервал (часов):",
        "lbl_last_backup_time": "Последний бэкап: {time}",
        "lbl_available_backups": "Доступные снимки в папке:",
        "empty_backups_list": "В этой папке пока нет файлов .hbak. Нажмите 'Экспортировать снимок (.hbak)' для создания.",
        "btn_restore_this": "⚡ Восстановить",
        "btn_refresh_backups": "🔄 Обновить список",
        "col_filename": "Файл снимка",
        "col_size": "Размер",
        "col_modified": "Дата создания",

        "export_title": "📦 Экспорт зашифрованного снимка (.hbak)",
        "export_desc": "Создаёт единый AES-256-GCM зашифрованный снимок со всеми инстансами, портами, URL и настройками.",
        "btn_export_hbak": "Экспортировать снимок (.hbak)",
        "export_success": "Зашифрованный снимок успешно экспортирован:\n{path}",

        "restore_title": "📥 Восстановление из резервной копии",
        "restore_desc": "Выберите файл .hbak и введите мастер-пароль для восстановления всех прокси и конфигураций.",
        "btn_restore_hbak": "Выбрать и восстановить (.hbak)",
        "restore_success": "Восстановление успешно завершено! Загружено {count} прокси-аккаунтов.",
        "restore_bad_pwd": "Не удалось расшифровать файл. Неверный мастер-пароль или файл повреждён.",

        "danger_title": "⚠️ Опасная зона: Заводской сброс",
        "danger_desc": "Принудительно останавливает все процессы SOCKS5 и удаляет все VLESS ссылки, аккаунты, сохранённые пароли и историю.",
        "btn_wipe_all": "🗑️ Полная очистка / Заводской сброс",
        "danger_confirm_1": "Вы уверены, что хотите сбросить все настройки прокси и удалить все аккаунты?",
        "danger_confirm_2": "ФИНАЛЬНОЕ ПОДТВЕРЖДЕНИЕ: Введите слово 'DELETE' для безвозвратного удаления:",
        "danger_wiped_msg": "Все аккаунты и конфиденциальные данные успешно стёрты. Настройки сброшены.",

        # Localization Tab
        "loc_title": "🌐 Язык / Localization",
        "loc_desc": "Выберите язык интерфейса. Изменения применяются мгновенно и сохраняются между запусками.",
        "lang_en": "🇬🇧 English (По умолчанию)",
        "lang_ru": "🇷🇺 Русский (Исходный)",
        "btn_apply_lang": "Применить язык / Apply",

        # Single Instance Lock
        "already_running": "vless2socks уже запущена.",

        # WSL Isolation Guard
        "nav_wsl_isolation": "🛡️ WSL Изоляция",
        "wsl_title": "Сетевая изоляция WSL2 (Ограничение на сокет 1015)",
        "wsl_subtitle": "Сетевая тюрьма ядра Linux (nftables): изоляция исходящего трафика WSL2 на сокет прокси без утечек DNS",
        "wsl_lbl_status": "Статус WSL:",
        "wsl_lbl_kernel": "Тюрьма ядра Linux:",
        "wsl_lbl_port": "Порт SOCKS5 прокси:",
        "wsl_lbl_port_status": "Доступность сокета:",
        "wsl_lbl_dns": "Делегирование DNS:",
        "wsl_lbl_leak": "Утечка прямого IP:",
        "btn_wsl_apply": "🚀 Применить изоляцию",
        "btn_wsl_test": "🧪 Провести аудит и тест утечек",
        "btn_wsl_remove": "🌐 Снять изоляцию (Direct IP)",
        "btn_wsl_refresh": "🔄 Обновить статус",
        "btn_wsl_clear_log": "Очистить журнал",
        "wsl_msg_applied": "Правила сетевой изоляции WSL2 успешно применены!",
        "wsl_msg_removed": "Сетевая изоляция WSL2 снята. Прямой доступ к интернету восстановлен.",
        "wsl_msg_confirm_remove": "Вы уверены, что хотите снять сетевую изоляцию WSL2? Подсистема Linux получит прямой доступ в сеть.",

        # Launchers & Shortcuts Modal & Settings
        "btn_launchers_guide": "🛠️ Как создать батники и ярлыки",
        "lbl_launchers_guide_title": "Генератор батников и ярлыков (.bat / .lnk / .ico)",
        "lbl_launchers_guide_desc": "Иерархия папки C:\\MyFiles\\Proxy (bat, ico, ink), изоляция профилей Chrome, цветные иконки без искажений, промпты для Antigravity и генерация в 1 клик.",
        "dlg_launchers_title": "Как создать батники и ярлыки (.bat / .lnk)",
        "btn_quick_generate_launchers": "🚀 Сгенерировать / обновить всё сейчас",
        "btn_copy_prompt": "📋 Скопировать",
        "btn_prompt_copied": "✅ Скопировано!",
        "btn_open_proxy_dir": "📁 Открыть C:\\MyFiles\\Proxy",
        "launchers_generated_success": "Батники, иконки и ярлыки успешно созданы в {path}!",
        "launchers_generating": "Генерация батников и многослойных иконок...",

        # System Tray
        "tray_show": "Показать окно",
        "tray_exit": "Выход",
        "tray_running": "vless2socks — {count} работает",
        "tray_starting": "vless2socks — запускается...",
        "tray_stopped": "vless2socks — остановлен",
    },
}


def load_language_preference() -> str:
    """Load saved language from settings, default to 'en'."""
    global _CURRENT_LANGUAGE
    lang = settings_manager.get_setting("language", "en")
    if lang in TRANSLATIONS:
        _CURRENT_LANGUAGE = lang
        return lang
    _CURRENT_LANGUAGE = "en"
    return "en"


def save_language_preference(lang: str) -> None:
    """Save selected language to settings."""
    global _CURRENT_LANGUAGE
    if lang not in TRANSLATIONS:
        lang = "en"
    _CURRENT_LANGUAGE = lang
    settings_manager.set_setting("language", lang)


def get_current_language() -> str:
    """Return currently active language code."""
    return _CURRENT_LANGUAGE


def set_language(lang: str) -> None:
    """Set and persist language."""
    save_language_preference(lang)


def t(key: str, **kwargs: Any) -> str:
    """Get localized string by key."""
    lang_dict = TRANSLATIONS.get(_CURRENT_LANGUAGE, TRANSLATIONS["en"])
    val = lang_dict.get(key)
    if val is None:
        val = TRANSLATIONS["en"].get(key, key)
    if kwargs:
        try:
            return val.format(**kwargs)
        except Exception:
            return val
    return val


# Initialize on import
load_language_preference()
