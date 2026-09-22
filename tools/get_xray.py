#!/usr/bin/env python3
"""Скачать xray-core под текущую платформу в папку bin/.

Берёт релиз с GitHub, сверяет SHA-256 из файла ``.dgst``, который публикуется
рядом с архивом, и распаковывает. Без проверки хеша не устанавливает.

    python tools/get_xray.py                 # последний релиз
    python tools/get_xray.py --version 25.3.6
    python tools/get_xray.py --list          # показать, что качалось бы
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = "XTLS/Xray-core"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
API_TAG = f"https://api.github.com/repos/{REPO}/releases/tags/{{tag}}"
USER_AGENT = "vless2socks-get-xray"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = PROJECT_ROOT / "bin"

#: Что распаковываем из архива.
WANTED = ("xray", "xray.exe", "geoip.dat", "geosite.dat")


class DownloadError(RuntimeError):
    pass


def configure_console() -> None:
    """Не дать выводу упасть на кодировке.

    Скрипт ставится первым, до всего остального, поэтому не зависит от пакета
    и чинит вывод сам. У системной локали Windows вполне может быть cp1252,
    где кириллицы нет вообще, — тогда обычный print падает с UnicodeEncodeError.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            redirected = not stream.isatty()
        except (AttributeError, OSError, ValueError):
            redirected = False
        try:
            encoding = getattr(stream, "encoding", "") or ""
            try:
                "Проверка".encode(encoding) if encoding else None
                fits = True
            except (UnicodeEncodeError, LookupError):
                fits = False
            if redirected and not fits:
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
            else:
                stream.reconfigure(errors="backslashreplace")
        except (AttributeError, OSError, ValueError):
            pass


# ------------------------------------------------------------ выбор архива


