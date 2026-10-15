"""macOS-style controls for Tk: rounded buttons, a segmented control, tab chips.

Tk cannot round a Button and its canvas shapes are not anti-aliased, so the rounded
backgrounds are drawn with Pillow at 4x and scaled down (smooth corners), cached per
size/color, and shown on a Canvas; the label stays a Tk text item, crisp at any DPI.
Without Pillow the shapes fall back to smoothed canvas polygons.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from typing import Any, Callable, Iterable, Optional, Sequence

try:
    from PIL import Image, ImageDraw, ImageTk
    HAS_PIL = True
except ImportError:  # pragma: no cover - Pillow ships with the app (tray icon)
    HAS_PIL = False

# ── Palette (macOS dark mode system colors, tuned for the app's dark background) ──
MAC = {
    "accent": "#0a84ff",
    "green": "#2fb350",
    "red": "#ff453a",
    "orange": "#ff9f0a",
    "control": "#3a3a40",        # default push button / segment track
    "control_hi": "#4a4a52",
    "track": "#2a2a31",          # segmented control track
    "segment_on": "#5b5b64",     # selected segment
    "label": "#f2f2f7",          # primary text
    "label_2": "#a1a1aa",        # secondary text
    "label_3": "#6c6c75",        # disabled text
    "separator": "#3f3f47",
    "chip_border": "#4a4a54",
}

# variant -> (fill, text color)
VARIANTS = {
    "primary": (MAC["accent"], "#ffffff"),
    "success": (MAC["green"], "#ffffff"),
    "danger": (MAC["red"], "#ffffff"),
    "warning": (MAC["orange"], "#1d1d1f"),
    "secondary": (MAC["control"], MAC["label"]),
    "tinted": (MAC["control"], "#64a8ff"),
    "ghost": ("", MAC["label"]),
}

RADIUS = 7          # design px
_FAMILY_CACHE: dict[str, str] = {}


def ui_family(root: tk.Misc) -> str:
    """Segoe UI Variable (Windows 11, closest to SF Pro) or Segoe UI."""
    key = str(root.winfo_toplevel())
    if key not in _FAMILY_CACHE:
        fams = set(tkfont.families(root))
        _FAMILY_CACHE[key] = next((f for f in ("Segoe UI Variable Text", "Segoe UI") if f in fams), "TkDefaultFont")
    return _FAMILY_CACHE[key]


def ui_font(root: tk.Misc, size: int = 9, bold: bool = True) -> tkfont.Font:
    return tkfont.Font(root=root, family=ui_family(root), size=size, weight="bold" if bold else "normal")


def scale_of(widget: tk.Misc) -> float:
    try:
        return max(1.0, float(widget.winfo_fpixels("1i")) / 96.0)
    except Exception:
        return 1.0


# ── color helpers ──
def _rgb(color: str) -> tuple[int, int, int]:
    """#rrggbb, or any Tk color name ("SystemButtonFace", "white"...)."""
    if isinstance(color, str) and color.startswith("#") and len(color) == 7:
        return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    root = tk._default_root
    if root is not None:
        try:
            r, g, b = root.winfo_rgb(color)
            return r >> 8, g >> 8, b >> 8
        except tk.TclError:
            pass
    return 30, 30, 46


def _hex(rgb: Iterable[float]) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def mix(a: str, b: str, t: float) -> str:
    ra, rb = _rgb(a), _rgb(b)
    return _hex(x + (y - x) * t for x, y in zip(ra, rb))


def lighten(color: str, t: float) -> str:
    return mix(color, "#ffffff", t)


def darken(color: str, t: float) -> str:
    return mix(color, "#000000", t)


# ── rounded backgrounds ──
_PHOTO_CACHE: dict[tuple, Any] = {}


