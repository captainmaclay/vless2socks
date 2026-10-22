"""The "edit" icon is the slanted pencil U+270E (✎) everywhere.

U+270F with U+FE0F (✏️) came out on Windows/Tk as an unreadable horizontal stub: Segoe UI
has no pencil, the fallback Segoe UI Symbol draws U+270F lying flat, and Tk does not
handle the emoji selector U+FE0F."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PENCIL = "✎"
FILES = ("gui.py", "i18n.py", "overview_view.py", "macui.py", "tray_widget.py")


class EditIconTest(unittest.TestCase):
    def test_no_old_pencil_anywhere(self):
        for name in FILES:
            path = ROOT / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertNotIn("✏", text)        # ✏ (with or without U+FE0F)

    def test_configure_label_uses_the_pencil(self):
        import i18n
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                i18n.set_language(lang)
                self.assertTrue(i18n.t("btn_view_tab").startswith(PENCIL + " "))
        i18n.set_language("ru")

    def test_short_form_is_the_pencil(self):
        import i18n
        import responsive
        self.assertEqual(responsive.icon_of(i18n.t("btn_view_tab")), PENCIL)


if __name__ == "__main__":
    unittest.main()
