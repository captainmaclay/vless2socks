"""
UI-компонент Zapret2 Watcher для главной страницы vless2socks.
Включает:
- Строку с функцией Zapret2 Watcher
- Кнопку-свитчер ON/OFF (тускло-зеленый при ON, приглушенный при OFF)
- Прилегающую кнопку со стрелочкой (▼ / ▲)
- Выпадающее меню со slide-down анимацией
- Чекбоксы MainZapret2 и Telegram Proxy с ярко-оранжевыми галочками
- Защиту от зависания окна при сворачивании главного приложения
"""

from __future__ import annotations

import sys
import tkinter as tk
from typing import Any, Callable, Dict, Optional

import settings_manager
from .watcher import Zapret2Watcher

# Цветовая палитра
THEME = {
    "bg": "#1e1e2e",
    "surface": "#181825",
    "card": "#252538",
    "overlay": "#313244",
    "text": "#cdd6f4",
    "subtext": "#a6adc8",
    "muted": "#6c7086",
    "dim_green": "#264830",        # Тускло-зеленый фон для активного свитчера ON
    "dim_green_hover": "#2f593b",
    "green_fg": "#a6e3a1",         # Мягкий зеленый текст
    "off_bg": "#36384a",           # Приглушенный фон для OFF
    "off_bg_hover": "#42455a",
    "off_fg": "#9399b2",
    "arrow_bg": "#2a2b3d",
    "arrow_hover": "#3c3e56",
    "border": "#45475a",
    "orange": "#ff7700",           # Ярко-оранжевый для чекбоксов
    "blue": "#89b4fa",
    "teal": "#94e2d5",
    "red": "#f38ba8",
}


class OrangeCheckbox(tk.Canvas):
    """Кастомный чекбокс с ярко-оранжевой галочкой."""

    def __init__(
        self,
        parent,
        variable: tk.BooleanVar,
        command: Optional[Callable[[], None]] = None,
        bg: str = "#252538",
        orange_color: str = "#ff7700",
        size: int = 18,
    ):
        super().__init__(
            parent,
            width=size,
            height=size,
            bg=bg,
            highlightthickness=0,
            cursor="hand2",
        )
        self.variable = variable
        self.command = command
        self.orange_color = orange_color
        self.bg_color = bg
        self.size = size

        self.bind("<Button-1>", self._toggle)
        self.bind("<KeyPress-space>", self._toggle)
        self.bind("<Return>", self._toggle)

        if hasattr(self.variable, "trace_add"):
            try:
                self.variable.trace_add("write", lambda *_: self.redraw())
            except Exception:
                pass
        self.redraw()

    def _toggle(self, event=None):
        try:
            val = bool(self.variable.get())
        except Exception:
            val = False
        self.variable.set(not val)
        self.redraw()
        if self.command:
            try:
                self.command()
            except Exception:
                pass

    def redraw(self):
        self.delete("all")
        s = self.size
        try:
            is_checked = bool(self.variable.get())
        except Exception:
            is_checked = False

        r = 3
        # Отрисовка рамки
        self.create_rectangle(
            1, 1, s - 1, s - 1,
            fill="#1e1e2e" if not is_checked else "#2a2220",
            outline=self.orange_color if is_checked else "#585b70",
            width=2 if is_checked else 1,
        )

        if is_checked:
            # Ярко-оранжевая галочка
            coords = [
                (s * 0.22, s * 0.50),
                (s * 0.42, s * 0.72),
                (s * 0.78, s * 0.28),
            ]
            self.create_line(
                coords[0][0], coords[0][1], coords[1][0], coords[1][1],
                fill=self.orange_color, width=2.5, capstyle=tk.ROUND, joinstyle=tk.ROUND
            )
            self.create_line(
                coords[1][0], coords[1][1], coords[2][0], coords[2][1],
                fill=self.orange_color, width=2.5, capstyle=tk.ROUND, joinstyle=tk.ROUND
            )


