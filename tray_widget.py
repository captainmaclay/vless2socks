#!/usr/bin/env python3
"""Системный tray-виджет для vlesstosocks5.

Управление прокси из системного трея Windows:
  - Запуск / остановка прокси
  - Индикатор статуса (зелёный / красный / жёлтый)
  - Просмотр лога
  - Health-check по TCP

Зависимости: pystray, Pillow
    pip install pystray Pillow
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from typing import Optional

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print(
        "Не найдены зависимости для tray-виджета.\n"
        "Установите:  pip install pystray Pillow",
        file=sys.stderr,
    )
    sys.exit(1)


# ── Конфигурация ──────────────────────────────────────────────

ROOT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = ROOT_DIR / "config.json"
MAIN_SCRIPT = ROOT_DIR / "main.py"

# Python из виртуального окружения, если есть
VENV_PYTHON = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

# Размер буфера лога (строк)
LOG_BUFFER_SIZE = 500

# Интервал health-check (секунды)
HEALTH_CHECK_INTERVAL = 5

# Таймаут TCP-проверки порта (секунды)
TCP_PROBE_TIMEOUT = 2


# ── Читаем порт из config.json ────────────────────────────────

def read_listen_addr() -> tuple[str, int]:
    """Извлечь адрес и порт из config.json."""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        listen = cfg.get("listen", "127.0.0.1:1081")
        host, _, port = listen.rpartition(":")
        return host or "127.0.0.1", int(port or 1081)
    except Exception:
        return "127.0.0.1", 1081


# ── Генерация иконок ──────────────────────────────────────────

def _make_icon(color: str, text: str = "") -> Image.Image:
    """Создать квадратную иконку 64×64 с цветным кругом и текстом."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    colors = {
        "green": (76, 175, 80),
        "red": (244, 67, 54),
        "yellow": (255, 193, 7),
        "gray": (158, 158, 158),
    }
    fill = colors.get(color, colors["gray"])

    # Круг
    margin = 4
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=fill,
        outline=(255, 255, 255, 200),
        width=2,
    )

    # Текст по центру
    if text:
        try:
            font = ImageFont.truetype("segoeui.ttf", 22)
        except (OSError, IOError):
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            ((size - tw) / 2, (size - th) / 2 - 2),
            text, fill=(255, 255, 255), font=font,
        )

    return img


ICON_STOPPED = _make_icon("red", "×")
ICON_RUNNING = _make_icon("green", "✓")
ICON_STARTING = _make_icon("yellow", "…")
ICON_ERROR = _make_icon("red", "!")


# ── TCP health-check ──────────────────────────────────────────

