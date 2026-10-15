"""Layout audit: finds controls that are clipped, overlap, or are squeezed below the
size their text needs ("buttons that drift"), plus the same for the drawn Overview cards.

Used by the layout tests and by hand: audit(root) -> list of Issue.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from typing import Iterable, Optional

try:
    from macui import MacButton, MacSegmented, MacTabChip
    _SIZED = (tk.Button, tk.Label, tk.Checkbutton, tk.Radiobutton, MacButton, MacSegmented, MacTabChip)
except Exception:  # pragma: no cover
    _SIZED = (tk.Button, tk.Label, tk.Checkbutton, tk.Radiobutton)


@dataclass
class Issue:
    kind: str           # clipped | overlap | squeezed | card-overlap | card-clipped
    where: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.where}: {self.detail}"


Rect = tuple[int, int, int, int]


def _rect(w: tk.Misc) -> Rect:
    x, y = w.winfo_rootx(), w.winfo_rooty()
    return x, y, x + w.winfo_width(), y + w.winfo_height()


def _inter(a: Rect, b: Rect) -> Optional[Rect]:
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return r if r[0] < r[2] and r[1] < r[3] else None


def _area(r: Optional[Rect]) -> int:
    return 0 if r is None else (r[2] - r[0]) * (r[3] - r[1])


def _label(w: tk.Misc) -> str:
    for key in ("text",):
        try:
            text = w.cget(key)
            if text:
                return f"{w.winfo_class()}('{str(text)[:30]}')"
        except Exception:
            pass
    return f"{w.winfo_class()} {str(w)[-40:]}"


def _master(w: tk.Misc) -> str:
    info = w.pack_info() if w.winfo_manager() == "pack" else w.grid_info()
    return str(info.get("in", w.master))


def audit(root: tk.Misc, skip: Iterable[tk.Misc] = (), tolerance: int = 1) -> list[Issue]:
    """Walk the mapped widget tree under root. Scrolling canvases are not entered
    (their content is meant to extend beyond the viewport); use audit_overview for it."""
    issues: list[Issue] = []
    skip = set(map(str, skip))

    def visit(w: tk.Misc, clip: Rect) -> None:
        if str(w) in skip or not w.winfo_ismapped():
            return
        if w.winfo_width() <= 1 or w.winfo_height() <= 1:
            return
        r = _rect(w)
        if w is not root:
            if (r[0] < clip[0] - tolerance or r[1] < clip[1] - tolerance
                    or r[2] > clip[2] + tolerance or r[3] > clip[3] + tolerance):
                issues.append(Issue("clipped", _label(w), f"{r} outside visible {clip}"))
            if isinstance(w, _SIZED):
                req_w = w.req_size()[0] if hasattr(w, "req_size") else w.winfo_reqwidth()
                req_h = w.req_size()[1] if hasattr(w, "req_size") else w.winfo_reqheight()
                if w.winfo_width() + tolerance < req_w or w.winfo_height() + tolerance < req_h:
                    issues.append(Issue("squeezed", _label(w),
                                        f"{w.winfo_width()}x{w.winfo_height()} < needed {req_w}x{req_h}"))
        kids = [c for c in w.winfo_children()
                if not isinstance(c, (tk.Toplevel, tk.Menu)) and c.winfo_ismapped()
                and c.winfo_width() > 1 and str(c) not in skip]
        placed = [c for c in kids if c.winfo_manager() in ("pack", "grid")]
        for i, a in enumerate(placed):
            for b in placed[i + 1:]:
                # only widgets laid out by the same master can collide (pack(in_=...) moves
                # a child into another frame, which then legitimately contains it)
                if _master(a) != _master(b):
                    continue
                if _area(_inter(_rect(a), _rect(b))) > tolerance * tolerance * 4:
                    issues.append(Issue("overlap", f"{_label(a)} × {_label(b)}", f"{_rect(a)} ∩ {_rect(b)}"))
        inner = _inter(clip, r) or r
        if isinstance(w, tk.Canvas) and getattr(w, "is_scroll_host", False):
            # scrolling area: content may run past the bottom, never past the sides
            inner = (inner[0], -10 ** 9, inner[2], 10 ** 9)
        for c in kids:
            visit(c, inner)

    visit(root, _rect(root))
    return issues


def audit_overview(view) -> list[Issue]:
    """Drawn Overview cards: text must not run into buttons or past the card's edge."""
    issues: list[Issue] = []
    cv = view.canvas
    width = cv.winfo_width()
    for key, slots in view.card_slots.items():
        for y, tag in slots:
            items = cv.find_withtag(tag)
            buttons, texts = [], []
            for item in items:
                tags = cv.gettags(item)
                bbox = cv.bbox(item)
                if not bbox:
                    continue
                if bbox[2] > width + 1 or bbox[0] < -1:
                    issues.append(Issue("card-clipped", f"card {y}", f"item {cv.type(item)} {bbox} beyond width {width}"))
                if any(t.startswith("btn") for t in tags):
                    if cv.type(item) in ("rectangle", "image", "polygon"):
                        buttons.append(bbox)
                elif cv.type(item) == "text":
                    texts.append((cv.itemcget(item, "text"), bbox))
            for text, tb in texts:
                for bb in buttons:
                    if _area(_inter(tb, bb)) > 4:
                        issues.append(Issue("card-overlap", f"card {y}", f"text '{text[:24]}' {tb} overlaps button {bb}"))
            for i, a in enumerate(buttons):
                for b in buttons[i + 1:]:
                    if _area(_inter(a, b)) > 4:
                        issues.append(Issue("card-overlap", f"card {y}", f"buttons {a} and {b} overlap"))
    return issues
