"""
Низкоуровневые операции взаимодействия с Zapret 2.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

ZAPRET_DIR = r"C:\Zapret\Stable"
EXE_DIR = os.path.join(ZAPRET_DIR, "exe")
INTERNAL_DIR = os.path.join(ZAPRET_DIR, "_internal")
USER_DIR = os.path.join(ZAPRET_DIR, "user")

WINWS2_EXE = os.path.join(EXE_DIR, "winws2.exe")
ZAPRET_EXE = os.path.join(INTERNAL_DIR, "Zapret.exe")
DB_PATH = os.path.join(USER_DIR, "settings.sqlite3")
PRESETS_DIR = os.path.join(ZAPRET_DIR, "presets", "winws2_builtin")
CONFIG_TMP_DIR = os.path.join(USER_DIR, "tmp", "winws2_at_config")

DEFAULT_PRESET = "Default v1 (game filter).txt"
DEFAULT_TG_PORT = 1373
DEFAULT_TG_HOST = "127.0.0.1"


def get_process_pids(image_name: str) -> List[int]:
    """Вернуть список PID процессов по имени образа."""
    pids: List[int] = []
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {image_name}"],
            text=True,
            creationflags=flags,
            timeout=3.0,
        )
        for line in out.strip().splitlines():
            line = line.strip()
            if not line or "No tasks" in line or "не найдены" in line:
                continue
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                pids.append(int(parts[1]))
    except Exception:
        pass
    return pids


def is_winws2_running() -> bool:
    """Проверить, запущен ли процесс winws2.exe."""
    return len(get_process_pids("winws2.exe")) > 0


def is_endpoint_reachable(host: str, port: int, timeout: float = 0.5) -> bool:
    """Быстрая проверка доступности сокета TCP/SOCKS5 без вызова ошибок EOF."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            if s.connect_ex((host, int(port))) == 0:
                try:
                    s.settimeout(min(timeout, 0.3))
                    s.sendall(b"\x05\x01\x00")
                    _ = s.recv(2)
                except Exception:
                    pass
                return True
            return False
    except Exception:
        return False