def rounded_photo(master: tk.Misc, w: int, h: int, fill: str, radius: int,
                  border: str = "", gloss: bool = True):
    """Anti-aliased rounded rectangle as a Tk image (cached). None without Pillow."""
    if not HAS_PIL or w < 2 or h < 2:
        return None
    key = (id(master.tk), w, h, fill, radius, border, gloss)
    photo = _PHOTO_CACHE.get(key)
    # an image belongs to one Tk interpreter: never hand out one from a destroyed root
    if photo is not None and photo.tk is master.tk:
        return photo
    k = 4
    img = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rk = max(1, radius * k)
    if fill:
        if gloss:
            # macOS controls: a faint top-to-bottom gradient and a light top edge
            top, bottom = lighten(fill, 0.07), darken(fill, 0.04)
            grad = Image.new("RGBA", (1, h * k))
            for y in range(h * k):
                grad.putpixel((0, y), _rgb(mix(top, bottom, y / max(1, h * k - 1))) + (255,))
            grad = grad.resize((w * k, h * k))
            mask = Image.new("L", (w * k, h * k), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=rk, fill=255)
            img.paste(grad, (0, 0), mask)
            draw.rounded_rectangle((k, k, w * k - 1 - k, h * k - 1 - k), radius=max(1, rk - k),
                                   outline=lighten(fill, 0.16) + "55", width=k)
        else:
            draw.rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=rk, fill=fill)
    if border:
        draw.rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=rk, outline=border, width=max(1, k))
    img = img.resize((w, h), Image.LANCZOS)
    photo = ImageTk.PhotoImage(img, master=master)
    if len(_PHOTO_CACHE) > 600:
        _PHOTO_CACHE.clear()
    _PHOTO_CACHE[key] = photo
    return photo


def draw_rounded(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float, radius: float,
                 fill: str, border: str = "", gloss: bool = True, tags: Sequence[str] = ()) -> int:
    """Rounded rectangle on a canvas: a smooth image, or a polygon without Pillow."""
    w, h = int(round(x1 - x0)), int(round(y1 - y0))
    # a widget can be configured to ~1 px for a moment (relayout, minimized window)
    photo = rounded_photo(canvas, w, h, fill, int(radius), border, gloss) if w >= 4 and h >= 4 else None
    if photo is not None:
        item = canvas.create_image(x0, y0, image=photo, anchor="nw", tags=tuple(tags))
        canvas._mac_refs = getattr(canvas, "_mac_refs", set()) | {photo}
        return item
    r = radius
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
           x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(pts, smooth=True, fill=fill or "", outline=border or "", tags=tuple(tags))


def set_rounded_fill(canvas: tk.Canvas, item: int, w: int, h: int, radius: int, fill: str,
                     border: str = "", gloss: bool = True) -> None:
    """Recolor a shape made by draw_rounded."""
    if canvas.type(item) == "image":
        if w < 4 or h < 4:
            return
        photo = rounded_photo(canvas, w, h, fill, radius, border, gloss)
        if photo is not None:
            canvas.itemconfig(item, image=photo)
            canvas._mac_refs = getattr(canvas, "_mac_refs", set()) | {photo}
    else:
        canvas.itemconfig(item, fill=fill or "", outline=border or "")


