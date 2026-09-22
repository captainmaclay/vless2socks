#!/usr/bin/env python3
"""vless2socks — локальный SOCKS5-прокси поверх VLESS (TCP + TLS).

Примеры::

    python main.py -c config.json
    python main.py --url "vless://UUID@host:443?security=tls&sni=host&type=tcp" -l 127.0.0.1:1081
    python main.py -c config.json --test
    python main.py --init-config config.json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import signal
import sys
from pathlib import Path

from vless2socks.backend import XRAY, resolve_backend
from vless2socks.config import DEFAULT_CONFIG, AppConfig, load_config
from vless2socks.doctor import format_report, run_diagnostics
from vless2socks.logging_setup import configure_console, get_logger, setup_logging
from vless2socks.selftest import check_tunnel
from vless2socks.socks5 import Socks5Server
from vless2socks.url import ConfigError

log = get_logger("vless2socks")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vless2socks",
        description="Локальный SOCKS5-прокси поверх VLESS (TCP+TLS)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("-c", "--config", metavar="FILE", help="путь к config.json")
    p.add_argument("-u", "--url", metavar="VLESS_URL", help="ссылка vless://...")
    p.add_argument(
        "-l", "--listen", metavar="HOST:PORT",
        help="адрес SOCKS5-сервера (по умолчанию 127.0.0.1:1081)",
    )
    p.add_argument("--username", help="логин для SOCKS5 (включает аутентификацию)")
    p.add_argument("--password", help="пароль для SOCKS5")
    p.add_argument(
        "--no-udp", action="store_true", help="отключить SOCKS5 UDP ASSOCIATE"
    )
    p.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error", "none"],
        help="уровень логирования",
    )
    p.add_argument(
        "--test", action="store_true",
        help="проверить туннель и выйти (не поднимая SOCKS5)",
    )
    p.add_argument(
        "--doctor", action="store_true",
        help="подробная диагностика по шагам: параметры, DNS, TCP, TLS, "
             "рукопожатие VLESS, HTTP и UDP через туннель",
    )
    p.add_argument(
        "--test-url", metavar="HOST[:PORT][/PATH]", default=None,
        help="цель для --test (по умолчанию www.gstatic.com:80/generate_204)",
    )
    p.add_argument(
        "--ip", action="store_true",
        help="сравнить внешний IP напрямую и через туннель: показывает, "
             "подменяется ли адрес и что остальная система ходит со своим",
    )
    p.add_argument(
        "--ip-service", metavar="HOST[:PORT][/PATH]", default=None,
        help="свой сервис определения IP для --ip (по умолчанию api.ipify.org "
             "и запасные). Полезно, если стандартные недоступны",
    )
    p.add_argument(
        "--backend", choices=["auto", "python", "xray"],
        help="движок: auto (по умолчанию) — свой клиент, а для reality/xtls/ws "
             "автоматически xray-core; python — только свой; xray — только xray",
    )
    p.add_argument(
        "--xray-path", metavar="FILE",
        help="путь к исполняемому файлу xray (иначе ищется в bin/, рядом и в PATH)",
    )
    p.add_argument(
        "--xray-legacy-config", action="store_true",
        help="писать конфиг xray в старой форме (vnext) — для xray до версии 26. "
             "Обычно не нужно: формат подбирается автоматически",
    )
    p.add_argument(
        "--print-xray-config", action="store_true",
        help="показать сгенерированный конфиг xray (без UUID и паролей) и выйти",
    )
    p.add_argument(
        "--init-config", metavar="FILE",
        help="записать шаблон config.json и выйти",
    )
    return p


def _parse_probe(value: str | None) -> tuple[str, int, str]:
    if not value:
        return "www.gstatic.com", 80, "/generate_204"
    rest = value
    for prefix in ("http://", "https://"):
        if rest.lower().startswith(prefix):
            rest = rest[len(prefix):]
    hostport, _, path = rest.partition("/")
    host, _, port = hostport.partition(":")
    return host, int(port) if port else 80, "/" + path if path else "/"


def _write_template(path: str) -> int:
    target = Path(path)
    if target.exists():
        print(f"Файл уже существует: {target}", file=sys.stderr)
        return 1
    target.write_text(
        json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Шаблон записан: {target}")
    print("Подставьте свою vless:// ссылку в поле \"url\" и запустите:")
    print(f"  python main.py -c {target}")
    return 0


def _install_stop_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows: обработчики сигналов в ProactorEventLoop недоступны,
            # отработает KeyboardInterrupt в main().
            pass


async def run_server(config: AppConfig) -> int:
    try:
        choice = resolve_backend(config)
    except ConfigError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        print("Полная картина по шагам: запустите с --doctor", file=sys.stderr)
        return 1

    log.info("движок: %s", choice)
    if choice.name == XRAY:
        return await _run_with_xray(config, choice)
    return await _run_with_python(config)


async def _run_with_python(config: AppConfig) -> int:
    server = Socks5Server(config)
    try:
        await server.start()
    except OSError as exc:
        print(
            f"\nНе удалось занять {config.listen_host}:{config.listen_port}: {exc}\n\n"
            f"Порт уже занят другим процессом — возможно, вторым экземпляром\n"
            f"этой же программы или другим VPN-клиентом.\n"
            f"Выберите другой порт: -l 127.0.0.1:1081",
            file=sys.stderr,
        )
        return 1

    log.info("готов: %s", config.describe())
    log.info("проверка: curl -x socks5h://%s:%d https://ifconfig.me",
             config.listen_host, config.listen_port)

    stop = asyncio.Event()
    _install_stop_handlers(stop)
    try:
        # start_server уже принимает соединения; периодический тик нужен,
        # чтобы Ctrl+C гарантированно доходил до цикла событий на Windows.
        while not stop.is_set():
            await asyncio.sleep(0.5)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        server.close()
        log.info("остановлено")
    return 0


async def _run_with_xray(config: AppConfig, choice) -> int:
    from vless2socks.xray import XrayProcess, XrayStartupError

    if config.listen_port == 0:
        print("Для движка xray нужен конкретный порт, а не 0.", file=sys.stderr)
        return 1

    process = XrayProcess(
        config, xray_path=choice.xray_path,
        legacy_vnext=config.xray_legacy_config,
    )
    try:
        await process.start()
    except XrayStartupError as exc:
        log.error("%s", exc)
        return 2

    log.info("готов: %s", config.describe())
    log.info("проверка: curl -x socks5h://%s:%d https://ifconfig.me",
             config.listen_host, config.listen_port)

    stop = asyncio.Event()
    _install_stop_handlers(stop)
    supervisor = asyncio.ensure_future(process.supervise())
    try:
        while not stop.is_set() and not supervisor.done():
            await asyncio.sleep(0.5)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        supervisor.cancel()
        await asyncio.gather(supervisor, return_exceptions=True)
        await process.stop()
        log.info("остановлено")
    return 0


async def run_test(config: AppConfig, probe: str | None) -> int:
    host, port, path = _parse_probe(probe)
    try:
        choice = resolve_backend(config)
    except ConfigError as exc:
        log.error("%s", exc)
        return 1

    log.info("движок: %s", choice)
    log.info("проверяю туннель: %s -> %s:%d%s",
             config.server.describe(), host, port, path)

    if choice.name == XRAY:
        ok, message = await _test_via_xray(config, choice, host, port, path)
    else:
        ok, message = await check_tunnel(config, host, port, path)

    if ok:
        log.info("OK: %s", message)
        return 0
    log.error("НЕ РАБОТАЕТ: %s", message)
    log.error("Подробности по шагам: запустите с --doctor")
    return 2


async def _test_via_xray(config, choice, host, port, path) -> tuple[bool, str]:
    """Поднять xray на время проверки и постучаться через его SOCKS5."""
    from vless2socks.selftest import check_through_socks5
    from vless2socks.xray import XrayProcess, XrayStartupError

    if config.listen_port == 0:
        return False, "для движка xray нужен конкретный порт (-l 127.0.0.1:1081)"

    process = XrayProcess(
        config, xray_path=choice.xray_path, restart=False,
        legacy_vnext=config.xray_legacy_config,
    )
    try:
        await process.start()
    except XrayStartupError as exc:
        return False, str(exc)
    try:
        return await check_through_socks5(config, host, port, path)
    finally:
        await process.stop()


@contextlib.asynccontextmanager
async def running_proxy(config: AppConfig, choice):
    """Поднять выбранный движок на время проверки и погасить после.

    Порт нужен настоящий: проверка должна идти ровно тем путём, каким пойдёт
    браузер, — через локальный SOCKS5, а не мимо него.
    """
    if choice.name == XRAY:
        from vless2socks.xray import XrayProcess

        process = XrayProcess(
            config, xray_path=choice.xray_path, restart=False,
            legacy_vnext=config.xray_legacy_config,
        )
        await process.start()
        try:
            yield config.listen_host, config.listen_port
        finally:
            await process.stop()
        return

    server = Socks5Server(config)
    await server.start()
    try:
        yield config.listen_host, server.port
    finally:
        server.close()


async def run_ip_check(config: AppConfig, service: str | None = None) -> int:
    from vless2socks.ipcheck import DEFAULT_SERVICES, Service, compare_ip, format_ip_report
    from vless2socks.xray import XrayStartupError

    if service:
        host, port, path = _parse_probe(service)
        services = (Service(host, path, port),)
    else:
        services = DEFAULT_SERVICES

    try:
        choice = resolve_backend(config)
    except ConfigError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1

    log.info("движок: %s", choice)
    log.info("поднимаю прокси на время проверки...")
    try:
        async with running_proxy(config, choice) as (host, port):
            report = await compare_ip(
                host, port,
                username=config.username, password=config.password,
                services=services, backend=str(choice),
            )
    except XrayStartupError as exc:
        log.error("%s", exc)
        return 2
    except OSError as exc:
        print(
            f"\nНе удалось занять {config.listen_host}:{config.listen_port}: {exc}\n"
            f"Если прокси уже запущен в другом окне, проверку можно сделать так:\n"
            f"  curl -x socks5h://{config.listen_host}:{config.listen_port} "
            f"https://ifconfig.me\n"
            f"  curl https://ifconfig.me",
            file=sys.stderr,
        )
        return 1

    print(format_ip_report(report))
    return 0 if report.ok else 2


async def run_doctor(config: AppConfig, probe: str | None) -> int:
    host, port, path = _parse_probe(probe)
    results = await run_diagnostics(
        config, probe_host=host, probe_port=port, probe_path=path
    )
    print(format_report(results))
    return 2 if any(r.failed for r in results) else 0


def main(argv: list[str] | None = None) -> int:
    # Первым делом — до любого print: имя профиля в ссылке вполне может
    # содержать эмодзи, а консоль Windows живёт в cp866/cp1251.
    configure_console()
    args = build_parser().parse_args(argv)

    if args.init_config:
        return _write_template(args.init_config)

    setup_logging(args.log_level or "info")

    try:
        config = load_config(
            args.config,
            url=args.url,
            listen=args.listen,
            username=args.username,
            password=args.password,
            udp=False if args.no_udp else None,
            log_level=args.log_level,
            backend=args.backend,
            xray_path=args.xray_path,
            xray_legacy_config=args.xray_legacy_config or None,
            # Разбираем мягко: решение, потянет ли профиль тот или иной движок,
            # принимает resolve_backend, а не парсер ссылки.
            strict=False,
        )
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        if not args.doctor:
            print("Подробности: запустите с --doctor", file=sys.stderr)
        return 1

    setup_logging(config.log_level)

    if args.print_xray_config:
        from vless2socks.xray import build_xray_config
        from vless2socks.xray.config_builder import redact_config

        print(json.dumps(
            redact_config(
                build_xray_config(config, legacy_vnext=config.xray_legacy_config)
            ),
            indent=2, ensure_ascii=False,
        ))
        return 0

    if args.doctor:
        coro = run_doctor(config, args.test_url)
    elif args.ip:
        coro = run_ip_check(config, args.ip_service)
    elif args.test:
        coro = run_test(config, args.test_url)
    else:
        coro = run_server(config)
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        log.info("остановлено пользователем")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