def get_configured_telegram_proxies() -> List[Dict[str, Any]]:
    """Получить список сконфигурированных в vless2socks прокси с флагом TelegramProxy."""
    vless_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    instances_file = os.path.join(vless_root, "instances.json")
    if not os.path.exists(instances_file):
        return []
    try:
        with open(instances_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            res = []
            for inst in data:
                if inst.get("telegram_proxy") or inst.get("TelegramProxy"):
                    listen = inst.get("listen", "127.0.0.1:1081")
                    h, _, p_str = listen.rpartition(":")
                    h = h or "127.0.0.1"
                    try:
                        p = int(p_str or "1081")
                    except ValueError:
                        p = 1081
                    res.append({
                        "name": inst.get("name", f"Proxy-{p}"),
                        "host": h,
                        "port": p,
                        "raw": inst,
                    })
            return res
    except Exception:
        pass
    return []


def get_telegram_proxy_display_target(lang: str = "ru") -> str:
    """
    Формирует строку для отображения цели Telegram Proxy:
    - если ни один не выбран: 'не выбрано' (RU) / 'undefined' (EN)
    - если выбран: '127.0.0.1:1081' (или список)
    """
    proxies = get_configured_telegram_proxies()
    if not proxies:
        return "undefined" if lang.lower().startswith("en") else "не выбрано"
    if len(proxies) == 1:
        return f"{proxies[0]['host']}:{proxies[0]['port']}"
    return f"{proxies[0]['host']}:{proxies[0]['port']} (+{len(proxies)-1})"


def is_tg_proxy_running(host: str = DEFAULT_TG_HOST, port: int = DEFAULT_TG_PORT) -> bool:
    """
    Проверить, работает ли Telegram Proxy.
    Следим ТОЛЬКО за своими прокси во vless2socks, зафлаганными как TelegramProxy.
    Если ни один прокси не зафлаган - возвращает False.
    Если зафлаганы - проверяет, доступен ли виртуальный сокет диспетчера и хотя бы один апстрим.
    """
    try:
        from vless2socks import telegram_proxy
        actual_port = telegram_proxy.get_current_tg_port()
    except Exception:
        actual_port = port

    proxies = get_configured_telegram_proxies()
    if not proxies:
        return False

    # 1. Проверяем, слушает ли виртуальный порт TelegramProxy диспетчера (1373)
    if not is_endpoint_reachable(host, actual_port):
        return False

    # 2. Проверяем, доступен ли хотя бы один апстрим
    for p in proxies:
        if is_endpoint_reachable(p["host"], p["port"]):
            return True
    return False



def get_db_section(section_name: str) -> Dict[str, Any]:
    """Считать JSON-секцию из settings.sqlite3."""
    if not os.path.exists(DB_PATH):
        return {}
    try:
        con = sqlite3.connect(DB_PATH, timeout=5.0)
        cur = con.cursor()
        row = cur.execute("SELECT payload FROM settings_sections WHERE section=?", (section_name,)).fetchone()
        con.close()
        if row and row[0]:
            return json.loads(row[0])
    except Exception:
        pass
    return {}


def update_db_section(section_name: str, updates: Dict[str, Any]) -> bool:
    """Обновить JSON-секцию в settings.sqlite3."""
    if not os.path.exists(DB_PATH):
        return False
    try:
        con = sqlite3.connect(DB_PATH, timeout=10.0)
        with con:
            cur = con.cursor()
            row = cur.execute("SELECT payload FROM settings_sections WHERE section=?", (section_name,)).fetchone()
            current_data = json.loads(row[0]) if row and row[0] else {}
            current_data.update(updates)
            now_ms = int(time.time() * 1000)
            cur.execute(
                "UPDATE settings_sections SET payload=?, updated_at_ms=? WHERE section=?",
                (json.dumps(current_data, ensure_ascii=False), now_ms, section_name),
            )
            cur.execute("UPDATE settings_meta SET value=value+1 WHERE key='revision'")
        con.close()
        return True
    except Exception:
        return False


def prepare_winws2_config(preset_name: Optional[str] = None) -> Tuple[str, str]:
    """Подготовить чистый конфиг для winws2.exe без комментариев."""
    if not preset_name:
        prog = get_db_section("program")
        preset_name = prog.get("selected_source_preset_file_name_winws2") or DEFAULT_PRESET

    preset_path = os.path.join(PRESETS_DIR, preset_name)
    if not os.path.exists(preset_path):
        preset_name = DEFAULT_PRESET
        preset_path = os.path.join(PRESETS_DIR, preset_name)

    if not os.path.exists(preset_path):
        raise FileNotFoundError(f"Пресет {preset_name} не найден")

    with open(preset_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    clean_lines = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]

    os.makedirs(CONFIG_TMP_DIR, exist_ok=True)
    out_file = os.path.join(CONFIG_TMP_DIR, "winws2_active_config.txt")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n".join(clean_lines) + "\n")

    return out_file, preset_name


def start_winws2(preset_name: Optional[str] = None) -> bool:
    """Запустить основной модуль обхода winws2.exe."""
    if is_winws2_running():
        return True

    try:
        config_path, _ = prepare_winws2_config(preset_name)
        esc_exe = WINWS2_EXE.replace("'", "''")
        esc_cfg = config_path.replace("'", "''")
        esc_cwd = ZAPRET_DIR.replace("'", "''")

        ps_script = (
            f"Start-Process -FilePath '{esc_exe}' "
            f"-ArgumentList '@{esc_cfg}' "
            f"-WorkingDirectory '{esc_cwd}' "
            f"-WindowStyle Hidden -Verb RunAs"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, timeout=5.0)

        for _ in range(8):
            time.sleep(0.4)
            if is_winws2_running():
                return True
    except Exception:
        pass
    return is_winws2_running()


def stop_winws2() -> bool:
    """Остановить winws2.exe и службу Monkey."""
    if not is_winws2_running():
        return True
    try:
        cleanup_cmd = "taskkill /F /IM winws2.exe /T & sc.exe stop Monkey & sc.exe delete Monkey"
        ps_script = (
            f"Start-Process -FilePath cmd.exe "
            f"-ArgumentList '/c {cleanup_cmd}' "
            f"-WindowStyle Hidden -Verb RunAs -Wait"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, timeout=5.0)
        time.sleep(0.5)
    except Exception:
        pass
    return not is_winws2_running()


def start_tg_proxy() -> bool:
    """Запустить внутренний Telegram Proxy в vless2socks."""
    try:
        from vless2socks import telegram_proxy
        telegram_proxy.start_tg_dispatcher()
        for _ in range(10):
            if is_tg_proxy_running():
                return True
            time.sleep(0.1)
    except Exception:
        pass
    return is_tg_proxy_running()


def stop_tg_proxy() -> bool:
    """Остановить внутренний Telegram Proxy."""
    try:
        from vless2socks import telegram_proxy
        telegram_proxy.stop_tg_dispatcher()
        for _ in range(10):
            if not is_tg_proxy_running():
                return True
            time.sleep(0.1)
    except Exception:
        pass
    return not is_tg_proxy_running()


def restart_tg_proxy() -> bool:
    """Перезапустить внутренний Telegram Proxy и выполнить ротацию пула."""
    try:
        from vless2socks import telegram_proxy
        disp = telegram_proxy.get_tg_dispatcher()
        disp.rotate_to_next()
        if not disp.is_running():
            disp.start()
        return disp.is_running()
    except Exception:
        pass
    return is_tg_proxy_running()


def detect_tg_proxy_relay_error(*args, **kwargs) -> Tuple[bool, str]:
    """
    Проверка состояния внутреннего Telegram Proxy:
    Фиксирует отсутствие доступных рабочих прокси в пуле TelegramProxy.
    """
    try:
        from vless2socks import telegram_proxy
        disp = telegram_proxy.get_tg_dispatcher()
        if disp.is_running() and disp.get_pool():
            if not disp.get_active_proxy():
                return True, "Все прокси в пуле TelegramProxy недоступны"
    except Exception:
        pass
    return False, ""


def get_system_status() -> Dict[str, Any]:
    """Получить общий статус компонентов (Main winws2 и внутренний TG Proxy)."""
    has_relay_err, relay_err_msg = detect_tg_proxy_relay_error()
    return {
        "winws2_running": is_winws2_running(),
        "winws2_pids": get_process_pids("winws2.exe"),
        "tg_proxy_running": is_tg_proxy_running(),
        "tg_port": DEFAULT_TG_PORT,
        "relay_error": has_relay_err,
        "relay_error_msg": relay_err_msg,
    }