class ZapretRecoveryWidget(tk.Frame):
    """
    Блок Zapret2 Watcher для интеграции на главную страницу vless2socks.
    """

    def __init__(self, parent, watcher: Optional[Zapret2Watcher] = None, bg: str = THEME["card"]):
        super().__init__(parent, bg=bg, padx=12, pady=8, highlightthickness=1, highlightbackground=THEME["border"])
        self.watcher = watcher
        self._ui_root = self.winfo_toplevel()

        # Загрузка сохраненных настроек (по умолчанию ON)
        saved = settings_manager.get_zapret2_settings()
        self.enabled_var = tk.BooleanVar(value=saved.get("enabled", True))
        self.watch_main_var = tk.BooleanVar(value=saved.get("watch_main", True))
        self.watch_tg_var = tk.BooleanVar(value=saved.get("watch_tg", True))

        self._menu_open = False
        self.menu_win: Optional[tk.Toplevel] = None
        self._unmap_bind_id = None
        self._global_click_bind_id = None

        self._build_ui()

        # Инициализация наблюдателя
        if self.watcher:
            self.watcher.set_enabled(self.enabled_var.get())
            self.watcher.set_watch_targets(self.watch_main_var.get(), self.watch_tg_var.get())
            self.watcher.status_callback = self._on_watcher_update

        self._update_switcher_appearance()

    def _build_ui(self):
        """Построение интерфейса блока."""
        bg = self.cget("bg")

        # ── Левая часть: Название и статус-индикаторы ──
        left_box = tk.Frame(self, bg=bg)
        left_box.pack(side=tk.LEFT, fill=tk.Y)

        title_lbl = tk.Label(
            left_box,
            text="🛡️ Zapret2 Watcher",
            font=("Segoe UI", 10, "bold"),
            fg=THEME["text"],
            bg=bg,
        )
        title_lbl.pack(side=tk.LEFT, padx=(0, 10))

        # Бейджи статусов
        self.status_box = tk.Frame(left_box, bg=bg)
        self.status_box.pack(side=tk.LEFT)

        self.badge_main = tk.Label(
            self.status_box,
            text="Main: ?",
            font=("Segoe UI", 8, "bold"),
            bg=THEME["overlay"],
            fg=THEME["subtext"],
            padx=6,
            pady=1,
        )
        self.badge_main.pack(side=tk.LEFT, padx=3)

        self.badge_tg = tk.Label(
            self.status_box,
            text="TG Proxy: ?",
            font=("Segoe UI", 8, "bold"),
            bg=THEME["overlay"],
            fg=THEME["subtext"],
            padx=6,
            pady=1,
        )
        self.badge_tg.pack(side=tk.LEFT, padx=3)

        self.lbl_retry_info = tk.Label(
            self.status_box,
            text="",
            font=("Segoe UI", 8),
            bg=bg,
            fg=THEME["orange"],
        )
        self.lbl_retry_info.pack(side=tk.LEFT, padx=6)

        # ── Правая часть: Сплит-кнопка Свитчер ON/OFF + Стрелочка ▼ ──
        right_box = tk.Frame(self, bg=bg)
        right_box.pack(side=tk.RIGHT)

        # 1. Кнопка-свитчер ON/OFF
        self.btn_switcher = tk.Button(
            right_box,
            text="ON",
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            padx=14,
            pady=3,
            cursor="hand2",
            command=self._toggle_switcher,
        )
        self.btn_switcher.pack(side=tk.LEFT)

        # 2. Прилегающая кнопка со стрелочкой ▼
        self.btn_arrow = tk.Button(
            right_box,
            text="▼",
            font=("Segoe UI", 8, "bold"),
            bg=THEME["arrow_bg"],
            fg=THEME["text"],
            activebackground=THEME["arrow_hover"],
            activeforeground=THEME["blue"],
            relief=tk.FLAT,
            padx=7,
            pady=4,
            cursor="hand2",
            command=self.toggle_menu,
        )
        self.btn_arrow.pack(side=tk.LEFT, padx=(1, 0))

    def _toggle_switcher(self):
        """Переключение состояния свитчера ON/OFF."""
        new_val = not self.enabled_var.get()
        self.enabled_var.set(new_val)
        self._persist_settings()

        if self.watcher:
            self.watcher.set_enabled(new_val)

        self._update_switcher_appearance()

    def _update_switcher_appearance(self):
        """Обновление визуального стиля кнопки-свитчера."""
        is_on = self.enabled_var.get()
        if is_on:
            # Тускло-зеленый фон с надписью ON
            self.btn_switcher.config(
                text="ON",
                bg=THEME["dim_green"],
                fg=THEME["green_fg"],
                activebackground=THEME["dim_green_hover"],
                activeforeground="#ffffff",
            )
        else:
            # Приглушенный фон с надписью OFF
            self.btn_switcher.config(
                text="OFF",
                bg=THEME["off_bg"],
                fg=THEME["off_fg"],
                activebackground=THEME["off_bg_hover"],
                activeforeground="#ffffff",
            )

    def _persist_settings(self):
        """Сохранение состояния свитчера и чекбоксов в settings.json (попадает в бэкапы)."""
        settings_manager.set_zapret2_settings(
            enabled=self.enabled_var.get(),
            watch_main=self.watch_main_var.get(),
            watch_tg=self.watch_tg_var.get(),
        )

    def _on_watcher_update(self, state: Dict[str, Any]):
        """Потокобезопасное обновление статуса из Watcher."""
        # Вызывается из потока Watcher: никаких обращений к Tk здесь, только передача в GUI-поток
        # (after() главного окна vless2socks потокобезопасен). Существование виджета проверит
        # _apply_watcher_state уже в GUI-потоке.
        try:
            self._ui_root.after(0, lambda: self._apply_watcher_state(state))
        except Exception:
            pass

    def _apply_watcher_state(self, state: Dict[str, Any]):
        """Применить полученное состояние в UI."""
        if not self.winfo_exists():
            return

        main_ok = state.get("main_ok", False)
        tg_ok = state.get("tg_ok", False)
        has_tg_target = state.get("has_tg_target", True)
        recovering = state.get("recovering", False)
        retry_count = state.get("retry_count", 0)

        relay_error = state.get("relay_error", False)

        # Бейдж Main
        if main_ok:
            self.badge_main.config(text="Main: OK", bg="#20402b", fg=THEME["green_fg"])
        else:
            self.badge_main.config(text="Main: DOWN", bg="#4a262a", fg=THEME["red"])

        # Бейдж TG
        if not has_tg_target:
            lang = "ru"
            try:
                from i18n import get_current_language
                lang = get_current_language()
            except Exception:
                pass
            none_text = "TG: UNDEFINED" if lang.lower().startswith("en") else "TG: НЕ ВЫБРАН"
            self.badge_tg.config(text=none_text, bg="#36384a", fg="#9399b2")
        elif relay_error:
            self.badge_tg.config(text="TG: RELAY ERR", bg="#4a3520", fg=THEME["orange"])
        elif tg_ok:
            self.badge_tg.config(text="TG Proxy: OK", bg="#20402b", fg=THEME["green_fg"])
        else:
            self.badge_tg.config(text="TG Proxy: DOWN", bg="#4a262a", fg=THEME["red"])

        # Текст восстановления
        if recovering:
            if relay_error:
                self.lbl_retry_info.config(text=f"🔄 Реконнект TG ({retry_count})...")
            else:
                self.lbl_retry_info.config(text=f"🔄 Восстановление ({retry_count})...")
        else:
            self.lbl_retry_info.config(text="")



    def _get_tg_display_target(self) -> str:
        """Получить текущую строку цели для Telegram Proxy: 'не выбрано' / 'undefined' или хост:порт."""
        if self.watcher:
            st = self.watcher.get_state()
            if st.get("tg_target_display"):
                return st["tg_target_display"]
        lang = "ru"
        try:
            from i18n import get_current_language
            lang = get_current_language()
        except Exception:
            pass
        from . import core
        return core.get_telegram_proxy_display_target(lang=lang)

    # ── Выпадающее контекстное меню с анимацией и авто-сворачиванием ──

    def toggle_menu(self):

        """Переключить видимость контекстного меню."""
        if self._menu_open:
            self.close_menu()
        else:
            self.open_menu()

    def open_menu(self):
        """Открыть выплывающее контекстное меню с анимацией."""
        if self._menu_open:
            return

        self.close_menu()
        self._menu_open = True
        self.btn_arrow.config(text="▲")

        root = self.winfo_toplevel()

        # Создание Toplevel окна
        self.menu_win = tk.Toplevel(root)
        self.menu_win.overrideredirect(True)
        # ВАЖНО: transient(root) обеспечивает синхронное сворачивание Windows вместе с родителем!
        self.menu_win.transient(root)
        self.menu_win.configure(bg=THEME["border"])

        # Внешняя рамка с акцентной линией
        border_frame = tk.Frame(self.menu_win, bg=THEME["border"], padx=1, pady=1)
        border_frame.pack(fill=tk.BOTH, expand=True)

        inner = tk.Frame(border_frame, bg=THEME["card"], padx=14, pady=12)
        inner.pack(fill=tk.BOTH, expand=True)

        # Заголовок меню
        head_frame = tk.Frame(inner, bg=THEME["card"])
        head_frame.pack(fill=tk.X, pady=(0, 6))
        tk.Label(
            head_frame,
            text="⚙️  Настройки Zapret2 Watcher",
            font=("Segoe UI", 9, "bold"),
            fg=THEME["text"],
            bg=THEME["card"],
        ).pack(side=tk.LEFT)

        # Разделитель
        sep = tk.Frame(inner, bg=THEME["overlay"], height=1)
        sep.pack(fill=tk.X, pady=(0, 8))

        # Опция 1: MainZapret2
        row_main = tk.Frame(inner, bg=THEME["card"], cursor="hand2")
        row_main.pack(fill=tk.X, pady=4)

        lbl_main = tk.Label(
            row_main,
            text="🛡️  MainZapret2 (winws2.exe)",
            font=("Segoe UI", 9, "bold"),
            fg=THEME["blue"],
            bg=THEME["card"],
            cursor="hand2",
        )
        lbl_main.pack(side=tk.LEFT)

        cb_main = OrangeCheckbox(
            row_main,
            variable=self.watch_main_var,
            bg=THEME["card"],
            orange_color=THEME["orange"],
            size=18,
            command=self._on_options_changed,
        )
        cb_main.pack(side=tk.RIGHT)
        lbl_main.bind("<Button-1>", lambda e: cb_main._toggle())
        row_main.bind("<Button-1>", lambda e: cb_main._toggle())

        # Опция 2: Telegram Proxy
        row_tg = tk.Frame(inner, bg=THEME["card"], cursor="hand2")
        row_tg.pack(fill=tk.X, pady=4)

        tg_target = self._get_tg_display_target()
        lbl_tg = tk.Label(
            row_tg,
            text=f"✈️  Telegram Proxy ({tg_target})",
            font=("Segoe UI", 9, "bold"),
            fg=THEME["teal"],
            bg=THEME["card"],
            cursor="hand2",
        )
        lbl_tg.pack(side=tk.LEFT)


        cb_tg = OrangeCheckbox(
            row_tg,
            variable=self.watch_tg_var,
            bg=THEME["card"],
            orange_color=THEME["orange"],
            size=18,
            command=self._on_options_changed,
        )
        cb_tg.pack(side=tk.RIGHT)
        lbl_tg.bind("<Button-1>", lambda e: cb_tg._toggle())
        row_tg.bind("<Button-1>", lambda e: cb_tg._toggle())

        # Подсказка
        hint = tk.Label(
            inner,
            text="Автоматический перезапуск упавших модулей",
            font=("Segoe UI", 7),
            fg=THEME["subtext"],
            bg=THEME["card"],
        )
        hint.pack(anchor="w", pady=(8, 0))

        # Вычисление координат под кнопкой
        self.update_idletasks()
        try:
            arrow_x = self.btn_arrow.winfo_rootx()
            arrow_w = self.btn_arrow.winfo_width()
            arrow_y = self.btn_arrow.winfo_rooty()
            arrow_h = self.btn_arrow.winfo_height()

            target_w = 270
            target_h = 145
            popup_x = (arrow_x + arrow_w) - target_w
            popup_y = arrow_y + arrow_h + 3
        except Exception:
            popup_x = root.winfo_rootx() + 200
            popup_y = root.winfo_rooty() + 100
            target_w = 270
            target_h = 145

        self.menu_win.geometry(f"{target_w}x4+{popup_x}+{popup_y}")
        self.menu_win.deiconify()

        # ── Плавная slide-down анимация разворачивания ──
        def _slide_step(step=1, total_steps=6):
            if not self._menu_open or not self.menu_win or not self.menu_win.winfo_exists():
                return
            h = int(target_h * (step / total_steps))
            self.menu_win.geometry(f"{target_w}x{h}+{popup_x}+{popup_y}")
            if step < total_steps:
                self.after(12, lambda: _slide_step(step + 1, total_steps))

        _slide_step()

        # ── РЕШЕНИЕ БАГА СВОРАЧИВАНИЯ: закрывать меню при сворачивании окна ──
        def _on_root_unmap(event):
            """Срабатывает, когда главное окно сворачивается."""
            if event.widget == root and self._menu_open:
                self.close_menu()

        def _on_root_deactivate(event=None):
            """Срабатывает при деактивации окна."""
            if self._menu_open:
                self.close_menu()

        self._unmap_bind_id = root.bind("<Unmap>", _on_root_unmap, add="+")
        self._deact_bind_id = root.bind("<Deactivate>", _on_root_deactivate, add="+")

        # Дополнительная периодическая проверка состояния окна
        def _poll_parent_state():
            if not self.winfo_exists() or not self._menu_open or not self.menu_win or not self.menu_win.winfo_exists():
                return
            try:
                if root.wm_state() != "normal":
                    self.close_menu()
                    return
            except Exception:
                self.close_menu()
                return
            if self.winfo_exists() and self._menu_open:
                self._poll_id = self.after(120, _poll_parent_state)

        _poll_parent_state()

        # Закрытие при клике вне контекстного меню
        def _on_global_click(event):
            if not self._menu_open or not self.menu_win or not self.menu_win.winfo_exists():
                return
            try:
                ex, ey = event.x_root, event.y_root
                mx = self.menu_win.winfo_rootx()
                my = self.menu_win.winfo_rooty()
                mw = self.menu_win.winfo_width()
                mh = self.menu_win.winfo_height()
                ax = self.btn_arrow.winfo_rootx()
                ay = self.btn_arrow.winfo_rooty()
                aw = self.btn_arrow.winfo_width()
                ah = self.btn_arrow.winfo_height()

                # Если клик внутри меню или внутри кнопки со стрелочкой — не закрываем
                if (mx <= ex <= mx + mw and my <= ey <= my + mh) or (ax <= ex <= ax + aw and ay <= ey <= ay + ah):
                    return
                self.close_menu()
            except Exception:
                self.close_menu()

        self._global_click_bind_id = root.bind_all("<Button-1>", _on_global_click, add="+")

    def close_menu(self):
        """Закрыть контекстное меню."""
        self._menu_open = False
        if getattr(self, "_poll_id", None):
            try:
                self.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None

        if hasattr(self, "btn_arrow") and self.btn_arrow and self.btn_arrow.winfo_exists():
            try:
                self.btn_arrow.config(text="▼")
            except Exception:
                pass

        if hasattr(self, "menu_win") and self.menu_win:
            try:
                if self.menu_win.winfo_exists():
                    self.menu_win.destroy()
            except Exception:
                pass
        self.menu_win = None

    def _on_options_changed(self):
        """Обработка изменения чекбоксов."""
        self._persist_settings()
        if self.watcher:
            self.watcher.set_watch_targets(self.watch_main_var.get(), self.watch_tg_var.get())
