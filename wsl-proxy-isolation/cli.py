"""Главный интерфейс командной строки (CLI) для управления и тестирования изоляции WSL2.

Команды:
  check-wsl      Проверить наличие WSL и установленные дистрибутивы.
  apply          Активировать ограничение трафика WSL2 на сокет 1015 (nftables/iptables).
  remove         Снять ограничение трафика WSL2 (восстановить Direct IP).
  test           Запустить полный аудит изоляции (порты, ядро, утечки direct IP, socks5h).
  full-setup     Автоматический конвейер: Проверка WSL -> Применение изоляции -> Полный тест.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

import firewall_isolate
import isolation_tester
import wsl_detector


def print_banner():
    print("=" * 70)
    print(" 🛡️ WSL2 FAILS-SAFE NETWORK ISOLATION GUARD & AUDIT TOOL")
    print("    Standard: Herdr Control Center (Socket :1015 Strict Restriction)")
    print("=" * 70)


def cmd_check_wsl(args):
    installed = wsl_detector.is_wsl_installed()
    print(f"WSL установлен: {'ДА (True)' if installed else 'НЕТ (False)'}")
    if not installed:
        sys.exit(1)

    distros = wsl_detector.get_wsl_distributions()
    print(f"Обнаружено дистрибутивов: {len(distros)}")
    for d in distros:
        default_mark = " [DEFAULT]" if d.get("is_default") else ""
        print(f" • {d['name']} | Версия WSL: {d['version']} | Статус: {d['state']}{default_mark}")

    active = wsl_detector.get_active_distro()
    print(f"\nАктивный дистрибутив: {active}")


def cmd_apply(args):
    print(f"Применение сетевой изоляции WSL2 на сокет {args.port}...")
    if not wsl_detector.is_wsl_installed():
        print("Ошибка: WSL не установлен в системе.")
        sys.exit(1)

    distro = args.distro or wsl_detector.get_active_distro()
    print(f"Целевой дистрибутив: {distro}")

    ok = firewall_isolate.apply_wsl_isolation(
        port=args.port,
        http_port=args.http_port,
        distro=distro,
        make_persistent=(not args.no_persist),
    )
    if ok:
        print("✓ Правила изоляции ядра Linux успешно применены!")
        print(f"  - Разрешен loopback-трафик (lo, loopback0)")
        print(f"  - Весь прямой трафик WAN/LAN отсечен ядром Linux")
        print(f"  - Сокет обхода 2080 заблокирован на 127.0.0.1")
        print(f"  - Переменные окружения ALL_PROXY=socks5h://127.0.0.1:{args.port} установлены")
        if not args.no_persist:
            print(f"  - Персистентный автозапуск в /etc/wsl.conf и /etc/nftables.conf активирован")
    else:
        print("✕ Не удалось применить правила изоляции!")
        sys.exit(1)


def cmd_remove(args):
    print("Снятие сетевой изоляции WSL2 (восстановление прямого доступа Direct IP)...")
    distro = args.distro or wsl_detector.get_active_distro()
    ok = firewall_isolate.remove_wsl_isolation(distro=distro, full_clean=args.full)
    if ok:
        print("✓ Сетевая изоляция ядра снята. Прямой доступ к сети восстановлен.")
        if args.full:
            print("✓ Автозагрузочные скрипты (/etc/nftables.conf, herdr_boot_isolation.sh) удалены.")
    else:
        print("✕ Ошибка при удалении правил изоляции!")
        sys.exit(1)


def cmd_test(args):
    print(f"Запуск аудита сетевой изоляции WSL2 на сокет {args.port}...")
    distro = args.distro or wsl_detector.get_active_distro()

    def log_cb(level: str, msg: str):
        print(f"[{level:<7}] {msg}")

    result = isolation_tester.run_isolation_audit(
        host=args.host,
        port=args.port,
        http_port=args.http_port,
        distro=distro,
        log_callback=log_cb,
    )

    if args.json:
        print("\n" + json.dumps(result, ensure_ascii=False, indent=2))

    if result["status"] == "leak":
        sys.exit(2)
    elif result["status"] in ("isolated", "lockdown"):
        sys.exit(0)
    else:
        sys.exit(0)


def cmd_full_setup(args):
    print_banner()
    print("\n[ШАГ 1] Проверка WSL...")
    if not wsl_detector.is_wsl_installed():
        print("✕ WSL не установлен. Завершение работы.")
        sys.exit(1)

    distro = args.distro or wsl_detector.get_active_distro()
    print(f"✓ WSL обнаружен. Активный дистрибутив: {distro}")

    print("\n[ШАГ 2] Применение правил изоляции ядра...")
    ok = firewall_isolate.apply_wsl_isolation(
        port=args.port,
        http_port=args.http_port,
        distro=distro,
        make_persistent=(not args.no_persist),
    )
    if not ok:
        print("✕ Ошибка применения изоляции.")
        sys.exit(1)
    print("✓ Изоляция успешно сконфигурирована.")

    print("\n[ШАГ 3] Комплексное тестирование изоляции...")
    def log_cb(level: str, msg: str):
        print(f"[{level:<7}] {msg}")

    result = isolation_tester.run_isolation_audit(
        host=args.host,
        port=args.port,
        http_port=args.http_port,
        distro=distro,
        log_callback=log_cb,
    )

    print("\n[ИТОГ]")
    print(f"Вердикт: {result['status'].upper()}")
    if result["status"] in ("isolated", "lockdown"):
        print("🎉 ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ: Сетевой контур WSL2 надежно герметизирован!")
    else:
        print("⚠ Внимание: статус изоляции требует внимания (см. журнал выше).")


def main():
    parser = argparse.ArgumentParser(
        description="WSL2 Fail-Safe Network Isolation & Socket 1015 Strict Restriction Tool"
    )
    subparsers = parser.add_subparsers(dest="command", help="Команда для выполнения")

    # check-wsl
    subparsers.add_parser("check-wsl", help="Проверить установку и статус WSL")

    # apply
    p_apply = subparsers.add_parser("apply", help="Применить сетевую изоляцию на сокет 1015")
    p_apply.add_argument("--port", type=int, default=1015, help="SOCKS5 порт прокси (по умолчанию 1015)")
    p_apply.add_argument("--http-port", type=int, default=11015, help="HTTP порт (по умолчанию 11015)")
    p_apply.add_argument("--distro", type=str, default=None, help="Имя дистрибутива WSL")
    p_apply.add_argument("--no-persist", action="store_true", help="Не прописывать в автозагрузку wsl.conf")

    # remove
    p_remove = subparsers.add_parser("remove", help="Снять сетевую изоляцию (Direct IP)")
    p_remove.add_argument("--distro", type=str, default=None, help="Имя дистрибутива WSL")
    p_remove.add_argument("--full", action="store_true", help="Полная очистка включая boot-файлы")

    # test
    p_test = subparsers.add_parser("test", help="Протестировать изоляцию и утечки")
    p_test.add_argument("--host", type=str, default="127.0.0.1", help="Хост прокси")
    p_test.add_argument("--port", type=int, default=1015, help="SOCKS5 порт прокси")
    p_test.add_argument("--http-port", type=int, default=11015, help="HTTP порт прокси")
    p_test.add_argument("--distro", type=str, default=None, help="Имя дистрибутива WSL")
    p_test.add_argument("--json", action="store_true", help="Вывести результат в формате JSON")

    # full-setup
    p_full = subparsers.add_parser("full-setup", help="Полный цикл: проверка -> настройка -> тест")
    p_full.add_argument("--host", type=str, default="127.0.0.1", help="Хост прокси")
    p_full.add_argument("--port", type=int, default=1015, help="SOCKS5 порт прокси")
    p_full.add_argument("--http-port", type=int, default=11015, help="HTTP порт прокси")
    p_full.add_argument("--distro", type=str, default=None, help="Имя дистрибутива WSL")
    p_full.add_argument("--no-persist", action="store_true", help="Не прописывать в автозагрузку")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "check-wsl": cmd_check_wsl,
        "apply": cmd_apply,
        "remove": cmd_remove,
        "test": cmd_test,
        "full-setup": cmd_full_setup,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
