"""Модуль управления сетевой изоляцией (Firewall Isolation Guard) в WSL2.

Архитектура основана на решении из HerdrControlCenter (node_isolate_manager.py):
1. Изоляция в ядре Linux (WSL2 netfilter):
   - nftables (первичный механизм) или iptables (fallback).
   - Разрешается ТОЛЬКО loopback трафик (lo, loopback0 в WSL2 mirrored network mode).
   - Весь не-loopback трафик (eth0, прямые IP, WAN, LAN) безусловно отсекается (counter reject).
   - Блокируются попытки обхода через сторонние локальные порты (например, 2080).
2. Безопасное проксирование без DNS-утечек:
   - Протокол socks5h:// для сокета 1015 (делегирование разрешения DNS на сторону прокси).
   - HTTP CONNECT для сокета 11015.
   - Фиксация в /etc/profile.d/herdr_claude_env.sh.
3. Персистентность автозагрузки:
   - Сохранение правил в /etc/nftables.conf.
   - Скрипт загрузки /usr/local/bin/herdr_boot_isolation.sh.
   - Секция [boot] в /etc/wsl.conf для гарантированной изоляции с 1-й секунды старта WSL2.
4. Полная свобода хоста Windows:
   - Правила брандмауэра Windows очищаются, предотвращая блокировку локальных процессов Windows.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from typing import List, Optional, Tuple

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

import wsl_detector

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def run_wsl_root_cmd(bash_cmd: str, distro: Optional[str] = None, timeout: float = 6.0) -> Tuple[int, str]:
    """Выполняет команду от имени root внутри WSL."""
    d = distro or wsl_detector.get_active_distro()
    if not d:
        return -1, "WSL distribution not found"

    try:
        r = subprocess.run(
            ["wsl.exe", "-d", d, "-u", "root", "-e", "bash", "-c", bash_cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        combined = f"{out}\n{err}".strip() if err else out
        return r.returncode, combined
    except Exception as ex:
        return -1, str(ex)


def check_wsl_isolation_active(distro: Optional[str] = None) -> bool:
    """Проверяет, активна ли сетевая тюрьма (nftables / iptables) в ядре Linux WSL2."""
    rc, out = run_wsl_root_cmd("nft list table inet herdr_filter 2>/dev/null", distro=distro)
    if rc == 0 and "chain output" in out:
        return True
    rc2, out2 = run_wsl_root_cmd("iptables -C OUTPUT -j HERDR_ISOLATE 2>/dev/null", distro=distro)
    return rc2 == 0


def remove_windows_firewall_rule() -> bool:
    """Гарантирует удаление блокирующих правил брандмауэра Windows для исключения влияния на хост."""
    ps_script = """
    $ErrorActionPreference = 'SilentlyContinue'
    Remove-NetFirewallRule -DisplayName 'Claude Node Isolate' | Out-Null
    """
    try:
        encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        return True
    except Exception:
        return False


def install_persistent_wsl_isolation(
    port: int = 1015,
    http_port: Optional[int] = None,
    block_bypass_ports: Optional[List[int]] = None,
    distro: Optional[str] = None,
) -> bool:
    """Устанавливает постоянную (persistent) автозагрузку сетевой изоляции в WSL2."""
    if http_port is None:
        http_port = 10000 + int(port)
    if block_bypass_ports is None:
        block_bypass_ports = [2080]

    bypass_reject_rules = []
    for bp in block_bypass_ports:
        if bp != port and bp != http_port:
            bypass_reject_rules.append(f"        ip daddr 127.0.0.1 tcp dport {bp} counter reject")
    bypass_rules_str = "\n".join(bypass_reject_rules)

    nft_conf_content = f"""#!/usr/sbin/nft -f

flush ruleset

table inet herdr_filter {{
    chain output {{
        type filter hook output priority filter; policy accept;
{bypass_rules_str}
        oifname {{ "lo", "loopback0" }} counter accept
        counter reject
    }}
}}
"""

    boot_script_content = f"""#!/bin/bash
