#!/usr/bin/env python3
"""Полный прогон в один запуск: установка xray, диагностика, проверка IP.

Смысл — собрать всё в один файл отчёта, который можно целиком переслать.
Каждый шаг выполняется независимо: упавший не прерывает остальные, потому
что интересна вся картина, а не первая ошибка.

    python tools/selftest_all.py
    python tools/selftest_all.py --config config.json --out отчёт.txt
"""

from __future__ import annotations

import argparse
import io
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "selftest-output.txt"
STEP_TIMEOUT = 180

# Этот скрипт и сам печатает по-русски, поэтому чинит свой вывод первым делом.
# get_xray.py носит такую же функцию внутри себя: он ставится до всего
# остального и не должен зависеть от пакета.
sys.path.insert(0, str(PROJECT_ROOT))
from vless2socks.logging_setup import configure_console  # noqa: E402


@dataclass
class Step:
    title: str
    argv: list[str]
    #: Пропустить шаг, если условие не выполнено.
    skip_if: str = ""
    optional: bool = False
    output: str = field(default="", init=False)
    code: int | None = field(default=None, init=False)
    elapsed: float = field(default=0.0, init=False)
    skipped: str = field(default="", init=False)


def python_executable() -> str:
    """Тот же интерпретатор, которым запущен этот скрипт."""
    return sys.executable or "python"


def build_steps(config: Path, xray: Path) -> list[Step]:
    py = python_executable()
    main = str(PROJECT_ROOT / "main.py")
    return [
        Step("Версия Python", [py, "--version"]),
        Step("Установка xray", [py, str(PROJECT_ROOT / "tools" / "get_xray.py")]),
        Step("Версия xray", [str(xray), "version"], skip_if="нет bin/xray"),
        Step(
            "Конфиг для xray (без секретов)",
            [py, main, "--print-xray-config", "-c", str(config)],
        ),
        Step("ДИАГНОСТИКА", [py, main, "--doctor", "-c", str(config)]),
        Step("ПРОВЕРКА ПОДМЕНЫ IP", [py, main, "--ip", "-c", str(config)]),
        Step(
            "Контроль: прямой IP через curl",
            ["curl", "-sS", "-m", "20", "https://api.ipify.org"],
            skip_if="нет curl",
            optional=True,
        ),
    ]


def child_env() -> dict[str, str]:
    """Заставить дочерние процессы говорить в UTF-8.

    Их вывод уходит в канал, а не в консоль, и Python берёт для него системную
    локаль. На этой машине это оказалась cp1252, где кириллицы нет вообще:
    один процесс упал с UnicodeEncodeError, у остальных текст в отчёте
    превратился в escape-последовательности. Канал читаем мы сами,
    поэтому UTF-8 здесь уместен.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_step(step: Step) -> None:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            step.argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="backslashreplace",
            timeout=STEP_TIMEOUT,
            cwd=str(PROJECT_ROOT),
            env=child_env(),
        )
        step.output = (proc.stdout or "") + (proc.stderr or "")
        step.code = proc.returncode
    except FileNotFoundError:
        step.output = f"не найдена программа: {step.argv[0]}"
        step.code = 127
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or b""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "backslashreplace")
        step.output = f"{partial}\n[шаг не уложился в {STEP_TIMEOUT} с]"
        step.code = 124
    except OSError as exc:
        step.output = f"{type(exc).__name__}: {exc}"
        step.code = 1
    step.elapsed = time.monotonic() - started


def render(steps: list[Step], config: Path, xray: Path) -> str:
    out = io.StringIO()
    line = "=" * 72

    out.write(f"{line}\nSELFTEST vless2socks\n{line}\n")
    out.write(f"дата:      {datetime.now():%Y-%m-%d %H:%M:%S}\n")
    out.write(f"система:   {platform.platform()}\n")
    out.write(f"python:    {sys.version.split()[0]} ({python_executable()})\n")
    out.write(f"проект:    {PROJECT_ROOT}\n")
    out.write(f"конфиг:    {config} ({'есть' if config.exists() else 'НЕТ'})\n")
    out.write(f"xray:      {xray} ({'есть' if xray.exists() else 'нет'})\n")

    for number, step in enumerate(steps, 1):
        out.write(f"\n{'-' * 72}\n")
        out.write(f">>> {number}. {step.title}\n")
        out.write(f"{'-' * 72}\n")
        if step.skipped:
            out.write(f"[пропущено: {step.skipped}]\n")
            continue
        out.write(f"$ {' '.join(step.argv)}\n\n")
        out.write(step.output.rstrip() or "(пусто)")
        out.write(f"\n\n[код возврата: {step.code}, {step.elapsed:.1f} с]\n")

    out.write(f"\n{line}\nКОРОТКО\n{line}\n")
    for number, step in enumerate(steps, 1):
        if step.skipped:
            mark = "[ -- ]"
        elif step.code == 0:
            mark = "[ OK ]"
        elif step.optional:
            mark = "[WARN]"
        else:
            mark = "[FAIL]"
        out.write(f"{mark} {number}. {step.title}\n")
    out.write(f"{line}\n")
    return out.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="selftest_all",
        description="Полный прогон: установка xray, диагностика, проверка IP",
    )
    parser.add_argument(
        "-c", "--config", default=str(PROJECT_ROOT / "config.json"),
        help="путь к config.json",
    )
    parser.add_argument(
        "-o", "--out", default=str(DEFAULT_OUT), help="куда записать отчёт",
    )
    parser.add_argument(
        "--no-open", action="store_true", help="не открывать отчёт после прогона",
    )
    configure_console()
    args = parser.parse_args(argv)

    config = Path(args.config)
    xray = PROJECT_ROOT / "bin" / ("xray.exe" if os.name == "nt" else "xray")

    if not config.exists():
        print(f"Не найден {config}.", file=sys.stderr)
        print(
            "Создайте его: python main.py --init-config config.json\n"
            "и впишите свою vless:// ссылку в поле \"url\".",
            file=sys.stderr,
        )
        return 1

    steps = build_steps(config, xray)
    print(f"Прогон из {len(steps)} шагов. Отчёт будет здесь: {args.out}\n")

    for number, step in enumerate(steps, 1):
        if step.skip_if == "нет curl" and not shutil.which("curl"):
            step.skipped = "curl не установлен"
            print(f"  {number}. {step.title} — пропущено ({step.skipped})")
            continue
        if step.skip_if == "нет bin/xray" and not xray.exists():
            step.skipped = "xray не установился"
            print(f"  {number}. {step.title} — пропущено ({step.skipped})")
            continue

        print(f"  {number}. {step.title} ...", end=" ", flush=True)
        run_step(step)
        print(f"код {step.code} ({step.elapsed:.1f} с)")

    report = render(steps, config, xray)
    out_path = Path(args.out)
    out_path.write_text(report, encoding="utf-8")

    print(f"\nГотово. Отчёт: {out_path}")
    print("Пришлите его целиком — в нём нет ни UUID, ни паролей.")

    if not args.no_open and os.name == "nt":
        try:
            os.startfile(str(out_path))  # noqa: S606 - открыть блокнотом
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
