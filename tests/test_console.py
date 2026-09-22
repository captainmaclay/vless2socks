"""Вывод не должен падать в консоли Windows.

Имя профиля в ссылке часто содержит эмодзи (флаг страны — обычное дело в
панелях), а консоль Windows живёт в cp866/cp1251. Без защиты отчёт
--doctor обрывается посреди строки с UnicodeEncodeError.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import uuid as uuid_mod
from urllib.parse import quote

from vless2socks.config import AppConfig
from vless2socks.doctor import CheckResult, Status, check_parameters, format_report
from vless2socks.logging_setup import configure_console
from vless2socks.relay import RelayStats
from vless2socks.url import parse_vless_url

UUID = str(uuid_mod.uuid4())
FLAG = "\U0001F1EB\U0001F1EE"  # флаг Финляндии, как в реальной ссылке
EMOJI_URL = (
    f"vless://{UUID}@h.example:443?security=tls&type=tcp"
    f"#{quote(FLAG + ' FINLAND 3 VLESS TCP')}"
)

#: Кодировки, в которых реально оказывается консоль Windows.
CONSOLE_ENCODINGS = ("cp866", "cp1251")


def render(text: str, encoding: str) -> str:
    """Проверить, что текст переживает запись в консоль с такой кодировкой."""
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding=encoding, errors="backslashreplace")
    stream.write(text)
    stream.flush()
    return raw.getvalue().decode(encoding)


class ReportEncodingTest(unittest.TestCase):
    def test_emoji_profile_name_does_not_crash_report(self):
        cfg = AppConfig(
            server=parse_vless_url(EMOJI_URL, strict=False),
            listen_host="127.0.0.1",
            listen_port=1080,
        )
        report = format_report([check_parameters(cfg)])
        self.assertIn(FLAG, report)  # в самой строке эмодзи остаётся
        for encoding in CONSOLE_ENCODINGS:
            with self.subTest(encoding=encoding):
                rendered = render(report, encoding)
                self.assertIn("FINLAND 3 VLESS TCP", rendered)

    def test_strict_encoding_would_have_crashed(self):
        """Фиксируем саму проблему: без errors= это падение, а не теория."""
        with self.assertRaises(UnicodeEncodeError):
            (FLAG + " FINLAND").encode("cp1251")

    def test_report_marks_are_ascii(self):
        report = format_report(
            [CheckResult("Шаг", Status.FAIL, "деталь", hint="подсказка")]
        )
        for encoding in CONSOLE_ENCODINGS:
            with self.subTest(encoding=encoding):
                rendered = render(report, encoding)
                self.assertIn("[FAIL]", rendered)
                self.assertIn("ИТОГ", rendered)
                self.assertNotIn("\\u", rendered, "в нашем тексте escape быть не должно")

    def test_relay_stats_are_ascii(self):
        stats = RelayStats()
        stats.sent, stats.received = 1024, 2048
        text = str(stats)
        self.assertTrue(text.isascii(), f"статистика должна быть ASCII: {text!r}")
        self.assertIn("tx", text)
        self.assertIn("rx", text)


class SecretsInReportTest(unittest.TestCase):
    """Отчёт делается для пересылки — UUID в нём быть не должно."""

    def test_uuid_is_masked_in_report(self):
        cfg = AppConfig(
            server=parse_vless_url(EMOJI_URL, strict=False),
            listen_host="127.0.0.1",
            listen_port=1080,
        )
        report = format_report([check_parameters(cfg)])
        self.assertNotIn(UUID, report, "полный UUID попал в отчёт")
        self.assertIn(UUID[:8], report, "по краям UUID должен быть узнаваем")
        self.assertIn(UUID[-4:], report)


class ConfigureConsoleTest(unittest.TestCase):
    def test_does_not_raise_on_substituted_streams(self):
        """В тестах и при перенаправлении потоки бывают без reconfigure."""
        import sys

        saved_out, saved_err = sys.stdout, sys.stderr
        try:
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()
            configure_console()  # не должно бросить
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err


if __name__ == "__main__":
    unittest.main()


class RedirectedOutputTest(unittest.TestCase):
    """Перенаправленный вывод в кодировке без кириллицы — реальный случай.

    Системная локаль Windows вполне может быть cp1252, где кириллицы нет
    вообще. Когда вывод уходит в канал (а не в консоль), Python берёт именно
    её — и обычный print падает с UnicodeEncodeError.
    """

    def _stream(self, encoding: str, tty: bool):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding=encoding)
        stream.isatty = lambda: tty  # type: ignore[method-assign]
        return raw, stream

    def test_switches_to_utf8_when_redirected_and_encoding_cannot_hold_cyrillic(self):
        from vless2socks.logging_setup import _needs_utf8

        _, stream = self._stream("cp1252", tty=False)
        self.assertTrue(_needs_utf8(stream))

    def test_keeps_console_encoding_when_it_is_a_terminal(self):
        from vless2socks.logging_setup import _needs_utf8

        _, stream = self._stream("cp1252", tty=True)
        self.assertFalse(
            _needs_utf8(stream),
            "у живой консоли своя кодовая страница — подменять нельзя",
        )

    def test_leaves_capable_encoding_alone(self):
        from vless2socks.logging_setup import _needs_utf8

        for encoding in ("utf-8", "cp1251"):
            with self.subTest(encoding=encoding):
                _, stream = self._stream(encoding, tty=False)
                self.assertFalse(_needs_utf8(stream))


class SubprocessOutputTest(unittest.TestCase):
    """Сквозная проверка ровно того, на чём всё встало у пользователя."""

    ROOT = Path(__file__).resolve().parent.parent

    def run_child(self, argv, expect_codes=None):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "cp1252"  # локаль без кириллицы
        proc = subprocess.run(
            [sys.executable, *argv],
            capture_output=True, text=True, encoding="utf-8",
            errors="backslashreplace", timeout=120, cwd=str(self.ROOT), env=env,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        self.assertNotIn(
            "UnicodeEncodeError", output,
            f"вывод упал на кодировке:\n{output[:600]}",
        )
        if expect_codes is not None:
            self.assertIn(proc.returncode, expect_codes, output)
        return output

    def test_get_xray_survives_locale_without_cyrillic(self):
        output = self.run_child(["tools/get_xray.py", "--list"])
        self.assertTrue(output.strip(), "скрипт ничего не напечатал")

    def test_doctor_survives_locale_without_cyrillic(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as fh:
            json.dump({"url": EMOJI_URL, "listen": "127.0.0.1:1080"}, fh)
            config = fh.name
        try:
            output = self.run_child(["main.py", "--doctor", "-c", config])
        finally:
            os.unlink(config)
        self.assertIn("ДИАГНОСТИКА", output, "кириллица не дошла до вывода")

    def test_selftest_runner_survives_locale_without_cyrillic(self):
        with tempfile.TemporaryDirectory() as d:
            config = Path(d) / "config.json"
            config.write_text(
                json.dumps({"url": EMOJI_URL, "listen": "127.0.0.1:1080"}),
                encoding="utf-8",
            )
            report = Path(d) / "report.txt"
            self.run_child([
                "tools/selftest_all.py", "-c", str(config),
                "-o", str(report), "--no-open",
            ], expect_codes={0})
            text = report.read_text(encoding="utf-8")
        self.assertIn("SELFTEST", text)
        self.assertIn("ДИАГНОСТИКА", text, "в отчёте кириллица потерялась")
        self.assertNotIn("\\u04", text, "в отчёте остались escape-последовательности")