# Herdr WSL2 Autonomous Boot Containment Guard
nft -f /etc/nftables.conf 2>/dev/null || true

cat << 'HERDR_ENV' > /etc/profile.d/herdr_claude_env.sh
export HTTPS_PROXY='http://127.0.0.1:{http_port}'
export HTTP_PROXY='http://127.0.0.1:{http_port}'
export ALL_PROXY='socks5h://127.0.0.1:{port}'
export https_proxy='http://127.0.0.1:{http_port}'
export http_proxy='http://127.0.0.1:{http_port}'
export all_proxy='socks5h://127.0.0.1:{port}'
HERDR_ENV
chmod 644 /etc/profile.d/herdr_claude_env.sh
"""

    b64_nft = base64.b64encode(nft_conf_content.encode("utf-8")).decode("ascii")
    b64_boot = base64.b64encode(boot_script_content.encode("utf-8")).decode("ascii")

    installer_script = f"""
echo {b64_nft} | base64 -d > /etc/nftables.conf
chmod 755 /etc/nftables.conf
echo {b64_boot} | base64 -d > /usr/local/bin/herdr_boot_isolation.sh
chmod 755 /usr/local/bin/herdr_boot_isolation.sh

if [ -f /etc/wsl.conf ]; then
    if ! grep -q 'herdr_boot_isolation.sh' /etc/wsl.conf; then
        if grep -q '\\[boot\\]' /etc/wsl.conf; then
            sed -i '/\\[boot\\]/a command=/usr/local/bin/herdr_boot_isolation.sh' /etc/wsl.conf
        else
            echo -e "\\n[boot]\\nsystemd=true\\ncommand=/usr/local/bin/herdr_boot_isolation.sh" >> /etc/wsl.conf
        fi
    fi
else
    echo -e "[boot]\\nsystemd=true\\ncommand=/usr/local/bin/herdr_boot_isolation.sh\\n" > /etc/wsl.conf
fi

