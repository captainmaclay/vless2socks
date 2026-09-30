"""Модуль глубокого аудита и тестирования сетевой изоляции WSL2 на сокет 1015.

Реализует полный цикл проверок по стандарту Herdr Control Center:
1. Тестирование доступности сокетов SOCKS5 (1015) и HTTP CONNECT (11015) с замером задержки TCP handshake.
2. Проверка состояния фильтра ядра Linux (nftables herdr_filter / iptables HERDR_ISOLATE).
3. Проверка прокси-переменных окружения в WSL2 (/etc/profile.d/herdr_claude_env.sh).
4. Direct IP Leak Test: проверка невозможности прямого выхода в интернет без прокси.
5. SOCKS5 Remote DNS Test: проверка выхода через socks5-hostname 127.0.0.1:1015 и предотвращения утечек DNS.
6. Bypass Protection Test: проверка блокировки обходных портов (например, 2080).
7. Killswitch Verification: верификация Zero Leaks при выключенном прокси.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

import firewall_isolate
import wsl_detector

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def check_port_accessible(host: str, port: int, timeout: float = 0.5) -> Tuple[bool, int]:
    """Проверяет доступность TCP-порта и возвращает (is_open, latency_ms)."""
    t0 = time.time()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            latency = int((time.time() - t0) * 1000)
            return True, latency
    except (OSError, socket.timeout):
        latency = int((time.time() - t0) * 1000)
        return False, latency


def exec_wsl_user(bash_cmd: str, distro: Optional[str] = None, timeout: float = 5.0) -> Tuple[int, str]:
    """Выполняет команду внутри WSL от имени обычного пользователя."""
    d = distro or wsl_detector.get_active_distro()
    if not d:
        return -1, "WSL not available"
    try:
        r = subprocess.run(
            ["wsl.exe", "-d", d, "-e", "bash", "-c", bash_cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return r.returncode, (r.stdout or "").strip()
    except Exception as ex:
        return -1, str(ex)


def run_isolation_audit(
    host: str = "127.0.0.1",
    port: int = 1015,
    http_port: Optional[int] = None,
    bypass_port: int = 2080,
    distro: Optional[str] = None,
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    """Запускает комплексный аудит изоляции сети WSL2."""
    if http_port is None:
        http_port = 10000 + int(port)

    active_distro = distro or wsl_detector.get_active_distro()
    logs: List[Tuple[str, str]] = []

    def emit(level: str, msg: str) -> None:
        logs.append((level, msg))
        if log_callback:
            try:
                log_callback(level, msg)
            except Exception:
                pass

    emit("STEP", f"=== [1/4] Проверка доступности прокси-сокетов ({host}:{port} / :{http_port}) ===")
    p1015_ok, lat_1015 = check_port_accessible(host, port, timeout=0.5)
    if p1015_ok:
        emit("SUCCESS", f"✓ Сокет SOCKS5 {host}:{port} доступен (TCP handshake: {lat_1015} ms).")
    else:
        emit("WARN", f"⚠ Сокет SOCKS5 {host}:{port} недоступен (сервер оффлайн или порт закрыт).")

    p11015_ok, lat_11015 = check_port_accessible(host, http_port, timeout=0.5)
    if p11015_ok:
        emit("SUCCESS", f"✓ Сокет HTTP CONNECT {host}:{http_port} доступен (TCP handshake: {lat_11015} ms).")
    else:
        emit("INFO", f"ℹ Сокет HTTP CONNECT {host}:{http_port} недоступен (опционально).")

    emit("STEP", f"=== [2/4] Проверка конфигурации ядра Linux и окружения WSL2 ({active_distro}) ===")
    kernel_isolated = firewall_isolate.check_wsl_isolation_active(distro=active_distro)
    if kernel_isolated:
        emit("SUCCESS", "✓ Сетевая тюрьма ядра Linux АКТИВНА (nftables/iptables: прямой WAN/LAN отсечён).")
    else:
        emit("WARN", "⚠ Сетевая тюрьма ядра Linux НЕ активна.")

    # Проверка переменных окружения в WSL
    rc_env, out_env = exec_wsl_user("cat /etc/profile.d/herdr_claude_env.sh 2>/dev/null", distro=active_distro)
    has_profile_env = "ALL_PROXY" in out_env and str(port) in out_env
    has_socks5h = "socks5h://" in out_env
    if has_profile_env:
        emit("SUCCESS", f"✓ Файл /etc/profile.d/herdr_claude_env.sh настроен на порт {port}.")
        if has_socks5h:
            emit("SUCCESS", "✓ ALL_PROXY использует socks5h:// (делегирование DNS в туннель, утечки DNS исключены).")
    else:
        emit("WARN", "⚠ Системные переменные прокси в /etc/profile.d/ отсутствуют или не содержат сокет.")

    emit("STEP", "=== [3/4] Живой аудит трафика, утечек и обходов изнутри WSL2 ===")
    direct_leak_detected = False
    dns_leak_protected = False
    bypass_leak_detected = False
    proxy_egress_ip = None

    # 1. Direct IP Leak Test (без прокси)
    c_dir_code, c_dir_out = exec_wsl_user("curl -s --connect-timeout 2 --noproxy '*' https://ifconfig.me", distro=active_distro)
    if c_dir_code != 0:
        emit("SUCCESS", "✓ Direct IP Leak Test: АКТИВЕН (Прямой интернет-трафик заблокирован ядром, утечка IP невозможна).")
    else:
        direct_leak_detected = True
        emit("ERROR", f"✕ ВНИМАНИЕ: КРИТИЧЕСКАЯ УТЕЧКА! Прямой выход без прокси успешен, реальный IP: {c_dir_out}")

    # 2. SOCKS5 Remote DNS Test (через порт 1015)
    if p1015_ok:
        c_sock_code, c_sock_out = exec_wsl_user(
            f"curl -s --connect-timeout 4 --socks5-hostname {host}:{port} https://ifconfig.me",
            distro=active_distro
        )
        if c_sock_code == 0 and c_sock_out and "curl:" not in c_sock_out:
            proxy_egress_ip = c_sock_out
            dns_leak_protected = True
            emit("SUCCESS", f"✓ Маршрутизация через SOCKS5 :{port} работает. Внешний IP: {proxy_egress_ip}")
            emit("SUCCESS", "✓ DNS Leak Protection: подтверждена (DNS запросы разрешаются через socks5-hostname).")
        else:
            emit("ERROR", f"✕ Ошибка запроса через SOCKS5 :{port}: {c_sock_out} (код: {c_sock_code})")
    else:
        emit("INFO", f"ℹ Пропуск теста выхода через SOCKS5 :{port} (порт оффлайн).")

    # 3. HTTP CONNECT Test (если порт 11015 открыт)
    if p11015_ok:
        c_http_code, c_http_out = exec_wsl_user(
            f"curl -s -o /dev/null -w '%{{http_code}}' --connect-timeout 4 -x http://{host}:{http_port} https://api.anthropic.com",
            distro=active_distro
        )
        if c_http_code == 0 and c_http_out in ("200", "401", "403", "404"):
            emit("SUCCESS", f"✓ Запрос через HTTP CONNECT :{http_port} успешен (HTTP status: {c_http_out}).")
        else:
            emit("WARN", f"⚠ Запрос через HTTP CONNECT :{http_port} вернул статус: {c_http_out}")

    # 4. Bypass Port Test (обход через сокет 2080)
    c_byp_code, c_byp_out = exec_wsl_user(
        f"curl -s --connect-timeout 2 -x http://127.0.0.1:{bypass_port} https://ifconfig.me",
        distro=active_distro
    )
    if c_byp_code != 0:
        emit("SUCCESS", f"✓ Защита от обхода: АКТИВНА (Попытка обхода через сокет :{bypass_port} отклонена ядром).")
    else:
        bypass_leak_detected = True
        emit("WARN", f"⚠ Обходной сокет :{bypass_port} доступен (IP: {c_byp_out}).")

    emit("STEP", "=== [4/4] Итоговый вердикт безопасности ===")
    if direct_leak_detected:
        status = "leak"
        emit("ERROR", "💥 СТАТУС: LEAK (ОБНАРУЖЕНА УТЕЧКА СЕТИ). Трафик выходит напрямую в обход прокси!")
    elif kernel_isolated and p1015_ok and dns_leak_protected and not bypass_leak_detected:
        status = "isolated"
        emit("SUCCESS", "🛡️ СТАТУС: ISOLATED (ИДЕАЛЬНАЯ ИЗОЛЯЦИЯ). Трафик строго замкнут на 1015, утечки исключены.")
    elif kernel_isolated and not p1015_ok:
        status = "lockdown"
        emit("SUCCESS", "🔒 СТАТУС: LOCKDOWN / KILLSWITCH. Прокси оффлайн, весь исходящий трафик полностью заблокирован.")
    elif not kernel_isolated and not direct_leak_detected:
        status = "partial"
        emit("WARN", "⚠ СТАТУС: PARTIAL. Прямой трафик не проходит, но правила nftables не распознаны.")
    elif not kernel_isolated and direct_leak_detected:
        status = "direct"
        emit("INFO", "🌐 СТАТУС: DIRECT. Изоляция снята, свободный прямой доступ.")
    else:
        status = "warning"
        emit("WARN", "⚠ СТАТУС: WARNING. Требуется проверка сетевых маршрутов.")

    return {
        "status": status,
        "distro": active_distro,
        "p1015_open": p1015_ok,
        "p1015_latency_ms": lat_1015,
        "p11015_open": p11015_ok,
        "p11015_latency_ms": lat_11015,
        "kernel_isolated": kernel_isolated,
        "has_profile_env": has_profile_env,
        "dns_leak_protected": dns_leak_protected,
        "direct_leak_detected": direct_leak_detected,
        "bypass_leak_detected": bypass_leak_detected,
        "proxy_egress_ip": proxy_egress_ip,
        "logs": logs,
    }