def asset_name() -> str:
    """Имя архива релиза для текущей ОС и архитектуры."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        os_part = "windows"
    elif system == "darwin":
        os_part = "macos"
    elif system == "linux":
        os_part = "linux"
    else:
        raise DownloadError(f"неизвестная ОС: {platform.system()}")

    if machine in ("amd64", "x86_64", "x64"):
        arch_part = "64"
    elif machine in ("arm64", "aarch64"):
        arch_part = "arm64-v8a"
    elif machine in ("i386", "i686", "x86"):
        arch_part = "32"
    else:
        raise DownloadError(f"неизвестная архитектура: {platform.machine()}")

    return f"Xray-{os_part}-{arch_part}.zip"


def pick_asset(release: dict, wanted: str) -> tuple[str, str]:
    """Найти в релизе архив и его .dgst. Возвращает ``(url_zip, url_dgst)``."""
    assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
    if wanted not in assets:
        available = ", ".join(sorted(n for n in assets if n.endswith(".zip")))
        raise DownloadError(
            f"в релизе {release.get('tag_name')} нет файла {wanted}.\n"
            f"Есть: {available}"
        )
    dgst = f"{wanted}.dgst"
    if dgst not in assets:
        raise DownloadError(
            f"для {wanted} не опубликован {dgst} — проверить хеш нечем, "
            f"установка отменена"
        )
    return assets[wanted], assets[dgst]


# --------------------------------------------------------------- загрузка


def fetch(url: str, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise DownloadError(f"{url} -> HTTP {exc.code} {exc.reason}") from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            raise DownloadError(
                f"{url} -> ошибка TLS: {reason}. "
                f"Если вы за корпоративным прокси, задайте "
                f"SSL_CERT_FILE с корневым сертификатом."
            ) from None
        raise DownloadError(f"{url} -> {reason}") from None


def parse_dgst(text: str) -> str:
    """Достать SHA-256 из .dgst. Формат менялся: SHA256=, SHA2-256=, sha256:."""
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
        elif ":" in line:
            key, _, value = line.partition(":")
        else:
            continue
        normalized = re.sub(r"[^A-Z0-9]", "", key.upper())
        if normalized in ("SHA256", "SHA2256"):
            digest = value.strip().lower()
            if re.fullmatch(r"[0-9a-f]{64}", digest):
                return digest
    raise DownloadError(
        "в .dgst нет строки с SHA-256 — проверить целостность архива нечем"
    )


def extract(archive: Path, dest: Path) -> list[Path]:
    """Распаковать нужные файлы, не давая архиву вылезти за пределы dest."""
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            name = Path(member.filename).name
            if member.is_dir() or name not in WANTED:
                continue
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise DownloadError(f"подозрительный путь в архиве: {member.filename}")
            with zf.open(member) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            if name in ("xray", "xray.exe"):
                target.chmod(target.stat().st_mode | 0o755)
            written.append(target)
    if not written:
        raise DownloadError(
            f"в архиве нет ни одного из файлов {WANTED} — формат релиза изменился"
        )
    return written


# ------------------------------------------------------------------ сценарий


def install(version: str | None, dest: Path, dry_run: bool = False) -> int:
    wanted = asset_name()
    url = API_LATEST if not version else API_TAG.format(tag=_normalize_tag(version))

    print(f"Платформа: {platform.system()} {platform.machine()} -> {wanted}")
    print(f"Смотрю релиз: {url}")

    release = json.loads(fetch(url).decode("utf-8"))
    tag = release.get("tag_name", "?")
    zip_url, dgst_url = pick_asset(release, wanted)
    print(f"Релиз {tag}")
    print(f"  архив: {zip_url}")
    print(f"  хеш:   {dgst_url}")

    if dry_run:
        print("\n--list: ничего не скачиваю.")
        return 0

    print("Скачиваю контрольную сумму...")
    expected = parse_dgst(fetch(dgst_url).decode("utf-8", "replace"))

    print("Скачиваю архив...")
    blob = fetch(zip_url, timeout=300)
    actual = hashlib.sha256(blob).hexdigest()
    if actual != expected:
        raise DownloadError(
            "SHA-256 не совпал — архив повреждён или подменён.\n"
            f"  ожидался: {expected}\n"
            f"  получен:  {actual}"
        )
    print(f"SHA-256 совпал: {actual[:32]}...")

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / wanted
        archive.write_bytes(blob)
        written = extract(archive, dest)

    print(f"\nУстановлено в {dest}:")
    for path in written:
        print(f"  {path.name}  ({path.stat().st_size:,} байт)")

    binary = dest / ("xray.exe" if os.name == "nt" else "xray")
    print(f"\nГотово. Проверить: {binary} version")
    print("Теперь можно запускать: python main.py -c config.json")
    return 0


def _normalize_tag(version: str) -> str:
    version = version.strip()
    return version if version.startswith("v") else f"v{version}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="get_xray",
        description="Скачать xray-core в папку bin/ с проверкой SHA-256",
    )
    parser.add_argument("--version", help="версия релиза, например 25.3.6")
    parser.add_argument(
        "--dest", default=str(DEFAULT_DEST), help="куда положить (по умолчанию bin/)"
    )
    parser.add_argument(
        "--list", action="store_true", dest="dry_run",
        help="показать, что скачивалось бы, и выйти",
    )
    args = parser.parse_args(argv)
    configure_console()

    try:
        return install(args.version, Path(args.dest), dry_run=args.dry_run)
    except DownloadError as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        try:
            direct = (
                f"https://github.com/{REPO}/releases/latest/download/{asset_name()}"
            )
        except DownloadError:
            direct = f"https://github.com/{REPO}/releases"
        print(
            "\nЕсли GitHub недоступен из консоли, откройте эту ссылку в браузере\n"
            f"  {direct}\n"
            f"и распакуйте из архива xray.exe, geoip.dat, geosite.dat\n"
            f"в папку {args.dest}\n"
            "После этого всё заработает — скачивание нужно только один раз.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