systemctl enable nftables 2>/dev/null || true
"""
    rc, _ = run_wsl_root_cmd(installer_script, distro=distro)
    return rc == 0


def apply_wsl_isolation(
    port: int = 1015,
    http_port: Optional[int] = None,
    block_bypass_ports: Optional[List[int]] = None,
    killswitch: bool = False,
    distro: Optional[str] = None,
    make_persistent: bool = True,
) -> bool:
    """Применяет строгую изоляцию ядра Linux в WSL2 на указанный сокет."""
    if http_port is None:
        http_port = 10000 + int(port)
    if block_bypass_ports is None:
        block_bypass_ports = [2080]

    # Команды nftables
    nft_commands = [
        "nft add table inet herdr_filter 2>/dev/null || true",
        "nft 'add chain inet herdr_filter output { type filter hook output priority filter; policy accept; }' 2>/dev/null || true",
        "nft flush chain inet herdr_filter",
    ]
    for bp in block_bypass_ports:
        if bp != port and bp != http_port:
            nft_commands.append(
                f"nft add rule inet herdr_filter output ip daddr 127.0.0.1 tcp dport {bp} counter reject"
            )
    nft_commands.append('nft add rule inet herdr_filter output oifname { "lo", "loopback0" } counter accept')
    nft_commands.append("nft add rule inet herdr_filter output counter reject")

    combined_cmd = " && ".join(nft_commands)
    rc, _ = run_wsl_root_cmd(combined_cmd, distro=distro)

    success = False
    if rc == 0:
        success = True
    else:
        # Fallback на iptables & ip6tables
        fallback_script = f"""
        iptables -N HERDR_ISOLATE 2>/dev/null || iptables -F HERDR_ISOLATE
        iptables -C OUTPUT -j HERDR_ISOLATE 2>/dev/null || iptables -I OUTPUT 1 -j HERDR_ISOLATE
        iptables -F HERDR_ISOLATE
        iptables -A HERDR_ISOLATE -p tcp -d 127.0.0.1 --dport 2080 -j REJECT
        iptables -A HERDR_ISOLATE -o lo -j ACCEPT
        iptables -A HERDR_ISOLATE -o loopback0 -j ACCEPT
        iptables -A HERDR_ISOLATE -j REJECT

        ip6tables -N HERDR_ISOLATE 2>/dev/null || ip6tables -F HERDR_ISOLATE
        ip6tables -C OUTPUT -j HERDR_ISOLATE 2>/dev/null || ip6tables -I OUTPUT 1 -j HERDR_ISOLATE
        ip6tables -F HERDR_ISOLATE
        ip6tables -A HERDR_ISOLATE -o lo -j ACCEPT
        ip6tables -A HERDR_ISOLATE -o loopback0 -j ACCEPT
        ip6tables -A HERDR_ISOLATE -j REJECT
        """
        rc_fb, _ = run_wsl_root_cmd(fallback_script, distro=distro)
        success = (rc_fb == 0)

    # Запись системных переменных в /etc/profile.d/
    if killswitch:
        env_content = (
            "export HTTPS_PROXY='http://127.0.0.1:1'\n"
            "export HTTP_PROXY='http://127.0.0.1:1'\n"
            "export ALL_PROXY='socks5h://127.0.0.1:1'\n"
            "export https_proxy='http://127.0.0.1:1'\n"
            "export http_proxy='http://127.0.0.1:1'\n"
            "export all_proxy='socks5h://127.0.0.1:1'\n"
        )
    else:
        env_content = (
            f"export HTTPS_PROXY='http://127.0.0.1:{http_port}'\n"
            f"export HTTP_PROXY='http://127.0.0.1:{http_port}'\n"
            f"export ALL_PROXY='socks5h://127.0.0.1:{port}'\n"
            f"export https_proxy='http://127.0.0.1:{http_port}'\n"
            f"export http_proxy='http://127.0.0.1:{http_port}'\n"
            f"export all_proxy='socks5h://127.0.0.1:{port}'\n"
        )

    write_env_cmd = (
        f"cat << 'EOF' > /etc/profile.d/herdr_claude_env.sh\n{env_content}EOF\n"
        "chmod 644 /etc/profile.d/herdr_claude_env.sh"
    )
    run_wsl_root_cmd(write_env_cmd, distro=distro)

    if make_persistent:
        try:
            install_persistent_wsl_isolation(
                port=port,
                http_port=http_port,
                block_bypass_ports=block_bypass_ports,
                distro=distro,
            )
        except Exception:
            pass

    remove_windows_firewall_rule()
    return success


def remove_wsl_isolation(distro: Optional[str] = None, full_clean: bool = False) -> bool:
    """Снимает сетевую изоляцию WSL2, восстанавливая прямой доступ Direct IP."""
    cmd = """
    nft flush chain inet herdr_filter 2>/dev/null || true
    nft delete table inet herdr_filter 2>/dev/null || true
    iptables -D OUTPUT -j HERDR_ISOLATE 2>/dev/null || true
    iptables -F HERDR_ISOLATE 2>/dev/null || true
    iptables -X HERDR_ISOLATE 2>/dev/null || true
    ip6tables -D OUTPUT -j HERDR_ISOLATE 2>/dev/null || true
    ip6tables -F HERDR_ISOLATE 2>/dev/null || true
    ip6tables -X HERDR_ISOLATE 2>/dev/null || true
    rm -f /etc/profile.d/herdr_claude_env.sh
    """
    if full_clean:
        cmd += """
        rm -f /etc/nftables.conf
        rm -f /usr/local/bin/herdr_boot_isolation.sh
        if [ -f /etc/wsl.conf ]; then
            sed -i '/herdr_boot_isolation.sh/d' /etc/wsl.conf
        fi
        """

    rc, _ = run_wsl_root_cmd(cmd, distro=distro)
    remove_windows_firewall_rule()
    return rc == 0
