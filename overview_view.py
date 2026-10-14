"""Overview list drawn as items of one Canvas.

Each Tk widget is a native window on Windows. The Overview used ~20 widgets per
proxy card, so a view of 70 proxies meant ~1500 native windows to create, map,
lay out and destroy: switching views took 5-8 s. Drawn as canvas items (texts and
rectangles, no child windows) the same list redraws in tens of milliseconds.

The renderer only draws models and dispatches clicks; the app builds the models.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass, field
from typing import Any, Callable, Hashable, Optional


@dataclass
class Button:
    label: str
    command: Optional[Callable[[], Any]] = None
    bg: str = "#313244"
    fg: str = "#cdd6f4"
    enabled: bool = True
    bold: bool = False
    name: str = ""           # stable id for tests / lookups ("toggle", "geo", ...)
    # Every label this button can show (e.g. Start / Stop / ⏳): it is drawn as wide as the
    # widest, so a status change never shifts the other buttons and can be applied in place
    width_labels: tuple[str, ...] = ()


@dataclass
class CardModel:
    key: Hashable                      # the proxy
    dot: str
    order_text: str
    name: str
    ports: str
    geo: str
    geo_fg: str
    on_order: Optional[Callable[[], Any]] = None
    on_rename: Optional[Callable[[], Any]] = None
    badges: list[tuple[str, str]] = field(default_factory=list)      # (text, color)
    buttons: list[Button] = field(default_factory=list)               # right side, left to right
    checked: Optional[bool] = None                                    # None: no checkbox
    on_check: Optional[Callable[[bool], Any]] = None


@dataclass
class HeaderModel:
    key: Hashable                      # group id, or None for Unsorted
    icon: str
    name: str
    count_text: str
    first: bool = False
    # Inline rename: called with the new text; returns True when the edit is over
    # (saved, or nothing to save) and False to keep editing (e.g. invalid name).
    on_rename: Optional[Callable[[str, bool], bool]] = None
    select_button: Optional[Button] = None


@dataclass
class HintModel:
    text: str
    italic: bool = True
    big: bool = False


class OverviewView:
    # Sizes depend on the fonts and the screen scaling (125-200% on most laptops),
    # so they are computed in __init__ (see _layout_metrics), never fixed pixels.

    def __init__(self, canvas: tk.Canvas, palette: dict[str, str]):
        self.canvas = canvas
        self.C = palette
        self.f_name = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.f_small = tkfont.Font(family="Segoe UI", size=9)
        self.f_small_b = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        self.f_badge = tkfont.Font(family="Segoe UI", size=7, weight="bold")
        self.f_mono = tkfont.Font(family="Consolas", size=9)
        self.f_order = tkfont.Font(family="Consolas", size=10, weight="bold")
        self.f_dot = tkfont.Font(family="Segoe UI", size=14)
        self.f_head = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        self.f_head_icon = tkfont.Font(family="Segoe UI", size=11)
        self.f_hint = tkfont.Font(family="Segoe UI", size=9, slant="italic")
        self.f_empty = tkfont.Font(family="Segoe UI", size=11)

        self.rows: list[Any] = []
        self.card_slots: dict[Hashable, list[tuple[int, str]]] = {}    # key -> [(y, tag)]
        self.header_slots: dict[Hashable, tuple[int, str]] = {}
        self._commands: dict[str, Callable[[], Any]] = {}
        self._button_index: dict[str, tuple[str, Hashable]] = {}   # tag -> (button name, owner key)
        self._uid = 0
        self._width = 0
        self._editor: Optional[tk.Entry] = None
        self._deferred: Optional[list] = None      # rows waiting for an inline edit to end

        self._layout_metrics()
        self._button_bg: dict[str, str] = {}      # button tag -> normal fill
        self._owner_buttons: dict[str, list[str]] = {}   # card/header tag -> its button tags
        self._slot_live: dict[str, dict[str, Any]] = {}   # card tag -> model + item ids for in-place status
        self._hovered: Optional[str] = None
        self._measure_cache: dict[tuple[int, str], int] = {}
        self._fit_cache: dict[tuple[int, str, int], str] = {}
        canvas.bind("<Configure>", self._on_resize, add="+")
        # One handler each for the whole canvas: binding every drawn button separately
        # cost thousands of tag_bind calls per redraw
        canvas.bind("<Button-1>", self._on_click, add="+")
        canvas.bind("<Motion>", self._on_motion, add="+")
        canvas.bind("<Leave>", lambda _e: self._set_hover(None), add="+")

    # ── public ──────────────────────────────────────────────
    def render(self, rows: list[Any], reset_scroll: bool = False) -> None:
        """Draw the whole list (fast: tens of ms for ~80 cards).

        While a group name is being edited, background redraws wait until the edit is
        over (they would wipe the entry); a view switch (reset_scroll) closes it."""
        if self._editor is not None and not reset_scroll:
            self._deferred = rows
            return
        self._deferred = None
        self._close_editor()
        self.rows = rows
        cv = self.canvas
        cv.delete("all")
        self._commands.clear()
        self._button_index.clear()
        self._button_bg.clear()
        self._owner_buttons.clear()
        self._slot_live.clear()
        self._hovered = None
        self.card_slots = {}
        self.header_slots = {}
        width = self._current_width()
        y = 0
        for row in rows:
            if isinstance(row, CardModel):
                y += self.CARD_GAP
                tag = self._new_tag("card")
                self._draw_card(row, y, width, tag)
                self.card_slots.setdefault(row.key, []).append((y, tag))
                y += self.CARD_H + self.CARD_GAP
            elif isinstance(row, HeaderModel):
                y += self.FIRST_HEADER_GAP if row.first else self.HEADER_GAP
                tag = self._new_tag("head")
                self._draw_header(row, y, width, tag)
                self.header_slots[row.key] = (y, tag)
                y += self.HEADER_H + self.FIRST_HEADER_GAP
            elif isinstance(row, HintModel):
                if row.big:
                    cv.create_text(width // 2, y + self.EMPTY_H // 2, text=row.text, font=self.f_empty, fill=self.C["subtext"])
                    y += self.EMPTY_H
                else:
                    cv.create_text(self.px(12), y + self.HINT_H / 2, text=row.text, font=self.f_hint,
                                   fill=self.C["muted"], anchor="w")
                    y += self.HINT_H
        cv.configure(scrollregion=(0, 0, width, max(y, 1)))
        if reset_scroll:
            cv.yview_moveto(0)

    def update_card(self, model: CardModel) -> None:
        """Redraw every copy of one card in place (a proxy can be in several groups)."""
        slots = self.card_slots.get(model.key)
        if not slots:
            return
        width = None
        for y, tag in slots:
            if self._update_in_place(tag, model):
                continue
            if width is None:
                width = self._current_width()
            self._forget_commands(tag)
            self.canvas.delete(tag)
            self._draw_card(model, y, width, tag)
        self.rows = [model if isinstance(r, CardModel) and r.key == model.key else r for r in self.rows]

    @staticmethod
    def _layout_key(m: CardModel) -> tuple:
        """Everything that decides where things are drawn: if it is unchanged, a status
        change is just new colors / a new label on the dot and the first button."""
        first = m.buttons[0] if m.buttons else None
        return (
            m.checked, m.order_text, m.name, m.ports, m.geo, m.geo_fg, tuple(m.badges), m.on_rename is None,
            tuple((b.name, b.label, b.bg, b.fg, b.enabled) for b in m.buttons[1:]),
            (first.name, tuple(first.width_labels), first.bold) if first else None,
            bool(first and first.width_labels),
        )

    def _update_in_place(self, tag: str, m: CardModel) -> bool:
        live = self._slot_live.get(tag)
        if not live or not live.get("button") or not m.buttons or not m.buttons[0].width_labels:
            return False
        old: CardModel = live["model"]
        if self._layout_key(old) != self._layout_key(m):
            return False
        cv = self.canvas
        btn = m.buttons[0]
        btn_tag, rect, text = live["button"]
        cv.itemconfig(live["dot"], fill=m.dot)
        cv.itemconfig(rect, fill=btn.bg)
        cv.itemconfig(text, text=btn.label, fill=btn.fg if btn.enabled else self.C["muted"])
        # the click target follows the new state (e.g. Start -> disabled -> Stop)
        self._commands.pop(btn_tag, None)
        self._button_bg[btn_tag] = btn.bg
        if btn.enabled and btn.command:
            self._commands[btn_tag] = btn.command
        if self._hovered == btn_tag:
            self._hovered = None
            cv.config(cursor="")
        live["model"] = m
        return True

    def update_header(self, model: HeaderModel) -> None:
        slot = self.header_slots.get(model.key)
        if not slot:
            return
        y, tag = slot
        self._forget_commands(tag)
        self.canvas.delete(tag)
        self._draw_header(model, y, self._current_width(), tag)
        self.rows = [model if isinstance(r, HeaderModel) and r.key == model.key else r for r in self.rows]

    def texts(self) -> list[str]:
        """All drawn texts, top to bottom then left to right (for tests and diagnostics)."""
        items = [i for i in self.canvas.find_all() if self.canvas.type(i) == "text"]
        items.sort(key=lambda i: (round(self.canvas.coords(i)[1] / 8), self.canvas.coords(i)[0]))
        return [self.canvas.itemcget(i, "text") for i in items]

    def buttons(self, name: Optional[str] = None, key: Hashable = "__any__") -> list[str]:
        """Tags of clickable buttons, optionally filtered by button name and card/header key."""
        out = []
        for tag, (btn_name, owner) in self._button_index.items():
            if (name is None or btn_name == name) and (key == "__any__" or owner == key):
                out.append(tag)
        return out

    def card_dots(self, key: Hashable) -> list[str]:
        """Status dot color of every drawn copy of a card."""
        out = []
        for _y, tag in self.card_slots.get(key, []):
            for item in self.canvas.find_withtag(tag):
                if self.canvas.type(item) == "text" and self.canvas.itemcget(item, "text") == "●":
                    out.append(self.canvas.itemcget(item, "fill"))
        return out

    def card_checked(self, key: Hashable) -> list[bool]:
        """Checkbox state of every drawn copy of a card (empty list: no checkbox)."""
        out = []
        for _y, tag in self.card_slots.get(key, []):
            boxes = [b for b in self.buttons("check", key) if self.canvas.find_withtag(b)
                     and tag in self.canvas.gettags(self.canvas.find_withtag(b)[0])]
            for b in boxes:
                out.append(any(self.canvas.type(i) == "text" and self.canvas.itemcget(i, "text") == "✓"
                               for i in self.canvas.find_withtag(b)))
        return out

    def invoke(self, tag: str) -> None:
        cmd = self._commands.get(tag)
        if cmd is not None:
            cmd()

    def button_text(self, tag: str) -> str:
        for item in self.canvas.find_withtag(tag):
            if self.canvas.type(item) == "text":
                return self.canvas.itemcget(item, "text")
        return ""

    def button_fill(self, tag: str) -> str:
        for item in self.canvas.find_withtag(tag):
            if self.canvas.type(item) == "rectangle":
                return self.canvas.itemcget(item, "fill")
        return ""

    # ── drawing ─────────────────────────────────────────────
    def _layout_metrics(self) -> None:
        try:
            scale = float(self.canvas.winfo_fpixels("1i")) / 96.0
        except Exception:
            scale = 1.0
        self.scale = max(1.0, scale)
        px = self.px
        line = lambda f: int(f.metrics("linespace"))
        self.PAD_X = px(2)
        self.CARD_PAD = px(8)
        self.LINE_GAP = px(4)
        self.NAME_H = max(line(self.f_name), line(self.f_badge) + px(6))
        self.DETAIL_H = max(line(self.f_mono), line(self.f_small_b))
        self.CARD_H = self.CARD_PAD * 2 + self.NAME_H + self.LINE_GAP + self.DETAIL_H
        self.CARD_GAP = px(3)                           # above and below every card
        self.BTN_H = line(self.f_small_b) + px(6)
        self.HEADER_H = max(line(self.f_head), self.BTN_H) + px(8)
        self.HEADER_GAP = px(18)
        self.FIRST_HEADER_GAP = px(4)
        self.HINT_H = line(self.f_hint) + px(6)
        self.EMPTY_H = px(90)

    def px(self, value: float) -> int:
        """Design pixels (96 dpi) to screen pixels."""
        return int(round(value * self.scale))

    def _fit(self, font: tkfont.Font, text: str, max_w: float) -> str:
        """`text` shortened with an ellipsis so it is at most max_w wide."""
        if max_w <= 0:
            return ""
        full = self._w(font, text)
        if full <= max_w:
            return text
        key = (id(font), text, int(max_w))
        cached = self._fit_cache.get(key)
        if cached is not None:
            return cached
        # Start from the proportional estimate, then correct by a character or two:
        # a few measurements instead of a binary search over every prefix
        n = max(0, min(len(text) - 1, int(len(text) * max_w / max(full, 1))))
        while n > 0 and font.measure(text[:n] + "…") > max_w:
            n -= 1
        while n + 1 < len(text) and font.measure(text[:n + 1] + "…") <= max_w:
            n += 1
        result = text[:n] + "…" if n else ""
        if len(self._fit_cache) > 4000:
            self._fit_cache.clear()
        self._fit_cache[key] = result
        return result

    def _w(self, font: tkfont.Font, text: str) -> int:
        key = (id(font), text)
        width = self._measure_cache.get(key)
        if width is None:
            if len(self._measure_cache) > 4000:
                self._measure_cache.clear()
            width = self._measure_cache[key] = font.measure(text)
        return width

    def _current_width(self) -> int:
        w = self.canvas.winfo_width()
        if w <= 1:
            w = int(self.canvas.cget("width") or 780)
        self._width = max(w, 420)
        return self._width

    def _on_resize(self, event) -> None:
        if abs(event.width - self._width) > 2 and self.rows:
            # debounce: dragging the window edge fires many events
            if getattr(self, "_resize_job", None):
                try:
                    self.canvas.after_cancel(self._resize_job)
                except Exception:
                    pass
            self._resize_job = self.canvas.after(60, lambda: self.render(self.rows))

    def _new_tag(self, prefix: str) -> str:
        self._uid += 1
        return f"{prefix}{self._uid}"

    def _forget_commands(self, owner_tag: str) -> None:
        for tg in self._owner_buttons.pop(owner_tag, []):
            self._commands.pop(tg, None)
            self._button_index.pop(tg, None)
            self._button_bg.pop(tg, None)
            if self._hovered == tg:
                self._hovered = None
        self._slot_live.pop(owner_tag, None)

    def _button(self, x_right: int, y: int, h: int, btn: Button, owner_tag: str, owner_key: Hashable,
                font: Optional[tkfont.Font] = None, pad: int = 6) -> int:
        """Draw a button whose right edge is x_right; returns its left edge."""
        font = font or (self.f_small_b if btn.bold else self.f_small)
        w = max(self._w(font, label) for label in (btn.label,) + tuple(btn.width_labels)) + 2 * self.px(pad)
        x0 = x_right - w
        tag = self._new_tag("btn")
        bg = btn.bg
        cv = self.canvas
        rect = cv.create_rectangle(x0, y, x_right, y + h, fill=bg, outline="", tags=(owner_tag, tag))
        text = cv.create_text(x0 + w / 2, y + h / 2, text=btn.label, font=font,
                              fill=btn.fg if btn.enabled else self.C["muted"], tags=(owner_tag, tag))
        self._register(tag, btn, owner_key, bg, owner_tag)
        self._last_button = (tag, rect, text)
        return x0

    def _register(self, tag: str, btn: Button, owner_key: Hashable, bg: str, owner_tag: str = "") -> None:
        if owner_tag:
            self._owner_buttons.setdefault(owner_tag, []).append(tag)
        self._button_index[tag] = (btn.name or btn.label, owner_key)
        if btn.enabled and btn.command:
            self._commands[tag] = btn.command
            self._button_bg[tag] = bg

    def _button_tag_at(self, event) -> Optional[str]:
        for item in self.canvas.find_withtag("current"):
            for tg in self.canvas.gettags(item):
                if tg in self._commands:
                    return tg
        return None

    def _on_click(self, event) -> None:
        tag = self._button_tag_at(event)
        if tag is not None:
            self.invoke(tag)

    def _on_motion(self, event) -> None:
        self._set_hover(self._button_tag_at(event))

    def _set_hover(self, tag: Optional[str]) -> None:
        if tag == self._hovered:
            return
        cv = self.canvas
        for old in ([self._hovered] if self._hovered else []):
            for item in cv.find_withtag(old):
                if cv.type(item) == "rectangle":
                    cv.itemconfig(item, fill=self._button_bg.get(old, ""))
        self._hovered = tag
        cv.config(cursor="hand2" if tag else "")
        if tag:
            bg = self._button_bg.get(tag, "")
            plain = (self.C.get("overlay"), self.C.get("card"), self.C.get("bg"), self.C.get("surface"))
            if bg in plain:
                for item in cv.find_withtag(tag):
                    if cv.type(item) == "rectangle":
                        cv.itemconfig(item, fill=self.C.get("hover", bg))

    def _text_button(self, x: int, y: int, text: str, font: tkfont.Font, fill: str, command, owner_tag: str,
                     owner_key: Hashable, name: str, anchor: str = "w") -> int:
        """A clickable bare text (no background); returns its right edge."""
        tag = self._new_tag("btn")
        self.canvas.create_text(x, y, text=text, font=font, fill=fill, anchor=anchor, tags=(owner_tag, tag))
        self._register(tag, Button(text, command, bg="", fg=fill, name=name), owner_key, "", owner_tag)
        return x + self._w(font, text)

    def _draw_card(self, m: CardModel, y: int, width: int, tag: str) -> None:
        cv, C, px = self.canvas, self.C, self.px
        x1 = width - self.PAD_X
        cv.create_rectangle(self.PAD_X, y, x1, y + self.CARD_H, fill=C["card"], outline="", tags=(tag,))
        mid = y + self.CARD_H / 2
        x = self.PAD_X + px(10)

        if m.checked is not None:
            box = self._new_tag("btn")
            half = px(7)
            cv.create_rectangle(x, mid - half, x + 2 * half, mid + half, fill=C["surface"], outline=C["blue"], tags=(tag, box))
            if m.checked:
                cv.create_text(x + half, mid, text="✓", font=self.f_small_b, fill=C["blue"], tags=(tag, box))
            on_check = m.on_check
            self._register(box, Button("☐", (lambda v=not m.checked: on_check(v)) if on_check else None,
                                       bg=C["surface"], name="check"), m.key, C["surface"], tag)
            x += 2 * half + px(8)

        dot_item = cv.create_text(x, mid, text="●", font=self.f_dot, fill=m.dot, anchor="w", tags=(tag,))
        x += self._w(self.f_dot, "●") + px(4)
        x = self._text_button(x, mid, m.order_text, self.f_order, C["blue"], m.on_order, tag, m.key, "order") + px(14)

        # right side buttons, laid out from the right edge; text on the left stops before them
        right = x1 - px(10)
        first_button = None
        for btn in reversed(m.buttons):
            right = self._button(right, mid - self.BTN_H / 2, self.BTN_H, btn, tag, m.key) - px(4)
            first_button = self._last_button
        self._slot_live[tag] = {"model": m, "dot": dot_item, "button": first_button}
        limit = right - px(8)

        # name row + badges
        ty = y + self.CARD_PAD + self.NAME_H / 2
        rename_w = (self._w(self.f_small, "✏️") + px(6)) if m.on_rename else 0
        name = self._fit(self.f_name, m.name, limit - x - rename_w)
        cv.create_text(x, ty, text=name, font=self.f_name, fill=C["text"], anchor="w", tags=(tag,))
        bx = x + self._w(self.f_name, name) + px(6)
        if m.on_rename:
            bx = self._text_button(bx, ty, "✏️", self.f_small, C["subtext"], m.on_rename, tag, m.key, "rename") + px(6)
        bh = (self.NAME_H - px(2)) / 2
        for text, color in m.badges:
            w = self._w(self.f_badge, text) + px(10)
            if bx + w > limit:
                break
            cv.create_rectangle(bx, ty - bh, bx + w, ty + bh, fill=C["hover"], outline="", tags=(tag,))
            cv.create_text(bx + w / 2, ty, text=text, font=self.f_badge, fill=color, tags=(tag,))
            bx += w + px(6)

        # ports + geo, cut to the space left of the buttons
        dy = ty + self.NAME_H / 2 + self.LINE_GAP + self.DETAIL_H / 2
        ports = self._fit(self.f_mono, m.ports, limit - x)
        cv.create_text(x, dy, text=ports, font=self.f_mono, fill=C["subtext"], anchor="w", tags=(tag,))
        gx = x + self._w(self.f_mono, ports) + px(12)
        geo = self._fit(self.f_small_b, m.geo, limit - gx)
        if geo:
            cv.create_text(gx, dy, text=geo, font=self.f_small_b, fill=m.geo_fg, anchor="w", tags=(tag,))

    def _draw_header(self, m: HeaderModel, y: int, width: int, tag: str) -> None:
        cv, C, px = self.canvas, self.C, self.px
        mid = y + self.HEADER_H / 2
        x = self.PAD_X
        cv.create_text(x, mid, text=m.icon, font=self.f_head_icon, fill=C["blue"], anchor="w", tags=(tag,))
        x += self._w(self.f_head_icon, m.icon) + px(6)
        name_item = cv.create_text(x, mid, text=m.name, font=self.f_head, fill=C["text"], anchor="w", tags=(tag, f"{tag}name"))
        name_x = x
        x += self._w(self.f_head, m.name) + px(6)
        if m.on_rename:
            x = self._text_button(x, mid, "✏️", self.f_small, C["subtext"],
                                  lambda: self._start_edit(m, name_x, mid, name_item), tag, m.key, "rename") + px(6)
            cv.tag_bind(name_item, "<Double-Button-1>", lambda _e: self._start_edit(m, name_x, mid, name_item))
        cv.create_text(x, mid, text=m.count_text, font=self.f_small, fill=C["subtext"], anchor="w", tags=(tag,))
        x += self._w(self.f_small, m.count_text) + px(10)
        if m.select_button:
            bw = self._w(self.f_small_b, m.select_button.label) + px(16)
            self._button(x + bw, mid - self.BTN_H / 2, self.BTN_H, m.select_button, tag, m.key, font=self.f_small_b)
            x += bw + px(10)
        cv.create_line(x, mid, width - self.PAD_X, mid, fill=C["overlay"], tags=(tag,))

    # ── inline group rename ─────────────────────────────────
    def _start_edit(self, m: HeaderModel, x: int, mid: float, name_item: int) -> None:
        self._close_editor()
        cv = self.canvas
        entry = tk.Entry(cv, font=self.f_head, width=24, bg=self.C["card"], fg=self.C["text"],
                         insertbackground=self.C["text"], relief=tk.FLAT)
        entry.insert(0, m.name)
        entry.select_range(0, tk.END)
        win = cv.create_window(x, mid, window=entry, anchor="w")
        cv.itemconfig(name_item, state="hidden")
        self._editor = entry
        state = {"busy": False, "done": False}

        def finish(save: bool, from_blur: bool = False):
            if state["busy"] or state["done"] or not entry.winfo_exists():
                return
            if save and m.on_rename:
                state["busy"] = True
                try:
                    closed = m.on_rename(entry.get(), from_blur)
                finally:
                    state["busy"] = False
                if not closed:
                    if entry.winfo_exists():
                        entry.focus_set()
                    return
            state["done"] = True
            self._editor = None
            try:
                cv.delete(win)
                cv.itemconfig(name_item, state="normal")
                entry.destroy()
            except tk.TclError:
                pass
            deferred = getattr(self, "_deferred", None)
            if deferred is not None:
                cv.after_idle(lambda: self.render(deferred) if self._editor is None else None)

        entry.bind("<Return>", lambda e: finish(True))
        entry.bind("<KP_Enter>", lambda e: finish(True))
        entry.bind("<Escape>", lambda e: finish(False))
        entry.bind("<FocusOut>", lambda e: finish(True, from_blur=True))
        entry.focus_set()

    def _close_editor(self) -> None:
        if self._editor is not None:
            try:
                self._editor.destroy()
            except tk.TclError:
                pass
            self._editor = None

    @property
    def editor(self) -> Optional[tk.Entry]:
        return self._editor
