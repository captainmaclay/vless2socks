"""Responsive layout: five named layouts by window proportion + label fitting per row.

Two mechanisms keep controls from drifting at any window size:

1. Rows fit their labels to the width they actually get: every adaptive control has
   variants (full text -> short text -> icon); a row uses the most detailed level at
   which everything fits. A row that does not fit even with icons reports overflow and
   the layout moves a part of it to its own line. This works for every width, not only
   at the five presets.
2. Five named layouts (by width and portrait/landscape) decide the structure: what is
   shown, what gets its own line, how a proxy card is arranged. They also give the
   preset window sizes (Options / Ctrl+1..5).

All sizes here are logical (DIP, 96 dpi); multiply by the screen scale for pixels.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

# ── the five layouts ──
MINI, SNAP, COMPACT, STANDARD, WIDE = "mini", "snap", "compact", "standard", "wide"


@dataclass(frozen=True)
class Layout:
    key: str
    title_key: str           # i18n key of the name shown in Options
    preset: tuple[int, int]  # preset window size, DIP
    aspect: str              # human-readable proportion


LAYOUTS: tuple[Layout, ...] = (
    Layout(MINI, "layout_mini", (560, 920), "3:5"),
    Layout(SNAP, "layout_snap", (768, 920), "5:6"),
    Layout(COMPACT, "layout_compact", (900, 680), "4:3"),
    Layout(STANDARD, "layout_standard", (1280, 800), "16:10"),
    Layout(WIDE, "layout_wide", (1536, 864), "16:9"),
)
BY_KEY = {lay.key: lay for lay in LAYOUTS}

#: The window may not get smaller than this (DIP): the Mini layout's minimum
MIN_SIZE = (520, 560)
#: Allowed width/height range while resizing: no near-invisible strips, yet Windows Snap
#: halves (~0.8) and maximized windows on 16:9 / 16:10 / 21:9 screens all fit in it
ASPECT_RANGE = (0.45, 2.6)


def classify(width: float, height: float) -> str:
    """Layout for a window of width x height DIP."""
    if width < 700:
        return MINI
    if width < 900 and height >= width:
        return SNAP
    if width < 1100:
        return COMPACT
    if width < 1440:
        return STANDARD
    return WIDE


def fit_preset(key: str, screen_w: int, screen_h: int, margin: int = 40) -> tuple[int, int]:
    """Preset size of a layout, shrunk to the screen (DIP) when it is larger."""
    w, h = BY_KEY[key].preset
    return min(w, screen_w - margin), min(h, screen_h - margin)


# ── adaptive labels ──
class Adaptive:
    """A control with label variants, most detailed first. Supports MacButton,
    MacSegmented (variants are lists of segment labels), Label and anything with
    width_for()/configure(text=...)."""

    def __init__(self, widget: Any, variants: Sequence[Any], hide_at: Optional[int] = None):
        self.widget = widget
        self.variants = list(variants)
        self.hide_at = hide_at          # at this level and beyond the control is hidden
        self.level = 0

    def variant(self, level: int) -> Any:
        return self.variants[min(level, len(self.variants) - 1)]

    def hidden_at(self, level: int) -> bool:
        return self.hide_at is not None and level >= self.hide_at

    def width_at(self, level: int) -> int:
        if self.hidden_at(level):
            return 0
        w, v = self.widget, self.variant(level)
        if hasattr(w, "width_for"):
            return int(w.width_for(v))
        font = tkfont.Font(font=w.cget("font"))
        pad = 2 * int(str(w.cget("padx") or 0)) if "padx" in w.keys() else 0
        return font.measure(v) + pad + 2 * int(str(w.cget("bd") or 0))

    def apply(self, level: int) -> None:
        self.level = level
        w, v = self.widget, self.variant(level)
        if hasattr(w, "set_labels"):
            w.set_labels(v)
        elif w.cget("text") != v:
            w.configure(text=v)
        info = getattr(w, "_adaptive_pack", None)
        if self.hidden_at(level):
            if w.winfo_manager() == "pack":
                slaves = w.master.pack_slaves()
                i = slaves.index(w) if w in slaves else -1
                # remember the neighbour so the widget comes back to the same place
                w._adaptive_pack = (w.pack_info(), slaves[i + 1] if 0 <= i < len(slaves) - 1 else None)
                w.pack_forget()
        elif info is not None and not w.winfo_manager():
            opts, before = info
            opts = dict(opts)
            opts.pop("in", None)
            if before is not None and before.winfo_exists() and before.winfo_manager() == "pack":
                opts["before"] = before
            w.pack(**opts)
            w._adaptive_pack = None


@dataclass
class AdaptiveRow:
    """Controls sharing one row. `fixed` are widgets that keep their size; `gap` is
    the total of paddings between items (DIP * scale), `levels` how many label levels."""
    items: list[Adaptive]
    fixed: list[Any] = field(default_factory=list)
    gap: int = 0
    levels: int = 3
    level: int = 0

    def width_at(self, level: int) -> int:
        fixed = 0
        for w in self.fixed:
            if w.winfo_manager():
                fixed += w.req_size()[0] if hasattr(w, "req_size") else w.winfo_reqwidth()
        return sum(item.width_at(level) for item in self.items) + fixed + self.gap

    def fit(self, available: int) -> bool:
        """Apply the most detailed level that fits. Returns False when even the last
        level overflows (the caller then rearranges the row)."""
        chosen, ok = self.levels - 1, False
        for level in range(self.levels):
            if self.width_at(level) <= available:
                chosen, ok = level, True
                break
        if chosen != self.level or any(i.level != chosen for i in self.items):
            for item in self.items:
                item.apply(chosen)
            self.level = chosen
        return ok


def icon_of(label: str) -> str:
    """The leading symbol of a label ("🔗 Добавить подписку" -> "🔗")."""
    head = label.strip().split(" ")[0] if label.strip() else label
    return head if head and not head[0].isalnum() else label


def variants(full: str, short: Optional[str] = None, icon: Optional[str] = None) -> list[str]:
    return [full, short if short is not None else full, icon if icon is not None else icon_of(full)]


# ── wrapping rows and labels (tabs other than the Overview) ──
class Flow:
    """Lays widgets out left to right inside `container`, wrapping onto new lines when
    the container is too narrow. While everything fits on one line, `right` widgets stay
    right-aligned (like pack(side=RIGHT)); when wrapped, they continue left-aligned.

    All widgets must be children of `container` (Tk can pack a widget only into its
    parent or the parent's descendants). The container must get its width from its
    master (pack fill=X / grid sticky=ew)."""

    def __init__(self, container: tk.Misc, left: Sequence[tk.Misc], right: Sequence[tk.Misc] = (),
                 gap: int = 6, line_gap: int = 4):
        self.container = container
        self.left, self.right = list(left), list(right)
        self.gap, self.line_gap = gap, line_gap
        self._lines: list[tk.Frame] = []
        self._width = 0
        for w in self.left + self.right:
            if w.winfo_manager():
                w.pack_forget() if w.winfo_manager() == "pack" else w.grid_forget()
        container.bind("<Configure>", self._on_configure, add="+")
        container.after_idle(self.reflow)

    @staticmethod
    def _req(w: tk.Misc) -> int:
        return w.req_size()[0] if hasattr(w, "req_size") else w.winfo_reqwidth()

    def _on_configure(self, event) -> None:
        if event.widget is self.container and event.width != self._width:
            self.reflow()

    def _line(self, n: int) -> tk.Frame:
        while len(self._lines) <= n:
            self._lines.append(tk.Frame(self.container, bg=self.container.cget("bg")))
        return self._lines[n]

    def reflow(self) -> None:
        c = self.container
        try:
            if not c.winfo_exists():
                return
        except tk.TclError:
            return
        width = c.winfo_width()
        if width <= 1:
            return
        self._width = width
        items = [w for w in self.left + self.right if w.winfo_exists()]
        # every item is packed with its gap after it, the last one on a line included
        total = sum(self._req(w) + self.gap for w in items)
        one_line = total <= width
        lines: list[list[tk.Misc]] = [[]]
        x = 0
        for w in items:
            cw = self._req(w) + self.gap
            if not one_line and x and x + cw > width:
                lines.append([])
                x = 0
            lines[-1].append(w)
            x += cw
        for w in items:
            w.pack_forget()
        for n, ws in enumerate(lines):
            line = self._line(n)
            line.pack(side=tk.TOP, fill=tk.X, anchor="w", pady=(0, self.line_gap if n < len(lines) - 1 else 0))
            for w in ws:
                side = tk.RIGHT if (one_line and w in self.right) else tk.LEFT
                pad = (self.gap, 0) if side == tk.RIGHT else (0, self.gap)
                w.pack(in_=line, side=side, padx=pad, anchor="w")
                w.tk.call("raise", w._w, line._w)     # line frames are created later: keep widgets on top
        for line in self._lines[len(lines):]:
            line.pack_forget()
        # right-aligned widgets were packed after the left ones; keep the visual order
        if one_line:
            for w in reversed([w for w in self.right if w.winfo_exists()]):
                w.pack_configure(side=tk.RIGHT)


def autowrap(root: tk.Misc, min_chars: int = 40, margin: int = 8) -> None:
    """Long single-column labels (descriptions) under `root` wrap to their container's
    width instead of overflowing it. Labels packed beside other widgets are left alone."""
    def visit(w: tk.Misc) -> None:
        for c in w.winfo_children():
            if isinstance(c, tk.Label) and not getattr(c, "_autowrap", False):
                try:
                    text = str(c.cget("text"))
                    info = c.pack_info() if c.winfo_manager() == "pack" else None
                except tk.TclError:
                    continue
                if len(text) >= min_chars and info is not None and info.get("side") in ("top", "bottom"):
                    _bind_wrap(c, info, margin)
            visit(c)
    visit(root)


def _bind_wrap(label: tk.Label, info: dict, margin: int) -> None:
    label._autowrap = True
    master = label.master
    padx = info.get("padx", 0)
    pads = sum(int(p) for p in padx) if isinstance(padx, (tuple, list)) else 2 * int(padx or 0)
    if str(label.cget("justify")) == "center":
        label.configure(justify=tk.LEFT)

    def on_configure(event=None):
        try:
            width = master.winfo_width()
            if width > 1:
                wrap = max(80, width - pads - margin)
                if int(str(label.cget("wraplength")) or 0) != wrap:
                    label.configure(wraplength=wrap)
        except tk.TclError:
            pass

    master.bind("<Configure>", on_configure, add="+")
    label.after_idle(on_configure)
