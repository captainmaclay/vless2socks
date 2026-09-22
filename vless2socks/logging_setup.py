"""Настройка логирования."""

from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"
DATE_FORMAT = "%H:%M:%S"

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "none": logging.CRITICAL + 10,
}


def configure_console() -> None:
    """Сделать вывод устойчивым к кодировке консоли.

    Консоль Windows по умолчанию живёт в cp866/cp1251. Имя профиля из ссылки
    вполне может содержать эмодзи (флаг страны — обычное дело в панелях), и
    тогда обычный print падает с UnicodeEncodeError прямо посреди отчёта.
    Ставим backslashreplace: непредставимый символ станет escape-последовательностью,
    а не аварией. Кодировку не трогаем — русский текст должен остаться читаемым.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if _needs_utf8(stream):
                # Вывод уходит в файл или канал, а текущая кодировка кириллицу
                # не тянет (у системной локали cp1252 её нет вовсе). Никакая
                # консоль эти байты не читает, поэтому UTF-8 здесь — верный
                # ответ: в файле будет нормальный текст, а не \uXXXX.
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
            else:
                stream.reconfigure(errors="backslashreplace")
        except (AttributeError, OSError, ValueError):
            # Поток подменён (перенаправление, тесты) — ничего страшного.
            pass


def _needs_utf8(stream) -> bool:
    """Стоит ли переключить поток на UTF-8.

    Только для перенаправленного вывода: у живой консоли своя кодовая
    страница, и подменять её — получить мазню вместо текста.
    """
    try:
        if stream.isatty():
            return False
    except (AttributeError, OSError, ValueError):
        return False

    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return True
    try:
        "Проверка кодировки".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return True
    return False


def setup_logging(level: str = "info") -> None:
    """Инициализировать корневой логгер.

    :param level: debug | info | warning | error | none
    """
    configure_console()
    lvl = _LEVELS.get(str(level).lower(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(lvl)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
