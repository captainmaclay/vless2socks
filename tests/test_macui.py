"""macOS-style controls: real mouse events, colors, sizing, image cache."""

import gc
import sys
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import macui
from macui import MAC, MacButton, MacDivider, MacSegmented, MacTabChip


class _TkCase(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.configure(bg="#1e1e2e")
        self.root.geometry("+40+40")
        self.frame = tk.Frame(self.root, bg="#1e1e2e")
        self.frame.pack()

    def tearDown(self):
        self.root.destroy()
        self.root = None
        gc.collect()

    def show(self, w):
        w.pack(padx=10, pady=10)
        self.root.update()
        return w

    def click(self, w, x, y, release_at=None):
        w.event_generate("<Enter>", x=x, y=y)
        w.event_generate("<ButtonPress-1>", x=x, y=y)
        rx, ry = release_at or (x, y)
        w.event_generate("<ButtonRelease-1>", x=rx, y=ry)
        self.root.update()


class TestMacButton(_TkCase):
    def test_click_inside_invokes_release_outside_cancels(self):
        cmd = mock.MagicMock()
        b = self.show(MacButton(self.frame, text="Start", variant="success", command=cmd))
        self.click(b, 10, 10)
        cmd.assert_called_once()
        self.click(b, 10, 10, release_at=(b.winfo_width() + 30, 10))   # dragged off: no action (macOS behaviour)
        cmd.assert_called_once()

    def test_disabled_does_nothing(self):
        cmd = mock.MagicMock()
        b = self.show(MacButton(self.frame, text="Go", command=cmd, state=tk.DISABLED))
        self.click(b, 5, 5)
        self.assertIsNone(b.invoke())
        cmd.assert_not_called()
        self.assertEqual(b["state"], tk.DISABLED)          # item access, as old tk.Button code did
        b.config(state=tk.NORMAL)
        self.click(b, 5, 5)
        cmd.assert_called_once()

    def test_hover_lighter_pressed_darker(self):
        b = self.show(MacButton(self.frame, text="X", variant="primary"))
        normal = b.colors()[0]
        b._on_enter()
        hover = b.colors()[0]
        b._on_press()
        pressed = b.colors()[0]
        lum = lambda c: sum(macui._rgb(c))
        self.assertEqual(normal, MAC["accent"])
        self.assertGreater(lum(hover), lum(normal))
        self.assertLess(lum(pressed), lum(normal))

    def test_selected_secondary_turns_accent(self):
        b = self.show(MacButton(self.frame, text="Select"))
        self.assertEqual(b.colors()[0], MAC["control"])
        b.config(selected=True)
        self.assertEqual(b.colors(), (MAC["accent"], "#ffffff"))

    def test_width_labels_keep_width_stable(self):
        b = self.show(MacButton(self.frame, text="Restart", width_labels=("⏳ Restarting services…", "✅ Done")))
        w = b.winfo_width()
        for text in ("⏳ Restarting services…", "✅ Done", "Restart"):
            b.config(text=text)
            self.root.update()
            self.assertEqual(b.winfo_width(), w)

    def test_text_change_resizes_without_width_labels(self):
        b = self.show(MacButton(self.frame, text="A"))
        w = b.winfo_width()
        b.config(text="A much longer label")
        self.root.update()
        self.assertGreater(b.winfo_width(), w)
        self.assertEqual(b.cget("text"), "A much longer label")

    def test_badge_is_not_interactive(self):
        b = self.show(MacButton(self.frame, text="BADGE", variant="primary", interactive=False))
        self.assertEqual(str(b.cget("cursor")), "")
        b._on_enter()
        self.assertEqual(b.colors()[0], MAC["accent"])   # no hover tint

    def test_icon_only_is_square_or_wider(self):
        img = tk.PhotoImage(master=self.root, width=16, height=16)
        b = self.show(MacButton(self.frame, image=img))
        self.assertGreaterEqual(b.winfo_width(), b.winfo_height())


class TestRoundedImage(_TkCase):
    def test_corners_transparent_center_filled(self):
        if not macui.HAS_PIL:
            self.skipTest("Pillow missing")
        photo = macui.rounded_photo(self.root, 60, 24, "#0a84ff", 7, gloss=False)
        self.assertEqual(photo.width(), 60)
        img = macui.ImageTk.getimage(photo)
        self.assertLess(img.getpixel((0, 0))[3], 40)                 # rounded corner: see-through
        r, g, b, a = img.getpixel((30, 12))
        self.assertEqual(a, 255)
        self.assertEqual((r, g, b), macui._rgb("#0a84ff"))

    def test_cache_never_returns_an_image_of_a_destroyed_root(self):
        if not macui.HAS_PIL:
            self.skipTest("Pillow missing")
        macui.rounded_photo(self.root, 30, 20, "#ff0000", 5)
        self.root.destroy()
        self.root = tk.Tk()
        photo = macui.rounded_photo(self.root, 30, 20, "#ff0000", 5)
        self.assertIs(photo.tk, self.root.tk)
        c = tk.Canvas(self.root)
        c.create_image(0, 0, image=photo)                        # would raise "image doesn't exist"

    def test_named_colors_work(self):
        self.assertEqual(macui.mix("white", "#000000", 0.0), "#ffffff")


class TestSegmented(_TkCase):
    def test_click_switches_and_notifies_once(self):
        got = []
        seg = self.show(MacSegmented(self.frame, [("list", "List"), ("groups", "By groups")], value="list",
                                     command=got.append))
        _, a1, b1 = seg._bounds[1]
        self.click(seg, int((a1 + b1) / 2), 10)
        self.assertEqual(got, ["groups"])
        self.assertEqual(seg.value, "groups")
        self.click(seg, int((a1 + b1) / 2), 10)                  # clicking the current one: no event
        self.assertEqual(got, ["groups"])
        seg.set_value("list")                                    # programmatic: no event
        self.assertEqual(got, ["groups"])


class TestTabChip(_TkCase):
    def test_close_area_vs_body(self):
        sel, close = mock.MagicMock(), mock.MagicMock()
        chip = self.show(MacTabChip(self.frame, text="📁 Finland", command=sel, on_close=close))
        w, h = chip.winfo_width(), chip.winfo_height()
        self.click(chip, 12, h // 2)
        sel.assert_called_once()
        close.assert_not_called()
        self.click(chip, w - 8, h // 2)
        close.assert_called_once()
        sel.assert_called_once()

    def test_active_state(self):
        chip = self.show(MacTabChip(self.frame, text="Tab"))
        chip.set_active(True)
        self.assertTrue(chip.active)


class TestDivider(_TkCase):
    def test_divider_is_thin(self):
        d = self.show(MacDivider(self.frame, height=20))
        line = d.winfo_children()[0]
        self.assertLessEqual(line.winfo_width(), 3)


if __name__ == "__main__":
    unittest.main()