def tcp_probe(host: str, port: int, timeout: float = TCP_PROBE_TIMEOUT) -> bool:
    """Проверить, слушает ли что-то на host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


# ── Окно просмотра лога ───────────────────────────────────────

class LogWindow:
    """Tkinter-окно для просмотра лога прокси."""

    def __init__(self, log_lines: deque[str]):
        self._log_lines = log_lines
        self._root: Optional[tk.Tk] = None
        self._text: Optional[tk.Text] = None
        self._thread: Optional[threading.Thread] = None

    def show(self):
        if self._thread and self._thread.is_alive():
            return  # уже открыто
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        self._root = tk.Tk()
        self._root.title("vless2socks — Лог")
        self._root.geometry("780x420")
        self._root.configure(bg="#1e1e2e")

        # Текстовое поле
        self._text = tk.Text(
            self._root,
            bg="#1e1e2e", fg="#cdd6f4",
            font=("Consolas", 10),
            wrap=tk.WORD,
            state=tk.DISABLED,
            borderwidth=0,
            padx=8, pady=8,
        )
        scrollbar = tk.Scrollbar(self._root, command=self._text.yview)
        self._text.config(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._text.pack(fill=tk.BOTH, expand=True)

        # Кнопки внизу
        btn_frame = tk.Frame(self._root, bg="#1e1e2e")
        btn_frame.pack(fill=tk.X, pady=4)
        tk.Button(
            btn_frame, text="Обновить", command=self._refresh,
            bg="#313244", fg="#cdd6f4", activebackground="#45475a",
            relief=tk.FLAT, padx=12, pady=4,
        ).pack(side=tk.LEFT, padx=8)
        tk.Button(
            btn_frame, text="Очистить", command=self._clear,
            bg="#313244", fg="#cdd6f4", activebackground="#45475a",
            relief=tk.FLAT, padx=12, pady=4,
        ).pack(side=tk.LEFT)

        self._refresh()
        self._root.mainloop()

    def _refresh(self):
        if not self._text:
            return
        self._text.config(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.insert(tk.END, "\n".join(self._log_lines) or "(лог пуст)")
        self._text.see(tk.END)
        self._text.config(state=tk.DISABLED)

    def _clear(self):
        self._log_lines.clear()
        self._refresh()


# ── Основной класс tray-приложения ────────────────────────────

class VlessTrayApp:
    """Управление vlesstosocks5 из системного трея."""

    def __init__(self):
        self._host, self._port = read_listen_addr()
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._healthy = False
        self._log_lines: deque[str] = deque(maxlen=LOG_BUFFER_SIZE)
        self._log_window = LogWindow(self._log_lines)
        self._icon: Optional[pystray.Icon] = None
        self._stop_event = threading.Event()

    def run(self):
        """Запустить tray-приложение."""
        menu = pystray.Menu(
            pystray.MenuItem(
                self._status_text,
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("▶ Запустить", self._on_start, visible=lambda _: not self._running),
            pystray.MenuItem("⏹ Остановить", self._on_stop, visible=lambda _: self._running),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("📋 Показать лог", self._on_show_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🚪 Выход", self._on_exit),
        )

        self._icon = pystray.Icon(
            name="vless2socks",
            icon=ICON_STOPPED,
            title=f"vless2socks — остановлен",
            menu=menu,
        )

        # Фоновый поток health-check
        health_thread = threading.Thread(target=self._health_loop, daemon=True)
        health_thread.start()

        self._log("Tray-виджет запущен. Прокси: {host}:{port}".format(
            host=self._host, port=self._port,
        ))

        self._icon.run()

    # ── Текст статуса ──

    def _status_text(self, _=None) -> str:
        if self._running and self._healthy:
            return f"✅ Работает — {self._host}:{self._port}"
        elif self._running:
            return f"🟡 Запускается..."
        else:
            return f"🔴 Остановлен"

    # ── Обработчики меню ──

    def _on_start(self, icon=None, item=None):
        if self._running:
            return
        self._start_proxy()

    def _on_stop(self, icon=None, item=None):
        if not self._running:
            return
        self._stop_proxy()

    def _on_show_log(self, icon=None, item=None):
        self._log_window.show()

    def _on_exit(self, icon=None, item=None):
        self._log("Завершение работы...")
        self._stop_proxy()
        self._stop_event.set()
        if self._icon:
            self._icon.stop()

    # ── Управление процессом ──

    def _start_proxy(self):
        """Запустить main.py как subprocess."""
        self._log("Запуск прокси...")
        self._running = True
        self._update_icon(ICON_STARTING, "Запускается...")

        cmd = [PYTHON, str(MAIN_SCRIPT), "-c", str(CONFIG_FILE)]
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(ROOT_DIR),
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except Exception as e:
            self._log(f"Ошибка запуска: {e}")
            self._running = False
            self._update_icon(ICON_ERROR, "Ошибка запуска")
            return

        self._log(f"Процесс запущен (PID {self._process.pid})")

        # Поток чтения stderr (там идут логи)
        reader = threading.Thread(
            target=self._read_output,
            args=(self._process,),
            daemon=True,
        )
        reader.start()

        # Поток наблюдения за жизнью процесса
        watcher = threading.Thread(
            target=self._watch_process,
            args=(self._process,),
            daemon=True,
        )
        watcher.start()

    def _stop_proxy(self):
        """Остановить процесс прокси."""
        proc = self._process
        if proc is None:
            self._running = False
            return

        self._log("Остановка прокси...")
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._log("Процесс не завершился, убиваю...")
                proc.kill()
                proc.wait(timeout=3)
        except Exception as e:
            self._log(f"Ошибка при остановке: {e}")

        self._process = None
        self._running = False
        self._healthy = False
        self._update_icon(ICON_STOPPED, "Остановлен")
        self._log("Прокси остановлен")

    def _read_output(self, proc: subprocess.Popen):
        """Читать stderr процесса и складывать в буфер лога."""
        try:
            for raw_line in iter(proc.stderr.readline, b""):
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if line:
                    self._log(line)
                    # Детектим строку "готов:" — значит прокси поднялся
                    if "готов:" in line or "ready:" in line.lower():
                        self._healthy = True
                        self._update_icon(ICON_RUNNING, f"Работает — :{self._port}")
        except Exception:
            pass

    def _watch_process(self, proc: subprocess.Popen):
        """Следить за жизнью процесса."""
        proc.wait()
        exit_code = proc.returncode
        if self._running:  # Неожиданное завершение
            self._log(f"Процесс завершился неожиданно (код {exit_code})")
            self._running = False
            self._healthy = False
            self._process = None
            self._update_icon(ICON_ERROR, f"Упал (код {exit_code})")

    # ── Health-check ──

    def _health_loop(self):
        """Периодически проверять TCP-доступность порта."""
        while not self._stop_event.is_set():
            if self._running:
                alive = tcp_probe(self._host, self._port)
                if alive and not self._healthy:
                    self._healthy = True
                    self._update_icon(ICON_RUNNING, f"Работает — :{self._port}")
                elif not alive and self._healthy:
                    self._healthy = False
                    self._update_icon(ICON_STARTING, "Порт не отвечает...")
            self._stop_event.wait(HEALTH_CHECK_INTERVAL)

    # ── Утилиты ──

    def _update_icon(self, image: Image.Image, tooltip: str):
        if self._icon:
            self._icon.icon = image
            self._icon.title = f"vless2socks — {tooltip}"

    def _log(self, message: str):
        ts = time.strftime("%H:%M:%S")
        self._log_lines.append(f"[{ts}] {message}")


# ── Точка входа ───────────────────────────────────────────────

def main():
    if not CONFIG_FILE.exists():
        print(
            f"Файл {CONFIG_FILE} не найден.\n"
            f"Создайте config.json и запустите снова.",
            file=sys.stderr,
        )
        sys.exit(1)

    app = VlessTrayApp()
    app.run()


if __name__ == "__main__":
    main()