# ── MacButton ──
class MacButton(tk.Canvas):
    """Rounded push button. Variants: primary, success, danger, warning, secondary,
    tinted, ghost; or explicit fill/fg. Supports text and/or an image (icon)."""

    def __init__(self, master: tk.Misc, text: str = "", command: Optional[Callable[[], Any]] = None,
                 variant: str = "secondary", image: Any = None, font: Optional[tkfont.Font] = None,
                 padx: int = 12, pady: int = 5, width_labels: Sequence[str] = (), min_width: int = 0,
                 fill: Optional[str] = None, fg: Optional[str] = None, state: str = tk.NORMAL,
                 selected: bool = False, name_tag: str = "", interactive: bool = True, **kw):
        bg = kw.pop("bg", None) or kw.pop("background", None) or _parent_bg(master)
        super().__init__(master, bg=bg, highlightthickness=0, bd=0, cursor="hand2" if interactive else "", **kw)
        self.s = scale_of(self)
        self._interactive = interactive
        self._text = text
        self._command = command
        self._variant = variant
        self._image = image
        self._font = font or ui_font(self, 9, True)
        self._padx, self._pady = padx, pady
        self._width_labels = tuple(width_labels)
        self._min_width = min_width
        self._fill_override, self._fg_override = fill, fg
        self._state = state
        self._selected = selected
        self._hover = self._pressed = False
        self.name_tag = name_tag or text
        self._size()
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    # sizing / colors
    def px(self, v: float) -> int:
        return int(round(v * self.s))

    def _content_width(self) -> int:
        labels = (self._text,) + self._width_labels
        tw = max((self._font.measure(t) for t in labels if t), default=0)
        iw = self._image.width() if self._image is not None else 0
        gap = self.px(6) if (iw and tw) else 0
        return tw + iw + gap

    def width_for(self, text: Optional[str] = None) -> int:
        """Width this button would need with `text` (current label if None), without changing it."""
        saved = self._text
        if text is not None:
            self._text = text
        try:
            line = int(self._font.metrics("linespace"))
            ih = self._image.height() if self._image is not None else 0
            h = max(line, ih) + 2 * self.px(self._pady)
            return max(self._content_width() + 2 * self.px(self._padx), self.px(self._min_width),
                       h if not self._text else 0)
        finally:
            self._text = saved

    def _size(self) -> None:
        line = int(self._font.metrics("linespace"))
        ih = self._image.height() if self._image is not None else 0
        h = max(line, ih) + 2 * self.px(self._pady)
        w = max(self._content_width() + 2 * self.px(self._padx), self.px(self._min_width), h if not self._text else 0)
        self._req = (w, h)
        tk.Canvas.configure(self, width=w, height=h)

    def colors(self) -> tuple[str, str]:
        fill, fg = VARIANTS.get(self._variant, VARIANTS["secondary"])
        if self._fill_override is not None:
            fill = self._fill_override
        if self._fg_override is not None:
            fg = self._fg_override
        if self._selected and self._variant in ("secondary", "ghost", "tinted"):
            fill = MAC["accent"]
            fg = "#ffffff"
        if self._state == tk.DISABLED:
            return (mix(fill, self["bg"], 0.45) if fill else ""), MAC["label_3"]
        if self._pressed:
            return (darken(fill, 0.14) if fill else MAC["control"]), fg
        if self._hover:
            return (lighten(fill, 0.10) if fill else MAC["control"]), fg
        return fill, fg

    def _draw(self) -> None:
        self.delete("all")
        w = self.winfo_width() if self.winfo_width() > 1 else self._req[0]
        h = self.winfo_height() if self.winfo_height() > 1 else self._req[1]
        fill, fg = self.colors()
        if fill:
            draw_rounded(self, 0, 0, w, h, self.px(RADIUS), fill, gloss=self._variant != "ghost")
        cw = self._content_width() if self._text else (self._image.width() if self._image else 0)
        x = (w - cw) / 2
        if self._image is not None:
            self.create_image(x, h / 2, image=self._image, anchor="w")
            x += self._image.width() + (self.px(6) if self._text else 0)
        if self._text:
            tw = self._font.measure(self._text)
            label_w = self._content_width() - ((self._image.width() + self.px(6)) if self._image else 0)
            self.create_text(x + (label_w - tw) / 2, h / 2, text=self._text, font=self._font, fill=fg, anchor="w")

    # events
    def _on_enter(self, _e=None):
        if self._state != tk.DISABLED and self._interactive:
            self._hover = True
            self._draw()

    def _on_leave(self, _e=None):
        self._hover = self._pressed = False
        self._draw()

    def _on_press(self, _e=None):
        if self._state != tk.DISABLED and self._interactive:
            self._pressed = True
            self._draw()

    def _on_release(self, e=None):
        was = self._pressed
        self._pressed = False
        self._draw()
        inside = e is None or (0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height())
        if was and inside:
            self.invoke()

    def invoke(self):
        if self._state != tk.DISABLED and self._command:
            return self._command()
        return None

    # Button-like options
    _OWN = ("text", "command", "state", "variant", "image", "fill", "fg", "selected", "font")

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        own = {k: kw.pop(k) for k in list(kw) if k in self._OWN}
        resize = False
        for k, v in own.items():
            if k == "text":
                self._text, resize = v, True
            elif k == "command":
                self._command = v
            elif k == "state":
                self._state = str(v)
                tk.Canvas.configure(self, cursor="" if (self._state == tk.DISABLED or not self._interactive) else "hand2")
            elif k == "variant":
                self._variant = v
            elif k == "image":
                self._image, resize = v, True
            elif k == "fill":
                self._fill_override = v
            elif k == "fg":
                self._fg_override = v
            elif k == "selected":
                self._selected = bool(v)
            elif k == "font":
                self._font, resize = v, True
        if kw:
            tk.Canvas.configure(self, **kw)
        if resize:
            self._size()
        if own:
            self._draw()

    config = configure

    def cget(self, key):
        mapping = {"text": self._text, "state": self._state, "variant": self._variant,
                   "selected": self._selected, "command": self._command}
        if key == "fill":
            return self._fill_override if self._fill_override is not None else VARIANTS.get(self._variant, VARIANTS["secondary"])[0]
        if key == "fg":
            return self._fg_override if self._fg_override is not None else VARIANTS.get(self._variant, VARIANTS["secondary"])[1]
        if key in mapping:
            return mapping[key]
        return tk.Canvas.cget(self, key)

    # widget["state"] / widget["text"]: tkinter binds these to Canvas.cget, not to our override
    def __getitem__(self, key):
        return self.cget(key)

    def __setitem__(self, key, value):
        self.configure(**{key: value})

    def req_size(self) -> tuple[int, int]:
        return self._req


def _parent_bg(master: tk.Misc) -> str:
    try:
        return master.cget("bg")
    except Exception:
        return "#1e1e2e"


# ── MacSegmented ──
class MacSegmented(tk.Canvas):
    """macOS segmented control: one track, the selected segment raised."""

    def __init__(self, master: tk.Misc, segments: Sequence[tuple[str, str]], value: str = "",
                 command: Optional[Callable[[str], Any]] = None, font: Optional[tkfont.Font] = None,
                 padx: int = 12, pady: int = 5, **kw):
        bg = kw.pop("bg", None) or _parent_bg(master)
        super().__init__(master, bg=bg, highlightthickness=0, bd=0, cursor="hand2", **kw)
        self.s = scale_of(self)
        self.segments = list(segments)
        self.value = value
        self.command = command
        self._font = font or ui_font(self, 9, True)
        self._padx, self._pady = padx, pady
        self._hover: Optional[str] = None
        self._bounds: list[tuple[str, float, float]] = []
        self._size()
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<ButtonRelease-1>", self._on_click)
        self._draw()

    def px(self, v):
        return int(round(v * self.s))

    def _size(self):
        widths = [self._font.measure(label) + 2 * self.px(self._padx) for _k, label in self.segments]
        self._seg_w = widths
        h = int(self._font.metrics("linespace")) + 2 * self.px(self._pady) + 2 * self.px(2)
        self._req = (sum(widths) + 2 * self.px(2), h)
        tk.Canvas.configure(self, width=self._req[0], height=h)

    def _draw(self):
        self.delete("all")
        w, h = self._req
        inset = self.px(2)
        draw_rounded(self, 0, 0, w, h, self.px(RADIUS + 1), MAC["track"], gloss=False)
        x = inset
        self._bounds = []
        keys = [k for k, _ in self.segments]
        for i, ((key, label), sw) in enumerate(zip(self.segments, self._seg_w)):
            on = key == self.value
            if on:
                draw_rounded(self, x, inset, x + sw, h - inset, self.px(RADIUS - 1), MAC["segment_on"])
            elif self._hover == key:
                draw_rounded(self, x, inset, x + sw, h - inset, self.px(RADIUS - 1), mix(MAC["track"], MAC["segment_on"], 0.4), gloss=False)
            nxt = keys[i + 1] if i + 1 < len(keys) else None
            if nxt is not None and not on and nxt != self.value:
                self.create_line(x + sw, h * 0.28, x + sw, h * 0.72, fill=MAC["separator"])
            self.create_text(x + sw / 2, h / 2, text=label, font=self._font,
                             fill=MAC["label"] if on else MAC["label_2"])
            self._bounds.append((key, x, x + sw))
            x += sw

    def _key_at(self, x):
        return next((k for k, a, b in self._bounds if a <= x <= b), None)

    def _set_hover(self, key):
        if key != self._hover:
            self._hover = key
            self._draw()

    def _on_motion(self, e):
        self._set_hover(self._key_at(e.x))

    def _on_click(self, e):
        key = self._key_at(e.x)
        if key is not None:
            self.select(key, notify=True)

    def select(self, key: str, notify: bool = False):
        changed = key != self.value
        self.value = key
        self._draw()
        if notify and self.command and changed:
            self.command(key)

    def set_value(self, key: str):
        self.select(key, notify=False)

    def width_for(self, labels: Optional[Sequence[str]] = None) -> int:
        labels = list(labels) if labels is not None else [label for _k, label in self.segments]
        return sum(self._font.measure(label) + 2 * self.px(self._padx) for label in labels) + 2 * self.px(2)

    def set_labels(self, labels: Sequence[str]) -> None:
        """Relabel the segments in order (keys unchanged)."""
        new = [(k, label) for (k, _old), label in zip(self.segments, labels)]
        if new != self.segments:
            self.configure_labels(new)

    def configure_labels(self, segments: Sequence[tuple[str, str]]):
        self.segments = list(segments)
        self._size()
        self._draw()

    def req_size(self):
        return self._req


# ── MacTabChip ──
class MacTabChip(tk.Canvas):
    """A closable tab shaped as a pill: outlined when idle, accent-tinted when active."""

    def __init__(self, master: tk.Misc, text: str, command: Optional[Callable[[], Any]] = None,
                 on_close: Optional[Callable[[], Any]] = None, active: bool = False,
                 font: Optional[tkfont.Font] = None, **kw):
        bg = kw.pop("bg", None) or _parent_bg(master)
        super().__init__(master, bg=bg, highlightthickness=0, bd=0, cursor="hand2", **kw)
        self.s = scale_of(self)
        self.text = text
        self.command, self.on_close = command, on_close
        self.active = active
        self._font = font or ui_font(self, 9, True)
        self._close_font = ui_font(self, 8, True)
        self._hover = self._hover_close = False
        self._size()
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonRelease-1>", self._on_click)
        self._draw()

    def px(self, v):
        return int(round(v * self.s))

    def _size(self):
        line = int(self._font.metrics("linespace"))
        h = line + 2 * self.px(5)
        self._close_w = self.px(20)
        w = self.px(12) + self._font.measure(self.text) + self.px(6) + self._close_w
        self._req = (w, h)
        tk.Canvas.configure(self, width=w, height=h)

    def _draw(self):
        self.delete("all")
        w, h = self._req
        r = h // 2
        if self.active:
            fill, border, fg = mix(self["bg"], MAC["accent"], 0.32), MAC["accent"], "#ffffff"
        else:
            fill = mix(self["bg"], MAC["control"], 0.55 if self._hover else 0.0)
            border, fg = MAC["chip_border"], MAC["label"]
        draw_rounded(self, 0, 0, w, h, r, fill or self["bg"], border=border, gloss=False)
        self.create_text(self.px(12), h / 2, text=self.text, font=self._font, fill=fg, anchor="w")
        cx = w - self._close_w / 2 - self.px(3)
        if self._hover_close:
            draw_rounded(self, cx - self.px(8), h / 2 - self.px(8), cx + self.px(8), h / 2 + self.px(8),
                         self.px(8), mix(fill or self["bg"], "#ffffff", 0.18), gloss=False)
        self.create_text(cx, h / 2, text="✕", font=self._close_font, fill=fg if self._hover_close else MAC["label_2"])

    def _in_close(self, x):
        return x >= self._req[0] - self._close_w - self.px(3)

    def _on_motion(self, e):
        hover_close = self._in_close(e.x)
        if not self._hover or hover_close != self._hover_close:
            self._hover, self._hover_close = True, hover_close
            self._draw()

    def _on_leave(self, _e):
        self._hover = self._hover_close = False
        self._draw()

    def _on_click(self, e):
        if self._in_close(e.x):
            self.close()
        else:
            self.select()

    def select(self):
        if self.command:
            self.command()

    def close(self):
        if self.on_close:
            self.on_close()

    def set_active(self, active: bool):
        self.active = bool(active)
        self._draw()

    def req_size(self):
        return self._req


class MacDivider(tk.Frame):
    """A thin vertical separator between groups of controls."""

    def __init__(self, master: tk.Misc, height: int = 22, **kw):
        bg = _parent_bg(master)
        super().__init__(master, bg=bg, **kw)
        s = scale_of(self)
        tk.Frame(self, bg=MAC["separator"], width=max(1, int(round(s))), height=int(round(height * s))).pack(
            padx=int(round(8 * s)))
